#!/usr/bin/env python3
"""Publish the Jetson-attached BNO085 through ROS 2 only.

Dashboard and archive components must subscribe to the ROS topics; they never
open the I2C device directly.  The raw orientation convention and mounting
extrinsic remain explicit provenance until independently measured.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import time
import warnings
from pathlib import Path
from typing import Any, Iterable


DRIVER_SCHEMA = "orchard.imu_driver_status.v1"
DRIVER_REVISION = "bno085_i2c_ros2_raw_covariance_unknown_v3"


class TransientSensorSample(ValueError):
    """A malformed warm-up sample that must not reset the BNO085 bus."""


def vector_dict(names: Iterable[str], values: Iterable[Any]) -> dict[str, float]:
    result = {name: float(value) for name, value in zip(names, values)}
    if len(result) != 3 or not all(math.isfinite(value) for value in result.values()):
        raise TransientSensorSample("BNO085 returned a non-finite vector")
    return result


def normalized_quaternion(values: Iterable[Any]) -> tuple[float, float, float, float]:
    result = tuple(float(value) for value in values)
    if len(result) != 4 or not all(math.isfinite(value) for value in result):
        raise TransientSensorSample("BNO085 returned an invalid quaternion")
    norm = math.sqrt(sum(value * value for value in result))
    if not math.isfinite(norm):
        raise TransientSensorSample("BNO085 quaternion norm overflowed")
    if norm < 1e-9:
        raise TransientSensorSample("BNO085 rotation vector is warming up (zero quaternion)")
    normalized = tuple(value / norm for value in result)
    if normalized[3] < 0.0:
        normalized = tuple(-value for value in normalized)
    return normalized  # type: ignore[return-value]


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="BNO085 I2C to ROS 2 sensor_msgs driver")
    parser.add_argument("--i2c-bus", type=int, default=int(os.environ.get("IMU_I2C_BUS", "7")))
    parser.add_argument("--i2c-address", type=lambda value: int(value, 0), default=int(os.environ.get("IMU_I2C_ADDRESS", "0x4A"), 0))
    parser.add_argument("--sample-hz", type=float, default=float(os.environ.get("IMU_SAMPLE_HZ", "20")))
    parser.add_argument("--frame-id", default=os.environ.get("IMU_FRAME_ID", "imu_link"))
    parser.add_argument("--raw-topic", default=os.environ.get("IMU_RAW_TOPIC", "/imu/data_raw"))
    parser.add_argument("--mag-topic", default=os.environ.get("IMU_MAG_TOPIC", "/imu/mag"))
    parser.add_argument("--status-topic", default=os.environ.get("IMU_DRIVER_STATUS_TOPIC", "/orchard/imu/driver_status"))
    parser.add_argument("--status-file", default=os.environ.get("IMU_DRIVER_STATUS_FILE", "/app/runtime/imu_driver_status.json"))
    parser.add_argument("--retry-s", type=float, default=float(os.environ.get("IMU_RETRY_S", "5")))
    args = parser.parse_args()
    args.sample_hz = max(1.0, min(100.0, float(args.sample_hz)))
    args.retry_s = max(1.0, min(60.0, float(args.retry_s)))

    try:
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import Imu, MagneticField
        from std_msgs.msg import String
    except ImportError as exc:
        raise SystemExit(f"ROS 2 Iron Python packages are required: {exc}") from exc

    try:
        from adafruit_bno08x import (
            BNO_REPORT_ACCELEROMETER,
            BNO_REPORT_GYROSCOPE,
            BNO_REPORT_MAGNETOMETER,
            BNO_REPORT_ROTATION_VECTOR,
        )
        from adafruit_bno08x.i2c import BNO08X_I2C
        from adafruit_extended_bus import ExtendedI2C
    except ImportError as exc:
        raise SystemExit(f"Install pinned BNO085 dependencies in the ROS2 image: {exc}") from exc

    warnings.filterwarnings("ignore", message="I2C frequency is not settable in python, ignoring!")

    class Bno085Node(Node):
        def __init__(self):
            super().__init__("ubts_bno085_imu_driver")
            self.raw_publisher = self.create_publisher(Imu, args.raw_topic, qos_profile_sensor_data)
            self.mag_publisher = self.create_publisher(MagneticField, args.mag_topic, qos_profile_sensor_data)
            self.status_publisher = self.create_publisher(String, args.status_topic, 10)
            self.status_path = Path(args.status_file)
            self.sensor = None
            self.i2c = None
            self.sequence = 0
            self.next_retry_at = 0.0
            self.last_sample_at: float | None = None
            self.last_error: str | None = None
            self.transient_sample_count = 0
            self.latest: dict[str, Any] = {}
            self.timer = self.create_timer(1.0 / args.sample_hz, self.tick)
            self.publish_status("starting")

        def connect(self) -> bool:
            now = time.monotonic()
            if now < self.next_retry_at:
                return False
            self.close_sensor()
            try:
                self.i2c = ExtendedI2C(args.i2c_bus)
                self.sensor = BNO08X_I2C(self.i2c, address=args.i2c_address)
                interval_us = max(10_000, int(round(1_000_000.0 / args.sample_hz)))
                for report in (
                    BNO_REPORT_ACCELEROMETER,
                    BNO_REPORT_GYROSCOPE,
                    BNO_REPORT_MAGNETOMETER,
                    BNO_REPORT_ROTATION_VECTOR,
                ):
                    try:
                        self.sensor.enable_feature(report, interval_us)
                    except TypeError:
                        self.sensor.enable_feature(report)
                self.last_error = None
                self.get_logger().info(
                    f"BNO085 ready on /dev/i2c-{args.i2c_bus} address 0x{args.i2c_address:02X}; raw topic {args.raw_topic}"
                )
                self.publish_status("connected")
                return True
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.next_retry_at = now + args.retry_s
                self.close_sensor()
                self.publish_status("retry_wait")
                return False

        def tick(self) -> None:
            if self.sensor is None and not self.connect():
                return
            try:
                acceleration = vector_dict(("x", "y", "z"), self.sensor.acceleration)
                gyroscope = vector_dict(("x", "y", "z"), self.sensor.gyro)
                magnetic_ut = vector_dict(("x", "y", "z"), self.sensor.magnetic)
                qi, qj, qk, qr = normalized_quaternion(self.sensor.quaternion)
                stamp = self.get_clock().now().to_msg()

                imu = Imu()
                imu.header.stamp = stamp
                imu.header.frame_id = args.frame_id
                imu.orientation.x = qi
                imu.orientation.y = qj
                imu.orientation.z = qk
                imu.orientation.w = qr
                imu.angular_velocity.x = gyroscope["x"]
                imu.angular_velocity.y = gyroscope["y"]
                imu.angular_velocity.z = gyroscope["z"]
                imu.linear_acceleration.x = acceleration["x"]
                imu.linear_acceleration.y = acceleration["y"]
                imu.linear_acceleration.z = acceleration["z"]
                # All three estimates above were read and validated. ROS Imu
                # uses an all-zero matrix for unknown covariance; leading -1
                # would tell consumers to discard the associated estimate.
                imu.orientation_covariance = [0.0] * 9
                imu.angular_velocity_covariance = [0.0] * 9
                imu.linear_acceleration_covariance = [0.0] * 9

                magnetic = MagneticField()
                magnetic.header = imu.header
                magnetic.magnetic_field.x = magnetic_ut["x"] * 1e-6
                magnetic.magnetic_field.y = magnetic_ut["y"] * 1e-6
                magnetic.magnetic_field.z = magnetic_ut["z"] * 1e-6
                # MagneticField defines an all-zero covariance matrix as
                # unknown (unlike Imu, whose unavailable member uses -1).

                self.raw_publisher.publish(imu)
                self.mag_publisher.publish(magnetic)
                self.sequence += 1
                self.last_sample_at = time.time()
                self.last_error = None
                self.latest = {
                    "stamp": {"sec": int(stamp.sec), "nanosec": int(stamp.nanosec)},
                    "orientation": {"x": qi, "y": qj, "z": qk, "w": qr},
                    "angular_velocity_rad_s": gyroscope,
                    "linear_acceleration_m_s2": acceleration,
                    "magnetic_field_uT": magnetic_ut,
                }
                if self.sequence == 1 or self.sequence % max(1, int(args.sample_hz)) == 0:
                    self.publish_status("streaming")
            except TransientSensorSample as exc:
                # BNO085 commonly returns one all-zero rotation vector just
                # after enabling reports.  Keep the open I2C session so the
                # next report can become valid instead of reconnect-looping.
                self.transient_sample_count += 1
                self.last_error = str(exc)
                self.publish_status("sensor_warmup")
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.next_retry_at = time.monotonic() + args.retry_s
                self.close_sensor()
                self.publish_status("read_error")

        def status(self, state: str) -> dict[str, Any]:
            age = None if self.last_sample_at is None else max(0.0, time.time() - self.last_sample_at)
            alive = age is not None and age < max(1.0, 4.0 / args.sample_hz)
            return {
                "schema": DRIVER_SCHEMA,
                "revision": DRIVER_REVISION,
                "updated_at": time.time(),
                "state": "streaming" if alive else state,
                "alive": alive,
                "sensor": "BNO085",
                "transport": "i2c",
                "i2c_bus": args.i2c_bus,
                "i2c_address": f"0x{args.i2c_address:02X}",
                "frame_id": args.frame_id,
                "raw_topic": args.raw_topic,
                "mag_topic": args.mag_topic,
                "sample_hz": args.sample_hz,
                "sequence": self.sequence,
                "transient_sample_count": self.transient_sample_count,
                "last_sample_age_s": None if age is None else round(age, 4),
                "last_error": self.last_error,
                "units": {"acceleration": "m/s^2", "angular_velocity": "rad/s", "magnetic_field": "uT"},
                "orientation_source": "BNO085 rotation vector",
                "orientation_convention": "sensor_native_unverified_mount",
                "rep145_ready": False,
                "rep145_blocker": "imu_to_base_link mount axes/extrinsic require independent validation",
                "latest": dict(self.latest),
            }

        def publish_status(self, state: str) -> None:
            payload = self.status(state)
            write_json_atomic(self.status_path, payload)
            message = String()
            message.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            self.status_publisher.publish(message)

        def close_sensor(self) -> None:
            self.sensor = None
            if self.i2c is not None:
                try:
                    self.i2c.deinit()
                except Exception:
                    pass
            self.i2c = None

        def destroy_node(self):
            self.publish_status("stopped")
            self.close_sensor()
            return super().destroy_node()

    rclpy.init()
    node = Bno085Node()
    stop = False

    def request_stop(*_args):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while rclpy.ok() and not stop:
            rclpy.spin_once(node, timeout_sec=0.2)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
