from __future__ import annotations
import argparse
import asyncio
import base64
import copy
import json
import math
import os
import re
import signal
import struct
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
import cv2
import numpy as np

# One camera packet's send and feedback share this absolute deadline. Other
# sensor ACK limits and vehicle control/freshness limits are independent.
CAMERA_ACK_TIMEOUT_S = 8.0

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CAMERA_INFO_IMAGE_PROVENANCE_REVISION = "camera_info_image_header_integrity_v2"
DEFAULT_CAMERA_INFO_SYNC_MAX_DELTA_S = 0.12
CAMERA_BINARY_MAGIC = b"UBCF"
CAMERA_BINARY_VERSION = 1
CAMERA_BINARY_HEADER_SIZE = 9


def bridge_log(event: str, **fields: Any) -> None:
    print(
        json.dumps(
            {"ts": round(time.time(), 3), "event": f"camera_bridge.{event}", **fields},
            ensure_ascii=False,
        ),
        flush=True,
    )


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def ros_stamp_payload(stamp_sec: int, stamp_nanosec: int) -> tuple[dict[str, int], int]:
    sec = max(0, int(stamp_sec))
    nanosec = max(0, min(999999999, int(stamp_nanosec)))
    return ({"sec": sec, "nanosec": nanosec}, sec * 1000000000 + nanosec)


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def nonnegative_integer(value: Any) -> int | None:
    """Return an exact non-negative integer without silently truncating metadata."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate or not candidate.isdecimal():
            return None
        integer = int(candidate)
        return integer if integer >= 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0.0 or (not value.is_integer()):
            return None
        return int(value)
    try:
        integer = int(value)
        if integer < 0 or value != integer:
            return None
        return integer
    except (TypeError, ValueError, OverflowError):
        return None


def camera_info_payload(msg: Any) -> dict[str, Any]:
    stamp, stamp_ns = ros_stamp_payload(msg.header.stamp.sec, msg.header.stamp.nanosec)
    raw_k = getattr(msg, "k", None)
    raw_d = getattr(msg, "d", None)
    return {
        "width": int(getattr(msg, "width", 0) or 0),
        "height": int(getattr(msg, "height", 0) or 0),
        "k": [float(value) for value in (list(raw_k) if raw_k is not None else [])[:9]],
        "d": [float(value) for value in (list(raw_d) if raw_d is not None else [])],
        "distortion_model": str(getattr(msg, "distortion_model", "") or ""),
        "frame_id": str(getattr(msg.header, "frame_id", "") or ""),
        "stamp": stamp,
        "stamp_ns": stamp_ns,
    }


@dataclass
class CameraState:
    image_frame_id: str = "camera_link_optical"
    stamp_sec: int = 0
    stamp_nanosec: int = 0
    frame_bgr: Any | None = None
    source_width: int | None = None
    source_height: int | None = None
    encoding: str = ""
    last_frame_at: float | None = None
    frame_count: int = 0
    fps_window_started_at: float | None = None
    fps_window_count: int = 0
    source_fps: float | None = None
    preview_frame_id: str = "camera_link_optical"
    preview_stamp_sec: int = 0
    preview_stamp_nanosec: int = 0
    preview_jpeg: bytes | None = None
    preview_width: int | None = None
    preview_height: int | None = None
    preview_last_frame_at: float | None = None
    preview_frame_count: int = 0
    preview_fps_window_started_at: float | None = None
    preview_fps_window_count: int = 0
    preview_source_fps: float | None = None
    camera_info: dict[str, Any] | None = None
    camera_info_count: int = 0
    image_last_stamp_ns: int | None = None
    camera_info_last_stamp_ns: int | None = None
    image_clock_epoch: int = 0
    camera_info_clock_epoch: int = 0
    image_clock_regression_count: int = 0
    camera_info_clock_regression_count: int = 0
    client_count: int = 0
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    frame_notify: Any | None = None

    def update_frame(
        self,
        frame_id: str,
        stamp_sec: int,
        stamp_nanosec: int,
        frame_bgr: Any,
        encoding: str,
    ) -> None:
        with self.lock:
            _, stamp_ns = ros_stamp_payload(stamp_sec, stamp_nanosec)
            if (
                self.image_last_stamp_ns is not None
                and stamp_ns <= self.image_last_stamp_ns
            ):
                self.image_clock_epoch += 1
                self.image_clock_regression_count += 1
            self.image_last_stamp_ns = stamp_ns
            self.image_frame_id = str(frame_id or "").strip()
            self.stamp_sec = int(stamp_sec)
            self.stamp_nanosec = int(stamp_nanosec)
            self.frame_bgr = frame_bgr
            self.source_height, self.source_width = frame_bgr.shape[:2]
            self.encoding = encoding
            now = time.time()
            self.last_frame_at = now
            self.frame_count += 1
            if self.fps_window_started_at is None:
                self.fps_window_started_at = now
                self.fps_window_count = 0
            self.fps_window_count += 1
            elapsed = now - self.fps_window_started_at
            if elapsed >= 1.0:
                self.source_fps = round(self.fps_window_count / elapsed, 1)
                self.fps_window_started_at = now
                self.fps_window_count = 0
            self.error = None
        if self.frame_notify is not None:
            self.frame_notify()

    def update_camera_info(self, payload: dict[str, Any]) -> None:
        with self.lock:
            row = copy.deepcopy(payload)
            stamp_ns = nonnegative_integer(row.get("stamp_ns")) or 0
            if (
                self.camera_info_last_stamp_ns is not None
                and stamp_ns <= self.camera_info_last_stamp_ns
            ):
                self.camera_info_clock_epoch += 1
                self.camera_info_clock_regression_count += 1
            self.camera_info_last_stamp_ns = stamp_ns
            row["_clock_epoch"] = self.camera_info_clock_epoch
            self.camera_info = row
            self.camera_info_count += 1

    def update_preview(
        self,
        frame_id: str,
        stamp_sec: int,
        stamp_nanosec: int,
        jpeg: bytes,
        compressed_format: str = "",
    ) -> None:
        if not jpeg or not jpeg.startswith(b"\xff\xd8"):
            raise ValueError("compressed camera message is not a JPEG")
        with self.lock:
            self.preview_frame_id = str(frame_id or "").strip()
            self.preview_stamp_sec = int(stamp_sec)
            self.preview_stamp_nanosec = int(stamp_nanosec)
            self.preview_jpeg = bytes(jpeg)
            info = self.camera_info or {}
            width_match = re.search(
                "(?:^|;)\\s*width=(\\d+)(?:;|$)", compressed_format, re.I
            )
            height_match = re.search(
                "(?:^|;)\\s*height=(\\d+)(?:;|$)", compressed_format, re.I
            )
            self.preview_width = (
                int(width_match.group(1))
                if width_match
                else int(info.get("width") or self.source_width or 0) or None
            )
            self.preview_height = (
                int(height_match.group(1))
                if height_match
                else int(info.get("height") or self.source_height or 0) or None
            )
            now = time.time()
            self.preview_last_frame_at = now
            self.preview_frame_count += 1
            if self.preview_fps_window_started_at is None:
                self.preview_fps_window_started_at = now
                self.preview_fps_window_count = 0
            self.preview_fps_window_count += 1
            elapsed = now - self.preview_fps_window_started_at
            if elapsed >= 1.0:
                self.preview_source_fps = round(
                    self.preview_fps_window_count / elapsed, 1
                )
                self.preview_fps_window_started_at = now
                self.preview_fps_window_count = 0

        if self.frame_notify is not None:
            self.frame_notify()

    def preview_snapshot(self, include_jpeg: bool = True) -> dict[str, Any]:
        with self.lock:
            _, stamp_ns = ros_stamp_payload(
                self.preview_stamp_sec, self.preview_stamp_nanosec
            )
            age = (
                None
                if self.preview_last_frame_at is None
                else round(time.time() - self.preview_last_frame_at, 3)
            )
            return {
                "jpeg": self.preview_jpeg if include_jpeg else None,
                "frame_id": self.preview_frame_id,
                "stamp_s": stamp_ns / 1000000000,
                "last_frame_age_sec": age,
                "frame_count": self.preview_frame_count,
                "source_fps": self.preview_source_fps,
                "source_width": self.preview_width,
                "source_height": self.preview_height,
                "error": self.error,
            }

    def set_error(self, message: str) -> None:
        with self.lock:
            self.error = message

    def adjust_clients(self, delta: int) -> None:
        with self.lock:
            self.client_count = max(0, self.client_count + delta)

    def snapshot(self, include_frame: bool = True) -> dict[str, Any]:
        with self.lock:
            frame = (
                None
                if not include_frame or self.frame_bgr is None
                else self.frame_bgr.copy()
            )
            age = (
                None
                if self.last_frame_at is None
                else round(time.time() - self.last_frame_at, 3)
            )
            stamp, stamp_ns = ros_stamp_payload(self.stamp_sec, self.stamp_nanosec)
            now = time.time()
            return {
                "frame_id": self.image_frame_id,
                "stamp": stamp,
                "stamp_ns": stamp_ns,
                "stamp_s": stamp_ns / 1000000000,
                "frame_bgr": frame,
                "source_width": self.source_width,
                "source_height": self.source_height,
                "encoding": self.encoding,
                "last_frame_age_sec": age,
                "frame_count": self.frame_count,
                "source_fps": self.source_fps,
                "camera_info_count": self.camera_info_count,
                "image_clock_epoch": self.image_clock_epoch,
                "camera_info_clock_epoch": self.camera_info_clock_epoch,
                "image_clock_regression_count": self.image_clock_regression_count,
                "camera_info_clock_regression_count": self.camera_info_clock_regression_count,
                "client_count": self.client_count,
                "error": self.error,
            }


def frame_topic_alive(snapshot: dict[str, Any]) -> bool:
    age = snapshot.get("last_frame_age_sec")
    return age is not None and age < 2.0


def preview_topic_alive(snapshot: dict[str, Any]) -> bool:
    age = snapshot.get("last_frame_age_sec")
    return snapshot.get("frame_count", 0) > 0 and age is not None and (age < 2.0)


def status_payload(state, *, image_topic, compressed_topic, camera_info_topic):
    preview = state.preview_snapshot(False)
    raw = state.snapshot(False)
    snap = preview if preview_topic_alive(preview) else raw
    alive = frame_topic_alive(snap)
    return {
        "type": "camera_bridge_status",
        "updated_at": time.time(),
        "frame_topic_alive": alive,
        "last_frame_age_sec": snap["last_frame_age_sec"],
        "frame_count": snap["frame_count"],
        "source_fps": snap["source_fps"],
        "source_width": snap["source_width"],
        "source_height": snap["source_height"],
        "frame_id": snap["frame_id"],
        "image_topic": image_topic,
        "compressed_topic": compressed_topic,
        "camera_info_topic": camera_info_topic,
        "error": snap["error"] or (None if alive else "Camera topic waiting"),
    }


def write_status(path: Path, payload: dict[str, Any]) -> None:
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


def status_writer_thread(
    state: CameraState,
    path: Path,
    interval_s: float,
    stop: threading.Event,
    status_kwargs: dict[str, Any],
) -> None:
    while not stop.is_set():
        write_status(path, status_payload(state, **status_kwargs))
        stop.wait(max(0.2, interval_s))
    write_status(path, status_payload(state, **status_kwargs))


def image_to_bgr(msg: Any) -> Any:
    height = int(getattr(msg, "height", 0) or 0)
    width = int(getattr(msg, "width", 0) or 0)
    encoding = str(getattr(msg, "encoding", "") or "").lower()
    data = bytes(getattr(msg, "data", b"") or b"")
    if height <= 0 or width <= 0 or (not data):
        raise ValueError("empty image message")
    channels = (
        4
        if encoding in {"bgra8", "rgba8"}
        else 3
        if encoding in {"bgr8", "rgb8"}
        else 1
    )
    if encoding not in {"bgr8", "rgb8", "bgra8", "rgba8", "mono8", "8uc1"}:
        raise ValueError(f"unsupported image encoding: {encoding}")
    row_bytes = width * channels
    step = int(getattr(msg, "step", 0) or row_bytes)
    if step < row_bytes or len(data) < height * step:
        raise ValueError("image message data is shorter than height*step")
    packed = np.frombuffer(data, dtype=np.uint8, count=height * step).reshape(
        (height, step)
    )[:, :row_bytes]
    arr = (
        packed.reshape((height, width, channels))
        if channels > 1
        else packed.reshape((height, width))
    )
    if encoding == "bgr8":
        return arr.copy()
    if encoding == "rgb8":
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    if encoding in {"bgra8", "rgba8"}:
        code = cv2.COLOR_BGRA2BGR if encoding == "bgra8" else cv2.COLOR_RGBA2BGR
        return cv2.cvtColor(arr, code)
    return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)


def ros_thread(
    state: CameraState,
    image_topic: str,
    compressed_topic: str,
    camera_info_topic: str,
    ready: threading.Event,
    stop: threading.Event,
) -> None:
    try:
        import rclpy
        from rclpy.node import Node
        from rclpy.parameter import Parameter
        from rclpy.qos import (
            DurabilityPolicy,
            HistoryPolicy,
            QoSProfile,
            ReliabilityPolicy,
        )
        from sensor_msgs.msg import CameraInfo, CompressedImage, Image

    except ImportError as exc:
        state.set_error(f"ROS2 Python packages are required on Jetson: {exc}")
        bridge_log("ros_import_error", error=str(exc))
        ready.set()
        return
    sensor_qos = QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )

    class BridgeNode(Node):
        def __init__(self):
            super().__init__("ubts_camera_ws_bridge", parameter_overrides=[Parameter("use_sim_time", value=os.environ.get("DASHBOARD_MODE") == "simulation")])
            self.create_subscription(Image, image_topic, self.on_image, sensor_qos)
            self.create_subscription(
                CompressedImage, compressed_topic, self.on_compressed, sensor_qos
            )
            self.create_subscription(
                CameraInfo, camera_info_topic, self.on_camera_info, sensor_qos
            )
            self.last_published_generation = 0
            self.last_log_at = 0.0
            bridge_log(
                "ros_ready",
                image_topic=image_topic,
                compressed_topic=compressed_topic,
                camera_info_topic=camera_info_topic,
                subscription_qos="best_effort_keep_last_1",
            )

        def on_image(self, msg: Any) -> None:
            try:
                frame = image_to_bgr(msg)
            except Exception as exc:
                state.set_error(str(exc))
                return
            state.update_frame(
                msg.header.frame_id,
                msg.header.stamp.sec,
                msg.header.stamp.nanosec,
                frame,
                msg.encoding,
            )
            now = time.time()
            if state.frame_count == 1 or now - self.last_log_at >= 10:
                self.last_log_at = now
                bridge_log(
                    "frame_received",
                    topic=image_topic,
                    width=msg.width,
                    height=msg.height,
                    encoding=msg.encoding,
                    count=state.frame_count,
                )

        def on_camera_info(self, msg: Any) -> None:
            state.update_camera_info(camera_info_payload(msg))

        def on_compressed(self, msg: Any) -> None:
            try:
                state.update_preview(
                    msg.header.frame_id,
                    msg.header.stamp.sec,
                    msg.header.stamp.nanosec,
                    bytes(msg.data),
                    str(msg.format or ""),
                )
            except Exception as exc:
                state.set_error(str(exc))

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


def client_settings(actual_path: str) -> dict[str, Any]:
    params = parse_qs(urlparse(actual_path).query)
    resolution = (params.get("resolution") or ["1280x720"])[0]
    try:
        width_text, height_text = str(resolution).lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except (ValueError, TypeError):
        width, height = (1280, 720)
    allowed = {
        (320, 180),
        (640, 360),
        (320, 240),
        (640, 480),
        (800, 600),
        (1024, 768),
        (1280, 720),
        (1280, 1024),
        (1920, 1080),
    }
    if (width, height) not in allowed:
        width, height = (1280, 720)
    try:
        fps = int((params.get("fps") or ["30"])[0])
    except (TypeError, ValueError):
        fps = 15
    try:
        quality = int((params.get("quality") or ["75"])[0])
    except (TypeError, ValueError):
        quality = 75
    transport = str((params.get("transport") or ["json-base64"])[0]).strip().lower()
    if transport not in {"json-base64", "binary-v1"}:
        transport = "json-base64"
    flow = str((params.get("flow") or ["none"])[0]).strip().lower()
    if flow not in {"none", "decode-ack", "latest-ack", "decode-ack-v2"}:
        flow = "none"
    window_values=params.get("ack_window")or[]
    ack_window=int(window_values[0])if len(window_values)==1 and window_values[0]in("2","3")and flow=="decode-ack-v2"and transport=="binary-v1"else 1
    if flow=="decode-ack-v2"and ack_window==1:flow="decode-ack"
    return {
        "profile": str((params.get("profile") or ["auto"])[0]),
        "width": width,
        "height": height,
        "fps": max(1, min(60, fps)),
        "quality": max(35, min(92, quality)),
        "transport": transport,
        "flow": flow if transport == "binary-v1" else "none",
        "ack_window": ack_window,
    }


def encode_binary_camera_frame(metadata: dict[str, Any], jpeg: bytes) -> bytes:
    """Pack one JPEG and its metadata into one binary WebSocket message."""
    metadata_bytes = json.dumps(
        metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return (
        CAMERA_BINARY_MAGIC
        + bytes([CAMERA_BINARY_VERSION])
        + struct.pack("!I", len(metadata_bytes))
        + metadata_bytes
        + bytes(jpeg)
    )


def encode_camera_preview(
    frame: Any, width: int, height: int, quality: int
) -> tuple[bytes | None, float]:
    started = time.perf_counter()
    out = frame if frame.shape[:2] == (height, width) else cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return (encoded.tobytes() if ok else None, elapsed_ms)


async def wait_for_camera_decode_ack(
    websocket: Any, frame_count: int, timeout_s: float = 1.0
) -> dict[str, Any] | None:
    """Match one frame and preserve decoded/error/timeout feedback separately."""
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return None
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return None
        if not isinstance(raw, str):
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("type") != "frame_ack":
            continue
        acknowledged = payload.get("frame_count")
        if type(acknowledged) is not int:
            continue
        if acknowledged == int(frame_count):
            status = payload.get("decode_status", "unknown")
            if not isinstance(status, str) or status not in {"decoded", "error", "timeout", "not_visible", "unknown"}:
                status = "unknown"
            return {"frame_count": acknowledged, "decode_status": status}


def network_preview(frame_snap, settings, budget, compressed):
    """Resize/re-encode even compressed ROS previews; never alter source frames."""
    from scripts.stream_policy import fit_size

    frame = (
        cv2.imdecode(
            np.frombuffer(frame_snap["jpeg"], dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if compressed
        else frame_snap["frame_bgr"]
    )
    if frame is None:
        raise ValueError("camera preview decode failed")
    source_height, source_width = frame.shape[:2]
    limit_width, limit_height, _, quality, _ = budget.values
    width, height = fit_size(
        source_width,
        source_height,
        min(settings["width"], limit_width),
        min(settings["height"], limit_height),
    )
    requested_quality = max(70, settings["quality"]) if budget.profile == "auto" else settings["quality"]
    jpeg, elapsed = encode_camera_preview(frame, width, height, min(requested_quality, quality))
    return (jpeg, elapsed, width, height, source_width, source_height)


def camera_stream_budget(profile, *, ack_window=1):
    from scripts.camera_stream_policy import CameraStreamBudget,CameraWindowStreamBudget
    local = (os.environ.get("DASHBOARD_MODE") == "simulation"
             and os.environ.get("CAMERA_PREVIEW_LOCAL_FPS") == "30")
    factory=CameraWindowStreamBudget if ack_window in(2,3)else CameraStreamBudget
    options=dict(ack_window=ack_window)if ack_window in(2,3)else{}
    return factory(profile, local_camera_30fps=local, **options)


class CameraPreviewCache:
    """Serialize JPEG work and coalesce viewers, selecting latest after waiting.

    There is no FIFO of images to encode. Waiting clients re-read the latest
    source under the encoder lock; identical profiles share one encoded JPEG.
    """
    def __init__(self):
        self.lock = asyncio.Lock()
        self.frames = {}
        self.encodes = 0

    @staticmethod
    def key(frame, settings, budget, compressed):
        from scripts.stream_policy import fit_size
        max_width, max_height, _, max_quality, _ = budget.values
        width, height = min(settings["width"], max_width), min(settings["height"], max_height)
        if frame.get("source_width") and frame.get("source_height"):
            width, height = fit_size(frame["source_width"], frame["source_height"], width, height)
        # Frame size/quality determine the JPEG. Requested FPS, byte budgets,
        # and a larger requested viewport do not change the encoded pixels.
        return (compressed, frame["frame_count"], width, height,
                min(max(70, settings["quality"]) if budget.profile == "auto" else settings["quality"], max_quality))

    async def latest(self, state, settings, budget, compressed):
        async with self.lock:
            frame = state.preview_snapshot(include_jpeg=False) if compressed else state.snapshot(include_frame=False)
            key = self.key(frame, settings, budget, compressed)
            if key in self.frames:
                return frame, self.frames[key]
            frame = state.preview_snapshot() if compressed else state.snapshot()
            key = self.key(frame, settings, budget, compressed)
            encoded = await asyncio.to_thread(network_preview, frame, settings, budget, compressed)
            if len(self.frames) >= 8:
                self.frames.pop(next(iter(self.frames)))
            self.frames[key] = encoded
            self.encodes += 1
            frame.pop("frame_bgr", None)
            frame.pop("jpeg", None)
            return frame, encoded


async def serve_websocket(
    state: CameraState, host: str, port: int, path: str, stop: threading.Event
) -> None:
    import websockets
    from scripts.stream_policy import FramePacer
    preview_cache = CameraPreviewCache()
    frame_ready = asyncio.Event()
    loop = asyncio.get_running_loop()
    def notify_frame():
        try:
            loop.call_soon_threadsafe(frame_ready.set)
        except RuntimeError:
            pass  # The server is already shutting down.
    state.frame_notify = notify_frame
    lag_handle=None

    async def handler(websocket, legacy_path=None):
        request = getattr(websocket, "request", None)
        actual_path = (
            getattr(request, "path", path)
            if request is not None
            else legacy_path or getattr(websocket, "path", path)
        )
        if urlparse(actual_path).path != path:
            await websocket.close(code=1008, reason="unsupported path")
            return
        settings = client_settings(actual_path)
        if state.snapshot(include_frame=False)["client_count"] >= 4:
            await websocket.close(code=1013, reason="camera viewer limit")
            return
        budget = camera_stream_budget(settings["profile"],ack_window=settings['ack_window'])
        binary = settings["transport"] == "binary-v1"
        ack_flow = binary and settings["flow"] != "none"
        from scripts.camera_stream_policy import CameraAckWindow
        window=CameraAckWindow(settings['ack_window'],uuid.uuid4().hex,timeout_s=CAMERA_ACK_TIMEOUT_S)if settings['ack_window']in(2,3)else None
        flow_name='decode-ack-v2'if window is not None else'decode-ack'if ack_flow else'none'
        credit=asyncio.Event();window_failed=asyncio.Event();receiver=None;feedback=dict(error=None)
        def source_key(frame,compressed):
            return(compressed,frame.get('frame_id'),frame.get('source_width'),frame.get('source_height'),
                frame.get('raw_preview_width'),frame.get('raw_preview_height'))
        async def receive_window_acks():
            try:
                while not stop.is_set():
                    deadline=window.deadline();now=time.monotonic()
                    timeout=.5 if deadline is None else max(.001,min(.5,deadline-now))
                    try:raw=await asyncio.wait_for(websocket.recv(),timeout)
                    except asyncio.TimeoutError:
                        if window.expired():raise asyncio.TimeoutError()
                        continue
                    try:payload=json.loads(raw)if isinstance(raw,str)else None
                    except(ValueError,TypeError):payload=None
                    acknowledged=window.acknowledge(payload)
                    if acknowledged is not None:
                        elapsed=acknowledged['elapsed']
                        budget.acknowledge(elapsed,acknowledged['bytes'],decode_status=acknowledged['decode_status'])
                        credit.set()
                    if sum(v for k,v in window.counts.items()if k not in('accepted','accepted_out_of_order'))>=64:
                        raise ValueError('camera_V2_invalid_ACK_limit64')
            except asyncio.CancelledError:raise
            except Exception as exc:feedback['error']=exc
            finally:credit.set();window_failed.set()
        async def wait_window_credit(size=1):
            if size>window.MAX_PACKET_BYTES:
                await websocket.close(code=1009,reason='camera preview packet limit');return False
            while not stop.is_set():
                if feedback['error']is not None or window.expired():
                    timeout=isinstance(feedback['error'],asyncio.TimeoutError)or window.expired()
                    await websocket.close(code=1013 if timeout else 1008,reason='preview acknowledgement timeout'if timeout else'camera V2 acknowledgement rejected')
                    return False
                if window.can_send(size):return True
                credit.clear()
                if window.can_send(size):return True
                deadline=window.deadline()
                remaining=CAMERA_ACK_TIMEOUT_S if deadline is None else max(0.,deadline-time.monotonic())
                try:await asyncio.wait_for(credit.wait(),remaining)
                except asyncio.TimeoutError:
                    await websocket.close(code=1013,reason='preview acknowledgement timeout');return False
            return False
        async def pace_window(fps,byte_rate):
            # Keep the original FramePacer math. Only V2's background receiver
            # can interrupt its sleep when the oldest absolute ACK expires or
            # the connection closes; no sleeping send/ACK task survives close.
            paced=asyncio.create_task(pacer.wait(fps,byte_rate))
            failed=asyncio.create_task(window_failed.wait())
            try:
                deadline=window.deadline()
                timeout=None if deadline is None else max(0.,deadline-time.monotonic())
                done,_=await asyncio.wait((paced,failed),timeout=timeout,return_when=asyncio.FIRST_COMPLETED)
                if paced not in done:
                    await wait_window_credit();return None
                return paced.result()
            finally:
                for task in(paced,failed):
                    if not task.done():task.cancel()
                await asyncio.gather(paced,failed,return_exceptions=True)
        last_count = -1
        last_status = 0.0
        pacer = FramePacer()
        state.adjust_clients(1)
        try:
            await websocket.send(
                json.dumps(
                    {
                        "type": "socket",
                        "ok": True,
                        "transport": settings["transport"],
                        "flow": flow_name,
                        "latest_only": True,
                        **(dict(stream_session=window.stream_session,**window.status())if window is not None else{}),
                    }
                )
            )
            if window is not None:receiver=asyncio.create_task(receive_window_acks())
            while not stop.is_set():
                if window is not None and not await wait_window_credit():return
                pace_started=time.monotonic()
                paced=(await pace_window(min(settings["fps"],budget.values[2]),budget.values[4])if window is not None
                    else await pacer.wait(min(settings["fps"], budget.values[2]), budget.values[4]))
                if window is not None and paced is None:return
                started = time.monotonic()
                frame_ready.clear()
                preview = state.preview_snapshot(include_jpeg=False)
                compressed = preview_topic_alive(preview)
                snap = preview if compressed else state.snapshot(include_frame=False)
                alive = compressed or frame_topic_alive(snap)
                if started - last_status >= 1:
                    await asyncio.wait_for(
                        websocket.send(
                            json.dumps(
                                {
                                    "type": "status",
                                    "frame_topic_alive": alive,
                                    "last_frame_age_sec": snap["last_frame_age_sec"],
                                    "frame_count": snap["frame_count"],
                                    "source_fps": snap["source_fps"],
                                    "source_width": snap["source_width"],
                                    "source_height": snap["source_height"],
                                    "requested_fps": min(
                                        settings["fps"], budget.values[2]
                                    ),
                                    "profile": budget.profile,
                                    "effective_profile": budget.effective_profile,
                                    "ack_latency_ms": budget.ack_latency_ms,
                                    "budget_bytes_s": budget.values[4],
                                    "latest_only": True,
                                    "max_inflight_frames": window.limit if window is not None else 1 if ack_flow else None,
                                    "flow": flow_name,
                                    "preview_decoupled": True,
                                    "ack_deadline_s": CAMERA_ACK_TIMEOUT_S,
                                    **budget.status(),
                                    **(window.status()if window is not None else{}),
                                },
                                separators=(",", ":"),
                            )
                        ),
                        timeout=2,
                    )
                    last_status = started
                count = int(snap["frame_count"] or 0)
                if window is not None and alive and count>0:
                    try:window.observe_source(source_key(snap,compressed),count,snap['stamp_s'])
                    except ValueError:
                        await websocket.close(code=1012,reason='camera source changed');return
                if not alive or count == last_count:
                    try:
                        await asyncio.wait_for(frame_ready.wait(), timeout=.5)
                    except asyncio.TimeoutError:
                        pass
                    continue
                frame, encoded = await preview_cache.latest(state, settings, budget, compressed)
                count = int(frame["frame_count"] or 0)
                jpeg, elapsed, width, height, source_width, source_height = (
                    encoded
                )
                if jpeg is None:
                    await asyncio.sleep(0.1)
                    continue
                metadata = {
                    "type": "frame",
                    "mime": "image/jpeg",
                    "width": width,
                    "height": height,
                    "source_width": source_width,
                    "source_height": source_height,
                    "frame_id": frame["frame_id"],
                    "stamp": frame["stamp_s"],
                    "frame_count": count,
                    "source_fps": frame["source_fps"],
                    "transport": settings["transport"],
                    "flow": flow_name,
                    "encoded_bytes": len(jpeg),
                    "encode_ms": round(elapsed, 2),
                    "source_transport": "ros2-compressed-jpeg"
                    if compressed
                    else "ros2-raw-bgr",
                    "preview_decoupled": True,
                    "ack_deadline_s": CAMERA_ACK_TIMEOUT_S,
                    **budget.status(),
                }
                if window is not None:
                    # Encoder waiting may select a newer frame. Bind the exact
                    # selected source before registering its delivery credit.
                    try:window.observe_source(source_key(frame,compressed),count,frame['stamp_s'])
                    except ValueError:
                        await websocket.close(code=1012,reason='camera source changed');return
                    metadata.update(stream_session=window.stream_session,delivery_sequence=window.last_sequence+1,
                        inflight_measurement='before_this_delivery_sequence_is_registered',**window.status())
                if binary:
                    message = encode_binary_camera_frame(metadata, jpeg)
                else:
                    metadata["data"] = base64.b64encode(jpeg).decode("ascii")
                    message = json.dumps(metadata, separators=(",", ":"))
                sent = time.monotonic()
                deadline = sent + CAMERA_ACK_TIMEOUT_S
                if window is not None:
                    if not window.can_send(len(message)):
                        if not await wait_window_credit(len(message)):return
                        # No stale frame FIFO: re-read newest source after credit.
                        continue
                    window.register(count,len(message),now=sent)
                try:
                    send_timeout=(max(0.,window.deadline()-time.monotonic())if window is not None else CAMERA_ACK_TIMEOUT_S)
                    await asyncio.wait_for(websocket.send(message), timeout=send_timeout)
                except asyncio.TimeoutError:
                    await websocket.close(code=1013, reason="preview acknowledgement timeout")
                    return
                last_count = count
                pacer.sent(sent, len(message))
                if ack_flow and window is None:
                    ack_wait_started=time.monotonic()
                    acknowledged = await wait_for_camera_decode_ack(
                        websocket, count, timeout_s=max(0.0, deadline-time.monotonic())
                    )
                    if acknowledged is None:
                        await websocket.close(
                            code=1013, reason="preview acknowledgement timeout"
                        )
                        return
                    budget.acknowledge(time.monotonic() - sent, len(message),
                                       decode_status=acknowledged["decode_status"])
        except Exception as exc:
            bridge_log("preview_closed", reason=type(exc).__name__)
        finally:
            if receiver is not None:
                receiver.cancel();await asyncio.gather(receiver,return_exceptions=True)
            if window is not None:window.close()
            state.adjust_clients(-1)

    async with websockets.serve(
        handler,
        host,
        port,
        max_size=4096,
        max_queue=2,
        write_limit=16384,
        compression=None,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=1,
    ):
        while not stop.is_set():
            await asyncio.sleep(0.2)
    if lag_handle is not None:lag_handle.cancel()
    state.frame_notify = None


def main():
    parser = argparse.ArgumentParser(description="ROS camera to raw JPEG WebSocket")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--path", default="/ws/camera")
    parser.add_argument(
        "--topic", default=os.environ.get("CAMERA_IMAGE_TOPIC", "/camera/image_raw")
    )
    parser.add_argument(
        "--compressed-topic",
        default=os.environ.get("CAMERA_COMPRESSED_TOPIC", "/camera/image_jpeg"),
    )
    parser.add_argument(
        "--camera-info-topic",
        default=os.environ.get("CAMERA_INFO_TOPIC", "/camera/camera_info"),
    )
    parser.add_argument(
        "--status-file",
        default=os.environ.get(
            "CAMERA_BRIDGE_STATUS_FILE", "/app/runtime/camera_bridge_status.json"
        ),
    )
    args = parser.parse_args()
    state, stop, ready = CameraState(), threading.Event(), threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    kwargs = dict(
        image_topic=args.topic,
        compressed_topic=args.compressed_topic,
        camera_info_topic=args.camera_info_topic,
    )
    threads = [
        threading.Thread(
            target=ros_thread,
            args=(
                state,
                args.topic,
                args.compressed_topic,
                args.camera_info_topic,
                ready,
                stop,
            ),
            daemon=True,
        ),
        threading.Thread(
            target=status_writer_thread,
            args=(state, Path(args.status_file), 0.5, stop, kwargs),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    try:
        asyncio.run(serve_websocket(state, args.host, args.port, args.path, stop))
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
