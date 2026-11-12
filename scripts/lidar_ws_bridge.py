from __future__ import annotations
import argparse
import asyncio
import json
import math
import os
import signal
import struct
import sys
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
    raw_scan: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Called (from the ROS thread) after every new scan; the WebSocket server
    # uses it to send a new scan as soon as it arrives instead of polling.
    notify: Any = None

    def update(
        self,
        frame_id: str,
        stamp: float,
        points: list[dict[str, float | int]],
        source: str,
        raw_scan=None,
    ) -> None:
        with self.lock:
            self.frame_id = frame_id or "laser"
            self.source = source
            self.stamp = stamp
            self.points = points
            self.raw_scan = raw_scan or {}
            self.last_scan_at = time.time()
            self.error = None
        notify = self.notify
        if notify is not None:
            notify()

    def set_error(self, message: str) -> None:
        with self.lock:
            self.error = message

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            age = (
                None
                if self.last_scan_at is None
                else round(time.time() - self.last_scan_at, 3)
            )
            return {
                "frame_id": self.frame_id,
                "source": self.source,
                "stamp": self.stamp,
                "points": list(self.points),
                "raw_scan": self.raw_scan,
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
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass


def bridge_log(event: str, **fields: Any) -> None:
    payload = {"ts": round(time.time(), 3), "event": f"lidar_bridge.{event}", **fields}
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def status_writer_thread(
    state: ScanState, path: Path, interval_s: float, stop: threading.Event
) -> None:
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
        quality = (
            int(intensities[index])
            if index < len(intensities) and math.isfinite(float(intensities[index]))
            else 0
        )
        valid.append(
            {
                "i": index,
                "angle_deg": round(angle_deg, 3),
                "distance_m": round(r, 3),
                "quality": quality,
                "x_m": round(math.sin(theta) * r, 3),
                "y_m": round(math.cos(theta) * r, 3),
            }
        )
    return decimate_points(valid, max_points)


def decimate_points(
    points: list[dict[str, float | int]], max_points: int
) -> list[dict[str, float | int]]:
    max_points = max(50, int(max_points or 900))
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
    return max(50, min(max(source_max, default), value))


def ros_thread(
    state: ScanState,
    topic: str,
    max_points: int,
    ready: threading.Event,
    stop: threading.Event,
) -> None:
    try:
        import rclpy
        from rclpy.node import Node
        from rclpy.parameter import Parameter
        from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
        from sensor_msgs.msg import LaserScan
    except ImportError as exc:
        state.set_error(f"ROS2 Python packages are required on Jetson: {exc}")
        bridge_log("ros_import_error", error=str(exc))
        ready.set()
        return

    class BridgeNode(Node):
        def __init__(self):
            super().__init__("ubts_lidar_ws_bridge", parameter_overrides=[Parameter("use_sim_time", value=os.environ.get("DASHBOARD_MODE") == "simulation")])
            preview_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                                     durability=DurabilityPolicy.VOLATILE)
            self.create_subscription(LaserScan, topic, self.on_scan, preview_qos)
            self.scan_count = 0
            self.last_log_at = 0.0
            bridge_log("ros_subscribed", topic=topic, max_points=max_points)

        def on_scan(self, msg):
            stamp = (
                float(msg.header.stamp.sec)
                + float(msg.header.stamp.nanosec) / 1000000000
            )
            points = scan_to_points(msg, max_points)
            raw_scan = dict(header=dict(stamp=dict(sec=msg.header.stamp.sec,nanosec=msg.header.stamp.nanosec),frame_id=msg.header.frame_id),
                ranges=[float(v) if math.isfinite(v) else None for v in msg.ranges],
                angle_min=float(msg.angle_min),angle_increment=float(msg.angle_increment),
                range_min=float(msg.range_min),range_max=float(msg.range_max),
                scan_time=float(msg.scan_time),time_increment=float(msg.time_increment))
            state.update(msg.header.frame_id or "laser", stamp, points, "ros2", raw_scan)
            self.scan_count += 1
            now = time.time()
            if self.scan_count == 1 or now - self.last_log_at >= 10:
                self.last_log_at = now
                bridge_log(
                    "scan_received",
                    topic=topic,
                    frame_id=msg.header.frame_id or "laser",
                    raw_ranges=len(getattr(msg, "ranges", []) or []),
                    points=len(points),
                    count=self.scan_count,
                )

    rclpy.init()
    node = BridgeNode()
    ready.set()
    try:
        from scripts.ros_executor import persistent_executor
        with persistent_executor(node) as executor:
            while rclpy.ok() and (not stop.is_set()):
                executor.spin_once(timeout_sec=0.1)
    finally:
        node.destroy_node()
        rclpy.shutdown()






def compact_scan_payload(snap, points, sequence, compact=True, overlay=True):
    payload = {
        "type": "scan",
        "frame_id": snap["frame_id"],
        "source": snap["source"],
        "stamp": snap["stamp"],
        "sequence": sequence,
        "point_count": len(points),
        "source_point_count": snap["point_count"],
        "requested_max_points": len(points),
        "coverage_deg": len(
            {int(p["angle_deg"]) % 360 for p in snap.get("points", points)}
        ),
    }
    if compact:
        payload["point_encoding"] = "polar-v1"
        payload["points"] = [
            [p["angle_deg"], p["distance_m"], p.get("quality", 0)] for p in points
        ]
    else:
        payload["points"] = points
    return payload


# ---- polar-v2 (NET-07): one binary message per scan for the dashboard preview.
#   bytes 0-3   magic b"ULS2"
#   byte  4     version 2
#   bytes 5-8   metadata length L (uint32, big-endian, like the camera's binary-v1)
#   bytes 9..   metadata JSON (UTF-8): the polar-v1 fields without "points", plus
#               angle_encoding, angle_min_deg, angle_increment_deg
#   then        N uint16 angles, N uint16 distances in mm, N uint8 qualities (little-endian)
# The points are exactly those of polar-v1 for the same scan and point limit.
# Angles are the beam index i (angle = angle_min + i * angle_increment, the
# same formula the bridge used for polar-v1) or, without beam geometry,
# centi-degrees. Only valid beams are sent, so a distance is never 0.
LIDAR_BINARY_MAGIC = b"ULS2"
LIDAR_BINARY_VERSION = 2


def encode_scan_v2(snap, points, sequence) -> bytes:
    raw = snap.get("raw_scan") or {}
    angle_min = raw.get("angle_min")
    increment = raw.get("angle_increment")
    by_index = (
        isinstance(angle_min, (int, float)) and isinstance(increment, (int, float))
        and math.isfinite(angle_min) and math.isfinite(increment) and increment != 0
        and all(isinstance(p.get("i"), int) and 0 <= p["i"] <= 0xFFFF for p in points)
    )
    meta = compact_scan_payload(snap, points, sequence, compact=True)
    meta.pop("points", None)
    meta["point_encoding"] = "polar-v2"
    if by_index:
        meta.update(angle_encoding="beam_index", angle_min_deg=math.degrees(angle_min),
                    angle_increment_deg=math.degrees(increment))
        angles = [p["i"] for p in points]
    else:
        meta["angle_encoding"] = "centi_degree"
        angles = [int(round(float(p["angle_deg"]) * 100)) % 36000 for p in points]
    distances = [max(1, min(0xFFFF, int(round(float(p["distance_m"]) * 1000)))) for p in points]
    qualities = [max(0, min(255, int(p.get("quality") or 0))) for p in points]
    count = len(points)
    metadata = json.dumps(meta, allow_nan=False, separators=(",", ":")).encode("utf-8")
    return (LIDAR_BINARY_MAGIC + bytes([LIDAR_BINARY_VERSION]) + struct.pack("!I", len(metadata)) + metadata
            + struct.pack(f"<{count}H", *angles) + struct.pack(f"<{count}H", *distances)
            + struct.pack(f"<{count}B", *qualities))


def decode_scan_v2(message: bytes) -> dict:
    """The polar-v1 payload of a polar-v2 message (used by tests and tools)."""
    if message[:4] != LIDAR_BINARY_MAGIC or message[4] != LIDAR_BINARY_VERSION:
        raise ValueError("not a polar-v2 scan")
    length = struct.unpack("!I", message[5:9])[0]
    meta = json.loads(message[9:9 + length].decode("utf-8"))
    body = message[9 + length:]
    count = int(meta["point_count"])
    if len(body) != 5 * count:
        raise ValueError("polar-v2 scan has the wrong length")
    angles = struct.unpack(f"<{count}H", body[:2 * count])
    distances = struct.unpack(f"<{count}H", body[2 * count:4 * count])
    qualities = struct.unpack(f"<{count}B", body[4 * count:])
    if meta["angle_encoding"] == "beam_index":
        degrees = [(meta["angle_min_deg"] + i * meta["angle_increment_deg"]) % 360.0 for i in angles]
    else:
        degrees = [a / 100.0 for a in angles]
    meta["points"] = [[round(a, 3), d / 1000.0, q] for a, d, q in zip(degrees, distances, qualities)]
    meta["point_encoding"] = "polar-v1"
    return meta


async def serve_websocket(
    state: ScanState,
    host: str,
    port: int,
    path: str,
    max_hz: float,
    source_max_points: int,
):
    import websockets
    from scripts.stream_policy import StreamBudget, wait_for_ack

    loop = asyncio.get_running_loop()
    waiters: set = set()

    def notify_scan():
        for event in list(waiters):
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass  # the server is shutting down
    state.notify = notify_scan

    async def handler(websocket, legacy_path=None):
        actual_path = (
            getattr(getattr(websocket, "request", None), "path", None)
            or legacy_path
            or getattr(websocket, "path", path)
        )
        if urlparse(actual_path).path != path:
            await websocket.close(code=1008, reason="unsupported path")
            return
        params = parse_qs(urlparse(actual_path).query)
        mapping = (params.get("profile") or [""])[0] == "mapping"
        point_format = (params.get("format") or [""])[0]
        binary = point_format == "polar-v2" and not mapping
        compact = point_format in ("polar-v1", "polar-v2")
        ack_flow = (params.get("flow") or [""])[0] == "ack"
        # polar-v2 clients may let two scans be on the way (NET-07): the next scan
        # leaves while the answer for the previous one is still travelling, so the
        # rate is not one scan per round trip. Still the newest scan only.
        try:
            ack_window = int((params.get("ack_window") or ["1"])[0])
        except ValueError:
            ack_window = 1
        ack_window = 2 if (binary and ack_flow and ack_window == 2) else 1
        scan_ready = asyncio.Event()
        waiters.add(scan_ready)
        acked = {"sequence": 0, "error": None}
        sent_at = {}
        ack_event = asyncio.Event()
        reader = None
        budget = StreamBudget((params.get("profile") or ["auto"])[0])
        requested = client_max_points(
            actual_path, default=900, source_max=source_max_points
        )
        last_stamp = None
        sequence = 0
        last_status = 0.0

        async def read_acks():
            # Background reader for the windowed flow; the newest acknowledged
            # sequence frees the window. Anything else is ignored.
            try:
                async for raw in websocket:
                    try:
                        reply = json.loads(raw) if isinstance(raw, str) else None
                    except ValueError:
                        reply = None
                    if not isinstance(reply, dict) or reply.get("type") != "scan_ack":
                        continue
                    number = reply.get("sequence")
                    if type(number) is not int or number <= acked["sequence"] or number > sequence:
                        continue
                    acked["sequence"] = number
                    started_at = sent_at.pop(number, None)
                    for old in [key for key in sent_at if key < number]:
                        sent_at.pop(old, None)
                    if started_at is not None:
                        budget.acknowledge(time.monotonic() - started_at)
                    ack_event.set()
                acked["error"] = ConnectionError("closed")
            except Exception as exc:  # the connection closed
                acked["error"] = exc
            finally:
                ack_event.set()

        try:
            await websocket.send(
                json.dumps(
                    {
                        "type": "socket",
                        "ok": True,
                        "latest_only": True,
                        "max_inflight_frames": ack_window,
                        "point_encoding": "polar-v2" if binary else ("polar-v1" if compact else "points"),
                    }
                )
            )
            while True:
                started = time.monotonic()
                snap = state.snapshot()
                alive = (
                    snap["last_scan_age_sec"] is not None
                    and snap["last_scan_age_sec"] < 2
                )
                limit = (
                    60
                    if budget.values[0] <= 160
                    else 180
                    if budget.values[0] <= 320
                    else 450
                    if budget.values[0] <= 640
                    else source_max_points
                )
                points = decimate_points(snap["points"], min(requested, limit))
                if started - last_status >= 1:
                    await asyncio.wait_for(
                        websocket.send(
                            json.dumps(
                                {
                                    "type": "status",
                                    "device_found": alive,
                                    "scan_topic_alive": alive,
                                    "last_scan_age_sec": snap["last_scan_age_sec"],
                                    "point_count": len(points),
                                    "source_point_count": snap["point_count"],
                                    "requested_max_points": min(requested, limit),
                                    "source": snap["source"],
                                    "error": snap["error"],
                                    "latest_only": True,
                                    "profile": budget.profile,
                                    "effective_profile": budget.effective_profile,
                                    "ack_latency_ms": budget.ack_latency_ms,
                                },
                                separators=(",", ":"),
                            )
                        ),
                        timeout=2,
                    )
                    last_status = started
                if not alive or not points or snap["stamp"] == last_stamp:
                    # Wait for the next scan (woken by ScanState.update), not a fixed 40 ms poll.
                    scan_ready.clear()
                    if state.snapshot()["stamp"] == snap["stamp"]:
                        try:
                            await asyncio.wait_for(scan_ready.wait(), 0.25)
                        except asyncio.TimeoutError:
                            pass
                    continue
                if ack_window > 1:
                    if reader is None:
                        reader = asyncio.create_task(read_acks())
                    # The window is full: wait for an answer. No time limit here: a
                    # slow link only slows the scans; a dead connection ends through
                    # the WebSocket keepalive (ping) and closes the reader.
                    while sequence - acked["sequence"] >= ack_window and acked["error"] is None:
                        ack_event.clear()
                        if sequence - acked["sequence"] < ack_window:
                            break
                        await ack_event.wait()
                    if acked["error"] is not None:
                        break
                    snap = state.snapshot()
                    points = decimate_points(snap["points"], min(requested, limit))
                    if not points or snap["stamp"] == last_stamp:
                        continue
                sequence += 1
                if binary:
                    message = encode_scan_v2(snap, points, sequence)
                else:
                    payload = compact_scan_payload(
                        snap, points, sequence, compact, budget.values[0] > 320
                    )
                    if mapping:
                        payload["raw_scan"] = snap["raw_scan"]
                        payload["points"] = []
                        payload["source"] = "source_native_LaserScan"
                    message = json.dumps(payload, allow_nan=False, separators=(",", ":"))
                if ack_window > 1:
                    sent_at[sequence] = time.monotonic()
                await asyncio.wait_for(websocket.send(message), timeout=2)
                last_stamp = snap["stamp"]
                if ack_flow and ack_window == 1:
                    await wait_for_ack(websocket, lambda reply:
                                       reply.get("type") == "scan_ack" and reply.get("sequence") == sequence)
                    budget.acknowledge(time.monotonic() - started)
                hz = min(
                    max_hz,
                    1
                    if budget.values[0] <= 160
                    else 3
                    if budget.values[0] <= 320
                    else 5
                    if budget.values[0] <= 640
                    else 10,
                )
                if mapping: hz = max_hz
                delay = max(
                    1 / max(0.1, hz), 0 if mapping else len(message) / max(2000, budget.values[4] / 4)
                ) - (time.monotonic() - started)
                if delay > 0:
                    await asyncio.sleep(delay)
        except Exception as exc:
            bridge_log("preview_closed", reason=type(exc).__name__)
        finally:
            waiters.discard(scan_ready)
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            await websocket.close()

    async with websockets.serve(
        handler,
        host,
        port,
        max_size=4096,
        max_queue=2,
        write_limit=8192,
        compression=None,
        close_timeout=1,
    ):
        await asyncio.Future()


def main():
    parser = argparse.ArgumentParser(description="Raw ROS LaserScan to WebSocket (no device fallback)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--path", default="/ws/lidar")
    parser.add_argument("--topic", default="/scan")
    parser.add_argument("--source", choices=["ros2"], default="ros2")
    parser.add_argument("--max-points", type=int, default=3200)
    parser.add_argument("--max-hz", type=float, default=10)
    parser.add_argument("--status-file", default=os.environ.get("LIDAR_BRIDGE_STATUS_FILE", "/runtime/lidar_bridge_status.json"))
    args=parser.parse_args()
    state, stop, ready = ScanState(), threading.Event(), threading.Event()
    workers=[threading.Thread(target=ros_thread,args=(state,args.topic,args.max_points,ready,stop),daemon=True),
             threading.Thread(target=status_writer_thread,args=(state,Path(args.status_file),.5,stop),daemon=True)]
    for t in workers:t.start()
    try:asyncio.run(serve_websocket(state,args.host,args.port,args.path,args.max_hz,args.max_points))
    finally:
        stop.set()
        for t in workers:t.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
