#!/usr/bin/env python3
"""Bridge ROS 2 IMU topics to the dashboard WebSocket."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_server.imu_processing import quaternion_to_euler_deg


BRIDGE_SCHEMA = "orchard.imu_websocket.v1"
BRIDGE_REVISION = "ros2_raw_corrected_bridge_v1"


def vector_payload(vector: Any) -> dict[str, float]:
    return {"x": float(vector.x), "y": float(vector.y), "z": float(vector.z)}


def quaternion_payload(quaternion: Any) -> dict[str, float]:
    return {
        "x": float(quaternion.x),
        "y": float(quaternion.y),
        "z": float(quaternion.z),
        "w": float(quaternion.w),
    }


def stamp_payload(header: Any) -> dict[str, int]:
    return {"sec": int(header.stamp.sec), "nanosec": int(header.stamp.nanosec)}


class ImuBridgeState:
    def __init__(self):
        self.lock = threading.Lock()
        self.raw: dict[str, Any] = {}
        self.corrected: dict[str, Any] = {}
        self.magnetic: dict[str, Any] = {}
        self.calibration: dict[str, Any] = {}
        self.raw_count = 0
        self.corrected_count = 0
        self.last_raw_at: float | None = None
        self.last_corrected_at: float | None = None
        self.last_error: str | None = None

    def update_imu(self, msg: Any, *, corrected: bool) -> None:
        orientation = quaternion_payload(msg.orientation)
        row = {
            "frame_id": str(msg.header.frame_id or "imu_link"),
            "stamp": stamp_payload(msg.header),
            "orientation": orientation,
            "euler_deg": quaternion_to_euler_deg(orientation.values()),
            "angular_velocity": vector_payload(msg.angular_velocity),
            "linear_acceleration": vector_payload(msg.linear_acceleration),
        }
        now = time.time()
        with self.lock:
            if corrected:
                self.corrected = row
                self.corrected_count += 1
                self.last_corrected_at = now
            else:
                self.raw = row
                self.raw_count += 1
                self.last_raw_at = now
            self.last_error = None

    def update_magnetic(self, msg: Any) -> None:
        with self.lock:
            self.magnetic = {
                "frame_id": str(msg.header.frame_id or "imu_link"),
                "stamp": stamp_payload(msg.header),
                "magnetic_field_t": vector_payload(msg.magnetic_field),
                "magnetic_field_uT": {
                    key: value * 1e6 for key, value in vector_payload(msg.magnetic_field).items()
                },
            }

    def update_calibration(self, msg: Any) -> None:
        try:
            payload = json.loads(str(msg.data or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if isinstance(payload, dict):
            with self.lock:
                self.calibration = payload

    def set_error(self, error: str) -> None:
        with self.lock:
            self.last_error = error

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            raw_age = None if self.last_raw_at is None else max(0.0, now - self.last_raw_at)
            corrected_age = None if self.last_corrected_at is None else max(0.0, now - self.last_corrected_at)
            return {
                "schema": BRIDGE_SCHEMA,
                "revision": BRIDGE_REVISION,
                "updated_at": now,
                "imu_topic_alive": raw_age is not None and raw_age < 1.0,
                "corrected_topic_alive": corrected_age is not None and corrected_age < 1.0,
                "last_imu_age_s": None if raw_age is None else round(raw_age, 4),
                "last_corrected_age_s": None if corrected_age is None else round(corrected_age, 4),
                "raw_count": self.raw_count,
                "corrected_count": self.corrected_count,
                "raw": dict(self.raw),
                "corrected": dict(self.corrected),
                "magnetic": dict(self.magnetic),
                "calibration": dict(self.calibration),
                "error": self.last_error,
            }


def write_status(path: Path, state: ImuBridgeState) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(json.dumps(state.snapshot(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def ros_thread(state: ImuBridgeState, args: argparse.Namespace, ready: threading.Event, stop: threading.Event) -> None:
    try:
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
        from sensor_msgs.msg import Imu, MagneticField
        from std_msgs.msg import String
    except ImportError as exc:
        state.set_error(f"ROS 2 Python packages are required: {exc}")
        ready.set()
        return

    class BridgeNode(Node):
        def __init__(self):
            super().__init__("ubts_imu_ws_bridge")
            preview_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                                     durability=DurabilityPolicy.VOLATILE)
            self.create_subscription(Imu, args.raw_topic, lambda msg: state.update_imu(msg, corrected=False), preview_qos)
            self.create_subscription(Imu, args.corrected_topic, lambda msg: state.update_imu(msg, corrected=True), preview_qos)
            self.create_subscription(MagneticField, args.mag_topic, state.update_magnetic, preview_qos)
            self.create_subscription(String, args.calibration_topic, state.update_calibration, 10)

    rclpy.init()
    node = BridgeNode()
    ready.set()
    try:
        while rclpy.ok() and not stop.is_set():
            rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def status_thread(state: ImuBridgeState, path: Path, interval_s: float, stop: threading.Event) -> None:
    while not stop.wait(max(0.2, interval_s)):
        write_status(path, state)
    write_status(path, state)


async def serve(state: ImuBridgeState, args: argparse.Namespace, stop: threading.Event) -> None:
    from scripts.stream_policy import wait_for_ack, pace_stream
    try:
        import websockets
    except ImportError as exc:
        raise SystemExit("Install websockets in the ROS2 runtime image") from exc

    async def handler(websocket, legacy_path=None):
        request = getattr(websocket, "request", None)
        actual_path = getattr(request, "path", args.path) if request is not None else legacy_path or getattr(websocket, "path", args.path)
        if urlparse(actual_path).path != args.path:
            await websocket.close(code=1008, reason="unsupported path")
            return
        profile = (parse_qs(urlparse(actual_path).query).get("profile") or ["auto"])[0]
        hz = {"tiny": 1, "auto": 2, "slow": 2, "balanced": 5, "detail": 10}.get(profile, 2)
        interval = 1.0 / max(1.0, min(hz, float(args.max_hz)))
        ack = (parse_qs(urlparse(actual_path).query).get("flow") or [""])[0] == "ack"
        sequence = 0
        await websocket.send(json.dumps({"type": "socket", "ok": True, "schema": BRIDGE_SCHEMA}, separators=(",", ":")))
        try:
            while True:
                started = time.monotonic()
                sequence += 1
                await asyncio.wait_for(websocket.send(json.dumps({"type": "imu", **state.snapshot(), "sequence": sequence}, separators=(",", ":"))), timeout=2)
                if ack:
                    await wait_for_ack(websocket, lambda reply:
                                       reply.get("type") == "imu_ack" and reply.get("sequence") == sequence)
                # Send/ACK time already consumes this interval. Adding another
                # full interval made a 200 ms link needlessly 200 ms slower.
                await pace_stream(interval, started)
        except Exception as exc:
            connection_closed = getattr(getattr(websockets, "exceptions", None), "ConnectionClosed", None)
            if connection_closed is None or not isinstance(exc, connection_closed):
                print(f"IMU preview closed: {type(exc).__name__}", flush=True)
        finally:
            await websocket.close()

    async with websockets.serve(handler, args.host, args.port, max_size=4096, max_queue=2, write_limit=8192, compression=None, close_timeout=1):
        while not stop.is_set():
            await asyncio.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge ROS 2 IMU topics to browser WebSocket JSON")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8775)
    parser.add_argument("--path", default="/ws/imu")
    parser.add_argument("--raw-topic", default=os.environ.get("IMU_RAW_TOPIC", "/imu/data_raw"))
    parser.add_argument("--corrected-topic", default=os.environ.get("IMU_CORRECTED_TOPIC", "/imu/data"))
    parser.add_argument("--mag-topic", default=os.environ.get("IMU_MAG_TOPIC", "/imu/mag"))
    parser.add_argument("--calibration-topic", default=os.environ.get("IMU_CALIBRATION_TOPIC", "/orchard/imu/calibration"))
    parser.add_argument("--max-hz", type=float, default=float(os.environ.get("IMU_BRIDGE_MAX_HZ", "20")))
    parser.add_argument("--status-file", default=os.environ.get("IMU_BRIDGE_STATUS_FILE", "/app/runtime/imu_bridge_status.json"))
    parser.add_argument("--status-interval-s", type=float, default=float(os.environ.get("IMU_BRIDGE_STATUS_INTERVAL_S", "0.5")))
    args = parser.parse_args()

    state = ImuBridgeState()
    ready = threading.Event()
    stop = threading.Event()
    workers = [
        threading.Thread(target=ros_thread, args=(state, args, ready, stop), daemon=True),
        threading.Thread(target=status_thread, args=(state, Path(args.status_file), args.status_interval_s, stop), daemon=True),
    ]
    for worker in workers:
        worker.start()
    ready.wait(timeout=8.0)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(serve(state, args, stop))
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
