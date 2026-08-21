#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
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
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_server.camera_analysis import (
    LOCAL_VISION_ALGORITHM_NAME,
    LOCAL_VISION_ANALYSIS_REVISION,
    CameraOverlayStabilizer,
    analyze_camera_image,
    empty_camera_analysis,
)


VISION_BEARINGS_SCHEMA = "orchard.camera_bearings.v1"
VISION_TREE_CLASS = "orchard_tree_trunk"
DEFAULT_VISION_TOPIC = "/orchard/vision/tree_bearings"


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
    nanosec = max(0, min(999_999_999, int(stamp_nanosec)))
    return {"sec": sec, "nanosec": nanosec}, sec * 1_000_000_000 + nanosec


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def probability(value: Any, default: float | None = None) -> float | None:
    number = finite_number(value)
    if number is None:
        return default
    return max(0.0, min(1.0, number))


def sanitized_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    values = [finite_number(item) for item in value[:4]]
    if any(item is None for item in values):
        return None
    x, y, width, height = (float(item) for item in values)
    if width <= 0.0 or height <= 0.0:
        return None
    return [x, y, width, height]


def contour_points(value: Any, limit: int = 256) -> list[list[float]]:
    output: list[list[float]] = []

    def visit(item: Any) -> None:
        if len(output) >= limit or not isinstance(item, (list, tuple)):
            return
        if len(item) >= 2:
            x = finite_number(item[0])
            y = finite_number(item[1])
            if x is not None and y is not None:
                output.append([round(x, 3), round(y, 3)])
                return
        for child in item:
            visit(child)

    visit(value)
    return output


def camera_info_payload(msg: Any) -> dict[str, Any]:
    stamp, stamp_ns = ros_stamp_payload(msg.header.stamp.sec, msg.header.stamp.nanosec)
    raw_k = getattr(msg, "k", None)
    raw_d = getattr(msg, "d", None)
    return {
        "width": int(getattr(msg, "width", 0) or 0),
        "height": int(getattr(msg, "height", 0) or 0),
        # rclpy fixed-size arrays can expose NumPy-like containers whose
        # truth value is intentionally ambiguous. Never boolean-coerce them.
        "k": [float(value) for value in (list(raw_k) if raw_k is not None else [])[:9]],
        "d": [float(value) for value in (list(raw_d) if raw_d is not None else [])],
        "distortion_model": str(getattr(msg, "distortion_model", "") or ""),
        "frame_id": str(getattr(msg.header, "frame_id", "") or ""),
        "stamp": stamp,
        "stamp_ns": stamp_ns,
    }


def projection_intrinsics(
    image_width: int,
    camera_info: dict[str, Any] | None,
    nominal_fov_deg: float,
    calibration_valid: bool,
) -> dict[str, Any]:
    width = max(1, int(image_width))
    info = camera_info or {}
    info_width = int(info.get("width") or 0)
    k = info.get("k") or []
    fx = finite_number(k[0]) if len(k) >= 3 else None
    cx = finite_number(k[2]) if len(k) >= 3 else None
    if info_width > 0 and fx is not None and cx is not None and fx > 0.0:
        scale = width / info_width
        return {
            "fx_px": fx * scale,
            "cx_px": cx * scale,
            "source": "camera_info_operator_validated" if calibration_valid else "camera_info_nominal_or_unverified",
            "bearing_valid": bool(calibration_valid),
        }
    fov_deg = max(20.0, min(150.0, float(nominal_fov_deg)))
    fov_rad = math.radians(fov_deg)
    return {
        "fx_px": width / (2.0 * math.tan(fov_rad / 2.0)),
        "cx_px": width / 2.0,
        "source": f"nominal_fov_{fov_deg:g}deg_unverified",
        "bearing_valid": False,
    }


def vision_tree_bearings_payload(
    overlay: dict[str, Any] | None,
    *,
    stamp_sec: int,
    stamp_nanosec: int,
    frame_id: str = "camera_link",
    camera_info: dict[str, Any] | None = None,
    calibration_valid: bool = False,
    nominal_fov_deg: float = 70.0,
) -> dict[str, Any]:
    """Convert the shared worker-v4 overlay to the strict ROS bearing contract.

    Bearings use REP-103 convention (left-positive), so pixels right of cx are
    negative. CameraInfo is useful for the numeric pinhole projection but is
    unverified unless calibration_valid is explicitly enabled by the operator.
    No monocular detection is promoted to metric range.
    """

    stamp, stamp_ns = ros_stamp_payload(stamp_sec, stamp_nanosec)
    overlay = overlay if isinstance(overlay, dict) else {}
    image = overlay.get("image") if isinstance(overlay.get("image"), dict) else {}
    image_width = int(image.get("width") or 0)
    intrinsics = projection_intrinsics(image_width, camera_info, nominal_fov_deg, calibration_valid)
    contract_ok = (
        overlay.get("ok") is True
        and overlay.get("algorithm") == LOCAL_VISION_ALGORITHM_NAME
        and overlay.get("revision") == LOCAL_VISION_ANALYSIS_REVISION
        and image_width > 0
    )
    detections: list[dict[str, Any]] = []
    if contract_ok:
        fx = float(intrinsics["fx_px"])
        cx = float(intrinsics["cx_px"])
        for item in overlay.get("detections") or []:
            if len(detections) >= 64:
                break
            if not isinstance(item, dict):
                continue
            if str(item.get("map_admission") or "pending") != "accepted":
                continue
            source_classes = [str(item[key]) for key in ("class_name", "label") if item.get(key) is not None]
            if not source_classes or any(value != VISION_TREE_CLASS for value in source_classes):
                continue
            bbox = sanitized_bbox(item.get("bbox"))
            confidence = probability(item.get("confidence"))
            if bbox is None or confidence is None:
                continue
            center_u = bbox[0] + bbox[2] / 2.0
            if not (0.0 <= center_u <= float(image_width)):
                continue
            bearing_rad = -math.atan((center_u - cx) / fx)
            if not math.isfinite(bearing_rad) or not (-math.pi <= bearing_rad <= math.pi):
                continue
            tree_probability = probability(item.get("tree_probability"), confidence)
            ood_score = probability(item.get("ood_score"), 1.0)
            if tree_probability is None or ood_score is None:
                continue
            detection: dict[str, Any] = {
                "class_name": VISION_TREE_CLASS,
                "label": VISION_TREE_CLASS,
                "bearing_rad": round(bearing_rad, 8),
                "confidence": round(confidence, 6),
                "tree_probability": round(tree_probability, 6),
                "ood_score": round(ood_score, 6),
                "bbox": [round(value, 3) for value in bbox],
                "calibrated_metric": False,
            }
            contour = contour_points(item.get("contour"))
            if contour:
                detection["contour"] = contour
            detections.append(detection)

    error = ""
    if not contract_ok:
        error = str(overlay.get("error") or overlay.get("method") or "vision_worker_contract_invalid")
    return {
        "schema": VISION_BEARINGS_SCHEMA,
        "ok": bool(contract_ok),
        "algorithm": LOCAL_VISION_ALGORITHM_NAME,
        "revision": LOCAL_VISION_ANALYSIS_REVISION,
        "stamp": stamp,
        "stamp_ns": stamp_ns,
        "frame_id": str(frame_id or "camera_link"),
        "calibration": {
            "bearing_valid": bool(intrinsics["bearing_valid"]),
            "metric_range_valid": False,
        },
        "detections": detections if contract_ok else [],
        "source": "camera_ws_bridge_shared_worker_v4",
        "output": "bearing_only_no_monocular_metric_promotion",
        "bearing_convention": "REP-103 left-positive; pixel-right is negative",
        "intrinsics_source": intrinsics["source"],
        "error": error,
    }


def scale_overlay_geometry(overlay: dict[str, Any] | None, width: int, height: int) -> dict[str, Any]:
    if not isinstance(overlay, dict):
        return {}
    output = copy.deepcopy(overlay)
    image = output.get("image") if isinstance(output.get("image"), dict) else {}
    source_width = int(image.get("width") or width)
    source_height = int(image.get("height") or height)
    sx = width / max(1, source_width)
    sy = height / max(1, source_height)
    for detection in output.get("detections") or []:
        bbox = sanitized_bbox(detection.get("bbox"))
        if bbox is not None:
            detection["bbox"] = [
                int(round(bbox[0] * sx)),
                int(round(bbox[1] * sy)),
                int(round(bbox[2] * sx)),
                int(round(bbox[3] * sy)),
            ]
        contour = contour_points(detection.get("contour"))
        if contour:
            detection["contour"] = [[int(round(x * sx)), int(round(y * sy))] for x, y in contour]
        line = detection.get("line")
        if isinstance(line, (list, tuple)) and len(line) >= 4:
            numbers = [finite_number(value) for value in line[:4]]
            if all(value is not None for value in numbers):
                detection["line"] = [
                    int(round(float(numbers[0]) * sx)),
                    int(round(float(numbers[1]) * sy)),
                    int(round(float(numbers[2]) * sx)),
                    int(round(float(numbers[3]) * sy)),
                ]
    output["image"] = {**image, "width": int(width), "height": int(height)}
    return output


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
    camera_info: dict[str, Any] | None = None
    camera_info_count: int = 0
    vision_overlay: dict[str, Any] | None = None
    vision_payload: dict[str, Any] | None = None
    vision_generation: int = 0
    vision_processed_frame_count: int = 0
    vision_inference_count: int = 0
    vision_failure_count: int = 0
    vision_dropped_frame_count: int = 0
    vision_publish_count: int = 0
    vision_last_duration_ms: float | None = None
    vision_last_inference_at: float | None = None
    vision_last_publish_at: float | None = None
    client_count: int = 0
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update_frame(self, frame_id: str, stamp_sec: int, stamp_nanosec: int, frame_bgr: Any, encoding: str) -> None:
        with self.lock:
            self.image_frame_id = frame_id or "camera_link_optical"
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

    def update_camera_info(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.camera_info = copy.deepcopy(payload)
            self.camera_info_count += 1

    def inference_input_after(self, processed_frame_count: int) -> dict[str, Any] | None:
        with self.lock:
            if self.frame_bgr is None or self.frame_count <= processed_frame_count:
                return None
            return {
                "frame_bgr": self.frame_bgr.copy(),
                "frame_count": self.frame_count,
                "stamp_sec": self.stamp_sec,
                "stamp_nanosec": self.stamp_nanosec,
                "camera_info": copy.deepcopy(self.camera_info),
            }

    def update_vision(
        self,
        overlay: dict[str, Any],
        payload: dict[str, Any],
        processed_frame_count: int,
        duration_ms: float,
        skipped_frames: int,
    ) -> None:
        with self.lock:
            self.vision_overlay = copy.deepcopy(overlay)
            self.vision_payload = copy.deepcopy(payload)
            self.vision_generation += 1
            self.vision_processed_frame_count = processed_frame_count
            self.vision_inference_count += 1
            self.vision_dropped_frame_count += max(0, skipped_frames)
            self.vision_last_duration_ms = round(duration_ms, 1)
            self.vision_last_inference_at = time.time()
            if payload.get("ok") is not True:
                self.vision_failure_count += 1

    def pending_vision(self, last_generation: int) -> tuple[int, dict[str, Any] | None]:
        with self.lock:
            if self.vision_generation <= last_generation or self.vision_payload is None:
                return last_generation, None
            return self.vision_generation, copy.deepcopy(self.vision_payload)

    def mark_published(self) -> None:
        with self.lock:
            self.vision_publish_count += 1
            self.vision_last_publish_at = time.time()

    def set_error(self, message: str) -> None:
        with self.lock:
            self.error = message

    def adjust_clients(self, delta: int) -> None:
        with self.lock:
            self.client_count = max(0, self.client_count + delta)

    def snapshot(self, include_frame: bool = True) -> dict[str, Any]:
        with self.lock:
            frame = None if not include_frame or self.frame_bgr is None else self.frame_bgr.copy()
            age = None if self.last_frame_at is None else round(time.time() - self.last_frame_at, 3)
            stamp, stamp_ns = ros_stamp_payload(self.stamp_sec, self.stamp_nanosec)
            return {
                "frame_id": self.image_frame_id,
                "stamp": stamp,
                "stamp_ns": stamp_ns,
                "stamp_s": stamp_ns / 1_000_000_000,
                "frame_bgr": frame,
                "source_width": self.source_width,
                "source_height": self.source_height,
                "encoding": self.encoding,
                "last_frame_age_sec": age,
                "frame_count": self.frame_count,
                "source_fps": self.source_fps,
                "camera_info_count": self.camera_info_count,
                "vision_overlay": copy.deepcopy(self.vision_overlay),
                "vision_generation": self.vision_generation,
                "vision_processed_frame_count": self.vision_processed_frame_count,
                "vision_inference_count": self.vision_inference_count,
                "vision_failure_count": self.vision_failure_count,
                "vision_dropped_frame_count": self.vision_dropped_frame_count,
                "vision_publish_count": self.vision_publish_count,
                "vision_last_duration_ms": self.vision_last_duration_ms,
                "vision_last_inference_at": self.vision_last_inference_at,
                "vision_last_publish_at": self.vision_last_publish_at,
                "vision_detection_count": len((self.vision_payload or {}).get("detections") or []),
                "vision_ok": (self.vision_payload or {}).get("ok") is True,
                "vision_bearing_valid": bool(((self.vision_payload or {}).get("calibration") or {}).get("bearing_valid")),
                "vision_metric_range_valid": bool(((self.vision_payload or {}).get("calibration") or {}).get("metric_range_valid")),
                "vision_payload_schema": (self.vision_payload or {}).get("schema"),
                "vision_payload_algorithm": (self.vision_payload or {}).get("algorithm"),
                "vision_payload_revision": (self.vision_payload or {}).get("revision"),
                "vision_payload_source": (self.vision_payload or {}).get("source"),
                "vision_payload_frame_id": (self.vision_payload or {}).get("frame_id"),
                "vision_payload_error": (self.vision_payload or {}).get("error"),
                "client_count": self.client_count,
                "error": self.error,
            }


def frame_topic_alive(snapshot: dict[str, Any]) -> bool:
    age = snapshot.get("last_frame_age_sec")
    return age is not None and age < 2.0


def status_payload(
    state: CameraState,
    *,
    image_topic: str,
    camera_info_topic: str,
    vision_topic: str,
    vision_hz: float,
    calibration_valid: bool,
) -> dict[str, Any]:
    snap = state.snapshot(include_frame=False)
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
        "encoding": snap["encoding"],
        "frame_id": snap["frame_id"],
        "client_count": snap["client_count"],
        "topics": {
            "image": image_topic,
            "camera_info": camera_info_topic,
            "vision_bearings": vision_topic,
        },
        "vision_publisher": {
            "always_on": True,
            "latest_only": True,
            "target_hz": vision_hz,
            "schema": VISION_BEARINGS_SCHEMA,
            "algorithm": LOCAL_VISION_ALGORITHM_NAME,
            "revision": LOCAL_VISION_ANALYSIS_REVISION,
            "bearing_calibration_requested": bool(calibration_valid),
            "bearing_valid": snap["vision_bearing_valid"],
            "bearing_calibration_valid": snap["vision_bearing_valid"],
            "metric_range_valid": snap["vision_metric_range_valid"],
            "camera_info_count": snap["camera_info_count"],
            "inference_count": snap["vision_inference_count"],
            "failure_count": snap["vision_failure_count"],
            "dropped_frame_count": snap["vision_dropped_frame_count"],
            "publish_count": snap["vision_publish_count"],
            "last_duration_ms": snap["vision_last_duration_ms"],
            "last_inference_at": snap["vision_last_inference_at"],
            "last_publish_at": snap["vision_last_publish_at"],
            "detection_count": snap["vision_detection_count"],
            "ok": snap["vision_ok"],
            "last_payload": {
                "schema": snap["vision_payload_schema"],
                "algorithm": snap["vision_payload_algorithm"],
                "revision": snap["vision_payload_revision"],
                "source": snap["vision_payload_source"],
                "frame_id": snap["vision_payload_frame_id"],
                "ok": snap["vision_ok"],
                "error": snap["vision_payload_error"],
            },
        },
        "error": snap["error"] or (None if alive else f"ROS2 {image_topic} is not being received yet."),
    }


def write_status(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
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
    if height <= 0 or width <= 0 or not data:
        raise ValueError("empty image message")
    channels = 4 if encoding in {"bgra8", "rgba8"} else 3 if encoding in {"bgr8", "rgb8"} else 1
    if encoding not in {"bgr8", "rgb8", "bgra8", "rgba8", "mono8", "8uc1"}:
        raise ValueError(f"unsupported image encoding: {encoding}")
    row_bytes = width * channels
    step = int(getattr(msg, "step", 0) or row_bytes)
    if step < row_bytes or len(data) < height * step:
        raise ValueError("image message data is shorter than height*step")
    packed = np.frombuffer(data, dtype=np.uint8, count=height * step).reshape((height, step))[:, :row_bytes]
    arr = packed.reshape((height, width, channels)) if channels > 1 else packed.reshape((height, width))
    if encoding == "bgr8":
        return arr.copy()
    if encoding == "rgb8":
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    if encoding in {"bgra8", "rgba8"}:
        code = cv2.COLOR_BGRA2BGR if encoding == "bgra8" else cv2.COLOR_RGBA2BGR
        return cv2.cvtColor(arr, code)
    return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)


def vision_inference_thread(
    state: CameraState,
    stop: threading.Event,
    *,
    target_hz: float,
    inference_width: int,
    output_frame: str,
    calibration_valid: bool,
    nominal_fov_deg: float,
) -> None:
    interval_s = 1.0 / max(0.1, target_hz)
    stabilizer = CameraOverlayStabilizer()
    last_processed = 0
    next_at = time.monotonic()
    while not stop.is_set():
        wait_s = next_at - time.monotonic()
        if wait_s > 0.0 and stop.wait(min(wait_s, 0.1)):
            break
        if time.monotonic() < next_at:
            continue
        sample = state.inference_input_after(last_processed)
        if sample is None:
            stop.wait(0.02)
            continue
        frame_count = int(sample["frame_count"])
        frame = sample["frame_bgr"]
        skipped = max(0, frame_count - last_processed - 1) if last_processed else 0
        started = time.perf_counter()
        try:
            source_height, source_width = frame.shape[:2]
            width = min(max(160, int(inference_width)), max(160, int(source_width)))
            height = max(1, int(round(source_height * width / max(1, source_width))))
            inference_frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA) if width != source_width else frame
            stamp_s = int(sample["stamp_sec"]) + int(sample["stamp_nanosec"]) / 1_000_000_000
            overlay = stabilizer.update(analyze_camera_image(inference_frame), stamp_s or time.time())
            if not isinstance(overlay.get("image"), dict):
                overlay["image"] = {}
            overlay["image"].setdefault("width", width)
            overlay["image"].setdefault("height", height)
        except Exception as exc:
            overlay = empty_camera_analysis("vision_loop_exception", str(exc))
            bridge_log("vision_inference_error", error=str(exc))
        payload = vision_tree_bearings_payload(
            overlay,
            stamp_sec=sample["stamp_sec"],
            stamp_nanosec=sample["stamp_nanosec"],
            frame_id=output_frame,
            camera_info=sample["camera_info"],
            calibration_valid=calibration_valid,
            nominal_fov_deg=nominal_fov_deg,
        )
        duration_ms = (time.perf_counter() - started) * 1000.0
        state.update_vision(overlay, payload, frame_count, duration_ms, skipped)
        last_processed = frame_count
        next_at = max(next_at + interval_s, time.monotonic())
        if state.vision_inference_count == 1 or state.vision_inference_count % max(1, int(target_hz * 10)) == 0:
            bridge_log(
                "vision_inference",
                count=state.vision_inference_count,
                ok=payload["ok"],
                detections=len(payload["detections"]),
                duration_ms=round(duration_ms, 1),
                dropped=state.vision_dropped_frame_count,
            )


def ros_thread(
    state: CameraState,
    image_topic: str,
    camera_info_topic: str,
    vision_topic: str,
    ready: threading.Event,
    stop: threading.Event,
) -> None:
    try:
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
        from sensor_msgs.msg import CameraInfo, Image
        from std_msgs.msg import String
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
            super().__init__("ubts_camera_ws_bridge")
            self.create_subscription(Image, image_topic, self.on_image, sensor_qos)
            self.create_subscription(CameraInfo, camera_info_topic, self.on_camera_info, sensor_qos)
            self.vision_publisher = self.create_publisher(String, vision_topic, 10)
            self.create_timer(0.02, self.publish_latest_vision)
            self.last_published_generation = 0
            self.last_log_at = 0.0
            bridge_log(
                "ros_ready",
                image_topic=image_topic,
                camera_info_topic=camera_info_topic,
                vision_topic=vision_topic,
                subscription_qos="best_effort_keep_last_1",
            )

        def on_image(self, msg: Any) -> None:
            try:
                frame = image_to_bgr(msg)
            except Exception as exc:
                state.set_error(str(exc))
                return
            state.update_frame(msg.header.frame_id, msg.header.stamp.sec, msg.header.stamp.nanosec, frame, msg.encoding)
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

        def publish_latest_vision(self) -> None:
            generation, payload = state.pending_vision(self.last_published_generation)
            if payload is None:
                return
            message = String()
            message.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            self.vision_publisher.publish(message)
            self.last_published_generation = generation
            state.mark_published()

    rclpy.init()
    node = BridgeNode()
    ready.set()
    try:
        while rclpy.ok() and not stop.is_set():
            rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def client_settings(actual_path: str) -> dict[str, int]:
    params = parse_qs(urlparse(actual_path).query)
    resolution = (params.get("resolution") or ["1280x720"])[0]
    try:
        width_text, height_text = str(resolution).lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except (ValueError, TypeError):
        width, height = 1280, 720
    allowed = {(320, 240), (640, 480), (800, 600), (1024, 768), (1280, 720), (1280, 1024), (1920, 1080)}
    if (width, height) not in allowed:
        width, height = 1280, 720
    try:
        fps = int((params.get("fps") or ["30"])[0])
    except (TypeError, ValueError):
        fps = 15
    try:
        quality = int((params.get("quality") or ["75"])[0])
    except (TypeError, ValueError):
        quality = 75
    return {
        "width": width,
        "height": height,
        "fps": max(1, min(60, fps)),
        "quality": max(35, min(92, quality)),
    }


async def serve_websocket(state: CameraState, host: str, port: int, path: str, stop: threading.Event) -> None:
    try:
        import websockets
    except ImportError as exc:
        raise SystemExit("Install the optional 'websockets' package on Jetson to run camera_ws_bridge.py") from exc

    async def handler(websocket: Any, legacy_path: str | None = None) -> None:
        request = getattr(websocket, "request", None)
        actual_path = getattr(request, "path", path) if request is not None else legacy_path or getattr(websocket, "path", path)
        parsed = urlparse(actual_path)
        if parsed.path != path:
            await websocket.close(code=1008, reason="unsupported path")
            return
        settings = client_settings(actual_path)
        interval = 1.0 / settings["fps"]
        state.adjust_clients(1)
        bridge_log("client_open", path=actual_path, **settings)
        try:
            await websocket.send(json.dumps({"type": "socket", "ok": True, "message": "camera websocket connected"}, separators=(",", ":")))
            while not stop.is_set():
                snap = state.snapshot()
                alive = frame_topic_alive(snap)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "status",
                            "frame_topic_alive": alive,
                            "last_frame_age_sec": snap["last_frame_age_sec"],
                            "frame_count": snap["frame_count"],
                            "source_fps": snap["source_fps"],
                            "source_width": snap["source_width"],
                            "source_height": snap["source_height"],
                            "frame_id": snap["frame_id"],
                            "encoding": snap["encoding"],
                            "requested_width": settings["width"],
                            "requested_height": settings["height"],
                            "requested_fps": settings["fps"],
                            "vision_generation": snap["vision_generation"],
                            "vision_inference_count": snap["vision_inference_count"],
                            "vision_detection_count": snap["vision_detection_count"],
                            "vision_shared_inference": True,
                            "error": snap["error"] or (None if alive else "ROS2 camera image is not being received yet."),
                        },
                        separators=(",", ":"),
                    )
                )
                frame = snap["frame_bgr"]
                if frame is not None:
                    out = cv2.resize(frame, (settings["width"], settings["height"]), interpolation=cv2.INTER_AREA)
                    overlay = scale_overlay_geometry(snap["vision_overlay"], settings["width"], settings["height"])
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
                                    "stamp": snap["stamp_s"],
                                    "frame_count": snap["frame_count"],
                                    "source_fps": snap["source_fps"],
                                    "overlay": overlay,
                                },
                                separators=(",", ":"),
                            )
                        )
                await asyncio.sleep(interval)
        except Exception as exc:
            connection_closed = getattr(getattr(websockets, "exceptions", None), "ConnectionClosed", None)
            if connection_closed is None or not isinstance(exc, connection_closed):
                bridge_log("handler_error", error=str(exc))
        finally:
            state.adjust_clients(-1)
            bridge_log("client_closed")

    bridge_log("ws_listen", host=host, port=port, path=path)
    async with websockets.serve(handler, host, port, max_size=8 * 1024 * 1024):
        while not stop.is_set():
            await asyncio.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge ROS2 Image/CameraInfo to WebSocket and strict tree-bearing ROS JSON.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--path", default="/ws/camera")
    parser.add_argument("--topic", default=os.environ.get("CAMERA_IMAGE_TOPIC", "/camera/image_raw"))
    parser.add_argument("--camera-info-topic", default=os.environ.get("CAMERA_INFO_TOPIC", "/camera/camera_info"))
    parser.add_argument("--vision-topic", default=os.environ.get("ORCHARD_VISION_BEARINGS_TOPIC", DEFAULT_VISION_TOPIC))
    parser.add_argument("--vision-hz", type=float, default=float(os.environ.get("ORCHARD_VISION_PUBLISH_HZ", "5.0")))
    parser.add_argument("--vision-width", type=int, default=int(os.environ.get("ORCHARD_VISION_WIDTH", "640")))
    parser.add_argument("--vision-output-frame", default=os.environ.get("ORCHARD_VISION_FRAME_ID", "camera_link"))
    parser.add_argument("--vision-nominal-fov-deg", type=float, default=float(os.environ.get("CAMERA_FOV_DEG", "70.0")))
    parser.add_argument(
        "--bearing-calibration-valid",
        default=env_bool("ORCHARD_VISION_BEARING_CALIBRATION_VALID", False),
        action=argparse.BooleanOptionalAction,
        help="Enable only after camera intrinsics have been independently validated; CameraInfo presence alone is insufficient.",
    )
    parser.add_argument("--status-file", default=os.environ.get("CAMERA_BRIDGE_STATUS_FILE", "/app/runtime/camera_bridge_status.json"))
    parser.add_argument("--status-interval-s", type=float, default=float(os.environ.get("CAMERA_BRIDGE_STATUS_INTERVAL_S", "0.5")))
    args = parser.parse_args()
    args.vision_hz = max(0.1, min(30.0, args.vision_hz))
    args.vision_width = max(160, min(1920, args.vision_width))
    bridge_log(
        "start",
        image_topic=args.topic,
        camera_info_topic=args.camera_info_topic,
        vision_topic=args.vision_topic,
        vision_hz=args.vision_hz,
        vision_width=args.vision_width,
        output_frame=args.vision_output_frame,
        bearing_calibration_valid=bool(args.bearing_calibration_valid),
        metric_range_valid=False,
        status_file=args.status_file,
    )
    state = CameraState()
    stop = threading.Event()
    ready = threading.Event()
    status_kwargs = {
        "image_topic": args.topic,
        "camera_info_topic": args.camera_info_topic,
        "vision_topic": args.vision_topic,
        "vision_hz": args.vision_hz,
        "calibration_valid": bool(args.bearing_calibration_valid),
    }
    threads = [
        threading.Thread(
            target=status_writer_thread,
            args=(state, Path(args.status_file), args.status_interval_s, stop, status_kwargs),
            daemon=True,
        ),
        threading.Thread(
            target=vision_inference_thread,
            args=(state, stop),
            kwargs={
                "target_hz": args.vision_hz,
                "inference_width": args.vision_width,
                "output_frame": args.vision_output_frame,
                "calibration_valid": bool(args.bearing_calibration_valid),
                "nominal_fov_deg": args.vision_nominal_fov_deg,
            },
            daemon=True,
        ),
        threading.Thread(
            target=ros_thread,
            args=(state, args.topic, args.camera_info_topic, args.vision_topic, ready, stop),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    ready.wait(timeout=8)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        loop.run_until_complete(serve_websocket(state, args.host, args.port, args.path, stop))
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
