"""Start-referenced BNO085 IMU calibration helpers.

The recorder intentionally keeps this module free of ROS dependencies so the
calibration contract can be unit-tested on development hosts.  Raw samples are
never overwritten: corrected samples are additional data with explicit
provenance and are only produced after a contiguous stationary window.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np


IMU_CALIBRATION_SCHEMA = "orchard.imu_start_reference.v1"
IMU_CALIBRATION_REVISION = "static_level_fixed_yaw_mount_gyro_bias_body_covariance_v3"
IMU_TIMELINE_SCHEMA = "orchard.imu_timeline.v1"


def _finite_vector(values: Iterable[Any], size: int) -> tuple[float, ...] | None:
    try:
        result = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return None
    if len(result) != size or not all(math.isfinite(value) for value in result):
        return None
    return result


def normalize_quaternion(values: Iterable[Any]) -> tuple[float, float, float, float] | None:
    quaternion = _finite_vector(values, 4)
    if quaternion is None:
        return None
    norm = math.sqrt(sum(value * value for value in quaternion))
    if norm < 1e-9:
        return None
    normalized = tuple(value / norm for value in quaternion)
    if normalized[3] < 0.0:
        normalized = tuple(-value for value in normalized)
    return normalized  # type: ignore[return-value]


def quaternion_conjugate(q: Iterable[Any]) -> tuple[float, float, float, float]:
    x, y, z, w = normalize_quaternion(q) or (0.0, 0.0, 0.0, 1.0)
    return (-x, -y, -z, w)


def quaternion_multiply(left: Iterable[Any], right: Iterable[Any]) -> tuple[float, float, float, float]:
    lx, ly, lz, lw = normalize_quaternion(left) or (0.0, 0.0, 0.0, 1.0)
    rx, ry, rz, rw = normalize_quaternion(right) or (0.0, 0.0, 0.0, 1.0)
    return normalize_quaternion(
        (
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
            lw * rw - lx * rx - ly * ry - lz * rz,
        )
    ) or (0.0, 0.0, 0.0, 1.0)


def rotate_vector(q: Iterable[Any], vector: Iterable[Any]) -> tuple[float, float, float]:
    quaternion = normalize_quaternion(q) or (0.0, 0.0, 0.0, 1.0)
    values = _finite_vector(vector, 3) or (0.0, 0.0, 0.0)
    # Do not normalize the pure-vector quaternion during multiplication.
    x, y, z, w = quaternion
    vx, vy, vz = values
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def quaternion_to_euler_deg(q: Iterable[Any]) -> dict[str, float]:
    x, y, z, w = normalize_quaternion(q) or (0.0, 0.0, 0.0, 1.0)
    sin_roll_cos_pitch = 2.0 * (w * x + y * z)
    cos_roll_cos_pitch = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sin_roll_cos_pitch, cos_roll_cos_pitch)
    sin_pitch = max(-1.0, min(1.0, 2.0 * (w * y - z * x)))
    pitch = math.asin(sin_pitch)
    sin_yaw_cos_pitch = 2.0 * (w * z + x * y)
    cos_yaw_cos_pitch = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(sin_yaw_cos_pitch, cos_yaw_cos_pitch)
    return {
        "roll": math.degrees(roll),
        "pitch": math.degrees(pitch),
        "yaw": math.degrees(yaw),
    }


def yaw_quaternion(q: Iterable[Any]) -> tuple[float, float, float, float]:
    yaw = math.radians(quaternion_to_euler_deg(q)["yaw"])
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def yaw_offset_quaternion_deg(angle_deg: float) -> tuple[float, float, float, float]:
    angle = math.radians(float(angle_deg))
    return (0.0, 0.0, math.sin(angle / 2.0), math.cos(angle / 2.0))


def markley_quaternion_mean(quaternions: Iterable[Iterable[Any]]) -> tuple[float, float, float, float] | None:
    rows = [normalize_quaternion(row) for row in quaternions]
    valid = [row for row in rows if row is not None]
    if not valid:
        return None
    accumulator = np.zeros((4, 4), dtype=np.float64)
    for row in valid:
        vector = np.asarray(row, dtype=np.float64).reshape(4, 1)
        accumulator += vector @ vector.T
    eigenvalues, eigenvectors = np.linalg.eigh(accumulator)
    return normalize_quaternion(eigenvectors[:, int(np.argmax(eigenvalues))].tolist())


def _variance(values: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    if len(values) < 2:
        return (0.0, 0.0, 0.0)
    matrix = np.asarray(values, dtype=np.float64)
    result = np.var(matrix, axis=0, ddof=1)
    return tuple(float(max(0.0, item)) for item in result)  # type: ignore[return-value]


@dataclass(frozen=True)
class ImuCalibrationConfig:
    stationary_window_s: float = 1.5
    min_samples: int = 20
    max_gyro_norm_rad_s: float = 0.08
    gravity_m_s2: float = 9.80665
    gravity_tolerance_m_s2: float = 2.0
    max_accel_norm_std_m_s2: float = 0.35
    body_yaw_offset_deg: float = 0.0


class StartReferenceCalibrator:
    """Build a recording-local level/mount reference from stationary samples."""

    def __init__(self, config: ImuCalibrationConfig | None = None):
        self.config = config or ImuCalibrationConfig()
        self.body_yaw_offset_deg = float(self.config.body_yaw_offset_deg)
        if not math.isfinite(self.body_yaw_offset_deg):
            raise ValueError("body_yaw_offset_deg must be finite")
        self.body_yaw_offset_quaternion = yaw_offset_quaternion_deg(self.body_yaw_offset_deg)
        self.reset()

    def _mount_solution(
        self,
        reference: Iterable[Any],
    ) -> tuple[
        tuple[float, float, float, float],
        tuple[float, float, float, float],
        tuple[float, float, float, float],
    ]:
        """Return sensor yaw, sensor-to-body, and reference body orientation.

        The stationary reference estimates the near-90-degree board tilt.  A
        fixed operator-validated yaw then restores the vehicle forward/left
        axes, which gravity alone cannot observe.
        """

        reference_q = normalize_quaternion(reference) or (0.0, 0.0, 0.0, 1.0)
        reference_sensor_yaw = yaw_quaternion(reference_q)
        sensor_to_level = quaternion_multiply(quaternion_conjugate(reference_sensor_yaw), reference_q)
        sensor_to_body = quaternion_multiply(self.body_yaw_offset_quaternion, sensor_to_level)
        reference_body = quaternion_multiply(reference_q, quaternion_conjugate(sensor_to_body))
        return reference_sensor_yaw, sensor_to_body, reference_body

    def mount_provenance(self) -> dict[str, Any]:
        return {
            "source": "operator_axis_mapping_plus_stationary_level_estimate",
            "body_convention": "REP-103 base_link: +X forward, +Y left, +Z up",
            "operator_reported_board_axes": {
                "+X": "+base_Z/up",
                "+Y": "+base_Y/left",
                "+Z": "-base_X/rear",
            },
            "body_yaw_offset_deg": self.body_yaw_offset_deg,
            "body_yaw_offset_quaternion": _quaternion_dict(self.body_yaw_offset_quaternion),
            "validation": "stationary gravity validated; forward/lateral motion sign validation pending",
        }

    def reset(self) -> None:
        self.recording_id: str | None = None
        self.started_monotonic: float | None = None
        self.samples: list[dict[str, Any]] = []
        self.rejected_samples = 0
        self.reset_count = 0
        self.last_rejection_reason: str | None = None
        self.calibration: dict[str, Any] | None = None

    def begin(self, recording_id: str, now_monotonic: float | None = None) -> None:
        self.reset()
        self.recording_id = str(recording_id)
        self.started_monotonic = float(now_monotonic if now_monotonic is not None else time.monotonic())

    def zero_from_sample(
        self,
        sample: dict[str, Any],
        *,
        reference_id: str,
        now_monotonic: float | None = None,
    ) -> dict[str, Any] | None:
        """Make the supplied pose the current body-frame zero immediately.

        This operator action is deliberately distinct from the recording-start
        stationary estimator: it uses one current sample, records that fact in
        provenance, and never claims a statistically estimated covariance.
        """

        now = float(now_monotonic if now_monotonic is not None else time.monotonic())
        quaternion = normalize_quaternion(
            (
                (sample.get("orientation") or {}).get("x"),
                (sample.get("orientation") or {}).get("y"),
                (sample.get("orientation") or {}).get("z"),
                (sample.get("orientation") or {}).get("w"),
            )
        )
        gyro = _xyz_tuple(sample.get("angular_velocity") or {})
        accel = _xyz_tuple(sample.get("linear_acceleration") or {})
        if quaternion is None or gyro is None or accel is None:
            self.last_rejection_reason = "manual_zero_malformed_or_non_finite"
            return None

        self.reset()
        self.recording_id = str(reference_id)
        self.started_monotonic = now
        reference_sensor_yaw, sensor_to_body, reference_body = self._mount_solution(quaternion)
        reference_yaw = yaw_quaternion(reference_body)
        reference_euler = quaternion_to_euler_deg(quaternion)
        reference_body_euler = quaternion_to_euler_deg(reference_body)
        accel_body = rotate_vector(sensor_to_body, accel)
        self.samples = [
            {
                "received_monotonic": now,
                "quaternion": quaternion,
                "gyro": gyro,
                "accel": accel,
                "accel_norm": math.sqrt(sum(value * value for value in accel)),
            }
        ]
        self.calibration = {
            "schema": IMU_CALIBRATION_SCHEMA,
            "revision": IMU_CALIBRATION_REVISION,
            "valid": True,
            "recording_id": self.recording_id,
            "sample_count": 1,
            "stationary_span_s": 0.0,
            "completed_monotonic": now,
            "source": "dashboard_manual_zero",
            "manual_zero": True,
            "assumption": "operator_selected_current_pose_as_body_frame_zero",
            "heading_policy": "manual_reference_body_roll_pitch_yaw_zero_after_mount_transform",
            "raw_orientation_convention": "bno085_rotation_vector_sensor_native",
            "reference_quaternion": _quaternion_dict(quaternion),
            "reference_euler_deg": {key: round(value, 6) for key, value in reference_euler.items()},
            "reference_body_quaternion": _quaternion_dict(reference_body),
            "reference_body_euler_deg": {key: round(value, 6) for key, value in reference_body_euler.items()},
            "sensor_to_body_quaternion": _quaternion_dict(sensor_to_body),
            "reference_sensor_yaw_quaternion": _quaternion_dict(reference_sensor_yaw),
            "reference_yaw_quaternion": _quaternion_dict(reference_yaw),
            "mount_transform": self.mount_provenance(),
            "gyro_bias_rad_s": _vector_dict(gyro),
            "gyro_bias_frame": "sensor_native_single_sample",
            "gyro_variance": _vector_dict((0.0, 0.0, 0.0)),
            "accel_variance": _vector_dict((0.0, 0.0, 0.0)),
            "covariance_frame": "base_link",
            "covariance_estimated": False,
            "accel_norm_mean_m_s2": round(math.sqrt(sum(value * value for value in accel)), 8),
            "accel_norm_std_m_s2": None,
            "reference_acceleration_body_m_s2": _vector_dict(accel_body),
            "stationarity_thresholds": None,
            "rejected_sample_count": 0,
            "window_reset_count": 0,
            "mapping_ready": False,
            "mapping_blockers": [
                "manual_zero_is_not_a_stationary_covariance_estimate",
                "imu_to_base_link_forward_lateral_motion_sign_not_validated",
                "time_alignment_and_wheel_odometry_not_validated",
            ],
        }
        return dict(self.calibration)

    @property
    def ready(self) -> bool:
        return bool(self.calibration and self.calibration.get("valid"))

    def observe(self, sample: dict[str, Any], now_monotonic: float | None = None) -> bool:
        if self.recording_id is None or self.ready:
            return self.ready
        now = float(now_monotonic if now_monotonic is not None else time.monotonic())
        quaternion = normalize_quaternion(
            (
                (sample.get("orientation") or {}).get("x"),
                (sample.get("orientation") or {}).get("y"),
                (sample.get("orientation") or {}).get("z"),
                (sample.get("orientation") or {}).get("w"),
            )
        )
        gyro = _xyz_tuple(sample.get("angular_velocity") or {})
        accel = _xyz_tuple(sample.get("linear_acceleration") or {})
        if quaternion is None or gyro is None or accel is None:
            self._reject("malformed_or_non_finite", reset_window=True)
            return False
        gyro_norm = math.sqrt(sum(value * value for value in gyro))
        accel_norm = math.sqrt(sum(value * value for value in accel))
        if gyro_norm > self.config.max_gyro_norm_rad_s:
            self._reject("angular_motion", reset_window=True)
            return False
        if abs(accel_norm - self.config.gravity_m_s2) > self.config.gravity_tolerance_m_s2:
            self._reject("acceleration_not_gravity", reset_window=True)
            return False
        if self.samples and now <= float(self.samples[-1]["received_monotonic"]):
            self._reject("timestamp_regression", reset_window=True)
            return False
        self.samples.append(
            {
                "received_monotonic": now,
                "quaternion": quaternion,
                "gyro": gyro,
                "accel": accel,
                "accel_norm": accel_norm,
            }
        )
        elapsed = now - float(self.samples[0]["received_monotonic"])
        if elapsed < self.config.stationary_window_s or len(self.samples) < self.config.min_samples:
            return False
        accel_norms = np.asarray([row["accel_norm"] for row in self.samples], dtype=np.float64)
        accel_norm_std = float(np.std(accel_norms, ddof=1)) if len(accel_norms) > 1 else 0.0
        if accel_norm_std > self.config.max_accel_norm_std_m_s2:
            self._reject("acceleration_unstable", reset_window=True)
            return False
        reference = markley_quaternion_mean(row["quaternion"] for row in self.samples)
        if reference is None:
            self._reject("quaternion_mean_failed", reset_window=True)
            return False
        reference_sensor_yaw, sensor_to_body, reference_body = self._mount_solution(reference)
        reference_yaw = yaw_quaternion(reference_body)
        gyro_matrix = np.asarray([row["gyro"] for row in self.samples], dtype=np.float64)
        gyro_bias = tuple(float(value) for value in np.mean(gyro_matrix, axis=0))
        # Corrected vectors are expressed in the recording-start body frame, so
        # their covariance must use that same frame.  Keeping sensor-axis
        # variances here would silently attach the wrong uncertainty axis when
        # the board is mounted close to 90 degrees, as it is on the Jetson.
        gyro_body_residuals = [
            rotate_vector(
                sensor_to_body,
                tuple(value - bias for value, bias in zip(row["gyro"], gyro_bias)),
            )
            for row in self.samples
        ]
        accel_body_samples = [rotate_vector(sensor_to_body, row["accel"]) for row in self.samples]
        gyro_variance = _variance(gyro_body_residuals)
        accel_variance = _variance(accel_body_samples)
        reference_euler = quaternion_to_euler_deg(reference)
        reference_body_euler = quaternion_to_euler_deg(reference_body)
        self.calibration = {
            "schema": IMU_CALIBRATION_SCHEMA,
            "revision": IMU_CALIBRATION_REVISION,
            "valid": True,
            "recording_id": self.recording_id,
            "sample_count": len(self.samples),
            "stationary_span_s": round(elapsed, 6),
            "completed_monotonic": now,
            "assumption": "vehicle_stationary_and_level_at_recording_start",
            "heading_policy": "recording_start_body_attitude_zero_after_mount_transform",
            "raw_orientation_convention": "bno085_rotation_vector_sensor_native",
            "reference_quaternion": _quaternion_dict(reference),
            "reference_euler_deg": {key: round(value, 6) for key, value in reference_euler.items()},
            "reference_body_quaternion": _quaternion_dict(reference_body),
            "reference_body_euler_deg": {key: round(value, 6) for key, value in reference_body_euler.items()},
            "sensor_to_body_quaternion": _quaternion_dict(sensor_to_body),
            "reference_sensor_yaw_quaternion": _quaternion_dict(reference_sensor_yaw),
            "reference_yaw_quaternion": _quaternion_dict(reference_yaw),
            "mount_transform": self.mount_provenance(),
            "gyro_bias_rad_s": _vector_dict(gyro_bias),
            "gyro_bias_frame": "sensor_native",
            "gyro_variance": _vector_dict(gyro_variance),
            "accel_variance": _vector_dict(accel_variance),
            "covariance_frame": "base_link",
            "accel_norm_mean_m_s2": round(float(np.mean(accel_norms)), 8),
            "accel_norm_std_m_s2": round(accel_norm_std, 8),
            "stationarity_thresholds": {
                "max_gyro_norm_rad_s": self.config.max_gyro_norm_rad_s,
                "gravity_m_s2": self.config.gravity_m_s2,
                "gravity_tolerance_m_s2": self.config.gravity_tolerance_m_s2,
                "max_accel_norm_std_m_s2": self.config.max_accel_norm_std_m_s2,
            },
            "rejected_sample_count": self.rejected_samples,
            "window_reset_count": self.reset_count,
            "mapping_ready": False,
            "mapping_blockers": [
                "imu_to_base_link_forward_lateral_motion_sign_not_validated",
                "time_alignment_and_wheel_odometry_not_validated",
            ],
        }
        return True

    def correct(self, sample: dict[str, Any]) -> dict[str, Any] | None:
        if not self.ready or self.calibration is None:
            return None
        raw_q = normalize_quaternion(
            (
                (sample.get("orientation") or {}).get("x"),
                (sample.get("orientation") or {}).get("y"),
                (sample.get("orientation") or {}).get("z"),
                (sample.get("orientation") or {}).get("w"),
            )
        )
        gyro = _xyz_tuple(sample.get("angular_velocity") or {})
        accel = _xyz_tuple(sample.get("linear_acceleration") or {})
        if raw_q is None or gyro is None or accel is None:
            return None
        sensor_to_body = _quaternion_tuple(self.calibration["sensor_to_body_quaternion"])
        reference_body = _quaternion_tuple(
            self.calibration.get("reference_body_quaternion")
            or self.calibration["reference_yaw_quaternion"]
        )
        gyro_bias = _vector_tuple(self.calibration["gyro_bias_rad_s"])
        current_body_q = quaternion_multiply(raw_q, quaternion_conjugate(sensor_to_body))
        local_body_q = quaternion_multiply(
            quaternion_conjugate(reference_body),
            current_body_q,
        )
        corrected_gyro = rotate_vector(sensor_to_body, tuple(value - bias for value, bias in zip(gyro, gyro_bias)))
        corrected_accel = rotate_vector(sensor_to_body, accel)
        return {
            "schema": IMU_TIMELINE_SCHEMA,
            "revision": IMU_CALIBRATION_REVISION,
            "frame_id": "base_link",
            "stamp": dict(sample.get("stamp") or {}),
            "orientation": _quaternion_dict(local_body_q),
            "euler_deg": {key: round(value, 6) for key, value in quaternion_to_euler_deg(local_body_q).items()},
            "angular_velocity": _vector_dict(corrected_gyro),
            "linear_acceleration": _vector_dict(corrected_accel),
            "covariance": {
                "angular_velocity_diagonal": dict(self.calibration["gyro_variance"]),
                "linear_acceleration_diagonal": dict(self.calibration["accel_variance"]),
                "orientation_diagonal": {"x": -1.0, "y": 0.0, "z": 0.0},
            },
            "calibration_valid": True,
            "calibration_revision": IMU_CALIBRATION_REVISION,
            "calibration_source": self.calibration.get("source", "recording_start_stationary_window"),
            "calibration_reference_id": self.recording_id,
            "mount_transform": dict(self.calibration.get("mount_transform") or {}),
        }

    def correct_magnetic(self, magnetic: dict[str, Any]) -> dict[str, float] | None:
        if not self.ready or self.calibration is None:
            return None
        values = _xyz_tuple(magnetic)
        if values is None:
            return None
        sensor_to_body = _quaternion_tuple(self.calibration["sensor_to_body_quaternion"])
        return _vector_dict(rotate_vector(sensor_to_body, values))

    def status(self, now_monotonic: float | None = None) -> dict[str, Any]:
        now = float(now_monotonic if now_monotonic is not None else time.monotonic())
        span = 0.0
        if self.ready and self.calibration is not None:
            span = max(0.0, float(self.calibration.get("stationary_span_s") or 0.0))
        elif self.samples:
            span = max(0.0, now - float(self.samples[0]["received_monotonic"]))
        return {
            "schema": IMU_CALIBRATION_SCHEMA,
            "revision": IMU_CALIBRATION_REVISION,
            "state": "ready" if self.ready else "collecting" if self.recording_id else "idle",
            "valid": self.ready,
            "recording_id": self.recording_id,
            "accepted_samples": len(self.samples),
            "required_samples": self.config.min_samples,
            "stationary_span_s": round(span, 3),
            "required_stationary_span_s": self.config.stationary_window_s,
            "rejected_samples": self.rejected_samples,
            "window_reset_count": self.reset_count,
            "last_rejection_reason": self.last_rejection_reason,
            "calibration": dict(self.calibration or {}),
        }

    def _reject(self, reason: str, *, reset_window: bool) -> None:
        self.rejected_samples += 1
        self.last_rejection_reason = reason
        if reset_window and self.samples:
            self.samples = []
            self.reset_count += 1


def _vector_dict(values: Iterable[Any]) -> dict[str, float]:
    x, y, z = _finite_vector(values, 3) or (0.0, 0.0, 0.0)
    return {"x": float(x), "y": float(y), "z": float(z)}


def _quaternion_dict(values: Iterable[Any]) -> dict[str, float]:
    x, y, z, w = normalize_quaternion(values) or (0.0, 0.0, 0.0, 1.0)
    return {"x": float(x), "y": float(y), "z": float(z), "w": float(w)}


def _vector_tuple(values: dict[str, Any]) -> tuple[float, float, float]:
    return _xyz_tuple(values) or (0.0, 0.0, 0.0)


def _xyz_tuple(values: dict[str, Any]) -> tuple[float, float, float] | None:
    return _finite_vector((values.get("x"), values.get("y"), values.get("z")), 3)  # type: ignore[return-value]


def _quaternion_tuple(values: dict[str, Any]) -> tuple[float, float, float, float]:
    return normalize_quaternion((values.get("x"), values.get("y"), values.get("z"), values.get("w"))) or (0.0, 0.0, 0.0, 1.0)
