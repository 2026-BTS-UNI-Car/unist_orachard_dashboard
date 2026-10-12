"""Continuous original-CDR protocol; no process, ROS init, thread or IO owner.

The runner verifies the unchanged physical package before importing this module,
constructs its exact receiver/factory, resets the receiver BEFORE native spawn,
and supplies the separately reviewed streaming observer contract SHA. This is a
single consumer. Stop may concurrently inhibit it; outputs are fresh caller-owned
JSON values, not sensor inputs or mutable references retained by this adapter.
"""
from dataclasses import asdict
import copy
import hashlib
import json
import math
import re
import threading

from common.original_cdr import LASER_TYPE, IMU_TYPE, WireScan, decode, MAX_CDR_BYTES
from common.native_ownership import RawReceipt, RAW_SCHEMA, _ns, _sha
from physical_ingress import PhysicalOriginalIngress, SOURCE_AGE_NS, RECEIPT_AGE_NS, require
from physical_factory import PhysicalNativeShadowFactory

SCHEMA = 'ubts.private.actual_native_cdr_observer.v1'
MAX_COUNTER = 2**63 - 1
MAX_METADATA_BYTES = 8192
MAX_LINE_BYTES = 2 * MAX_CDR_BYTES + MAX_METADATA_BYTES


def _counter(value):
    return type(value) is int and 0 <= value <= MAX_COUNTER


def _hex(value, count=None):
    require(type(value) is str and len(value) % 2 == 0 and
            len(value) <= 2 * MAX_CDR_BYTES and
            re.fullmatch('[0-9a-f]*', value) is not None,
            'bounded_exact_lowercase_byte_hex_required')
    require(count is None or len(value) == 2 * count,
            'original_byte_hex_length_mismatch')
    return bytes.fromhex(value)


def _unique_object(items):
    value = {}
    for key, item in items:
        require(key not in value, 'duplicate_native_stream_JSON_key')
        value[key] = item
    return value


def _invalid_number(_value):
    raise ValueError('nonfinite_native_stream_JSON_number')


def _finite_float(value):
    result = float(value)
    require(math.isfinite(result), 'nonfinite_native_stream_JSON_number')
    return result


class StreamObserverRows:
    """Strict contiguous streaming protocol with bounded latest state only.

    receive_line returns a tuple of fresh {diagnostic, evidence} JSON values.
    Every bracketed pair is consumed before the next original line. Startup
    scans without any available start IMU are counted as unadmitted priming;
    a pending end bracket waits for original IMU delivery without replacement.
    There is no lifetime sample limit, replay or silently skipped fit.
    The exact factory always emits HOLD/zero and unverified physical flags.

    stop() first sets both cancellation events, then invalidates original ingress;
    it does not wait for the factory's numeric lock. finish() is called by the
    sole consumer AFTER process/pipe drain and clears the factory's pending state.
    Post-Stop original lines still undergo byte/header/GID/sequence validation,
    but never enter the receiver, fit or publish another diagnostic.
    """
    def __init__(self, ingress, factory, *, observer_contract_sha256, map_adapter=None, gnss_dispatcher=None, control_adapter=None):
        require(type(ingress) is PhysicalOriginalIngress,
                'physical_original_ingress_required')
        require(type(factory) is PhysicalNativeShadowFactory and factory.ingress is ingress,
                'exact_physical_original_factory_required')
        require(_sha(observer_contract_sha256) and
                ingress.observer_contract_sha256 == observer_contract_sha256,
                'exact_reviewed_streaming_observer_contract_required')
        require(ingress.fault is None and _sha(getattr(ingress, 'domain', None)) and
                type(ingress.clock_session) is str and
                re.fullmatch('[0-9a-f]{32}', ingress.clock_session) is not None,
                'receiver_reset_before_native_spawn_required')
        if map_adapter is not None:
            from committed_native_map import CommittedNativeMapAdapter
            require(type(map_adapter) is CommittedNativeMapAdapter and
                    map_adapter.factory is factory and map_adapter.ingress is ingress,
                    'same_factory_native_map_adapter_required')
        self._map_adapter = map_adapter
        require(gnss_dispatcher is None or (callable(getattr(gnss_dispatcher,'receive',None)) and
                callable(getattr(gnss_dispatcher,'bind_begin',None)) and callable(getattr(gnss_dispatcher,'stop',None))),
                'optional_owned_GPS_dispatcher_required')
        self._gnss_dispatcher=gnss_dispatcher
        if control_adapter is not None:
            from native_control_input import NativeControlInputAdapter
            require(type(control_adapter) is NativeControlInputAdapter and
                    control_adapter.factory is factory and control_adapter.ingress is ingress,
                    'same_owned_native_control_adapter_required')
        self._control_adapter = control_adapter
        self.ingress, self.factory = ingress, factory
        self.observer_contract_sha256 = observer_contract_sha256
        self._binding = self._context()
        self._lock, self._stopping = threading.RLock(), threading.Event()
        self.expected_pid = self.expected_time_namespace = None
        self.begin = None
        self.ready = self.ended = self._done = False
        self._ready_before_stop = False
        self.fault = None
        self.rows = self.total_original_bytes = self.total_line_bytes = self._next_seq = 0
        self.samples = {LASER_TYPE: 0, IMU_TYPE: 0}
        # Delivery accounting is independent of optional GNSS quality/admission.
        self.gps_samples = {'/gps/data': 0, '/gps/fix': 0}
        self.gps_unavailable_rows = 0
        self.gps_done_counts_validated = False
        self.admitted_samples = {LASER_TYPE: 0, IMU_TYPE: 0}
        self.diagnostics = 0
        self.bootstrap_scans_unadmitted = 0
        self.waiting_reason = 'waiting_for_original_start_IMU'
        self._last_original_raw = self._last_original_system = None
        self._observed_header_floors = {LASER_TYPE: None, IMU_TYPE: None}
        self._proposal_input = None
        self._invalidate_control()
        self.factory_failure_timing = None
        self.factory_failure_timing_error = None
        self.admission_failure_timing = None
        self.admission_failure_timing_error = None

    def _retain_admission_failure_timing(self, clock, row, header_ns):
        # Only the failed gate's already sampled clock and validated originals.
        # No new comparison clock, restamp, raw CDR/history or data admission.
        if self.admission_failure_timing is not None or self.admission_failure_timing_error is not None:
            return
        try:
            raw_age = clock.receipt_ns-row['callback_monotonic_raw_ns']
            system_age = clock.source_ns-row['observer_system_ros_now_ns']
            header_age = clock.source_ns-header_ns
            header_to_callback = row['observer_system_ros_now_ns']-header_ns
            value = dict(schema='ubts.native.admission_failure_timing.v1',
                stage='startup_scan_without_original_start_IMU', seq=row['seq'],
                source_now_ns=clock.source_ns, receipt_now_ns=clock.receipt_ns,
                callback_raw_ns=row['callback_monotonic_raw_ns'],
                callback_system_ns=row['observer_system_ros_now_ns'], header_ns=header_ns,
                callback_raw_age_ns=raw_age, callback_system_age_ns=system_age,
                header_age_ns=header_age, header_to_callback_ns=header_to_callback,
                RAW_TTL_ns=RECEIPT_AGE_NS, SOURCE_TTL_ns=SOURCE_AGE_NS,
                checks=dict(raw_fresh=0 <= raw_age < RECEIPT_AGE_NS,
                    system_fresh=0 <= system_age < SOURCE_AGE_NS,
                    header_fresh=0 <= header_age < SOURCE_AGE_NS,
                    header_causal=header_to_callback >= 0), data_qualified=False)
            raw = json.dumps(value, allow_nan=False, separators=(',', ':'))
            require(len(raw.encode('utf-8')) <= 1024, 'admission_failure_timing_capacity')
            self.admission_failure_timing = json.loads(raw)
        except Exception as error:
            self.admission_failure_timing_error = str(error)[:128]

    def _retain_factory_failure_timing(self, answer):
        # Preserve only the already measured failed attempt, not raw inputs or
        # a replacement comparison clock. Diagnostic failure cannot mask cause.
        if self.factory_failure_timing is not None or self.factory_failure_timing_error is not None:
            return
        try:
            value = answer.get('diagnostic_stage_timing')
            require(type(value) is dict, 'failed_factory_timing_unavailable')
            raw = json.dumps(value, allow_nan=False, separators=(',', ':'))
            require(len(raw.encode('utf-8')) <= 2048, 'failed_factory_timing_capacity')
            self.factory_failure_timing = json.loads(raw)
        except Exception as error:
            self.factory_failure_timing_error = str(error)[:128]

    def _feature(self, name):
        value = getattr(self.ingress, name, False)
        require(type(value) is bool, "exact_native_recovery_feature_boolean_required:" + name)
        return value

    def _observe_priming(self, cdr, receipt, system_ns, gid, clock):
        # Legacy receivers have no priming validator or recovery features.
        # V9 keeps its full validation/watermarks even with recovery default OFF.
        validator = getattr(self.ingress, "observe_priming_scan", None)
        if validator is None:
            require(not any(self._feature(name) for name in ("imu_gap_reanchor_enabled",
                "startup_expiry_discard_enabled", "current_expiry_reanchor_enabled")),
                "reviewed_native_priming_validator_required")
            return None
        require(callable(validator), "reviewed_native_priming_validator_required")
        return validator(cdr, callback_receipt=receipt, callback_ros_system_now_ns=system_ns,
                         publisher_gid=gid, clock=clock)

    def _factory_expiry_feature(self):
        factory_expiry = getattr(self.factory, 'postconsumed_causal_raw_expiry_reanchor_enabled', False)
        require(type(factory_expiry) is bool, 'exact_factory_recovery_feature_boolean_required')
        return factory_expiry

    def _factory_receiver_feature(self):
        feature = getattr(self.factory, 'receiver_commit_causal_raw_expiry_reanchor_enabled', False)
        require(type(feature) is bool, 'exact_factory_receiver_recovery_feature_boolean_required')
        return feature

    def _factory_nearest_feature(self):
        nearest = getattr(self.factory, "strict_nearest_gates_enabled", False)
        require(type(nearest) is bool, "exact_factory_strict_nearest_feature_boolean_required")
        return nearest

    def _factory_preparation_feature(self):
        feature = getattr(self.factory, 'preparation_causal_raw_expiry_reanchor_enabled', False)
        require(type(feature) is bool, 'exact_factory_preparation_recovery_feature_boolean_required')
        return feature

    def _factory_final_feature(self):
        feature = getattr(self.factory, 'final_scalar_causal_raw_expiry_reanchor_enabled', False)
        require(type(feature) is bool, 'exact_factory_final_scalar_recovery_feature_boolean_required')
        return feature

    def _factory_registration_feature(self):
        feature = getattr(self.factory, 'registration_rejection_reanchor_enabled', False)
        require(type(feature) is bool, 'exact_factory_registration_recovery_feature_boolean_required')
        return feature

    def _context(self):
        i = self.ingress
        return (i.domain, i.clock_session, i.expected_gids, i.scan_topic, i.imu_topic,
                i.scan_frame, i.imu_frame, i.profile_revision, i.observer_contract_sha256,
                self._feature("imu_gap_reanchor_enabled"), self._feature("startup_expiry_discard_enabled"),
                self._feature("current_expiry_reanchor_enabled"),
                self._feature("positive_age_imu_gap_reanchor_enabled"),
                self._feature("scan_continuity_reanchor_enabled"), self._factory_expiry_feature(),
                self._factory_registration_feature(),
                self._factory_preparation_feature(), self._factory_receiver_feature(), self._factory_final_feature(), self._factory_nearest_feature())

    def _accept_owned_receiver_commit_raw_expiry_wait(self, outcome):
        if not self._factory_receiver_feature():
            return False
        validator = getattr(self.factory, 'is_owned_receiver_commit_raw_expiry_wait', None)
        require(callable(validator), 'reviewed_native_receiver_RAW_wait_validator_required')
        if not validator(outcome):
            return False
        require(not self._stopping.is_set() and self._context() == self._binding,
                'native_stream_source_context_changed_or_stopped')
        with self.factory.lock:
            require(validator(outcome) and not self._stopping.is_set(),
                    'native_stream_receiver_RAW_expiry_wait_revoked')
            self._proposal_input = None
            self._invalidate_control()
            self.waiting_reason = outcome.reason
            map_adapter = getattr(self, '_map_adapter', None)
            if map_adapter is not None:
                invalidate = getattr(map_adapter, 'invalidate_gap', None)
                require(callable(invalidate), 'native_map_gap_invalidator_required')
                invalidate('native_original_pair_wait')
                require(validator(outcome) and not self._stopping.is_set() and
                        self._context() == self._binding,
                        'native_stream_receiver_RAW_expiry_wait_revoked')
        return True

    def _accept_owned_preparation_raw_expiry_wait(self, outcome):
        if not self._factory_preparation_feature():
            return False
        validator = getattr(self.factory, 'is_owned_preparation_raw_expiry_wait', None)
        require(callable(validator), 'reviewed_native_preparation_RAW_wait_validator_required')
        if not validator(outcome):
            return False
        require(not self._stopping.is_set() and self._context() == self._binding,
                'native_stream_source_context_changed_or_stopped')
        with self.factory.lock:
            require(validator(outcome) and not self._stopping.is_set(),
                    'native_stream_preparation_RAW_expiry_wait_revoked')
            self._proposal_input = None
            self._invalidate_control()
            self.waiting_reason = outcome.reason
            map_adapter = getattr(self, '_map_adapter', None)
            if map_adapter is not None:
                invalidate = getattr(map_adapter, 'invalidate_gap', None)
                require(callable(invalidate), 'native_map_gap_invalidator_required')
                invalidate('native_original_pair_wait')
                require(validator(outcome) and not self._stopping.is_set() and
                        self._context() == self._binding,
                        'native_stream_preparation_RAW_expiry_wait_revoked')
        return True

    def _accept_owned_final_scalar_raw_expiry_wait(self, outcome):
        if not self._factory_final_feature():
            return False
        validator = getattr(self.factory, 'is_owned_final_scalar_raw_expiry_wait', None)
        require(callable(validator), 'reviewed_native_final_scalar_RAW_wait_validator_required')
        if not validator(outcome):
            return False
        require(not self._stopping.is_set() and self._context() == self._binding,
                'native_stream_source_context_changed_or_stopped')
        with self.factory.lock:
            require(validator(outcome) and not self._stopping.is_set(),
                    'native_stream_final_scalar_RAW_expiry_wait_revoked')
            self._proposal_input = None
            self._invalidate_control()
            self.waiting_reason = outcome.reason
            map_adapter = getattr(self, '_map_adapter', None)
            if map_adapter is not None:
                invalidate = getattr(map_adapter, 'invalidate_gap', None)
                require(callable(invalidate), 'native_map_gap_invalidator_required')
                invalidate('native_original_pair_wait')
                require(validator(outcome) and not self._stopping.is_set() and
                        self._context() == self._binding,
                        'native_stream_final_scalar_RAW_expiry_wait_revoked')
        return True

    def _accept_owned_consumed_raw_expiry_wait(self, outcome):
        if not self._factory_expiry_feature():
            return False
        validator = getattr(self.factory, 'is_owned_consumed_raw_expiry_wait', None)
        require(callable(validator), 'reviewed_native_consumed_RAW_wait_validator_required')
        if not validator(outcome):
            return False
        require(not self._stopping.is_set() and self._context() == self._binding,
                'native_stream_source_context_changed_or_stopped')
        with self.factory.lock:
            require(validator(outcome) and not self._stopping.is_set(),
                    'native_stream_consumed_RAW_expiry_wait_revoked')
            self._proposal_input = None
            self._invalidate_control()
            self.waiting_reason = outcome.reason
            if self._map_adapter is not None:self._map_adapter.invalidate_gap('native_original_pair_wait')
        return True

    def _accept_owned_gap_wait(self, outcome):
        if not self._feature("imu_gap_reanchor_enabled"):
            return False
        require(callable(getattr(self.ingress, "is_owned_gap_wait", None)),
                "reviewed_native_gap_wait_validator_required")
        if not self.ingress.is_owned_gap_wait(outcome):
            return False
        require(not self._stopping.is_set() and self._context() == self._binding,
                'native_stream_source_context_changed_or_stopped')
        with self.factory.lock:
            require(not self.factory.stop_intent.is_set() and self.factory.fault is None and
                    self.factory._pending is None and self.factory._pending_digest is None,
                    'one_clean_factory_before_IMU_gap_wait_required')
            require(self.ingress.is_owned_gap_wait(outcome) and not self._stopping.is_set(),
                    'native_stream_IMU_gap_wait_revoked')
            self._proposal_input = None
            self._invalidate_control()
            self.waiting_reason = outcome.reason
            if self._map_adapter is not None:self._map_adapter.invalidate_gap('native_original_pair_wait')
        return True

    def _accept_owned_scan_expiry_wait(self, outcome):
        if not (self._feature("startup_expiry_discard_enabled") or
                self._feature("current_expiry_reanchor_enabled")):
            return False
        require(callable(getattr(self.ingress, "is_owned_scan_expiry_wait", None)),
                "reviewed_native_scan_expiry_wait_validator_required")
        if not self.ingress.is_owned_scan_expiry_wait(outcome):
            return False
        require(not self._stopping.is_set() and self._context() == self._binding,
                'native_stream_source_context_changed_or_stopped')
        with self.factory.lock:
            require(not self.factory.stop_intent.is_set() and self.factory.fault is None and
                    self.factory._pending is None and self.factory._pending_digest is None,
                    'one_clean_factory_before_scan_expiry_wait_required')
            require(self.ingress.is_owned_scan_expiry_wait(outcome) and not self._stopping.is_set(),
                    'native_stream_scan_expiry_wait_revoked')
            self._proposal_input=None
            self._invalidate_control()
            self.waiting_reason=outcome.reason
            if self._map_adapter is not None:self._map_adapter.invalidate_gap('native_original_pair_wait')
        return True

    def _accept_owned_original_callback_expiry_wait(self, outcome):
        if not (self._feature("startup_expiry_discard_enabled") or
                self._feature("current_expiry_reanchor_enabled")):
            return False
        require(callable(getattr(self.ingress, "is_owned_original_callback_expiry_wait", None)),
                "reviewed_native_original_callback_expiry_wait_validator_required")
        if not self.ingress.is_owned_original_callback_expiry_wait(outcome):
            return False
        require(not self._stopping.is_set() and self._context() == self._binding,
                'native_stream_source_context_changed_or_stopped')
        with self.factory.lock:
            require(not self.factory.stop_intent.is_set() and self.factory.fault is None and
                    self.factory._pending is None and self.factory._pending_digest is None,
                    'one_clean_factory_before_original_callback_expiry_wait_required')
            require(self.ingress.is_owned_original_callback_expiry_wait(outcome) and not self._stopping.is_set(),
                    'native_stream_original_callback_expiry_wait_revoked')
            self._proposal_input=None
            self._invalidate_control()
            self.waiting_reason=outcome.reason
            if self._map_adapter is not None:self._map_adapter.invalidate_gap('native_original_pair_wait')
            require(self.ingress.is_owned_original_callback_expiry_wait(outcome) and
                    self._context() == self._binding and not self._stopping.is_set() and
                    not self.factory.stop_intent.is_set(),
                    "native_stream_original_callback_expiry_wait_revoked")
        return True

    def _accept_v21_wait(self, outcome, feature, validator_name, factory_owned, label, reasons):
        # V21: same fences as the existing typed WAIT acceptors, in the same order.
        if not feature:
            return False
        owner = self.factory if factory_owned else self.ingress
        validator = getattr(owner, validator_name, None)
        require(callable(validator), 'reviewed_native_' + label + '_wait_validator_required')
        if not validator(outcome):
            # A v21 WAIT that cannot be proven owned is never silently ignored.
            require(getattr(outcome, 'reason', None) not in reasons, 'unowned_native_' + label + '_wait')
            return False
        require(not self._stopping.is_set() and self._context() == self._binding,
                'native_stream_source_context_changed_or_stopped')
        with self.factory.lock:
            if not factory_owned:
                require(not self.factory.stop_intent.is_set() and self.factory.fault is None and
                        self.factory._pending is None and self.factory._pending_digest is None,
                        'one_clean_factory_before_' + label + '_wait_required')
            require(validator(outcome) and not self._stopping.is_set(),
                    'native_stream_' + label + '_wait_revoked')
            self._proposal_input = None
            self._invalidate_control()
            self.waiting_reason = outcome.reason
            map_adapter = getattr(self, '_map_adapter', None)
            if map_adapter is not None:
                invalidate = getattr(map_adapter, 'invalidate_gap', None)
                require(callable(invalidate), 'native_map_gap_invalidator_required')
                invalidate('native_original_pair_wait')
            require(validator(outcome) and self._context() == self._binding and
                    not self._stopping.is_set() and not self.factory.stop_intent.is_set(),
                    'native_stream_' + label + '_wait_revoked')
        return True

    def _accept_owned_registration_rejection_wait(self, outcome):
        return self._accept_v21_wait(outcome, self._factory_registration_feature(),
            'is_owned_registration_rejection_wait', True, 'registration_rejection',
            ('waiting_for_next_scan_after_rejected_native_registration',))

    def _accept_owned_positive_age_imu_gap_wait(self, outcome):
        return self._accept_v21_wait(outcome, self._feature("positive_age_imu_gap_reanchor_enabled"),
            'is_owned_positive_age_imu_gap_wait', False, 'positive_age_IMU_gap',
            ('waiting_for_fresh_scan_after_positive_age_original_IMU_bracket_gap',
             'waiting_for_fresh_scan_after_positive_age_original_IMU_span_loss'))

    def _accept_owned_scan_continuity_wait(self, outcome):
        return self._accept_v21_wait(outcome, self._feature("scan_continuity_reanchor_enabled"),
            'is_owned_scan_continuity_wait', False, 'scan_continuity',
            ('waiting_for_next_scan_after_missing_original_scan',
             'waiting_for_fresh_scan_after_pending_pair_replaced_continuity_loss'))

    def bind_process(self, pid, time_namespace):
        with self._lock:
            require(self.expected_pid is None and self.begin is None and not self.ended,
                    'native_stream_process_binding_is_single_use')
            require(_counter(pid) and pid > 0 and type(time_namespace) is str and
                    0 < len(time_namespace.encode('utf-8')) <= 128,
                    'root_verified_observer_process_and_time_namespace_required')
            self.expected_pid, self.expected_time_namespace = pid, time_namespace

    def stop(self):
        # Events become visible before any original ownership mutex is acquired.
        if self._map_adapter is not None:self._map_adapter.stop()
        self._stopping.set()
        if self._gnss_dispatcher is not None:self._gnss_dispatcher.stop()
        self.factory.stop_intent.set()
        if self._control_adapter is not None:self._control_adapter.stop()
        self.ingress.stop()

    def _fail(self, error):
        self.fault = self.fault or str(error)[:256]
        self.ended = True
        self.stop()

    def receive_line(self, raw_line):
        with self._lock:
            try:
                require(type(raw_line) is bytes and 0 < len(raw_line) <= MAX_LINE_BYTES and
                        raw_line.endswith(b'\n') and raw_line.count(b'\n') == 1 and b'\r' not in raw_line,
                        'one_bounded_original_native_NDJSON_line_required')
                row = json.loads(raw_line.decode('utf-8', errors='strict'),
                    object_pairs_hook=_unique_object, parse_constant=_invalid_number,
                    parse_float=_finite_float)
                require(type(row) is dict and not self.ended,
                        'live_native_stream_row_required')
                require(self.expected_pid is not None, 'native_stream_process_not_bound')
                require(self._context() == self._binding and
                        self.ingress.observer_contract_sha256 == self.observer_contract_sha256,
                        'native_stream_source_context_changed')
                seq = row.get('seq')
                require(_counter(seq) and seq == self._next_seq,
                        'contiguous_signed63_native_stream_sequence_required')
                require(seq < MAX_COUNTER or row.get('kind') == 'done',
                        'native_stream_sequence_exhausted')
                require(row.get('mission_admission') is False,
                        'native_observer_mission_admission_forbidden')
                require(self.total_line_bytes <= MAX_COUNTER - len(raw_line),
                        'native_stream_byte_counter_exhausted')
                if row.get('kind') not in ('original_cdr','original_gnss_cdr'):
                    require(len(raw_line) <= MAX_METADATA_BYTES,
                            'bounded_native_stream_metadata_required')
                answer = self._receive(row)
                self.total_line_bytes += len(raw_line)
                self._next_seq = seq if seq == MAX_COUNTER else seq + 1
                return answer
            except Exception as error:
                self._fail(error)
                raise

    def _receive(self, row):
        kind = row.get('kind')
        if kind == 'begin':
            require(self.begin is None and row['seq'] == 0 and row.get('schema') == SCHEMA and
                    row.get('streaming') is True and type(row.get('pid')) is int and
                    row['pid'] == self.expected_pid and
                    type(row.get('uid')) is int and row['uid'] == 0 and
                    type(row.get('gid')) is int and row['gid'] == 0 and
                    row.get('time_namespace') == self.expected_time_namespace,
                    'root_verified_streaming_native_observer_begin_required')
            require(type(row.get('observer_process_session')) is str and
                    re.fullmatch('[0-9a-f]{32}', row['observer_process_session']) is not None and
                    row.get('RAW_domain') == self.ingress.domain and
                    type(row.get('GID_storage_bytes')) is int and row['GID_storage_bytes'] == 16 and
                    row.get('header_clock_origin') == 'original_publisher_unmodified' and
                    row.get('observer_clock_type') == 'RCL_SYSTEM_TIME' and
                    row.get('use_sim_time') is False and row.get('clock_topic_created') is False and
                    row.get('publishers_created') is False,
                    'native_physical_clock_and_receipt_association_required')
            require(_hex(row.get('expected_scan_GID'), 16) == self.ingress.expected_gids[0] and
                    _hex(row.get('expected_IMU_GID'), 16) == self.ingress.expected_gids[1],
                    'reviewed_native_observer_publisher_binding_required')
            self.begin = row  # bounded metadata only; NOT publisher epoch
            if self._control_adapter is not None:
                self._control_adapter.bind_epoch(row['observer_process_session'])
            if self._gnss_dispatcher is not None:self._gnss_dispatcher.bind_begin(row)
            return ()
        if self.begin is None and kind == 'done' and row['seq'] == 0:
            # Native initialization can fail before BEGIN. Preserve that bounded
            # error, but it supplies no actor/source proof or cleanup admission.
            error = row.get('error')
            require(row.get('schema') == SCHEMA and row.get('streaming') is True and
                    row.get('ok') is False and row.get('termination_reason') == 'fault' and
                    row.get('ready_observed') is False and
                    type(error) is str and 0 < len(error) <= 512 and
                    type(row.get('scan_samples')) is int and row['scan_samples'] == 0 and
                    type(row.get('IMU_samples')) is int and row['IMU_samples'] == 0 and
                    row.get('physical_autonomy_qualified') is False,
                    'invalid_native_observer_startup_fault_metadata')
            self._validate_gps_done_counts(row)
            self.fault = 'native_observer_startup_fault:' + error
            raise ValueError(self.fault)
        require(self.begin is not None, 'native_observer_begin_missing')
        if kind == 'ready':
            require(not self.ready and min(self.samples.values()) >= 2 and
                    type(row.get('stable_graph_min_seconds')) in (int, float) and
                    row['stable_graph_min_seconds'] == .05 and
                    row.get('actual_original_CDR_and_per_message_GIDs') is True,
                    'native_observer_actual_GID_readiness_required')
            self.ready = True
            if not self._stopping.is_set():
                if self.bootstrap_scans_unadmitted == 0:
                    require(min(self.admitted_samples.values()) >= 2,
                            'native_stream_readiness_requires_original_ingress')
                self._ready_before_stop = min(self.admitted_samples.values()) >= 2
            return ()
        if kind == 'done':
            self.ended = True
            require(row.get('schema') == SCHEMA and row.get('streaming') is True and
                    row.get('termination_reason') == 'operator_stop' and self._stopping.is_set() and
                    row.get('ok') is True and row.get('error') == '' and
                    row.get('ROS_executor_destroyed') is True and
                    row.get('ROS_subscriptions_destroyed') is True and
                    row.get('ROS_node_destroyed') is True and
                    row.get('ROS_shutdown_completed') is True and
                    row.get('physical_autonomy_qualified') is False and
                    type(row.get('ready_observed')) is bool and row['ready_observed'] == self.ready,
                    'native_stream_failed_or_incomplete_cleanup')
            require(_counter(row.get('scan_samples')) and _counter(row.get('IMU_samples')) and
                    row['scan_samples'] == self.samples[LASER_TYPE] and
                    row['IMU_samples'] == self.samples[IMU_TYPE] and
                    _ns(row.get('observed_elapsed_ns')),
                    'native_observer_delivery_count_or_elapsed_mismatch')
            self.gps_done_counts_validated = self._validate_gps_done_counts(row)
            self._done = True
            return ()
        if kind in ('original_gnss_cdr','gnss_unavailable'):
            return self._gps(row)
        require(kind == 'original_cdr', 'native_observer_row_kind_invalid')
        return self._original(row)

    def _validate_gps_done_counts(self, row):
        # Old physical-only observers omitted both fields. New observers emit
        # both even when GNSS is OFF; supplied fields always require exact types.
        keys = ('GPS_data_samples', 'GPS_fix_samples')
        if self._gnss_dispatcher is None and not any(key in row for key in keys):
            return False
        require(all(_counter(row.get(key)) for key in keys) and
                row['GPS_data_samples'] == self.gps_samples['/gps/data'] and
                row['GPS_fix_samples'] == self.gps_samples['/gps/fix'],
                'native_observer_GPS_delivery_count_mismatch')
        return True

    def _gps(self, row):
        require(self._gnss_dispatcher is not None, 'optional_GPS_dispatch_not_enabled')
        if row['kind'] == 'gnss_unavailable':
            require(self.gps_unavailable_rows < MAX_COUNTER,
                    'native_stream_GPS_delivery_counter_exhausted')
            answer = self._gnss_dispatcher.receive(row)
            self.gps_unavailable_rows += 1
        else:
            # Topic is framing needed to compare the producer's per-type count;
            # CDR/quality/GID exclusions remain the optional dispatcher's concern.
            topic = row.get('topic')
            require(type(topic) is str and topic in self.gps_samples,
                    'native_observer_GPS_delivery_topic_invalid')
            require(self.gps_samples[topic] < MAX_COUNTER,
                    'native_stream_GPS_delivery_counter_exhausted')
            answer = self._gnss_dispatcher.receive(row)
            self.gps_samples[topic] += 1
        return answer

    def _original(self, row):
        ros_type = row.get('ros_type')
        require(ros_type in self.samples and row.get('topic') == (
            self.ingress.scan_topic if ros_type == LASER_TYPE else self.ingress.imu_topic),
            'original_native_topic_or_type_mismatch')
        cdr = _hex(row.get('original_CDR_hex'))
        require(type(row.get('original_CDR_bytes')) is int and
                len(cdr) == row['original_CDR_bytes'] and _sha(row.get('original_CDR_sha256')) and
                hashlib.sha256(cdr).hexdigest() == row['original_CDR_sha256'],
                'original_CDR_SHA_or_length_mismatch')
        wire = decode(cdr, ros_type)
        require(all(type(row.get(key)) is int for key in ('original_header_stamp_sec',
            'original_header_stamp_nanosec', 'original_header_stamp_ns', 'ranges_count', 'intensities_count')),
            'typed_original_native_header_and_counts_required')
        require((wire.header.sec, wire.header.nanosec, wire.header.stamp_ns) == (
            row['original_header_stamp_sec'], row['original_header_stamp_nanosec'],
            row['original_header_stamp_ns']) and
            wire.header.frame_id.encode('utf-8') == _hex(row.get('frame_id_bytes_hex')),
            'observer_original_header_metadata_mismatch')
        counts = (wire.count, len(wire.intensity_f32_bytes)//4) if type(wire) is WireScan else (0, 0)
        require(counts == (row['ranges_count'], row['intensities_count']),
                'observer_original_array_count_mismatch')
        require(wire.header.frame_id == (self.ingress.scan_frame if ros_type == LASER_TYPE else self.ingress.imu_frame),
                'original_native_frame_mismatch')
        if ros_type == LASER_TYPE:
            a,b,inc,dt,cycle,minimum,maximum=map(float,wire.metadata)
            require(all(math.isfinite(v) for v in (a,b,inc,dt,cycle,minimum,maximum)) and
                    120 <= wire.count <= 4096 and 0 < inc <= .1 and dt >= 0 and 0 <= cycle <= .5 and
                    0 <= minimum < maximum <= 100 and abs(b-(a+(wire.count-1)*inc)) <= 2e-4 and
                    (wire.count-1)*dt <= .2, 'bounded_original_physical_scan_geometry_required')
        require(row.get('RAW_schema') == RAW_SCHEMA and row.get('RAW_domain') == self.ingress.domain and
                _ns(row.get('callback_monotonic_raw_ns')) and _ns(row.get('observer_system_ros_now_ns')),
                'original_native_callback_clocks_required')
        gid = _hex(row.get('publisher_gid_hex'), 16)
        require(gid == self.ingress.expected_gids[0 if ros_type == LASER_TYPE else 1],
                'actual_native_callback_publisher_changed_or_missing')
        require(all(type(row.get(k)) in (int, float) and row[k] == 0 for k in
                    ('proposed_linear_mps', 'proposed_yaw_rate_rad_s')),
                'original_observer_nonzero_proposal_forbidden')
        require(self.rows < MAX_COUNTER and self.samples[ros_type] < MAX_COUNTER and
                self.total_original_bytes <= MAX_COUNTER - len(cdr),
                'native_stream_sample_counter_exhausted')
        self.rows += 1
        self.samples[ros_type] += 1
        self.total_original_bytes += len(cdr)
        if self._stopping.is_set():
            return ()
        floor=self._observed_header_floors[ros_type]
        require(floor is None or wire.header.stamp_ns > floor,'observed_original_Header_rewind')
        self._observed_header_floors[ros_type]=wire.header.stamp_ns
        if self._last_original_raw is not None:
            require(row['callback_monotonic_raw_ns'] >= self._last_original_raw and
                    row['observer_system_ros_now_ns'] >= self._last_original_system,
                    'original_native_callback_delivery_regressed')
        self._last_original_raw = row['callback_monotonic_raw_ns']
        self._last_original_system = row['observer_system_ros_now_ns']
        if ros_type == LASER_TYPE and self.ingress.previous is None:
            originals = tuple(self.ingress.imus)
            if not originals or originals[0].wire.header.stamp_ns > wire.header.stamp_ns:
                # Discovery can deliver scan callbacks before the first IMU.
                # Such an initial scan cannot acquire a historical bracket.
                # Count it explicitly without creating a velocity pair or
                # admitting a replacement/synthetic IMU. Keep strict clocks.
                clock = self.ingress._evaluate()
                if self._feature("startup_expiry_discard_enabled"):
                    require(all(callable(getattr(self.ingress, name, None)) for name in
                        ("_validated_observed_scan", "_positive_scan_expiry", "observe_priming_scan")),
                        "reviewed_native_startup_expiry_validator_required")
                    try:
                        _scan,ages=self.ingress._validated_observed_scan(cdr,
                            RawReceipt(row['callback_monotonic_raw_ns'],row['RAW_domain']),
                            row['observer_system_ros_now_ns'],gid,clock)
                    except Exception:
                        self._retain_admission_failure_timing(clock,row,wire.header.stamp_ns)
                        raise
                    if self.ingress._positive_scan_expiry(ages):
                        expired=self.ingress.observe_priming_scan(cdr,
                            callback_receipt=RawReceipt(row['callback_monotonic_raw_ns'],row['RAW_domain']),
                            callback_ros_system_now_ns=row['observer_system_ros_now_ns'],publisher_gid=gid,clock=clock)
                        require(self._accept_owned_scan_expiry_wait(expired),'owned_startup_scan_expiry_wait_required')
                        self.bootstrap_scans_unadmitted+=1
                        return ()
                try:
                    require(0 <= clock.receipt_ns-row['callback_monotonic_raw_ns'] < RECEIPT_AGE_NS,
                            'stale_or_future_original_callback_RAW')
                    require(0 <= clock.source_ns-row['observer_system_ros_now_ns'] < SOURCE_AGE_NS and
                            0 <= clock.source_ns-wire.header.stamp_ns < SOURCE_AGE_NS and
                            wire.header.stamp_ns <= row['observer_system_ros_now_ns'],
                            'stale_or_future_original_callback_system_time')
                except Exception:
                    self._retain_admission_failure_timing(clock, row, wire.header.stamp_ns)
                    raise
                self._observe_priming(cdr,
                    RawReceipt(row['callback_monotonic_raw_ns'],row['RAW_domain']),
                    row['observer_system_ros_now_ns'],gid,clock)
                self.bootstrap_scans_unadmitted += 1
                self.waiting_reason = 'startup_scan_without_original_start_IMU'
                return ()
        outcome = self.ingress.consume_original(ros_type, cdr,
            callback_receipt=RawReceipt(row['callback_monotonic_raw_ns'], row['RAW_domain']),
            callback_ros_system_now_ns=row['observer_system_ros_now_ns'], publisher_gid=gid)
        if self._stopping.is_set():
            return ()
        require(self.ingress.fault is None, 'native_original_ingress_rejected:' + outcome.reason)
        if self._accept_owned_scan_expiry_wait(outcome):
            return ()
        if self._accept_owned_original_callback_expiry_wait(outcome):
            return ()
        if self._accept_owned_scan_continuity_wait(outcome):
            if self.ingress.previous is not None:
                # Only the owned missing-scan WAIT keeps the newest scan as the anchor.
                self.admitted_samples[ros_type] += 1
            return ()
        self.admitted_samples[ros_type] += 1
        records = []
        while self.ingress.pending is not None and not self._stopping.is_set():
            previous, current = self.ingress.pending
            originals = tuple(self.ingress.imus)
            if originals and originals[-1].wire.header.stamp_ns < current.wire.header.stamp_ns:
                self.waiting_reason = 'waiting_for_original_end_IMU'
                break  # existing pending pair remains the one exact pair
            prepared = (self.ingress.prepare(positive_age_factory=self.factory)
                        if self._factory_preparation_feature() else self.ingress.prepare())
            if self._stopping.is_set():
                break
            if self._accept_owned_preparation_raw_expiry_wait(prepared):
                return ()
            if self._accept_owned_gap_wait(prepared):
                break
            if self._accept_owned_positive_age_imu_gap_wait(prepared):
                break
            require(prepared.prepared is not None,
                    'physical_original_pair_rejected:' + prepared.reason)
            owned = self.factory.prepare(prepared.prepared)
            if self._stopping.is_set():
                break
            if self._accept_owned_preparation_raw_expiry_wait(owned):
                return ()
            if self._accept_owned_registration_rejection_wait(owned):
                break
            if isinstance(owned, dict):
                self._retain_factory_failure_timing(owned)
            require(not isinstance(owned, dict), 'physical_internal_fit_rejected:' + str(owned))
            answer = (self._control_adapter.commit(owned, map_adapter=self._map_adapter)
                      if self._control_adapter is not None else
                      self.factory.commit(owned) if self._map_adapter is None
                      else self._map_adapter.commit(owned))
            if self._stopping.is_set():
                break
            if self._accept_owned_preparation_raw_expiry_wait(answer):
                return ()
            if self._accept_owned_final_scalar_raw_expiry_wait(answer):
                return ()
            if self._accept_owned_receiver_commit_raw_expiry_wait(answer):
                break
            if self._accept_owned_consumed_raw_expiry_wait(answer):
                break
            valid_answer = (answer.get('diagnostic') is not None and answer.get('body_velocity_mps') is None and
                    all(answer.get(k) is False for k in ('motor_command_sent', 'runtime_authority',
                        'autonomy_output_enabled', 'execution_eligible', 'physical_autonomy_qualified')) and
                    all(type(answer.get(k)) in (int, float) and answer[k] == 0 for k in
                        ('applied_linear_mps', 'applied_yaw_rate_rad_s', 'proposed_linear_mps',
                         'proposed_yaw_rate_rad_s')))
            if not valid_answer:
                self._retain_factory_failure_timing(answer)
            require(valid_answer,
                    'physical_shadow_final_fence_failed:' + str(answer.get('reason','unknown'))[:256])
            diagnostic = dict(answer, diagnostic=asdict(answer['diagnostic']))
            evidence = dict(observer_seq=row['seq'], original_CDR_sha256=row['original_CDR_sha256'],
                samples=self.rows, diagnostic_version=self.factory.snapshot().version,
                original_callback_RAW_ns=row['callback_monotonic_raw_ns'],
                diagnostic_stage_timing=self.factory.timing_snapshot())
            require(len(json.dumps(dict(diagnostic=diagnostic, evidence=evidence),
                    allow_nan=False).encode('utf-8')) <= MAX_METADATA_BYTES,
                    'bounded_physical_stream_diagnostic_required')
            self.diagnostics += 1
            self.waiting_reason = None
            if self.ready and min(self.admitted_samples.values()) >= 2:
                self._ready_before_stop = True
            original = prepared.prepared
            record = answer['diagnostic']
            scan_wire = original.current.wire
            a, _b, inc, _dt, _cycle, minimum, maximum = map(float, scan_wire.metadata)
            lower = next(e for e in original.imu_originals
                         if e.wire.header.stamp_ns == record.original_lower_imu_stamp_ns)
            q, _qc, angular, _ac, _linear, _lc = lower.wire.groups
            norm = math.sqrt(sum(float(v)*float(v) for v in q))
            require(.9 < norm < 1.1, 'original_imu_quaternion_required')
            x, y, z, w = (float(v)/norm for v in q)
            gx, gy, gz = map(float, angular)
            vertical = 2*(x*z-w*y)*gx+2*(y*z+w*x)*gy+(1-2*(x*x+y*y))*gz
            require(math.isfinite(vertical), 'original_vertical_gyro_required')
            self._proposal_input = dict(
                context=dict(source_session=self.ingress.clock_session,
                    epoch=self.begin['observer_process_session'], generation=self.ingress.generation,
                    source_clock_domain='ROS_SYSTEM_TIME',
                    receipt_clock_domain='CLOCK_MONOTONIC_RAW:'+self.ingress.domain),
                scan=dict(stamp_ns=scan_wire.header.stamp_ns, receipt_ns=original.current.receipt.ns,
                    sequence=self.diagnostics, frame_id=scan_wire.header.frame_id,
                    ranges=[float(v) if math.isfinite(float(v)) else None for v in scan_wire.ranges],
                    angle_min=a, angle_increment=inc, range_min=minimum, range_max=maximum,
                    original_native=True, nonfinite_ranges_as_unknown=True, original_CDR_sha256=original.current.original_sha256),
                motion=dict(previous_scan_stamp_ns=original.previous.wire.header.stamp_ns,
                    current_scan_stamp_ns=scan_wire.header.stamp_ns, receipt_ns=original.current.receipt.ns,
                    dx_m=float(record.delta_xy_laser_plane_m[0]), dy_m=float(record.delta_xy_laser_plane_m[1]),
                    dyaw_rad=float(record.fitted_laser_plane_yaw_rad),
                    accepted=True, map_eligible=True, path_break=False, motion_prior=False,
                    body_mount_verified=False, accuracy_verified=False),
                imu=dict(stamp_ns=lower.wire.header.stamp_ns, receipt_ns=lower.receipt.ns,
                    yaw_rate_rad_s=vertical, original_native=True),
                estimated_mount=True, physical_autonomy_qualified=False,
                runtime_authority=False, motor_command_sent=False)
            # Native numpy calculations can return float64 scalars. Serialize
            # this DERIVED diagnostic once into JSON primitives; original CDR,
            # integer stamps and ownership inputs are never rewritten.
            records.append(json.loads(json.dumps(dict(diagnostic=diagnostic, evidence=evidence),
                                                 allow_nan=False)))
        return tuple(records)

    def _invalidate_control(self):
        adapter = getattr(self, '_control_adapter', None)
        if adapter is not None:
            adapter.invalidate_gap('native_original_pair_wait')

    def control_input(self):
        """Same-ingress committed native sensor input, never an actuator permit.

        CDR/GID association is retained from capture, not rebuilt from the WEB
        proposal. Fresh read clocks do not change original sensor stamps.
        Unmounted input yields only laser diagnostics and BODY/swept HOLD.
        """
        with self._lock:
            adapter = self._control_adapter
            if adapter is None:
                return dict(enabled=False, input=None, state='HOLD',
                    reason='native_control_input_default_OFF', runtime_authority=False,
                    motor_command_sent=False, operational_frame_available=False)
            current = None
            if not self._stopping.is_set() and self.fault is None:
                try:
                    current = adapter.input(source_now_ns=self.ingress.source_now(),
                                            receipt_now_ns=self.ingress.raw.sample().ns)
                except (ValueError, TypeError):
                    # No capability or retained pose can rescue a failed read.
                    adapter.invalidate_gap('control_input_unavailable')
            return dict(adapter.status(), input=current)

    def proposal_input(self):
        """Latest native-derived planning input only; no body/actuator permit.

        Comparison clocks are fresh. Original sensor stamps and receipts remain
        unchanged. Nonfinite original ranges preserve their slot as unknown.
        """
        with self._lock:
            if self._stopping.is_set() or self.fault is not None or self._proposal_input is None:
                return None
            try:
                require(self._factory_preparation_feature() == self._binding[-4],
                        "native_stream_preparation_recovery_configuration_changed")
                require(self._factory_receiver_feature() == self._binding[-3],
                        "native_stream_receiver_recovery_configuration_changed")
                require(self._factory_final_feature() == self._binding[-2],
                        "native_stream_final_scalar_recovery_configuration_changed")
                require(self._factory_nearest_feature() == self._binding[-1],
                        "native_stream_strict_nearest_configuration_changed")
                require(self._factory_registration_feature() == self._binding[-5],
                        "native_stream_registration_recovery_configuration_changed")
            except Exception as error:
                self._fail(error)
                return None
            result = copy.deepcopy(self._proposal_input)
            result.update(source_now_ns=self.ingress.source_now(),
                          receipt_now_ns=self.ingress.raw.sample().ns)
            # A cached result is not a fresh sensor observation. Deny expired
            # input without restamping its source or relaxing physical bounds.
            if not (0 <= result['source_now_ns']-result['scan']['stamp_ns'] < SOURCE_AGE_NS and
                    all(0 <= result['receipt_now_ns']-result[key]['receipt_ns'] < RECEIPT_AGE_NS
                        for key in ('scan','motion','imu')) and
                    0 <= result['source_now_ns']-result['imu']['stamp_ns'] < SOURCE_AGE_NS):
                return None
            return result

    def status(self):
        with self._lock:
            return dict(schema='ubts.physical.native_stream_status.v1',
                native_control_input=None if self._control_adapter is None else self._control_adapter.status(),
                optional_GNSS=None if self._gnss_dispatcher is None else self._gnss_dispatcher.status(),
                optional_GNSS_delivery=dict(enabled=self._gnss_dispatcher is not None,
                    original_samples=dict(self.gps_samples), unavailable_rows=self.gps_unavailable_rows,
                    DONE_counts_validated=self.gps_done_counts_validated),
                ready_observed=self.ready, data_ready_before_stop=self._ready_before_stop,
                stopped=self._stopping.is_set(), ended=self.ended, fault=self.fault,
                next_sequence=self._next_seq, sample_rows=self.rows,
                original_bytes=self.total_original_bytes, line_bytes=self.total_line_bytes,
                samples=dict(self.samples), admitted_samples=dict(self.admitted_samples),
                startup_scans_unadmitted=self.bootstrap_scans_unadmitted,
                waiting_reason=self.waiting_reason,
                diagnostic_count=self.diagnostics, pending_pair=self.ingress.pending is not None,
                factory_failure_timing=copy.deepcopy(self.factory_failure_timing),
                factory_failure_timing_error=self.factory_failure_timing_error,
                admission_failure_timing=copy.deepcopy(self.admission_failure_timing),
                admission_failure_timing_error=self.admission_failure_timing_error,
                ingress_admission_failure_timing=copy.deepcopy(
                    getattr(self.ingress, 'admission_failure_timing', None)),
                ingress_admission_failure_timing_error=getattr(
                    self.ingress, 'admission_failure_timing_error', None),
                ingress_imu_gap_failure_timing=copy.deepcopy(
                    getattr(self.ingress, 'imu_gap_failure_timing', None)),
                ingress_imu_gap_failure_timing_error=getattr(
                    self.ingress, 'imu_gap_failure_timing_error', None),
                ingress_causal_imu_failure_timing=copy.deepcopy(
                    getattr(self.ingress, 'causal_imu_failure_timing', None)),
                ingress_causal_imu_failure_timing_error=getattr(
                    self.ingress, 'causal_imu_failure_timing_error', None),
                ingress_receiver_end_scalar_failure_timing=copy.deepcopy(
                    getattr(self.ingress, 'receiver_end_scalar_failure_timing', None)),
                ingress_receiver_end_scalar_failure_timing_error=getattr(
                    self.ingress, 'receiver_end_scalar_failure_timing_error', None),
                imu_gap_reanchor_enabled=self._feature("imu_gap_reanchor_enabled"),
                imu_gap_reanchor_count=getattr(self.ingress, "gap_reanchor_count", 0),
                startup_expiry_discard_enabled=self._feature("startup_expiry_discard_enabled"),
                current_expiry_reanchor_enabled=self._feature("current_expiry_reanchor_enabled"),
                original_callback_expiry_wait_count=getattr(self.ingress, "original_callback_expiry_wait_count", 0),
                original_callback_expiry_wait_timing=copy.deepcopy(getattr(self.ingress, "original_callback_expiry_wait_timing", None)),
                scan_expiry_wait_count=getattr(self.ingress, "scan_expiry_wait_count", 0),
                scan_expiry_wait_timing=copy.deepcopy(getattr(self.ingress, "scan_expiry_wait_timing", None)),
                postconsumed_causal_raw_expiry_reanchor_enabled=self._factory_expiry_feature(),
                receiver_commit_causal_raw_expiry_reanchor_enabled=self._factory_receiver_feature(),
                receiver_commit_raw_expiry_wait_count=getattr(self.factory, 'receiver_commit_raw_expiry_wait_count', 0),
                receiver_commit_raw_expiry_wait_timing=copy.deepcopy(getattr(
                    self.factory, 'receiver_commit_raw_expiry_wait_timing', None)),
                preparation_causal_raw_expiry_reanchor_enabled=self._factory_preparation_feature(),
                preparation_raw_expiry_wait_count=getattr(self.factory, 'preparation_raw_expiry_wait_count', 0),
                preparation_raw_expiry_wait_timing=copy.deepcopy(getattr(
                    self.factory, 'preparation_raw_expiry_wait_timing', None)),
                final_scalar_causal_raw_expiry_reanchor_enabled=self._factory_final_feature(),
                final_scalar_raw_expiry_wait_count=getattr(self.factory, 'final_scalar_raw_expiry_wait_count', 0),
                final_scalar_raw_expiry_wait_timing=copy.deepcopy(getattr(
                    self.factory, 'final_scalar_raw_expiry_wait_timing', None)),
                strict_nearest_gates_enabled=self._factory_nearest_feature(),
                consumed_raw_expiry_wait_count=getattr(self.factory, 'consumed_raw_expiry_wait_count', 0),
                consumed_raw_expiry_wait_timing=copy.deepcopy(getattr(
                    self.factory, 'consumed_raw_expiry_wait_timing', None)),
                registration_rejection_reanchor_enabled=self._factory_registration_feature(),
                registration_rejection_wait_count=getattr(self.factory, 'registration_rejection_wait_count', 0),
                registration_rejection_wait_streak=getattr(self.factory, 'registration_rejection_wait_streak', 0),
                registration_rejection_wait_max_streak=getattr(self.factory, 'registration_rejection_wait_max_streak', 0),
                registration_rejection_reason_counts=copy.deepcopy(getattr(
                    self.factory, 'registration_rejection_reason_counts', None)),
                registration_rejection_wait_timing=copy.deepcopy(getattr(
                    self.factory, 'registration_rejection_wait_timing', None)),
                positive_age_imu_gap_reanchor_enabled=self._feature("positive_age_imu_gap_reanchor_enabled"),
                positive_age_imu_gap_wait_count=getattr(self.ingress, 'positive_age_imu_gap_wait_count', 0),
                positive_age_imu_gap_wait_timing=copy.deepcopy(getattr(
                    self.ingress, 'positive_age_imu_gap_wait_timing', None)),
                scan_continuity_reanchor_enabled=self._feature("scan_continuity_reanchor_enabled"),
                scan_continuity_wait_count=getattr(self.ingress, 'scan_continuity_wait_count', 0),
                scan_continuity_wait_counts=copy.deepcopy(getattr(
                    self.ingress, 'scan_continuity_wait_counts', None)),
                scan_continuity_wait_timing=copy.deepcopy(getattr(
                    self.ingress, 'scan_continuity_wait_timing', None)),
                runtime_authority=False, motor_command_sent=False, mission_admission=False)

    def finish(self, exit_code, operator_stop):
        """Protocol cleanup only; runner separately proves process/reader joins."""
        with self._lock:
            ok = (type(exit_code) is int and exit_code == 0 and operator_stop is True and
                  self._stopping.is_set() and self._done and self.ended and self.fault is None)
            self.stop()
            self.factory.stop()  # sole consumer now drained; clear pending owned inputs
            return dict(ok=ok, owner_cleanup=ok, ready_observed=self.ready,
                data_ready_before_stop=self._ready_before_stop,
                error=self.fault if self.fault else None if ok else 'native_stream_protocol_incomplete',
                status=self.status(), runtime_authority=False, motor_command_sent=False,
                mission_admission=False, physical_autonomy_qualified=False)
