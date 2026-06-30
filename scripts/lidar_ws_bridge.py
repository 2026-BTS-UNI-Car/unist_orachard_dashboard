#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class ScanState:
    frame_id: str = "laser"
    source: str = "waiting"
    stamp: float | None = None
    points: list[dict[str, float | int]] = field(default_factory=list)
    last_scan_at: float | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, frame_id: str, stamp: float, points: list[dict[str, float | int]], source: str) -> None:
        with self.lock:
            self.frame_id = frame_id or "laser"
            self.source = source
            self.stamp = stamp
            self.points = points
            self.last_scan_at = time.time()
            self.error = None

    def set_error(self, message: str) -> None:
        with self.lock:
            self.error = message

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            age = None if self.last_scan_at is None else round(time.time() - self.last_scan_at, 3)
            return {
                "frame_id": self.frame_id,
                "source": self.source,
                "stamp": self.stamp,
                "points": list(self.points),
                "last_scan_age_sec": age,
                "point_count": len(self.points),
                "error": self.error,
            }


def scan_to_points(msg: Any, max_points: int) -> list[dict[str, float | int]]:
    ranges = list(getattr(msg, "ranges", []) or [])
    intensities = list(getattr(msg, "intensities", []) or [])
    angle_min = float(getattr(msg, "angle_min", 0.0))
    angle_increment = float(getattr(msg, "angle_increment", 0.0))
    range_min = float(getattr(msg, "range_min", 0.0) or 0.0)
    range_max = float(getattr(msg, "range_max", 30.0) or 30.0)
    valid = []
    for index, distance in enumerate(ranges):
        r = float(distance)
        if not math.isfinite(r) or r <= 0:
            continue
        if range_min and r < range_min:
            continue
        if range_max and r > range_max:
            continue
        theta = angle_min + index * angle_increment
        angle_deg = math.degrees(theta) % 360.0
        quality = int(intensities[index]) if index < len(intensities) and math.isfinite(float(intensities[index])) else 0
        valid.append(
            {
                "angle_deg": round(angle_deg, 3),
                "distance_m": round(r, 3),
                "quality": quality,
                "x_m": round(math.sin(theta) * r, 3),
                "y_m": round(math.cos(theta) * r, 3),
            }
        )
    if len(valid) <= max_points:
        return valid
    step = len(valid) / max_points
    return [valid[min(len(valid) - 1, int(i * step))] for i in range(max_points)]


def ros_thread(state: ScanState, topic: str, max_points: int, ready: threading.Event, stop: threading.Event) -> None:
    try:
        import rclpy
        from rclpy.node import Node
        from sensor_msgs.msg import LaserScan
    except ImportError as exc:
        state.set_error(f"ROS2 Python packages are required on Jetson: {exc}")
        ready.set()
        return

    class BridgeNode(Node):
        def __init__(self):
            super().__init__("ubts_lidar_ws_bridge")
            self.create_subscription(LaserScan, topic, self.on_scan, 10)

        def on_scan(self, msg):
            stamp = float(msg.header.stamp.sec) + float(msg.header.stamp.nanosec) / 1_000_000_000
            state.update(msg.header.frame_id or "laser", stamp, scan_to_points(msg, max_points), "ros2")

    rclpy.init()
    node = BridgeNode()
    ready.set()
    try:
        while rclpy.ok() and not stop.is_set():
            rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def serial_thread(
    state: ScanState,
    max_points: int,
    poll_s: float,
    ros_fallback_s: float,
    stop: threading.Event,
) -> None:
    try:
        from dashboard_server.telemetry import TelemetryProvider
    except ImportError as exc:
        state.set_error(f"Dashboard telemetry module is required for serial fallback: {exc}")
        return

    os.environ["LIDAR_SCAN_MAX_POINTS"] = str(max_points)
    provider = TelemetryProvider()
    while not stop.is_set():
        snap = state.snapshot()
        if (
            snap["source"] == "ros2"
            and snap["last_scan_age_sec"] is not None
            and snap["last_scan_age_sec"] < ros_fallback_s
        ):
            stop.wait(poll_s)
            continue
        try:
            result = provider.lidar_scan()
        except Exception as exc:  # noqa: BLE001 - keep bridge alive and report in WebSocket status.
            state.set_error(f"serial fallback error: {exc}")
            stop.wait(max(0.5, poll_s))
            continue
        points = result.get("points") or []
        if result.get("ok") and points:
            state.update(result.get("frame_id") or "laser", float(result.get("tested_at") or time.time()), points, "serial")
        else:
            message = result.get("message") or result.get("state") or "waiting for ROS2 /scan or serial LiDAR points"
            state.set_error(message)
        stop.wait(poll_s)


async def serve_websocket(state: ScanState, host: str, port: int, path: str, max_hz: float):
    try:
        import websockets
    except ImportError as exc:
        raise SystemExit("Install the optional 'websockets' package on Jetson to run lidar_ws_bridge.py") from exc

    interval = 1.0 / max(0.1, min(10.0, max_hz))

    async def handler(websocket, legacy_path=None):
        try:
            request_path = getattr(websocket, "request", None)
            if request_path is not None:
                actual_path = getattr(request_path, "path", path)
            else:
                actual_path = legacy_path or getattr(websocket, "path", path)
            if actual_path != path:
                await websocket.close(code=1008, reason="unsupported path")
                return
            await websocket.send(json.dumps({"type": "socket", "ok": True, "message": "lidar websocket connected"}))
            while True:
                snap = state.snapshot()
                await websocket.send(
                    json.dumps(
                        {
                            "type": "status",
                            "device_found": True,
                            "scan_topic_alive": snap["last_scan_age_sec"] is not None and snap["last_scan_age_sec"] < 2.0,
                            "last_scan_age_sec": snap["last_scan_age_sec"],
                            "point_count": snap["point_count"],
                            "source": snap["source"],
                            "frame_id": snap["frame_id"],
                            "error": snap["error"],
                        },
                        separators=(",", ":"),
                    )
                )
                if snap["points"]:
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "scan",
                                "frame_id": snap["frame_id"],
                                "source": snap["source"],
                                "stamp": snap["stamp"],
                                "point_count": snap["point_count"],
                                "points": snap["points"],
                            },
                            separators=(",", ":"),
                        )
                    )
                await asyncio.sleep(interval)
        except Exception:
            return

    async with websockets.serve(handler, host, port):
        await asyncio.Future()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge ROS2 /scan LaserScan to browser WebSocket point data.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--path", default="/ws/lidar")
    parser.add_argument("--topic", default="/scan")
    parser.add_argument("--max-points", type=int, default=900)
    parser.add_argument("--max-hz", type=float, default=10.0)
    parser.add_argument("--source", choices=["auto", "ros2", "serial"], default=os.environ.get("LIDAR_BRIDGE_SOURCE", "auto"))
    parser.add_argument("--serial-poll-s", type=float, default=float(os.environ.get("LIDAR_SERIAL_POLL_S", "0.2")))
    parser.add_argument("--ros-fallback-s", type=float, default=float(os.environ.get("LIDAR_ROS_FALLBACK_S", "2.5")))
    args = parser.parse_args()

    state = ScanState()
    ready = threading.Event()
    stop = threading.Event()
    threads: list[threading.Thread] = []
    if args.source in {"auto", "ros2"}:
        thread = threading.Thread(target=ros_thread, args=(state, args.topic, args.max_points, ready, stop), daemon=True)
        thread.start()
        threads.append(thread)
        ready.wait(timeout=8)
    if args.source in {"auto", "serial"}:
        thread = threading.Thread(
            target=serial_thread,
            args=(state, args.max_points, args.serial_poll_s, args.ros_fallback_s, stop),
            daemon=True,
        )
        thread.start()
        threads.append(thread)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(serve_websocket(state, args.host, args.port, args.path, args.max_hz))
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
