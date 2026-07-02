#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np


@dataclass
class CameraState:
    frame_id: str = "camera_link_optical"
    stamp: float | None = None
    frame_bgr: Any | None = None
    source_width: int | None = None
    source_height: int | None = None
    encoding: str = ""
    last_frame_at: float | None = None
    frame_count: int = 0
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, frame_id: str, stamp: float, frame_bgr: Any, encoding: str) -> None:
        with self.lock:
            self.frame_id = frame_id or "camera_link_optical"
            self.stamp = stamp
            self.frame_bgr = frame_bgr
            self.source_height, self.source_width = frame_bgr.shape[:2]
            self.encoding = encoding
            self.last_frame_at = time.time()
            self.frame_count += 1
            self.error = None

    def set_error(self, message: str) -> None:
        with self.lock:
            self.error = message

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            frame = None if self.frame_bgr is None else self.frame_bgr.copy()
            age = None if self.last_frame_at is None else round(time.time() - self.last_frame_at, 3)
            return {
                "frame_id": self.frame_id,
                "stamp": self.stamp,
                "frame_bgr": frame,
                "source_width": self.source_width,
                "source_height": self.source_height,
                "encoding": self.encoding,
                "last_frame_age_sec": age,
                "frame_count": self.frame_count,
                "error": self.error,
            }


def bridge_log(event: str, **fields: Any) -> None:
    print(json.dumps({"ts": round(time.time(), 3), "event": f"camera_bridge.{event}", **fields}, ensure_ascii=False), flush=True)


def frame_topic_alive(snapshot: dict[str, Any]) -> bool:
    age = snapshot.get("last_frame_age_sec")
    return age is not None and age < 2.0


def status_payload(state: CameraState) -> dict[str, Any]:
    snap = state.snapshot()
    alive = frame_topic_alive(snap)
    return {
        "type": "camera_bridge_status",
        "updated_at": time.time(),
        "frame_topic_alive": alive,
        "last_frame_age_sec": snap["last_frame_age_sec"],
        "frame_count": snap["frame_count"],
        "source_width": snap["source_width"],
        "source_height": snap["source_height"],
        "encoding": snap["encoding"],
        "frame_id": snap["frame_id"],
        "error": snap["error"] or (None if alive else "ROS2 /camera/image_raw 대기 중"),
    }


def write_status(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def status_writer_thread(state: CameraState, path: Path, interval_s: float, stop: threading.Event) -> None:
    while not stop.is_set():
        write_status(path, status_payload(state))
        stop.wait(max(0.2, interval_s))
    write_status(path, status_payload(state))


def image_to_bgr(msg: Any) -> Any:
    height = int(getattr(msg, "height", 0) or 0)
    width = int(getattr(msg, "width", 0) or 0)
    encoding = str(getattr(msg, "encoding", "") or "").lower()
    data = bytes(getattr(msg, "data", b"") or b"")
    if height <= 0 or width <= 0 or not data:
        raise ValueError("empty image message")
    if encoding in {"bgr8", "rgb8"}:
        arr = np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3))
        return arr.copy() if encoding == "bgr8" else cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    if encoding in {"bgra8", "rgba8"}:
        arr = np.frombuffer(data, dtype=np.uint8).reshape((height, width, 4))
        code = cv2.COLOR_BGRA2BGR if encoding == "bgra8" else cv2.COLOR_RGBA2BGR
        return cv2.cvtColor(arr, code)
    if encoding in {"mono8", "8uc1"}:
        arr = np.frombuffer(data, dtype=np.uint8).reshape((height, width))
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    raise ValueError(f"unsupported image encoding: {encoding}")


def client_settings(actual_path: str) -> dict[str, int]:
    params = parse_qs(urlparse(actual_path).query)
    resolution = (params.get("resolution") or ["1280x720"])[0]
    try:
        width_text, height_text = str(resolution).lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except (ValueError, TypeError):
        width, height = 1280, 720
    allowed = {(640, 480), (1280, 720), (1920, 1080)}
    if (width, height) not in allowed:
        width, height = 1280, 720
    try:
        fps = int((params.get("fps") or ["15"])[0])
    except (TypeError, ValueError):
        fps = 15
    try:
        quality = int((params.get("quality") or ["75"])[0])
    except (TypeError, ValueError):
        quality = 75
    return {
        "width": width,
        "height": height,
        "fps": max(1, min(30, fps)),
        "quality": max(35, min(92, quality)),
    }


def ros_thread(state: CameraState, topic: str, ready: threading.Event, stop: threading.Event) -> None:
    try:
        import rclpy
        from rclpy.node import Node
        from sensor_msgs.msg import Image
    except ImportError as exc:
        state.set_error(f"ROS2 Python packages are required on Jetson: {exc}")
        bridge_log("ros_import_error", error=str(exc))
        ready.set()
        return

    class BridgeNode(Node):
        def __init__(self):
            super().__init__("ubts_camera_ws_bridge")
            self.create_subscription(Image, topic, self.on_image, 10)
            self.last_log_at = 0.0
            bridge_log("ros_subscribed", topic=topic)

        def on_image(self, msg):
            try:
                frame = image_to_bgr(msg)
            except Exception as exc:
                state.set_error(str(exc))
                return
            stamp = float(msg.header.stamp.sec) + float(msg.header.stamp.nanosec) / 1_000_000_000
            state.update(msg.header.frame_id or "camera_link_optical", stamp, frame, msg.encoding)
            now = time.time()
            if state.frame_count == 1 or now - self.last_log_at >= 10:
                self.last_log_at = now
                bridge_log("frame_received", topic=topic, width=msg.width, height=msg.height, encoding=msg.encoding, count=state.frame_count)

    rclpy.init()
    node = BridgeNode()
    ready.set()
    try:
        while rclpy.ok() and not stop.is_set():
            rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        node.destroy_node()
        rclpy.shutdown()


async def serve_websocket(state: CameraState, host: str, port: int, path: str):
    try:
        import websockets
    except ImportError as exc:
        raise SystemExit("Install the optional 'websockets' package on Jetson to run camera_ws_bridge.py") from exc

    async def handler(websocket, legacy_path=None):
        request_path = getattr(websocket, "request", None)
        actual_path = getattr(request_path, "path", path) if request_path is not None else legacy_path or getattr(websocket, "path", path)
        parsed = urlparse(actual_path)
        if parsed.path != path:
            await websocket.close(code=1008, reason="unsupported path")
            return
        settings = client_settings(actual_path)
        interval = 1.0 / settings["fps"]
        bridge_log("client_open", path=actual_path, **settings)
        try:
            await websocket.send(json.dumps({"type": "socket", "ok": True, "message": "camera websocket connected"}, separators=(",", ":")))
            while True:
                snap = state.snapshot()
                alive = frame_topic_alive(snap)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "status",
                            "frame_topic_alive": alive,
                            "last_frame_age_sec": snap["last_frame_age_sec"],
                            "frame_count": snap["frame_count"],
                            "source_width": snap["source_width"],
                            "source_height": snap["source_height"],
                            "frame_id": snap["frame_id"],
                            "encoding": snap["encoding"],
                            "requested_width": settings["width"],
                            "requested_height": settings["height"],
                            "requested_fps": settings["fps"],
                            "error": snap["error"] or (None if alive else "ROS2 /camera/image_raw is not being received yet."),
                        },
                        separators=(",", ":"),
                    )
                )
                frame = snap["frame_bgr"]
                if frame is not None:
                    out = cv2.resize(frame, (settings["width"], settings["height"]), interpolation=cv2.INTER_AREA)
                    ok, encoded = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), settings["quality"]])
                    if ok:
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "frame",
                                    "mime": "image/jpeg",
                                    "data": base64.b64encode(encoded.tobytes()).decode("ascii"),
                                    "width": settings["width"],
                                    "height": settings["height"],
                                    "source_width": snap["source_width"],
                                    "source_height": snap["source_height"],
                                    "frame_id": snap["frame_id"],
                                    "stamp": snap["stamp"],
                                    "frame_count": snap["frame_count"],
                                },
                                separators=(",", ":"),
                            )
                        )
                await asyncio.sleep(interval)
        except Exception as exc:
            connection_closed = getattr(getattr(websockets, "exceptions", None), "ConnectionClosed", None)
            if connection_closed is not None and isinstance(exc, connection_closed):
                bridge_log("client_closed")
                return
            bridge_log("handler_error", error=str(exc))

    bridge_log("ws_listen", host=host, port=port, path=path)
    async with websockets.serve(handler, host, port, max_size=8 * 1024 * 1024):
        await asyncio.Future()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge ROS2 camera Image to browser WebSocket JPEG frames.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--path", default="/ws/camera")
    parser.add_argument("--topic", default=os.environ.get("CAMERA_IMAGE_TOPIC", "/camera/image_raw"))
    parser.add_argument("--status-file", default=os.environ.get("CAMERA_BRIDGE_STATUS_FILE", "/app/runtime/camera_bridge_status.json"))
    parser.add_argument("--status-interval-s", type=float, default=float(os.environ.get("CAMERA_BRIDGE_STATUS_INTERVAL_S", "0.5")))
    args = parser.parse_args()
    bridge_log("start", topic=args.topic, status_file=args.status_file)
    state = CameraState()
    stop = threading.Event()
    ready = threading.Event()
    threads = [
        threading.Thread(target=status_writer_thread, args=(state, Path(args.status_file), args.status_interval_s, stop), daemon=True),
        threading.Thread(target=ros_thread, args=(state, args.topic, ready, stop), daemon=True),
    ]
    for thread in threads:
        thread.start()
    ready.wait(timeout=8)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(serve_websocket(state, args.host, args.port, args.path))
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
