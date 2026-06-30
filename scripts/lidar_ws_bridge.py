#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import sys
import termios
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


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


def bridge_status_payload(state: ScanState) -> dict[str, Any]:
    snap = state.snapshot()
    alive = snap["last_scan_age_sec"] is not None and snap["last_scan_age_sec"] < 2.0
    return {
        "type": "lidar_bridge_status",
        "updated_at": time.time(),
        "scan_topic_alive": alive,
        "last_scan_age_sec": snap["last_scan_age_sec"],
        "point_count": snap["point_count"],
        "source": snap["source"],
        "frame_id": snap["frame_id"],
        "error": snap["error"] or (None if alive else "ROS2 /scan 대기 중"),
    }


def write_bridge_status(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def status_writer_thread(state: ScanState, path: Path, interval_s: float, stop: threading.Event) -> None:
    while not stop.is_set():
        write_bridge_status(path, bridge_status_payload(state))
        stop.wait(max(0.2, interval_s))
    write_bridge_status(path, bridge_status_payload(state))


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
    return decimate_points(valid, max_points)


def decimate_points(points: list[dict[str, float | int]], max_points: int) -> list[dict[str, float | int]]:
    max_points = max(100, int(max_points or 900))
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[min(len(points) - 1, int(i * step))] for i in range(max_points)]


def client_max_points(actual_path: str, default: int, source_max: int) -> int:
    parsed = urlparse(actual_path)
    raw = (parse_qs(parsed.query).get("max_points") or [default])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(100, min(max(source_max, default), value))


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
    if os.environ.get("UBTS_ALLOW_DIRECT_LIDAR", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        state.set_error("Serial direct mode is disabled. Start the ROS2 LiDAR driver and publish /scan.")
        while not stop.is_set():
            stop.wait(max(0.5, poll_s))
        return

    try:
        from dashboard_server.telemetry import RPLIDAR_SCAN, RPLIDAR_STOP, TelemetryProvider
    except ImportError as exc:
        state.set_error(f"Dashboard telemetry module is required for explicit direct serial mode: {exc}")
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
        status = provider.lidar_status()
        if not status.get("present"):
            state.set_error("LiDAR 장치를 찾지 못했습니다.")
            stop.wait(max(0.5, poll_s))
            continue
        if status.get("holders"):
            state.set_error("다른 프로세스가 LiDAR 포트를 사용 중입니다.")
            stop.wait(max(0.5, poll_s))
            continue

        opened = False
        for baud in provider._lidar_bauds():
            if stop.is_set():
                break
            speed = getattr(termios, f"B{baud}", None)
            if speed is None:
                continue
            try:
                opened = _serial_stream_at_baud(
                    provider,
                    state,
                    status["open_path"],
                    baud,
                    speed,
                    max_points,
                    poll_s,
                    RPLIDAR_STOP,
                    RPLIDAR_SCAN,
                    stop,
                )
            except OSError as exc:
                state.set_error(f"serial stream error at {baud} bps: {exc}")
                opened = False
            if opened:
                break
        if not opened:
            stop.wait(max(0.5, poll_s))


def _serial_stream_at_baud(
    provider: Any,
    state: ScanState,
    path: str,
    baud: int,
    speed: int,
    max_points: int,
    update_interval_s: float,
    stop_command: bytes,
    scan_command: bytes,
    stop: threading.Event,
) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    raw = bytearray()
    stream = bytearray()
    nodes: list[dict[str, Any]] = []
    descriptor_seen = False
    last_update = 0.0
    first_point_deadline = time.time() + 5.0
    motor_line = os.environ.get("LIDAR_MOTOR_LINE", "dtr").strip().lower() or "dtr"
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0
        attrs[4] = speed
        attrs[5] = speed
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
        provider._set_lidar_modem_lines(fd, motor_line)
        os.write(fd, stop_command)
        time.sleep(0.08)
        termios.tcflush(fd, termios.TCIFLUSH)
        os.write(fd, scan_command)

        while not stop.is_set():
            try:
                chunk = os.read(fd, 8192)
            except BlockingIOError:
                chunk = b""
            if chunk:
                if descriptor_seen:
                    stream.extend(chunk)
                else:
                    raw.extend(chunk)
                    marker = raw.find(b"\xA5\x5A")
                    if marker >= 0 and len(raw) >= marker + 7:
                        descriptor_seen = True
                        stream.extend(raw[marker + 7 :])
                        raw.clear()
                    elif len(raw) > 8192:
                        del raw[:-8192]

            if descriptor_seen and len(stream) >= 10:
                parse_len = len(stream) - 4
                payload = bytes(stream[:parse_len])
                del stream[:parse_len]
                parsed = provider._parse_normal_scan_nodes(payload)
                if parsed:
                    nodes.extend(parsed)
                    max_keep = max(max_points * 4, max_points)
                    if len(nodes) > max_keep:
                        del nodes[: len(nodes) - max_keep]

            now = time.time()
            if nodes and now - last_update >= update_interval_s:
                state.update("laser", now, provider._decimate_points(nodes, max_points), "serial")
                last_update = now
            elif not nodes and now > first_point_deadline:
                state.set_error(f"LiDAR stream did not produce points at {baud} bps.")
                return False

            time.sleep(0.004)
    finally:
        try:
            os.write(fd, stop_command)
            time.sleep(0.03)
        except OSError:
            pass
        provider._set_lidar_modem_lines(fd, "off")
        os.close(fd)
    return True


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
            parsed_path = urlparse(actual_path)
            if parsed_path.path != path:
                await websocket.close(code=1008, reason="unsupported path")
                return
            requested_max_points = client_max_points(actual_path, default=900, source_max=max_points)
            await websocket.send(json.dumps({"type": "socket", "ok": True, "message": "lidar websocket connected"}))
            while True:
                snap = state.snapshot()
                points = decimate_points(snap["points"], requested_max_points)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "status",
                            "device_found": True,
                            "scan_topic_alive": snap["last_scan_age_sec"] is not None and snap["last_scan_age_sec"] < 2.0,
                            "last_scan_age_sec": snap["last_scan_age_sec"],
                            "point_count": len(points),
                            "source_point_count": snap["point_count"],
                            "requested_max_points": requested_max_points,
                            "source": snap["source"],
                            "frame_id": snap["frame_id"],
                            "error": snap["error"] or (
                                None
                                if snap["last_scan_age_sec"] is not None and snap["last_scan_age_sec"] < 2.0
                                else "ROS2 /scan is not being received yet. Dashboard is subscribe-only and does not open the LiDAR serial port."
                            ),
                        },
                        separators=(",", ":"),
                    )
                )
                if points:
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "scan",
                                "frame_id": snap["frame_id"],
                                "source": snap["source"],
                                "stamp": snap["stamp"],
                                "point_count": len(points),
                                "source_point_count": snap["point_count"],
                                "requested_max_points": requested_max_points,
                                "points": points,
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
    parser.add_argument("--max-points", type=int, default=3600)
    parser.add_argument("--max-hz", type=float, default=10.0)
    parser.add_argument("--source", choices=["auto", "ros2", "serial"], default=os.environ.get("LIDAR_BRIDGE_SOURCE", "ros2"))
    parser.add_argument("--serial-poll-s", type=float, default=float(os.environ.get("LIDAR_SERIAL_POLL_S", "0.2")))
    parser.add_argument("--ros-fallback-s", type=float, default=float(os.environ.get("LIDAR_ROS_FALLBACK_S", "2.5")))
    parser.add_argument("--status-file", default=os.environ.get("LIDAR_BRIDGE_STATUS_FILE", "/app/runtime/lidar_bridge_status.json"))
    parser.add_argument("--status-interval-s", type=float, default=float(os.environ.get("LIDAR_BRIDGE_STATUS_INTERVAL_S", "0.5")))
    args = parser.parse_args()
    if args.source == "auto":
        args.source = "ros2"

    state = ScanState()
    state.source = "ros2" if args.source == "ros2" else args.source
    ready = threading.Event()
    stop = threading.Event()
    threads: list[threading.Thread] = []
    status_thread = threading.Thread(
        target=status_writer_thread,
        args=(state, Path(args.status_file), args.status_interval_s, stop),
        daemon=True,
    )
    status_thread.start()
    threads.append(status_thread)
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
