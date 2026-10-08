"""Bounded typed native-observer row association. No IO or ROS imports.

This validates an explicitly reviewed subprocess association; JSON is not a
sensor authority by itself. Original CDR is decoded from its exact byte hex,
never reconstructed from scalar/header/range fields.
"""
import inputs
import hashlib
import re
from common.original_cdr import LASER_TYPE, IMU_TYPE, WireScan, decode, MAX_CDR_BYTES
from common.native_ownership import RawReceipt, RAW_SCHEMA, _ns, _sha
from physical_ingress import PhysicalOriginalIngress, require


def hexadecimal(value, count=None):
    require(type(value) is str and len(value) % 2 == 0 and
            len(value) <= MAX_CDR_BYTES*2 and re.fullmatch('[0-9a-f]*', value) is not None,
            'bounded_exact_lowercase_byte_hex_required')
    raw = bytes.fromhex(value)
    require(count is None or len(raw) == count, 'original_byte_hex_length_mismatch')
    return raw


class OriginalObserverRows:
    def __init__(self, ingress, *, expected_observer_pid, expected_time_namespace):
        require(type(ingress) is PhysicalOriginalIngress, 'physical_original_ingress_required')
        require(ingress.observer_contract_sha256 == inputs.OBSERVER_SHA,
                'exact_reviewed_native_observer_contract_required')
        require(type(expected_observer_pid) is int and expected_observer_pid > 0 and
                type(expected_time_namespace) is str and len(expected_time_namespace) <= 128,
                'root_verified_observer_process_and_time_namespace_required')
        self.ingress = ingress
        self.expected_pid, self.expected_time_namespace = expected_observer_pid, expected_time_namespace
        self.begin, self.ended, self.ready = None, False, False
        self.rows, self.total_original_bytes = 0, 0
        self.samples = {LASER_TYPE: 0, IMU_TYPE: 0}

    def receive(self, row):
        try:
            return self._receive(row)
        except Exception:
            self.ended = True
            self.ingress.stop()
            raise

    def _receive(self, row):
        require(type(row) is dict and not self.ended, 'bounded_live_observer_row_required')
        require(row.get('mission_admission') is False, 'native_observer_mission_admission_forbidden')
        if row.get('kind') == 'begin':
            require(self.begin is None and row.get('schema') == 'ubts.private.actual_native_cdr_observer.v1'
                    and type(row.get('pid')) is int and row.get('pid') == self.expected_pid and
                    row.get('time_namespace') == self.expected_time_namespace,
                    'root_verified_native_observer_begin_required')
            require(type(row.get('observer_process_session')) is str and
                    re.fullmatch('[0-9a-f]{32}', row['observer_process_session']) is not None and
                    row.get('RAW_domain') == self.ingress.domain and row.get('GID_storage_bytes') == 16
                    and row.get('observer_clock_type') == 'RCL_SYSTEM_TIME' and
                    row.get('use_sim_time') is False and row.get('clock_topic_created') is False,
                    'native_physical_clock_and_receipt_association_required')
            require(hexadecimal(row.get('expected_scan_GID'), 16) == self.ingress.expected_gids[0]
                    and hexadecimal(row.get('expected_IMU_GID'), 16) == self.ingress.expected_gids[1],
                    'reviewed_native_observer_publisher_binding_required')
            self.begin = dict(row)  # observer session is NOT publisher epoch
            return None
        require(self.begin is not None, 'native_observer_begin_missing')
        if row.get('kind') == 'ready':
            require(not self.ready and min(self.samples.values()) >= 2 and
                    row.get('stable_graph_min_seconds') == .05 and
                    row.get('actual_original_CDR_and_per_message_GIDs') is True,
                    'native_observer_actual_GID_readiness_required')
            self.ready = True
            return None
        if row.get('kind') == 'done':
            self.ended = True
            require(self.ready and row.get('ok') is True and row.get('ROS_node_destroyed') is True and
                    row.get('ROS_shutdown_completed') is True and row.get('physical_autonomy_qualified') is False,
                    'native_observer_failed_or_incomplete')
            require(type(row.get('scan_samples')) is int and type(row.get('IMU_samples')) is int and
                    row.get('scan_samples') == self.samples[LASER_TYPE] and
                    row.get('IMU_samples') == self.samples[IMU_TYPE], 'native_observer_delivery_count_mismatch')
            return None
        require(row.get('kind') == 'original_cdr' and self.rows < 4096,
                'native_observer_row_kind_or_count_bound')
        kind = row.get('ros_type')
        require(kind in self.samples and row.get('topic') == (
            self.ingress.scan_topic if kind == LASER_TYPE else self.ingress.imu_topic),
            'original_native_topic_or_type_mismatch')
        cdr = hexadecimal(row.get('original_CDR_hex'))
        require(type(row.get('original_CDR_bytes')) is int and len(cdr) == row['original_CDR_bytes']
                and _sha(row.get('original_CDR_sha256')) and
                hashlib.sha256(cdr).hexdigest() == row['original_CDR_sha256'], 'original_CDR_SHA_or_length_mismatch')
        require(self.total_original_bytes+len(cdr) <= 16*1024*1024,
                'native_observer_original_payload_total_capacity')
        wire = decode(cdr, kind)
        require(all(type(row.get(key)) is int for key in ('original_header_stamp_sec',
            'original_header_stamp_nanosec', 'original_header_stamp_ns', 'ranges_count', 'intensities_count')),
            'typed_original_native_header_and_counts_required')
        require((wire.header.sec, wire.header.nanosec, wire.header.stamp_ns) == (
            row.get('original_header_stamp_sec'), row.get('original_header_stamp_nanosec'),
            row.get('original_header_stamp_ns')) and
            wire.header.frame_id.encode() == hexadecimal(row.get('frame_id_bytes_hex')),
            'observer_original_header_metadata_mismatch')
        expected_counts = (wire.count, len(wire.intensity_f32_bytes)//4) if type(wire) is WireScan else (0, 0)
        require(expected_counts == (row.get('ranges_count'), row.get('intensities_count')),
                'observer_original_array_count_mismatch')
        require(row.get('RAW_schema') == RAW_SCHEMA and row.get('RAW_domain') == self.ingress.domain and
                _ns(row.get('callback_monotonic_raw_ns')) and _ns(row.get('observer_system_ros_now_ns')),
                'original_native_callback_clocks_required')
        require(all(type(row.get(k)) in (int, float) and row[k] == 0
                    for k in ('proposed_linear_mps', 'proposed_yaw_rate_rad_s')),
                'original_observer_nonzero_proposal_forbidden')
        outcome = self.ingress.consume_original(kind, cdr,
            callback_receipt=RawReceipt(row['callback_monotonic_raw_ns'], row['RAW_domain']),
            callback_ros_system_now_ns=row['observer_system_ros_now_ns'],
            publisher_gid=hexadecimal(row.get('publisher_gid_hex'), 16))
        require(self.ingress.fault is None, 'native_original_ingress_rejected:'+outcome.reason)
        self.rows += 1
        self.samples[kind] += 1
        self.total_original_bytes += len(cdr)
        return outcome
