#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScanState:
    frame_id: str = "laser"
    stamp: float | None = None
    points: list[dict[str, float | int]] = field(default_factory=list)
    last_scan_at: float | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, frame_id: str, stamp: float, points: list[dict[str, float | int]]) -> None:
        with self.lock:
            self.frame_id = frame_id or "laser"
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
            state.update(msg.header.frame_id or "laser", stamp, scan_to_points(msg, max_points))

    rclpy.init()
    node = BridgeNode()
    ready.set()
    try:
        while rclpy.ok() and not stop.is_set():
            rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        node.destroy_node()
        rclpy.shutdown()


async def serve_websocket(state: ScanState, host: str, port: int, path: str, max_hz: float):
    try:
        import websockets
    except ImportError as exc:
        raise SystemExit("Install the optional 'websockets' package on Jetson to run lidar_ws_bridge.py") from exc

    interval = 1.0 / max(0.1, min(10.0, max_hz))

    async def handler(websocket, legacy_path=None):
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
                            "stamp": snap["stamp"],
                            "point_count": snap["point_count"],
                            "points": snap["points"],
                        },
                        separators=(",", ":"),
                    )
                )
            await asyncio.sleep(interval)

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
    args = parser.parse_args()

    state = ScanState()
    ready = threading.Event()
    stop = threading.Event()
    thread = threading.Thread(target=ros_thread, args=(state, args.topic, args.max_points, ready, stop), daemon=True)
    thread.start()
    ready.wait(timeout=8)
    if state.snapshot()["error"]:
        print(state.snapshot()["error"])
        return 2

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
        thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
