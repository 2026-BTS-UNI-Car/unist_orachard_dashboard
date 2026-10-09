"""Exact sensor-frame pairing extracted from frozen primitives; no SIM profile."""
from bisect import bisect_left
from dataclasses import dataclass
import math
from .sensor_contract import _imu_group
MAX_IMU_WINDOW=64
MAX_IMU_BRACKET_NS=60_000_000

def _stamp(value):return type(value) is int and 0<=value<=2**63-1

def _number(value):return type(value) in (int,float) and math.isfinite(value)

def _label(value):return isinstance(value,str) and bool(value.strip()) and len(value)<=256

@dataclass(frozen=True)
class NativeScan:
    epoch: str
    frame_id: str
    stamp_ns: int
    ranges: tuple
    angle_min: object
    angle_increment: object
    range_min: object
    range_max: object
    time_increment: object
    angle_max: object
    scan_time: object
    intensities: tuple
@dataclass(frozen=True)
class ImuSample:
    """Unmodified numeric fields and true receipt clock, not storage log time.

    Malformed/unavailable numeric groups remain representable and are rejected
    by the existing ROS IMU group contract when used. No -1 reinterpretation.
    """
    stamp_ns: int
    receipt_ns: int
    frame_id: str
    quaternion_xyzw: tuple
    angular_velocity: tuple
    orientation_covariance: tuple
    angular_velocity_covariance: tuple

@dataclass(frozen=True)
class ImuPairing:
    reason: str
    start_indices: tuple = ()
    end_indices: tuple = ()
    yaw_delta_rad: float | None = None
    yaw_rate_rad_s: float | None = None
    rate_stamp_ns: int | None = None
    rate_receipt_ns: int | None = None
    covariance_unknown: bool | None = None

def _angles(quaternion):
    x, y, z, w = quaternion
    roll = math.atan2(2*(w*x+y*z), 1-2*(x*x+y*y))
    pitch = math.asin(max(-1., min(1., 2*(w*y-z*x))))
    yaw = math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))
    return roll, pitch, yaw

def pair_imu(samples, previous_stamp_ns, stamp_ns, *, frame_id, receipt_now_ns,
             max_roll_pitch_rad=None):
    """Bracket yaw with available IMUs; use the actual lower-end gyro sample.

    Only yaw is interpolated. Yaw rate, its source stamp and receipt are from
    one actual sample at/before the ending scan, never a synthesized timestamp.
    The caller must wait for bounded brackets before invoking, or accept STOP;
    unavailable future messages and extrapolation cannot supply evidence.
    """
    if (not isinstance(samples, tuple) or not 1 <= len(samples) <= MAX_IMU_WINDOW
            or not all(isinstance(s, ImuSample) for s in samples)
            or not _stamp(previous_stamp_ns) or not _stamp(stamp_ns)
            or not _stamp(receipt_now_ns) or not _label(frame_id)
            or (max_roll_pitch_rad is not None and (not _number(max_roll_pitch_rad)
                or not 0 < max_roll_pitch_rad <= .05))):
        return ImuPairing('invalid_imu_window')
    if any(not _stamp(s.stamp_ns) or not _stamp(s.receipt_ns) for s in samples):
        return ImuPairing('invalid_imu_timestamps')
    if any(a.stamp_ns >= b.stamp_ns or a.receipt_ns > b.receipt_ns
           for a, b in zip(samples, samples[1:])):
        return ImuPairing('reordered_imu_window')
    if any(s.frame_id != frame_id for s in samples):
        return ImuPairing('imu_frame_mismatch')
    if any(s.receipt_ns > receipt_now_ns for s in samples):
        return ImuPairing('imu_not_yet_received')
    stamps = [s.stamp_ns for s in samples]
    brackets, yaws, unknown = [], [], False
    for at in (previous_stamp_ns, stamp_ns):
        index = bisect_left(stamps, at)
        if index < len(stamps) and stamps[index] == at:
            lo = hi = index
        elif 0 < index < len(stamps):
            lo, hi = index-1, index
        else:
            return ImuPairing('imu_outside_retained_span')
        gap = stamps[hi]-stamps[lo]
        if gap > MAX_IMU_BRACKET_NS:
            return ImuPairing('imu_bracket_gap_exceeded')
        angles = []
        for i in (lo, hi):
            sample = samples[i]
            values = dict(quaternion=sample.quaternion_xyzw,
                          orientation_covariance=sample.orientation_covariance,
                          angular_velocity=sample.angular_velocity,
                          angular_velocity_covariance=sample.angular_velocity_covariance)
            orientation = _imu_group(values, 'quaternion', 'orientation_covariance', 4)
            angular = _imu_group(values, 'angular_velocity', 'angular_velocity_covariance', 3)
            for name, group in (('orientation', orientation), ('angular_velocity', angular)):
                if not group.valid or not group.available:
                    return ImuPairing('imu_'+name+'_'+group.reason)
                unknown = unknown or group.uncertainty_unknown
            roll, pitch, yaw = _angles(orientation.values)
            if max_roll_pitch_rad is not None and max(abs(roll), abs(pitch)) > max_roll_pitch_rad:
                return ImuPairing('outside_conditional_planar_profile')
            angles.append(yaw)
        fraction = 0. if lo == hi else (at-stamps[lo])/gap
        delta = math.atan2(math.sin(angles[1]-angles[0]), math.cos(angles[1]-angles[0]))
        yaws.append(angles[0]+fraction*delta)
        brackets.append((lo, hi))
    delta = math.atan2(math.sin(yaws[1]-yaws[0]), math.cos(yaws[1]-yaws[0]))
    rate = samples[brackets[1][0]]
    return ImuPairing('bracketed_available_imu', brackets[0], brackets[1], delta,
                      float(rate.angular_velocity[2]), rate.stamp_ns, rate.receipt_ns, unknown)
