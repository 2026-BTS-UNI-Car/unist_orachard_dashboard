"""Original GNSS -> optional weak historical map factors; no ROS/control IO.

String's embedded ROS publication stamp is not its receiver measurement time.
The original CDR, header brackets and receiver epoch are kept separately.
Native source admission and accepted pose custody are external trusted hooks.
This component never replaces a local occupancy generation or grants authority.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from collections import OrderedDict, Counter
import bisect
import hashlib
import json
import math
import struct
import threading

MAX_CDR = 65536
MAX_NS = (1 << 63) - 1


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def integer(v):
    require(type(v) is int and 0 <= v <= MAX_NS, 'exact_integer_ns_required')
    return v


def number(v):
    require(type(v) in (int, float) and math.isfinite(v), 'finite_number_required')
    return float(v)


def token(v):
    require(type(v) is str and 0 < len(v) <= 128 and all(ord(c) >= 32 for c in v), 'context_token_required')
    return v


def context(v):
    require(type(v) is tuple and len(v) == 6, 'native_map_context_required')
    for i, item in enumerate(v):
        integer(item) if i == 1 else token(item)
    return v


def stamp(v):
    require(type(v) is dict and type(v.get('sec')) is int and 0 <= v['sec'] < (1 << 31)
            and type(v.get('nanosec')) is int and 0 <= v['nanosec'] < 10**9, 'original_ROS_stamp_required')
    return v['sec'] * 10**9 + v['nanosec']


def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, 'duplicate_JSON_key')
            out[key] = value
        return out
    def bad(value):
        raise ValueError('nonfinite_JSON_constant')
    value = json.loads(raw.decode('utf8'), object_pairs_hook=pairs, parse_constant=bad)
    require(type(value) is dict, 'GPS_JSON_object_required')
    return value


class _CDR:
    def __init__(self, raw):
        require(type(raw) is bytes and 4 <= len(raw) <= MAX_CDR, 'bounded_original_CDR_required')
        require(raw[:4] in (b'\0\1\0\0', b'\0\0\0\0'), 'CDR1_encapsulation_required')
        self.raw, self.at, self.endian = raw, 4, '<' if raw[1] else '>'

    def read(self, fmt, align):
        self.at += (align - (self.at - 4) % align) % align
        size = struct.calcsize(self.endian + fmt)
        require(self.at + size <= len(self.raw), 'truncated_original_CDR')
        value = struct.unpack_from(self.endian + fmt, self.raw, self.at)
        self.at += size
        return value

    def string(self, cap):
        size = self.read('I', 4)[0]
        require(1 <= size <= cap and self.at + size <= len(self.raw), 'bounded_original_string_required')
        value = self.raw[self.at:self.at + size]
        self.at += size
        require(value[-1:] == b'\0' and b'\0' not in value[:-1], 'original_string_terminator_required')
        return value[:-1]

    def finish(self):
        tail = self.raw[self.at:]
        require(not tail or (len(tail) == ((-self.at) & 3) and not any(tail)), 'unexpected_CDR_tail')


def decode_gps_data(raw):
    r = _CDR(raw)
    value = strict_json(r.string(32768))
    r.finish()
    require(value.get('schema') == 'orchard.gps.v1', 'GPS_schema_required')
    # There is NO std_msgs/Header in String; this is the producer payload field.
    return value, stamp(value.get('stamp'))


def decode_navsat(raw):
    r = _CDR(raw)
    sec, ns = r.read('iI', 4)
    source = stamp(dict(sec=sec, nanosec=ns))
    frame = r.string(129).decode('utf8')
    status = r.read('b', 1)[0]
    service = r.read('H', 2)[0]
    lat, lon, altitude = r.read('3d', 8)
    covariance = r.read('9d', 8)
    cov_type = r.read('B', 1)[0]
    r.finish()
    return dict(stamp_ns=source, frame_id=frame, status=status, service=service,
                latitude=lat, longitude=lon, altitude=altitude,
                covariance=covariance, covariance_type=cov_type)


@dataclass(frozen=True)
class OriginalGNSS:
    context: tuple
    data_cdr: bytes
    fix_cdr: bytes
    data_log_ns: int | None = None  # Original rosbag log time, NOT callback RAW.
    fix_log_ns: int | None = None
    data_gid: str | None = None
    fix_gid: str | None = None
    data_callback_RAW_ns: int | None = None
    fix_callback_RAW_ns: int | None = None


@dataclass(frozen=True)
class PoseWitness:
    context: tuple
    chain: str  # Changes on WAIT/gap/uncertain registration; never a global row ID.
    version: int
    source_ns: int
    xy: tuple
    scan_sha256: str


class OriginalGPSPairs:
    """Bounded exact-header collector for rosbag reader OR verified live rows.

    Caller supplies original bytes directly from read_next()/serialized callback.
    No serialized byte reconstruction, approximate bag-time or receiver-time join.
    Repeated header on one topic is ambiguous and cannot create a pair.
    """
    def __init__(self, ctx, capacity=16):
        self.context = context(ctx)
        require(type(capacity) is int and 1 <= capacity <= 64, 'bounded_pair_capacity_required')
        self.capacity, self.pending = capacity, OrderedDict()
        self.closed = False
        self.counts = Counter()
        self.floors = {}

    def stop(self):
        self.closed = True
        self.pending.clear()

    def receive(self, topic, raw, *, log_ns=None, gid=None, callback_RAW_ns=None):
        if self.closed:
            return None
        require(topic in ('/gps/data', '/gps/fix'), 'GPS_topic_required')
        if log_ns is not None: integer(log_ns)
        if callback_RAW_ns is not None: integer(callback_RAW_ns)
        if gid is not None:
            require(type(gid) is str and len(gid) == 32 and gid != '0' * 32
                    and all(c in '0123456789abcdef' for c in gid), 'exact_GID_required')
        t = decode_gps_data(raw)[1] if topic == '/gps/data' else decode_navsat(raw)['stamp_ns']
        require(t > self.floors.get(topic, -1), 'GPS_topic_header_duplicate_or_rewind')
        self.floors[topic] = t
        row = self.pending.setdefault(t, {})
        row[topic] = (raw, log_ns, gid, callback_RAW_ns)
        self.counts[topic] += 1
        if len(row) == 2:
            a, b = row['/gps/data'], row['/gps/fix']
            self.pending.pop(t)
            self.counts['pairs'] += 1
            return OriginalGNSS(self.context, a[0], b[0], a[1], b[1], a[2], b[2], a[3], b[3])
        if len(self.pending) > self.capacity:
            self.pending.popitem(last=False)
            self.counts['unmatched_evicted'] += 1
        return None


class LiveGPSPairs(OriginalGPSPairs):
    """Binding for original serialized callback rows; never accepts WS snapshots.

    Process/session validator is supplied by the owned original observer. GIDs,
    original CDR digest and recorded RAW/domain remain separately validated.
    GPS is optional to scan/IMU readiness; this creates no subscriber itself.
    """
    def __init__(self, ctx, expected_gids, *, process_validator, capacity=16):
        super().__init__(ctx, capacity)
        require(callable(process_validator), 'owned_observer_validator_required')
        require(type(expected_gids) is dict and set(expected_gids) == {'/gps/data', '/gps/fix'},
                'two_explicit_GPS_GIDs_required')
        for gid in expected_gids.values():
            require(type(gid) is str and len(gid) == 32 and gid != '0' * 32
                    and all(c in '0123456789abcdef' for c in gid), 'exact_GID_required')
        self.gids, self.validate_process = dict(expected_gids), process_validator

    def receive_row(self, row):
        require(type(row) is dict and self.validate_process(row) is True, 'owned_GNSS_observer_context_required')
        topic = row.get('topic')
        types = {'/gps/data': 'std_msgs/msg/String', '/gps/fix': 'sensor_msgs/msg/NavSatFix'}
        require(topic in types and row.get('ros_type') == types[topic], 'original_GPS_type_required')
        require(row.get('publisher_gid_hex') == self.gids[topic]
                and row.get('RAW_domain') == self.context[3].removeprefix('CLOCK_MONOTONIC_RAW:'), 'original_GPS_GID_or_RAW_domain_changed')
        text = row.get('original_CDR_hex')
        require(type(text) is str and 8 <= len(text) <= 2 * MAX_CDR
                and len(text) % 2 == 0 and all(c in '0123456789abcdef' for c in text), 'bounded_original_CDR_hex_required')
        raw = bytes.fromhex(text)
        require(type(row.get('original_CDR_bytes')) is int and row['original_CDR_bytes'] == len(raw)
                and row.get('original_CDR_sha256') == hashlib.sha256(raw).hexdigest(), 'original_GPS_CDR_digest_changed')
        require(row.get('original_header_present') is (topic == '/gps/fix'), 'String_has_no_Header')
        decoded = decode_navsat(raw) if topic == '/gps/fix' else None
        require(row.get('original_header_stamp_ns') == (decoded['stamp_ns'] if decoded else None),
                'original_GPS_header_changed')
        require(row.get('mission_admission') is False and row.get('proposed_linear_mps') == 0
                and row.get('proposed_yaw_rate_rad_s') == 0, 'GNSS_capture_has_no_authority')
        return self.receive(topic, raw, gid=row['publisher_gid_hex'],
                            callback_RAW_ns=integer(row.get('callback_monotonic_raw_ns')))


def solution(original):
    """Quality gate; report uncertainty, never convert NoFix into map position."""
    import numpy as np
    require(type(original) is OriginalGNSS, 'exact_original_GNSS_required')
    context(original.context)
    data, source = decode_gps_data(original.data_cdr)
    fix = decode_navsat(original.fix_cdr)
    require(source == fix['stamp_ns'] and data.get('frame_id') == fix['frame_id'] == 'gps_link',
            'exact_GPS_header_frame_pair_required')
    if data.get('valid') is not True or fix['status'] == -1:
        return None, 'NoFix'
    require(type(data.get('quality')) is int and data['quality'] in (1, 2, 4, 5)
            and type(fix['status']) is int and fix['status'] in (0, 1, 2), 'unsupported_GNSS_solution')
    require(data.get('simulated') is not True, 'physical_GNSS_not_virtual_truth_required')
    lat, lon = number(data.get('latitude')), number(data.get('longitude'))
    require(-90 < lat < 90 and -180 <= lon <= 180, 'WGS84_position_required')
    require(number(fix['latitude']) == lat and number(fix['longitude']) == lon, 'GPS_String_Fix_position_mismatch')
    require(fix['covariance_type'] in (1, 2, 3), 'unknown_position_covariance')
    covariance = np.array(fix['covariance'], dtype=float).reshape(3, 3)
    require(np.isfinite(covariance).all() and np.array_equal(covariance, covariance.T), 'invalid_position_covariance')
    require(np.linalg.eigvalsh(covariance).min() >= 0
            and np.linalg.eigvalsh(covariance[:2, :2]).min() > 0, 'invalid_position_covariance')
    cov_sigma = math.sqrt(float(np.linalg.eigvalsh(covariance[:2, :2]).max()))
    hacc = number(data.get('h_acc_m'))
    require(0 < hacc <= 50 and 0 < cov_sigma <= 50, 'invalid_horizontal_accuracy')
    epoch = None
    try:
        utc = datetime.fromisoformat(data['gps_time'])
        require(utc.tzinfo is not None, 'receiver_UTC_timezone_required')
        epoch = utc.astimezone(timezone.utc).isoformat()
        # iTow refines solution identity, NOT a fabricated host/UTC time offset.
        if 'i_tow_ms' in data:
            tow = data['i_tow_ms']
            require(type(tow) is int and 0 <= tow < 604800000, 'receiver_iTow_invalid')
            epoch += ':iTow=' + str(tow)
    except (KeyError, TypeError, ValueError):
        return None, 'missing_receiver_epoch'
    time_fields = ('gps_time_valid_date', 'gps_time_valid_time', 'gps_time_fully_resolved',
                   'gps_time_accuracy_s', 'gps_time_fraction_ns')
    # Old original archives remain useful weak constraints; missing receiver
    # quality never earns synchronized-time or native/control qualification.
    quality_present = all(k in data for k in time_fields)
    require(all(data[k] is True for k in time_fields[:3] if k in data), 'receiver_time_invalid')
    if quality_present:
        require(all(data[k] is True for k in time_fields[:3]), 'receiver_time_invalid')
        require(0 <= number(data['gps_time_accuracy_s']) <= 1
                and type(data['gps_time_fraction_ns']) is int
                and -10**9 < data['gps_time_fraction_ns'] < 10**9, 'receiver_time_quality_invalid')
    return dict(source_ns=source, epoch=epoch, latitude=lat, longitude=lon,
                scale_m=math.hypot(max(1., hacc, cov_sigma), .5), hacc_m=hacc,
                fix_type=str(data.get('fix_type') or 'unknown'),
                receiver_time_quality_present=quality_present,
                data_sha256=hashlib.sha256(original.data_cdr).hexdigest(),
                fix_sha256=hashlib.sha256(original.fix_cdr).hexdigest()), 'quality_position'


class WeakGNSSMapFactors:
    """Retained exact temporal associations and a separate weak global proposal.

    Validators must bind immutable originals and genuinely accepted map poses.
    Original history is never reset/evicted; reaching capacity rejects new work.
    The existing local map keeps running when GPS is absent/NoFix/rejected.
    """
    def __init__(self, math_backend, *, enabled=False, validate_original=None,
                 validate_pose=None, capacity=4096, max_bracket_ns=250_000_000):
        require(type(enabled) is bool and type(capacity) is int and 8 <= capacity <= 100000,
                'typed_bounded_configuration_required')
        integer(max_bracket_ns)
        require(max_bracket_ns > 0, 'positive_bracket_bound_required')
        self.math, self.enabled = math_backend, enabled
        self.validate_original, self.validate_pose = validate_original, validate_pose
        self.capacity, self.max_bracket_ns = capacity, max_bracket_ns
        self.lock, self.stop_event = threading.RLock(), threading.Event()
        self.active, self.ctx = False, None
        self.poses, self.factors, self.associations = [], [], []
        self.origin, self.epochs, self.rejections = None, set(), Counter()
        self.version = 0

    def start(self, ctx, *, explicit=False):
        context(ctx)
        require(self.enabled and explicit is True and callable(self.validate_original)
                and callable(self.validate_pose), 'explicit_trusted_original_pose_binding_required')
        with self.lock:
            require(not self.active and not self.stop_event.is_set(), 'one_owner_or_stopped_required')
            self.active, self.ctx = True, ctx
        return self.status()

    def stop(self):
        self.stop_event.set()  # Publication inhibition precedes numeric lock.
        with self.lock: self.active = False
        return self.status()

    def add_pose(self, pose):
        with self.lock:
            require(self.active and not self.stop_event.is_set(), 'owner_not_active')
            require(type(pose) is PoseWitness and context(pose.context) == self.ctx
                    and self.validate_pose(pose) is True, 'accepted_original_pose_binding_required')
            token(pose.chain); integer(pose.version); integer(pose.source_ns)
            require(type(pose.xy) is tuple and len(pose.xy) == 2 and len(pose.scan_sha256) == 64,
                    'exact_pose_metadata_required')
            for item in pose.xy: number(item)
            require(not self.poses or (pose.source_ns > self.poses[-1].source_ns
                    and pose.version > self.poses[-1].version), 'accepted_pose_rewind')
            require(len(self.poses) < self.capacity, 'retained_history_capacity')
            self.poses.append(pose)

    def add_original(self, original):
        with self.lock:
            if not self.active or self.stop_event.is_set(): return dict(accepted=False, reason='stopped')
            require(type(original) is OriginalGNSS and original.context == self.ctx
                    and self.validate_original(original) is True, 'original_GNSS_custody_required')
            try: s, reason = solution(original)
            except ValueError as error: s, reason = None, str(error)
            if s is None:
                self.rejections[reason] += 1
                return dict(accepted=False, reason=reason)
            if s['epoch'] in self.epochs:
                return dict(accepted=False, reason='duplicate_receiver_epoch')
            times = [p.source_ns for p in self.poses]
            j = bisect.bisect_left(times, s['source_ns'])
            # Exact timestamp match needs no motion interpolation.
            if j < len(times) and times[j] == s['source_ns']:
                a = b = self.poses[j]; fraction = 0.
            elif 0 < j < len(times):
                a, b = self.poses[j - 1:j + 1]
                if a.chain != b.chain or b.source_ns - a.source_ns > self.max_bracket_ns:
                    return dict(accepted=False, reason='no_continuous_exact_header_bracket')
                fraction = (s['source_ns'] - a.source_ns) / (b.source_ns - a.source_ns)
            else:
                return dict(accepted=False, reason='waiting_exact_native_pose_bracket')
            require(self.validate_pose(a) is True and self.validate_pose(b) is True,
                    'retained_original_pose_changed')
            require(self.validate_original(original) is True, 'original_GNSS_changed_during_association')
            if self.stop_event.is_set(): return dict(accepted=False, reason='stopped')
            require(len(self.factors) < self.capacity, 'retained_history_capacity')
            local = tuple(x + fraction * (y - x) for x, y in zip(a.xy, b.xy))
            if self.origin is None: self.origin = self.math.FixedENU(s['latitude'], s['longitude'])
            xy = tuple(map(float, self.origin.xy(s['latitude'], s['longitude'])))
            self.factors.append(s)
            self.associations.append(dict(local_xy=local, enu_xy=xy, chain=a.chain,
                pose_versions=(a.version, b.version), scan_sha256=(a.scan_sha256, b.scan_sha256),
                pose_source_ns=(a.source_ns, b.source_ns), interpolation=fraction))
            self.epochs.add(s['epoch']); self.version += 1
            return dict(accepted=True, reason='weak_historical_GNSS_factor', factor_version=self.version,
                        host_stamp_is_receiver_time=False, runtime_authority=False)

    def global_proposal(self):
        """Weak positional graph result; commit requires complete map rerasterization."""
        import numpy as np
        with self.lock:
            if not self.active or self.stop_event.is_set(): return None
            if not self.factors: return dict(accepted=False, reason='no_usable_GNSS')
            chain = self.associations[-1]['chain']
            selected = [(s, a) for s, a in zip(self.factors, self.associations) if a['chain'] == chain]
            epoch_ns = selected[0][0]['source_ns']
            factors = [self.math.GNSSFactor((s['source_ns'] - epoch_ns) / 1e9, s['epoch'],
                a['enu_xy'], s['scale_m'], s['hacc_m'], s['fix_type']) for s, a in selected]
            local = np.asarray([a['local_xy'] for _, a in selected])
            aligned = self.math.robust_rigid_alignment(local, factors)
            if not aligned['accepted']: return dict(accepted=False, reason=aligned['reason'])
            poses = [p for p in self.poses if p.chain == chain]
            require(all(self.validate_pose(p) is True for p in poses), 'retained_original_pose_changed')
            world = np.asarray([p.xy for p in poses]) @ aligned['rotation'].T + aligned['translation']
            times = [(p.source_ns - epoch_ns) / 1e9 for p in poses]
            corrected, proof = self.math.smooth_position_constraints(times, world, factors)
            require(all(self.validate_pose(p) is True for p in poses), 'retained_original_pose_changed')
            if self.stop_event.is_set(): return None
            return dict(accepted=True, reason='weak_global_position_proposal', factor_version=self.version,
                chain=chain, pose_versions=[p.version for p in poses], source_ns=[p.source_ns for p in poses],
                alignment_yaw_rad=float(__import__('math').atan2(aligned['rotation'][1,0],aligned['rotation'][0,0])),
                corrected_ENU_xy=corrected.tolist(), original_pose_history_unchanged=True,
                requires_complete_retained_scan_rerasterization=True, covariance_calibrated=False,
                receiver_time_synchronized=False, native_GNSS_pipeline_qualified=False,
                runtime_authority=False, motor_command_sent=False, autonomy_output_enabled=False,
                proof=proof)

    def status(self):
        with self.lock:
            return dict(enabled=self.enabled, active=self.active and not self.stop_event.is_set(),
                retained_poses=len(self.poses), retained_GNSS_factors=len(self.factors),
                factor_version=self.version, rejections=dict(self.rejections),
                origin_WGS84=(self.origin.latitude, self.origin.longitude) if self.origin else None,
                native_GNSS_pipeline_qualified=False, body_calibration_qualified=False,
                runtime_authority=False, motor_command_sent=False, autonomy_output_enabled=False)


if __name__ == '__main__':
    print(json.dumps(dict(default_inert=True, ROS_initialized=False, files_written=False,
                         native_GNSS_pipeline_qualified=False, motor_command_sent=False)))
