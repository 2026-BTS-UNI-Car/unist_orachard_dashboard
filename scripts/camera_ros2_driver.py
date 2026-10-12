#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import glob
import json
import math
import os
import re
import subprocess
import sys
import time
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
import rclpy
from scripts.camera_cadence import CameraCadence
from scripts.camera_mjpeg import (PREVIEW_DECODE_REDUCTIONS, JpegFrame, StageCounters, decoded_image, jpeg_frame,
                                  one_row_uint8, original_jpeg_format, preview_reduction, reencoded_jpeg_format)
from scripts.camera_preview_worker import LatestCameraWorker
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CameraInfo, CompressedImage, Image


USBDEVFS_RESET = 21780
VIDEO_CLASS = "0e"
DEFAULT_VIEW_MODES = [
    {"resolution": "1920x1080", "width": 1920, "height": 1080, "fps": [30], "label": "1080p 16:9"},
    {"resolution": "1280x720", "width": 1280, "height": 720, "fps": [60], "label": "720p 16:9"},
    {"resolution": "1024x768", "width": 1024, "height": 768, "fps": [30], "label": "768p 4:3"},
    {"resolution": "800x600", "width": 800, "height": 600, "fps": [60], "label": "600p 4:3"},
    {"resolution": "640x480", "width": 640, "height": 480, "fps": [30], "label": "480p 4:3"},
    {"resolution": "320x240", "width": 320, "height": 240, "fps": [30], "label": "240p 4:3"},
    {"resolution": "1280x1024", "width": 1280, "height": 1024, "fps": [30], "label": "1024p 5:4"},
]
DEFAULT_MODE_BY_RESOLUTION = {item["resolution"]: item for item in DEFAULT_VIEW_MODES}
DECODE_FLAGS = {1: cv2.IMREAD_COLOR, 2: cv2.IMREAD_REDUCED_COLOR_2, 4: cv2.IMREAD_REDUCED_COLOR_4}
USB_DEVICE_CACHE_S = 10.0
# Passthrough -> decoded fallback is a session decision driven only by evidence
# that the backend cannot pass JPEG through: an open whose buffer is not one
# uint8 row or whose reads never carry SOI, a streaming reopen caused mostly by
# invalid passthrough buffers, or a run of opens that deliver buffers but never
# a valid frame. Read failures and truncated/padded JPEGs alone are not such
# evidence. A sustained run of valid frames clears it. An engaged fallback is
# retried on a later reopen after PASSTHROUGH_FALLBACK_RETRY_S, and at once on
# any camera settings-file write. Fallback keeps the passthrough orientation
# contract, so a switch inside a recording changes payload provenance only.
PASSTHROUGH_FALLBACK_AFTER = 3
PASSTHROUGH_VALID_RUN_RESET = 60
PASSTHROUGH_INVALID_OPENS_PER_EVIDENCE = 5
PASSTHROUGH_FALLBACK_RETRY_S = 300.0
PASSTHROUGH_PALETTES = frozenset({"MJPG", "JPEG"})  # OpenCV returns these unconverted as one row.
OPEN_JPEG_READS = 3
READ_FAILURES_BEFORE_REOPEN = 5
# Passthrough acquisition is paced by the blocking V4L2 read, not by cadence
# slots. A buffer whose driver timestamp is older than this many frame periods
# was queued, so the same tick reads again and drains the backlog.
PASSTHROUGH_MAX_READS_PER_TICK = 4  # OpenCV's V4L2 buffer count.
PASSTHROUGH_BACKLOG_AGE_PERIODS = 1.5
# CAP_PROP_POS_MSEC is the dequeued buffer's timestamp (uvcvideo default
# CLOCK_MONOTONIC). Outside this age window it is not trusted for the stamp.
V4L2_STAMP_MIN_AGE_S = -0.005
V4L2_STAMP_MAX_AGE_S = 2.0


class OpenProbe(NamedTuple):
    kind: str  # ok | size_mismatch | unsupported | invalid | read_failed
    jpeg: Any = None
    stamp: Any = None
    reason: Any = None


class CaptureStamp(NamedTuple):
    msg: Any
    source: str  # v4l2_buffer_monotonic | read_completion
    age_s: Any  # Buffer age when read (s), None without a trusted timestamp.


def fourcc_text(value: Any) -> str | None:
    """Printable FOURCC of an OpenCV CAP_PROP_FOURCC value, else None."""
    try:
        code = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not 0 < code < 1 << 32:
        return None
    text = bytes((code >> shift) & 0xFF for shift in (0, 8, 16, 24))
    return text.decode("ascii") if all(32 <= byte < 127 for byte in text) else None


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"ts": round(time.time(), 3), "event": f"camera_driver.{event}", **fields}, ensure_ascii=False), flush=True)


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def write_json_atomic(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log("status_write_error", path=str(path), error=str(exc))


def load_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def normalize_mode(width: Any, height: Any, fps: Any, fourcc: Any) -> dict[str, Any] | None:
    try:
        normalized_width = int(width)
        normalized_height = int(height)
        normalized_fps = float(fps)
    except (TypeError, ValueError):
        return None
    if normalized_width <= 0 or normalized_height <= 0 or not math.isfinite(normalized_fps) or normalized_fps < 1:
        return None
    rounded_fps: int | float = int(round(normalized_fps)) if abs(normalized_fps - round(normalized_fps)) < 0.05 else round(normalized_fps, 2)
    normalized_fourcc = str(fourcc or "MJPG").strip().upper()[:4] or "MJPG"
    return {
        "resolution": f"{normalized_width}x{normalized_height}",
        "width": normalized_width,
        "height": normalized_height,
        "fps": rounded_fps,
        "fourcc": normalized_fourcc,
    }


def preferred_mode_for_resolution(resolution: str) -> dict[str, Any] | None:
    return DEFAULT_MODE_BY_RESOLUTION.get(str(resolution or "").lower())


def candidate_devices(configured: str) -> Iterable[str]:
    if configured and configured != "auto":
        yield configured
    for pattern in (
        "/dev/v4l/by-id/*Camera*",
        "/dev/v4l/by-id/*camera*",
        "/dev/v4l/by-id/*USB*",
        "/dev/video*",
    ):
        for path in sorted(glob.glob(pattern)):
            yield path


def video_nodes() -> list[str]:
    return sorted(glob.glob("/dev/video*"))


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def usb_sys_roots() -> list[Path]:
    roots = []
    for raw in (os.environ.get("HOST_USB_SYS"), "/host-sys/bus/usb/devices", "/sys/bus/usb/devices"):
        if not raw:
            continue
        path = Path(raw)
        if path.exists() and path not in roots:
            roots.append(path)
    return roots


def usb_video_devices() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in usb_sys_roots():
        for device in sorted(root.iterdir()):
            if ":" in device.name or not (device / "idVendor").exists():
                continue
            interfaces = []
            for interface in sorted(root.glob(f"{device.name}:*")):
                iface_class = (read_text(interface / "bInterfaceClass") or "").lower()
                if iface_class != VIDEO_CLASS:
                    continue
                driver = None
                driver_link = interface / "driver"
                try:
                    if driver_link.is_symlink():
                        driver = driver_link.resolve().name
                except OSError:
                    driver = None
                interfaces.append(
                    {
                        "name": interface.name,
                        "class": iface_class,
                        "subclass": read_text(interface / "bInterfaceSubClass"),
                        "protocol": read_text(interface / "bInterfaceProtocol"),
                        "driver": driver,
                    }
                )
            if not interfaces:
                continue
            key = f"{read_text(device / 'busnum')}:{read_text(device / 'devnum')}:{read_text(device / 'idVendor')}:{read_text(device / 'idProduct')}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "sys_name": device.name,
                    "vendor_id": read_text(device / "idVendor"),
                    "product_id": read_text(device / "idProduct"),
                    "manufacturer": read_text(device / "manufacturer"),
                    "product": read_text(device / "product"),
                    "busnum": read_text(device / "busnum"),
                    "devnum": read_text(device / "devnum"),
                    "devpath": read_text(device / "devpath"),
                    "speed_mbps": read_text(device / "speed"),
                    "authorized": read_text(device / "authorized"),
                    "interfaces": interfaces,
                    "sys_path": str(device),
                }
            )
    return rows


def run_quiet(cmd: list[str], timeout: float = 2.5) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def list_camera_modes(device: str) -> list[dict[str, Any]]:
    result = run_quiet(["v4l2-ctl", "--list-formats-ext", "-d", device], timeout=4.0)
    if result is None or result.returncode != 0:
        return []
    modes: dict[str, set[float]] = {}
    current_resolution: str | None = None
    for line in result.stdout.splitlines():
        size = re.search(r"Size:\s+Discrete\s+(\d+)x(\d+)", line)
        if size:
            current_resolution = f"{int(size.group(1))}x{int(size.group(2))}"
            modes.setdefault(current_resolution, set())
            continue
        fps = re.search(r"\((\d+(?:\.\d+)?)\s+fps\)", line)
        if fps and current_resolution:
            value = float(fps.group(1))
            if value >= 1:
                rounded = round(value)
                modes[current_resolution].add(float(rounded) if abs(value - rounded) < 0.05 else round(value, 2))
    rows = []
    for resolution, fps_values in modes.items():
        width, height = (int(part) for part in resolution.split("x", 1))
        preferred = preferred_mode_for_resolution(resolution)
        preferred_fps = preferred.get("fps") if preferred else None
        rows.append(
            {
                "resolution": resolution,
                "width": width,
                "height": height,
                "fps": list(preferred_fps or sorted(fps_values) or [30]),
                "label": preferred.get("label") if preferred else "",
            }
        )
    return sorted(rows, key=lambda item: (item["width"] * item["height"], item["width"]))


def usbdevfs_reset(device: dict[str, Any]) -> dict[str, Any]:
    busnum = str(device.get("busnum") or "").zfill(3)
    devnum = str(device.get("devnum") or "").zfill(3)
    path = Path("/dev/bus/usb") / busnum / devnum
    if not path.exists():
        return {"ok": False, "method": "usbdevfs_reset", "path": str(path), "error": "usb device node not found"}
    try:
        fd = os.open(path, os.O_WRONLY)
        try:
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
        finally:
            os.close(fd)
        return {"ok": True, "method": "usbdevfs_reset", "path": str(path)}
    except OSError as exc:
        return {"ok": False, "method": "usbdevfs_reset", "path": str(path), "error": str(exc)}


def authorized_cycle(device: dict[str, Any]) -> dict[str, Any]:
    auth = Path(str(device.get("sys_path") or "")) / "authorized"
    if not auth.exists():
        return {"ok": False, "method": "authorized_cycle", "path": str(auth), "error": "authorized control not found"}
    try:
        auth.write_text("0", encoding="utf-8")
        time.sleep(1.5)
        auth.write_text("1", encoding="utf-8")
        return {"ok": True, "method": "authorized_cycle", "path": str(auth)}
    except OSError as exc:
        return {"ok": False, "method": "authorized_cycle", "path": str(auth), "error": str(exc)}


def estimate_camera_info(width: int, height: int, frame_id: str, fov_deg: float = 70.0) -> CameraInfo:
    msg = CameraInfo()
    msg.width = width
    msg.height = height
    msg.header.frame_id = frame_id
    focal = width / (2.0 * math.tan(math.radians(fov_deg) / 2.0))
    cx = width / 2.0
    cy = height / 2.0
    msg.distortion_model = "plumb_bob"
    msg.d = [0.0, 0.0, 0.0, 0.0, 0.0]
    msg.k = [focal, 0.0, cx, 0.0, focal, cy, 0.0, 0.0, 1.0]
    msg.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    msg.p = [focal, 0.0, cx, 0.0, 0.0, focal, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
    return msg


class UsbCameraDriver(Node):
    # Capture-path defaults until an open decides; a capture without
    # CONVERT_RGB=0 hands every frame to the legacy decoded path unchanged.
    capture_mode: str | None = "legacy"
    mode_reason: str | None = None
    preview_effective: dict[str, Any] | None = None
    convert_rgb_disabled: bool | None = None
    fail_invalid = 0  # Invalid passthrough buffers inside the current failure streak.
    valid_run = 0  # Consecutive valid passthrough frames.
    passthrough_evidence = 0
    invalid_open_run = 0
    passthrough_fallback: dict[str, Any] | None = None
    last_buffer_ns: int | None = None

    @cached_property
    def counters(self) -> StageCounters:
        return StageCounters()

    def __init__(self, args):
        super().__init__("ubts_usb_camera_driver")
        self.args = args
        self.frame_id = args.frame_id
        self.status_file = Path(args.status_file) if args.status_file else None
        self.settings_file = Path(args.settings_file) if args.settings_file else None
        initial_mode = normalize_mode(args.width, args.height, args.fps, args.fourcc) or normalize_mode(1280, 720, 30, "MJPG")
        assert initial_mode is not None
        self.request_width = int(initial_mode["width"])
        self.request_height = int(initial_mode["height"])
        self.request_fps = float(initial_mode["fps"])
        self.request_fourcc = str(initial_mode["fourcc"])
        self.capture_cadence = CameraCadence(self.request_fps)
        self.last_read_ms: float | None = None
        self.last_publish_work_ms: float | None = None
        self.settings_mtime_ns: int | None = None
        self.next_settings_check_at = 0.0
        # Camera images are real-time sensor samples, not a reliable work queue.
        # KEEP_LAST(1) + BEST_EFFORT prevents multi-megabyte raw frames from
        # accumulating behind a slow dashboard/recorder subscriber.
        sensor_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.image_pub = self.create_publisher(Image, args.image_topic, sensor_qos)
        self.compressed_pub = self.create_publisher(CompressedImage, args.compressed_topic, sensor_qos)
        self.recording_pub = self.create_publisher(CompressedImage, args.recording_topic, sensor_qos)
        from scripts.native_camera_encoder import NativeCameraEncoder
        self.native_encoder = NativeCameraEncoder(self.publish_native, args.recording_jpeg_quality)
        self.info_pub = self.create_publisher(CameraInfo, args.camera_info_topic, sensor_qos)
        self.preview_info_pub = self.create_publisher(CameraInfo, args.preview_camera_info_topic, sensor_qos)
        self.cap: cv2.VideoCapture | None = None
        self.device = ""
        self.actual_width: int | None = None
        self.actual_height: int | None = None
        self.actual_fps: float | None = None
        self.fail_count = 0
        self.frame_count = 0
        self.raw_frame_count = 0
        self.compressed_frame_count = 0
        self.last_raw_publish_at = 0.0
        self.last_raw_submitted_at = 0.0
        self.raw_submit_count = 0
        self.last_compressed_encode_ms: float | None = None
        self.last_error: str | None = None
        self.last_recovery: dict[str, Any] | None = None
        self.recovery_count = 0
        self.supported_modes: list[dict[str, Any]] = []
        self.next_open_attempt_at = 0.0
        self.next_recovery_at = 0.0
        self.last_wait_log_at = 0.0
        self.last_status_write_at = 0.0
        self.last_publish_at = 0.0
        self.counters = StageCounters()  # Before any worker thread exists.
        self.capture_mode = None  # Decided per open: passthrough | fallback | legacy.
        self.convert_rgb_disabled = None
        self.passthrough_evidence = 0
        self.passthrough_evidence_reasons: dict[str, int] = {}
        self.passthrough_fallback = None
        self.invalid_open_run = 0
        self.valid_run = 0
        self.fail_invalid = 0
        self.last_invalid_reason: str | None = None
        self.last_buffer_ns = None
        self.stamp_sources = {"v4l2_buffer_monotonic": 0, "read_completion": 0}
        self.last_stamp: dict[str, Any] = {"source": None, "buffer_age_ms": None, "untrusted_reason": None}
        self.usb_devices_cache: list[dict[str, Any]] | None = None
        self.usb_devices_cached_at = 0.0
        self.preview_worker = LatestCameraWorker(self.process_preview_frame)
        self.apply_settings_file(force=True)
        self.timer = self.create_timer(1.0 / max(1.0, args.max_tick_fps), self.tick)
        log(
            "start",
            image_topic=args.image_topic,
            compressed_topic=args.compressed_topic,
            camera_info_topic=args.camera_info_topic,
            preview_camera_info_topic=args.preview_camera_info_topic,
            width=self.request_width,
            height=self.request_height,
            fps=self.request_fps,
            frame_id=args.frame_id,
            rotate_180=args.rotate_180,
            publisher_qos="best_effort_keep_last_1",
            raw_publish_fps=args.raw_publish_fps,
            compressed_jpeg_quality=args.compressed_jpeg_quality,
            compressed_preview_width=args.compressed_preview_width,
            mjpeg_passthrough=args.mjpeg_passthrough,
            preview_decode_reduction=args.preview_decode_reduction,
            auto_usb_recover=args.auto_usb_recover,
            status_file=args.status_file,
            settings_file=args.settings_file,
        )
        self.write_status("start", force=True)

    def state_name(self, usb_devices: list[dict[str, Any]] | None = None) -> str:
        if self.cap is not None:
            return "streaming"
        if (usb_video_devices() if usb_devices is None else usb_devices) and not video_nodes():
            return "usb_detected_video_node_missing"
        if video_nodes():
            return "waiting_for_open"
        return "not_found"

    def cached_usb_video_devices(self, force: bool = False) -> list[dict[str, Any]]:
        # The sysfs walk ran on the capture thread every status write. Forced
        # writes (open/reopen/recovery/settings transitions) still rescan.
        now = time.monotonic()
        if force or self.usb_devices_cache is None or now - self.usb_devices_cached_at >= USB_DEVICE_CACHE_S:
            self.usb_devices_cache = usb_video_devices()
            self.usb_devices_cached_at = now
        return self.usb_devices_cache

    def set_capture_mode(self, mode: str, reason: str | None) -> None:
        if (mode, reason) != (self.capture_mode, self.mode_reason):
            log("capture_mode", mode=mode, previous=self.capture_mode, reason=reason, device=self.device or None)
        self.capture_mode = mode
        self.mode_reason = reason

    def passthrough_configured(self) -> bool:
        """The session's recording orientation contract (process flag, never fourcc/mode).

        When configured, /camera/recording/jpeg always carries sensor-orientation
        pixels with declared rotation (passthrough and decoded fallback alike).
        When not, the legacy contract (pixels rotated, no tag) is unchanged.
        """
        return bool(getattr(getattr(self, "args", None), "mjpeg_passthrough", False))

    def passthrough_requested(self) -> bool:
        return self.passthrough_configured() and self.request_fourcc == "MJPG"

    def recording_payload(self) -> tuple[str | None, str | None, int | None]:
        """(payload kind, pixel orientation, declared rotation) of the open capture."""
        if self.cap is None or self.capture_mode is None:
            return None, None, None
        rotation = 180 if self.args.rotate_180 else 0
        if self.capture_mode == "passthrough":
            return "original_camera_mjpeg", "sensor_pixels_rotation_declared", rotation
        if self.capture_mode == "fallback":
            return f"reencoded_bgr_q{self.native_encoder.quality}", "sensor_pixels_rotation_declared", rotation
        return "legacy_reencoded_bgr_jpeg", "pixels_rotated_by_driver_untagged", None

    def write_status(self, event: str, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_status_write_at < self.args.status_interval_s:
            return
        self.last_status_write_at = now
        nodes = video_nodes()
        usb_devices = self.cached_usb_video_devices(force)
        recording_payload, recording_orientation, recording_rotation = self.recording_payload()
        fallback = self.passthrough_fallback
        mono = time.monotonic()
        payload = {
            "type": "camera_driver_status",
            "updated_at": now,
            "event": event,
            "state": self.state_name(usb_devices),
            "present": bool(nodes),
            "usb_present": bool(usb_devices),
            "opened": self.cap is not None,
            "device": self.device or None,
            "video_nodes": nodes,
            "usb_devices": usb_devices,
            "requested": {
                "width": self.request_width,
                "height": self.request_height,
                "fps": self.request_fps,
                "fourcc": self.request_fourcc,
                "rotate_180": bool(self.args.rotate_180),
            },
            "actual": {
                "width": self.actual_width,
                "height": self.actual_height,
                "fps": self.actual_fps,
            },
            "supported_view_modes": self.supported_modes,
            "default_view_modes": DEFAULT_VIEW_MODES,
            "settings_file": str(self.settings_file) if self.settings_file else None,
            "frame_count": self.frame_count,
            "raw_frame_count": self.raw_frame_count,
            "compressed_frame_count": self.compressed_frame_count,
            "raw_publish_fps": self.args.raw_publish_fps,
            "compressed_topic": self.args.compressed_topic,
                "recording_topic": self.args.recording_topic,
            "native_recording": self.native_encoder.status(),
            "preview_worker": self.preview_worker.status(),
            "preview_camera_info_topic": self.args.preview_camera_info_topic,
            "compressed_jpeg_quality": self.args.compressed_jpeg_quality,
            "compressed_preview_width": self.args.compressed_preview_width,
            "last_compressed_encode_ms": self.last_compressed_encode_ms,
            "mode": self.capture_mode,
            "mjpeg_passthrough": {
                "requested": self.passthrough_configured(),
                "eligible": self.passthrough_requested(),
                # True only while an open capture records original camera JPEG.
                "active": self.cap is not None and self.capture_mode == "passthrough",
                "convert_rgb_disabled": self.convert_rgb_disabled,
                "mode_reason": self.mode_reason,
                "fallback": None if fallback is None else {
                    "reason": fallback["reason"],
                    "engaged_at": fallback["engaged_at"],
                    "engaged_s": round(mono - fallback["engaged_monotonic"], 1),
                    "retry_on_reopen_in_s": round(max(0.0, fallback["engaged_monotonic"]
                                                      + PASSTHROUGH_FALLBACK_RETRY_S - mono), 1),
                    "clear_by": "camera_settings_file_write_or_reopen_after_retry",
                },
                "fallback_evidence_streak": self.passthrough_evidence,
                "fallback_after": PASSTHROUGH_FALLBACK_AFTER,
                "fallback_evidence_reasons": dict(self.passthrough_evidence_reasons),
                "invalid_open_run": self.invalid_open_run,
                "valid_run": self.valid_run,
                "valid_run_clears_evidence_after": PASSTHROUGH_VALID_RUN_RESET,
                "last_invalid_reason": self.last_invalid_reason,
                "recording_payload": recording_payload,
                "recording_orientation": recording_orientation,
                "recording_rotation_deg": recording_rotation,
            },
            "preview": {
                "requested_width": self.args.compressed_preview_width,
                "decode_reduction_setting": getattr(self.args, "preview_decode_reduction", None),
                **(self.preview_effective or {"effective_width": None, "effective_height": None, "decode_reduction": None}),
            },
            "capture_pacing": None if self.cap is None else (
                "blocking_read_drain" if self.capture_mode == "passthrough" else "cadence_slots"),
            "capture_timestamp": {
                "policy": ("v4l2_buffer_timestamp_to_ros_time_else_read_completion" if self.passthrough_configured()
                           else "legacy_publish_time"),
                "sources": dict(self.stamp_sources),
                **self.last_stamp,
            },
            "stage_counters": self.counters.status(),
            "capture_cadence": self.capture_cadence.status(),
            "last_read_ms": self.last_read_ms,
            "last_publish_work_ms": self.last_publish_work_ms,
            "fail_count": self.fail_count,
            "last_error": self.last_error,
            "last_recovery": self.last_recovery,
            "recovery_count": self.recovery_count,
        }
        write_json_atomic(self.status_file, payload)

    def current_mode(self) -> dict[str, Any]:
        return {
            "resolution": f"{self.request_width}x{self.request_height}",
            "width": self.request_width,
            "height": self.request_height,
            "fps": int(self.request_fps) if abs(self.request_fps - round(self.request_fps)) < 0.05 else round(self.request_fps, 2),
            "fourcc": self.request_fourcc,
        }

    def write_settings_file_if_missing(self) -> None:
        if self.settings_file is None or self.settings_file.exists():
            return
        payload = {**self.current_mode(), "updated_at": time.time(), "source": "driver_default"}
        write_json_atomic(self.settings_file, payload)
        try:
            self.settings_mtime_ns = self.settings_file.stat().st_mtime_ns
        except OSError:
            self.settings_mtime_ns = None

    def apply_settings_file(self, force: bool = False) -> bool:
        if self.settings_file is None:
            return False
        self.write_settings_file_if_missing()
        now = time.time()
        if not force and now < self.next_settings_check_at:
            return False
        self.next_settings_check_at = now + 0.5
        try:
            stat = self.settings_file.stat()
        except OSError:
            return False
        if not force and self.settings_mtime_ns == stat.st_mtime_ns:
            return False
        written = not force  # A real write seen by polling, not the startup read.
        self.settings_mtime_ns = stat.st_mtime_ns
        raw = load_json(self.settings_file)
        mode = normalize_mode(
            raw.get("width"),
            raw.get("height"),
            raw.get("fps"),
            raw.get("fourcc") or self.request_fourcc,
        )
        if mode is None and isinstance(raw.get("resolution"), str):
            try:
                width_text, height_text = raw["resolution"].lower().split("x", 1)
                mode = normalize_mode(width_text, height_text, raw.get("fps"), raw.get("fourcc") or self.request_fourcc)
            except ValueError:
                mode = None
        if mode is None:
            self.last_error = f"Invalid camera settings file: {self.settings_file}"
            log("settings_invalid", path=str(self.settings_file), payload=raw)
            self.write_status("settings_invalid", force=True)
            return False
        changed = (
            int(mode["width"]) != self.request_width
            or int(mode["height"]) != self.request_height
            or abs(float(mode["fps"]) - self.request_fps) >= 0.05
            or str(mode["fourcc"]) != self.request_fourcc
        )
        self.request_width = int(mode["width"])
        self.request_height = int(mode["height"])
        self.request_fps = float(mode["fps"])
        self.request_fourcc = str(mode["fourcc"])
        if changed:
            self.capture_cadence = CameraCadence(self.request_fps)
            self.clear_passthrough_fallback("settings_changed")
            self.last_read_ms = None
            self.last_publish_work_ms = None
            log("settings_changed", **self.current_mode())
            self.close_camera()
            self.next_open_attempt_at = 0.0
            self.write_status("settings_changed", force=True)
            return True
        if written and self.clear_passthrough_fallback("settings_file_written") and self.cap is not None \
                and self.capture_mode == "fallback":
            # Re-saving unchanged settings is the operator's retry: reopen now
            # so passthrough is attempted again instead of after the timeout.
            log("passthrough_retry_reopen", reason="settings_file_written", device=self.device or None)
            self.close_camera()
            self.next_open_attempt_at = 0.0
            self.write_status("settings_written", force=True)
            return True
        return False

    def clear_passthrough_fallback(self, reason: str) -> bool:
        """Drop fallback evidence and any engaged fallback; True when there was some."""
        had = bool(self.passthrough_fallback is not None or self.passthrough_evidence or self.invalid_open_run)
        if self.passthrough_fallback is not None:
            log("passthrough_fallback_cleared", reason=reason, fallback_reason=self.passthrough_fallback["reason"])
        self.passthrough_fallback = None
        self.passthrough_evidence = 0
        self.invalid_open_run = 0
        return had

    def expire_passthrough_fallback(self) -> None:
        """A reopen after the retry interval attempts passthrough again.

        One further piece of evidence re-engages the fallback at once, so a
        camera that cannot pass JPEG through costs one failed open per retry.
        """
        fallback = self.passthrough_fallback
        if fallback is None or time.monotonic() < fallback["engaged_monotonic"] + PASSTHROUGH_FALLBACK_RETRY_S:
            return
        log("passthrough_fallback_retry", reason=fallback["reason"], after_s=PASSTHROUGH_FALLBACK_RETRY_S)
        self.passthrough_fallback = None
        self.passthrough_evidence = PASSTHROUGH_FALLBACK_AFTER - 1
        self.invalid_open_run = 0

    def add_passthrough_evidence(self, reason: str) -> None:
        self.passthrough_evidence += 1
        self.passthrough_evidence_reasons[reason] = self.passthrough_evidence_reasons.get(reason, 0) + 1
        self.valid_run = 0
        log("passthrough_fallback_evidence", reason=reason, streak=self.passthrough_evidence,
            fallback_after=PASSTHROUGH_FALLBACK_AFTER)
        if self.passthrough_evidence >= PASSTHROUGH_FALLBACK_AFTER and self.passthrough_fallback is None:
            self.passthrough_fallback = {"reason": reason, "engaged_at": time.time(),
                                         "engaged_monotonic": time.monotonic()}
            log("passthrough_fallback_engaged", reason=reason, retry_after_s=PASSTHROUGH_FALLBACK_RETRY_S)

    def note_valid_passthrough_frame(self) -> None:
        self.valid_run += 1
        if self.valid_run >= PASSTHROUGH_VALID_RUN_RESET and self.passthrough_evidence:
            log("passthrough_fallback_evidence_cleared", valid_run=self.valid_run, streak=self.passthrough_evidence)
            self.passthrough_evidence = 0

    def note_failed_passthrough_open(self, path: str, probe: OpenProbe) -> None:
        self.valid_run = 0
        if probe.kind == "unsupported":
            self.add_passthrough_evidence(probe.reason)
        elif probe.kind == "invalid":
            # Buffers arrive and carry SOI but never validate: not proof by
            # itself (USB damage), but a persistent run must not loop forever.
            self.invalid_open_run += 1
            if self.invalid_open_run >= PASSTHROUGH_INVALID_OPENS_PER_EVIDENCE:
                self.invalid_open_run = 0
                self.add_passthrough_evidence("passthrough_opens_without_valid_frame")
        log("passthrough_open_frame_rejected", device=path, result=probe.kind, reason=probe.reason,
            streak=self.passthrough_evidence, invalid_open_run=self.invalid_open_run,
            fallback_after=PASSTHROUGH_FALLBACK_AFTER)

    def record_invalid(self, reason: str) -> None:
        self.counters.add("invalid_frames", reason=reason)
        self.last_invalid_reason = reason

    def maybe_recover_usb(self) -> None:
        if not self.args.auto_usb_recover or video_nodes():
            return
        devices = usb_video_devices()
        if not devices:
            return
        now = time.time()
        if now < self.next_recovery_at:
            return
        self.recovery_count += 1
        recovery_interval = min(
            self.args.usb_recover_max_interval_s,
            self.args.usb_recover_interval_s * max(1, 1 + (self.recovery_count // 3)),
        )
        self.next_recovery_at = now + recovery_interval
        results = []
        force_authorized_cycle = self.args.authorized_cycle_every > 0 and self.recovery_count % self.args.authorized_cycle_every == 0
        for device in devices:
            reset = usbdevfs_reset(device)
            results.append(reset)
            if force_authorized_cycle or not reset.get("ok"):
                results.append(authorized_cycle(device))
        self.last_recovery = {"at": now, "count": self.recovery_count, "next_interval_s": recovery_interval, "results": results}
        log("usb_recover_attempt", video_nodes=video_nodes(), usb_devices=devices, next_interval_s=recovery_interval, results=results)
        self.write_status("usb_recover_attempt", force=True)

    def open_camera(self) -> bool:
        now = time.time()
        if now < self.next_open_attempt_at:
            self.write_status("waiting")
            return False
        self.next_open_attempt_at = now + self.args.open_retry_interval_s
        self.close_camera()
        self.expire_passthrough_fallback()
        seen: set[str] = set()
        supported_seen: list[dict[str, Any]] = []
        for path in candidate_devices(self.args.device):
            resolved = str(Path(path).resolve()) if Path(path).exists() else path
            if resolved in seen:
                continue
            seen.add(resolved)
            modes = list_camera_modes(path)
            if modes:
                supported_seen = modes
                mode_resolutions = {item.get("resolution") for item in modes}
                requested_resolution = f"{self.request_width}x{self.request_height}"
                if requested_resolution not in mode_resolutions:
                    log("skip_unsupported_mode", device=path, requested=requested_resolution, supported=modes)
                    continue
            cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.request_fourcc))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.request_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.request_height)
            cap.set(cv2.CAP_PROP_FPS, self.request_fps)
            attempt, capture_reason = self.passthrough_open_plan(cap)
            convert_rgb_disabled = None
            if attempt:
                # A refused CONVERT_RGB=0 leaves OpenCV converting to BGR; an
                # accepted one is still proven only by the buffers themselves.
                convert_rgb_disabled = bool(cap.set(cv2.CAP_PROP_CONVERT_RGB, 0))
                if not convert_rgb_disabled:
                    attempt, capture_reason = False, "backend_rejected_convert_rgb_0"
            read_started = time.monotonic()
            ok, frame = cap.read()
            read_completed = time.monotonic()
            if not ok or frame is None:
                cap.release()
                continue
            self.counters.add("device_reads")
            jpeg = stamp = None
            if attempt:
                probe = self.open_jpeg(cap, frame, read_completed)
                read_completed = time.monotonic()
                if probe.kind not in ("ok", "size_mismatch"):
                    self.note_failed_passthrough_open(path, probe)
                    cap.release()
                    continue
                jpeg, stamp = probe.jpeg, probe.stamp
            elif self.passthrough_configured():
                stamp = self.capture_stamp(cap, read_completed).msg
                if not decoded_image(frame):
                    # Raw planes (e.g. YUYV HxWx2) would make cv2.imencode assert.
                    self.record_invalid("decoded_frame_not_bgr")
                    log("decoded_open_frame_rejected", device=path, shape=list(getattr(frame, "shape", ()) or ()))
                    cap.release()
                    continue
            self.cap = cap
            self.device = path
            self.fail_count = 0
            self.fail_invalid = 0
            self.frame_count = 0
            self.raw_frame_count = 0
            self.compressed_frame_count = 0
            self.last_raw_publish_at = 0.0
            self.last_raw_submitted_at = 0.0
            self.raw_submit_count = 0
            self.last_publish_at = 0.0
            self.capture_cadence = CameraCadence(self.request_fps)
            self.capture_cadence.claim(read_started)
            self.capture_cadence.succeeded(read_completed)
            self.last_read_ms = round((read_completed - read_started) * 1000.0, 2)
            self.last_error = None
            if jpeg is not None:
                # Coded size of the delivered frame (SOF); a (1, N) buffer shape
                # is not an image size. A requested-size frame was fully decoded.
                self.actual_width, self.actual_height = jpeg.width, jpeg.height
            else:
                self.actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or frame.shape[1])
                self.actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or frame.shape[0])
            self.actual_fps = round(float(cap.get(cv2.CAP_PROP_FPS) or self.request_fps), 2)
            if self.actual_width != self.request_width or self.actual_height != self.request_height:
                log(
                    "mode_rejected",
                    device=path,
                    requested_width=self.request_width,
                    requested_height=self.request_height,
                    actual_width=self.actual_width,
                    actual_height=self.actual_height,
                    fps=self.actual_fps,
                )
                cap.release()
                self.cap = None
                continue
            self.supported_modes = modes or list_camera_modes(path) or supported_seen
            self.convert_rgb_disabled = convert_rgb_disabled
            if jpeg is not None:
                self.invalid_open_run = 0
                self.set_capture_mode("passthrough", None)
                self.counters.add("passthrough_frames")
                self.note_valid_passthrough_frame()
            elif self.passthrough_configured():
                self.set_capture_mode("fallback", capture_reason)
            else:
                self.set_capture_mode("legacy", capture_reason)
            log(
                "opened",
                device=path,
                width=self.actual_width,
                height=self.actual_height,
                fps=self.actual_fps,
                fourcc=self.request_fourcc,
                mode=self.capture_mode,
                mode_reason=self.mode_reason,
                convert_rgb_disabled=convert_rgb_disabled,
                supported_modes=self.supported_modes,
            )
            self.publish_frame(jpeg if jpeg is not None else frame, stamp)
            self.write_status("opened", force=True)
            return True
        if supported_seen:
            self.supported_modes = supported_seen
        self.last_error = "USB camera video node not found" if not video_nodes() else "Unable to open USB camera video node"
        if now - self.last_wait_log_at >= self.args.wait_log_interval_s:
            self.last_wait_log_at = now
            log("waiting", message=self.last_error, video_nodes=video_nodes(), usb_devices=usb_video_devices())
        self.maybe_recover_usb()
        self.write_status("waiting", force=True)
        return False

    def passthrough_open_plan(self, cap) -> tuple[bool, str | None]:
        """Whether this open sets CONVERT_RGB=0, else the decoded-capture reason."""
        if not self.passthrough_configured():
            return False, "passthrough_disabled"
        if self.request_fourcc != "MJPG":
            return False, f"fourcc_{self.request_fourcc}_not_mjpg"
        if self.passthrough_fallback is not None:
            return False, "passthrough_fallback_engaged"
        try:
            negotiated = fourcc_text(cap.get(cv2.CAP_PROP_FOURCC))
        except Exception:  # Backend without the property: the buffers decide.
            negotiated = None
        if negotiated is not None and negotiated not in PASSTHROUGH_PALETTES:
            # OpenCV silently picks another palette when MJPG is refused; its
            # unconverted buffers would be raw planes, not JPEG.
            return False, f"backend_fourcc_{negotiated}_not_mjpg"
        return True, None

    def close_camera(self):
        self.preview_worker.reset()
        if self.cap is not None:
            self.cap.release()
        self.cap = None
        self.device = ""
        self.actual_width = None
        self.actual_height = None
        self.actual_fps = None
        # Capture-scoped state: a closed camera reports no mode or payload.
        # Session fallback state and invalid-reason history are kept.
        self.capture_mode = None
        self.mode_reason = None
        self.convert_rgb_disabled = None
        self.preview_effective = None
        self.last_buffer_ns = None

    def tick(self):
        if self.apply_settings_file():
            return
        if self.cap is None:
            self.open_camera()
            return
        if self.capture_mode == "passthrough":
            self.tick_passthrough()
            return
        read_started = time.monotonic()
        if not self.capture_cadence.claim(read_started):
            self.write_status("streaming")
            return
        ok, frame = self.cap.read()
        read_completed = time.monotonic()
        self.last_read_ms = round((read_completed - read_started) * 1000.0, 2)
        stamp = None
        if ok and frame is not None:
            self.counters.add("device_reads")
            if self.capture_mode == "fallback":
                # Decoded capture inside a passthrough session: buffer timestamp,
                # and planes that are not BGR count as failed reads.
                stamp = self.capture_stamp(self.cap, read_completed).msg
                if not decoded_image(frame):
                    self.record_invalid("decoded_frame_not_bgr")
                    frame = None
        if not ok or frame is None:
            self.capture_failed(invalid=False)
            return
        self.fail_count = 0
        self.fail_invalid = 0
        self.capture_cadence.succeeded(read_completed)
        if stamp is None:
            self.publish_frame(frame)
        else:
            self.publish_frame(frame, stamp)
        self.write_status("streaming")

    def tick_passthrough(self):
        """Read-paced acquisition: each blocking read waits for the next buffer.

        No cadence slot gates the read, so a V4L2 backlog left by a stall is
        drained (bounded per tick) instead of staying queued; every frame keeps
        its own driver timestamp, so drained frames keep their real spacing.
        Nothing is duplicated or synthesized.
        """
        backlog_age_s = PASSTHROUGH_BACKLOG_AGE_PERIODS / max(1.0, float(self.request_fps))
        for _ in range(PASSTHROUGH_MAX_READS_PER_TICK):
            read_started = time.monotonic()
            ok, frame = self.cap.read()
            read_completed = time.monotonic()
            self.last_read_ms = round((read_completed - read_started) * 1000.0, 2)
            if not ok or frame is None:
                self.capture_failed(invalid=False)
                break
            self.counters.add("device_reads")
            stamp = self.capture_stamp(self.cap, read_completed)
            jpeg = self.accept_frame(frame)
            if jpeg is None:
                self.capture_failed(invalid=True)
                if self.cap is None:
                    break
            else:
                self.fail_count = 0
                self.fail_invalid = 0
                self.capture_cadence.succeeded(read_completed)
                self.note_valid_passthrough_frame()
                self.publish_frame(jpeg, stamp.msg)
            if stamp.age_s is None or stamp.age_s <= backlog_age_s:
                break  # A fresh buffer: the queue is drained.
        if self.cap is not None:
            self.write_status("streaming")

    def capture_failed(self, invalid: bool) -> None:
        self.fail_count += 1
        self.valid_run = 0
        if invalid:
            self.fail_invalid += 1
        if self.fail_count < READ_FAILURES_BEFORE_REOPEN:
            return
        # A reopen driven mostly by invalid passthrough buffers is fallback
        # evidence; a stalled or unplugged device (read failures) is not.
        evidence = self.capture_mode == "passthrough" and 2 * self.fail_invalid > self.fail_count
        self.last_error = "Camera read failed repeatedly; rescanning"
        log("reopen", message=self.last_error, failures=self.fail_count, invalid_buffers=self.fail_invalid,
            fallback_evidence=evidence)
        self.close_camera()
        if evidence:
            self.add_passthrough_evidence("passthrough_stream_invalid_buffers")
        self.write_status("reopen", force=True)

    def capture_stamp(self, cap, read_completed: float) -> CaptureStamp:
        """ROS header stamp of the buffer just read.

        The V4L2 buffer timestamp (CAP_PROP_POS_MSEC, CLOCK_MONOTONIC by
        default for uvcvideo) is mapped to ROS time with the offset between
        the ROS clock and time.monotonic() sampled now. Without a plausible,
        increasing buffer timestamp the read completion time is used instead.
        """
        ros_now = self.get_clock().now()
        mono_now = time.monotonic()
        offset_ns = int(ros_now.nanoseconds) - round(mono_now * 1e9)
        try:
            pos_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC))
        except Exception:  # A backend or stand-in without the property.
            pos_ms = math.nan
        untrusted = None
        if not math.isfinite(pos_ms) or pos_ms <= 0:
            untrusted = "v4l2_timestamp_unavailable"
        else:
            buffer_ns = round(pos_ms * 1e6)
            age_s = mono_now - pos_ms / 1000.0
            if not V4L2_STAMP_MIN_AGE_S <= age_s <= V4L2_STAMP_MAX_AGE_S:
                untrusted = "v4l2_timestamp_not_monotonic_clock_or_stale"
            elif self.last_buffer_ns is not None and buffer_ns <= self.last_buffer_ns:
                untrusted = "v4l2_timestamp_not_increasing"
        if untrusted is None:
            self.last_buffer_ns = buffer_ns
            stamp_ns, source = buffer_ns + offset_ns, "v4l2_buffer_monotonic"
        else:
            stamp_ns, source, age_s = round(read_completed * 1e9) + offset_ns, "read_completion", None
        self.stamp_sources[source] = self.stamp_sources.get(source, 0) + 1
        self.last_stamp = {"source": source, "buffer_age_ms": None if age_s is None else round(age_s * 1000.0, 2),
                           "untrusted_reason": untrusted}
        msg = ros_now.to_msg()
        msg.sec, msg.nanosec = divmod(int(stamp_ns), 1_000_000_000)
        return CaptureStamp(msg, source, age_s)

    def open_jpeg(self, cap, frame, read_completed) -> OpenProbe:
        """First valid frame of a fresh passthrough stream, within a few reads.

        One full decode proves this camera's JPEG matches its SOF size; later
        frames are checked from headers only. A stream may begin with a partial
        buffer, so a bad first buffer is not proof against passthrough. Only a
        buffer that is not one uint8 row, or no SOI on any read, is.
        """
        soi_seen, reason = False, None
        for attempt in range(OPEN_JPEG_READS):
            if attempt:
                ok, frame = cap.read()
                read_completed = time.monotonic()
                if not ok or frame is None:
                    return OpenProbe("read_failed", reason="open_read_failed")
                self.counters.add("device_reads")
            stamp = self.capture_stamp(cap, read_completed)
            if not one_row_uint8(frame):
                self.record_invalid("passthrough_buffer_not_one_uint8_row")
                return OpenProbe("unsupported", reason="passthrough_buffer_not_one_uint8_row")
            soi_seen = soi_seen or frame.reshape(-1)[:2].tobytes() == b"\xff\xd8"
            try:
                jpeg = jpeg_frame(frame)
            except ValueError as error:
                reason = str(error)
                self.record_invalid(reason)
                continue
            if (jpeg.width, jpeg.height) != (self.request_width, self.request_height):
                return OpenProbe("size_mismatch", jpeg, stamp.msg)  # Never decode an unexpected size.
            try:
                image = cv2.imdecode(np.frombuffer(jpeg.data, np.uint8), cv2.IMREAD_COLOR)
            except cv2.error:
                image = None
            if image is None or image.shape[:2] != (jpeg.height, jpeg.width):
                reason = "jpeg_open_decode_failed"
                self.record_invalid(reason)
                continue
            self.note_trimmed_fill(jpeg)
            return OpenProbe("ok", jpeg, stamp.msg)
        if not soi_seen:
            return OpenProbe("unsupported", reason="passthrough_open_buffers_without_soi")
        return OpenProbe("invalid", reason=reason)

    def note_trimmed_fill(self, jpeg) -> None:
        if jpeg.trimmed_fill:
            self.counters.add("jpeg_fill_trimmed", jpeg.trimmed_fill)

    def accept_frame(self, frame):
        """Passthrough buffers are validated before they count as a read."""
        try:
            jpeg = jpeg_frame(frame)
            if (jpeg.width, jpeg.height) != (self.actual_width, self.actual_height):
                raise ValueError("jpeg_dimensions_changed")
        except ValueError as error:
            self.record_invalid(str(error))
            return None
        self.note_trimmed_fill(jpeg)
        self.counters.add("passthrough_frames")
        return jpeg

    def submit_preview(self, frame, work_started, stamp=None):
        self.last_publish_at = time.monotonic()
        if stamp is None:
            stamp = self.get_clock().now().to_msg()  # Legacy: publish time.
        raw_interval = 1.0 / max(0.1, float(self.args.raw_publish_fps))
        raw_due = self.raw_submit_count == 0 or self.last_publish_at - self.last_raw_submitted_at >= raw_interval
        overwritten = self.preview_worker.overwritten
        generation = self.preview_worker.submit(frame, stamp, raw_due, work_started)
        if self.preview_worker.overwritten != overwritten:
            self.counters.add("preview_overwritten")
        if raw_due and generation is not None:
            self.raw_submit_count += 1
            self.last_raw_submitted_at = self.last_publish_at
        return stamp, generation

    def publish_frame(self, frame, stamp=None):
        work_started = time.monotonic()
        if isinstance(frame, JpegFrame):
            # Original camera bytes: no decode, rotate or re-encode on the
            # capture thread. Preview/raw decode in the latest-only worker.
            stamp, generation = self.submit_preview(frame, work_started, stamp)
            if generation is not None and self.recording_pub.get_subscription_count():
                self.publish_original(frame, stamp, generation)
        else:
            sensor = frame
            if self.args.rotate_180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            # VideoCapture.read() provides a new owned array. It is never mutated
            # after this shared handoff to independent native/preview encoders.
            frame.flags.writeable = False
            stamp, generation = self.submit_preview(frame, work_started, stamp)
            if generation is not None and self.recording_pub.get_subscription_count():
                if self.capture_mode == "fallback":
                    # Passthrough session contract: record the sensor-orientation
                    # frame and declare the rotation; only preview/raw rotate.
                    sensor.flags.writeable = False
                    self.native_encoder.submit(sensor, (stamp, generation, "rotation_declared"))
                else:
                    self.native_encoder.submit(frame, (stamp, generation))
        self.frame_count += 1
        self.last_publish_work_ms = round((time.monotonic() - work_started) * 1000.0, 2)

    def publish_original(self, jpeg, stamp, generation):
        message = CompressedImage()
        message.header.stamp = stamp
        message.header.frame_id = self.frame_id
        message.format = original_jpeg_format(jpeg, self.args.rotate_180)
        message.data = jpeg.data
        if self.preview_worker.publish_current(generation, lambda: self.recording_pub.publish(message), 'native'):
            self.counters.add("recording_published", len(jpeg.data))

    def process_preview_frame(self, frame, stamp, raw_due, generation):
        if isinstance(frame, JpegFrame):
            return self.process_preview_jpeg(frame, stamp, raw_due, generation)
        if not self.preview_worker.is_current(generation):
            return
        self.publish_preview_image(frame, stamp, generation, None)
        # Raw BGR remains available for archive snapshots,
        # but is deliberately bounded. Live viewing consumes the high-rate
        # compressed ROS2 topic, avoiding roughly 6 MiB of DDS traffic per
        # 1080p frame.
        if raw_due and self.preview_worker.is_current(generation):
            self.publish_raw_image(frame, stamp, generation)

    def decode_upright(self, data, reduction):
        image = cv2.imdecode(np.frombuffer(data, np.uint8), DECODE_FLAGS[reduction])
        if image is None:
            self.counters.add("jpeg_decode_failed")
            raise ValueError("camera_jpeg_decode_failed")
        return cv2.rotate(image, cv2.ROTATE_180) if self.args.rotate_180 else image

    def process_preview_jpeg(self, jpeg, stamp, raw_due, generation):
        if not self.preview_worker.is_current(generation):
            return
        # libjpeg scaled decode serves the preview at a fraction of a full
        # decode, but never below the requested preview width; only the
        # decoded image is rotated, resized and re-encoded.
        requested = max(320, int(self.args.compressed_preview_width))
        reduction = preview_reduction(getattr(self.args, "preview_decode_reduction", "auto"), jpeg.width, requested)
        preview = self.decode_upright(jpeg.data, reduction)
        full = preview if reduction == 1 else None
        self.counters.add("preview_decoded")
        self.publish_preview_image(preview, stamp, generation, reduction)
        if raw_due and self.preview_worker.is_current(generation):
            # Full decode only at the bounded raw cadence (archive snapshots).
            self.publish_raw_image(full if full is not None else self.decode_upright(jpeg.data, 1), stamp, generation)

    def publish_preview_image(self, frame, stamp, generation, reduction):
        height, width = frame.shape[:2]
        preview = frame
        preview_width = min(width, max(320, int(self.args.compressed_preview_width)))
        if preview_width < width:
            preview_height = max(1, int(round(height * preview_width / width)))
            preview = cv2.resize(frame, (preview_width, preview_height), interpolation=cv2.INTER_AREA)
        else:
            preview_height = height
        effective = {"effective_width": preview_width, "effective_height": preview_height,
                     "decode_reduction": reduction,
                     "source": "decoded_bgr" if reduction is None else "original_jpeg"}
        encode_started = time.perf_counter()
        ok, encoded = cv2.imencode(
            ".jpg",
            preview,
            [int(cv2.IMWRITE_JPEG_QUALITY), max(40, min(92, int(self.args.compressed_jpeg_quality)))],
        )
        self.last_compressed_encode_ms = round((time.perf_counter() - encode_started) * 1000.0, 2)
        if ok:
            compressed = CompressedImage()
            compressed.header.stamp = stamp
            compressed.header.frame_id = self.frame_id
            compressed.format = f"bgr8; jpeg compressed bgr8; width={preview_width}; height={preview_height}"
            compressed.data = encoded.tobytes()
            preview_info = estimate_camera_info(preview_width, preview_height, self.frame_id, self.args.fov_deg)
            preview_info.header.stamp = stamp
            def publish_preview():
                self.compressed_pub.publish(compressed)
                self.preview_info_pub.publish(preview_info)
                self.compressed_frame_count += 1
                # Under the generation gate: a closed camera's stale frame
                # cannot re-publish the effective preview size.
                self.preview_effective = effective
            if self.preview_worker.publish_current(generation, publish_preview):
                self.counters.add("preview_published")

    def publish_raw_image(self, frame, stamp, generation):
        height, width = frame.shape[:2]
        image = Image()
        image.header.stamp = stamp
        image.header.frame_id = self.frame_id
        image.height = height
        image.width = width
        image.encoding = "bgr8"
        image.is_bigendian = 0
        image.step = width * 3
        image.data = frame.tobytes()
        info = estimate_camera_info(width, height, self.frame_id, self.args.fov_deg)
        info.header.stamp = stamp
        def publish_raw():
            self.image_pub.publish(image)
            self.info_pub.publish(info)
            self.raw_frame_count += 1
            self.last_raw_publish_at = time.monotonic()
        if self.preview_worker.publish_current(generation, publish_raw, 'raw'):
            self.counters.add("raw_published")

    def publish_native(self, data, envelope, width, height):
        stamp, generation = envelope[:2]
        message = CompressedImage()
        message.header.stamp = stamp
        message.header.frame_id = self.frame_id
        if len(envelope) > 2:  # Decoded fallback in a passthrough session.
            message.format = reencoded_jpeg_format(self.native_encoder.quality, self.args.rotate_180, width, height)
        else:  # Legacy contract, unchanged: pixels already rotated, no tag.
            message.format = f"bgr8; jpeg compressed bgr8; width={width}; height={height}"
        message.data = data
        accepted = self.preview_worker.publish_current(generation, lambda: self.recording_pub.publish(message), 'native')
        if accepted:
            self.counters.add("recording_published", len(data))
        return accepted

    def destroy_node(self):
        self.preview_worker.close()
        self.native_encoder.close()
        self.write_status("shutdown", force=True)
        self.close_camera()
        super().destroy_node()


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a UVC USB camera as ROS2 Image and CameraInfo.")
    parser.add_argument("--device", default=os.environ.get("CAMERA_DEVICE", "auto"))
    parser.add_argument("--image-topic", default=os.environ.get("CAMERA_IMAGE_TOPIC", "/camera/image_raw"))
    parser.add_argument("--compressed-topic", default=os.environ.get("CAMERA_COMPRESSED_TOPIC", "/camera/image_jpeg"))
    parser.add_argument("--recording-topic", default=os.environ.get("CAMERA_RECORDING_TOPIC", "/camera/recording/jpeg"))
    parser.add_argument("--recording-jpeg-quality", type=int, default=90)
    parser.add_argument("--camera-info-topic", default=os.environ.get("CAMERA_INFO_TOPIC", "/camera/camera_info"))
    parser.add_argument(
        "--preview-camera-info-topic",
        default=os.environ.get("CAMERA_PREVIEW_INFO_TOPIC", "/camera/preview/camera_info"),
    )
    parser.add_argument("--frame-id", default=os.environ.get("CAMERA_FRAME_ID", "camera_link_optical"))
    parser.add_argument("--width", type=int, default=int(os.environ.get("CAMERA_WIDTH", "1280")))
    parser.add_argument("--height", type=int, default=int(os.environ.get("CAMERA_HEIGHT", "720")))
    parser.add_argument("--fps", type=float, default=float(os.environ.get("CAMERA_FPS", "60")))
    parser.add_argument("--fourcc", default=os.environ.get("CAMERA_FOURCC", "MJPG"))
    parser.add_argument("--fov-deg", type=float, default=float(os.environ.get("CAMERA_FOV_DEG", "70")))
    parser.add_argument("--rotate-180", default=parse_bool(os.environ.get("CAMERA_ROTATE_180", "1"), True), action=argparse.BooleanOptionalAction)
    parser.add_argument("--status-file", default=os.environ.get("CAMERA_DRIVER_STATUS_FILE", "/app/runtime/camera_driver_status.json"))
    parser.add_argument("--settings-file", default=os.environ.get("CAMERA_SETTINGS_FILE", "/app/runtime/camera_settings.json"))
    parser.add_argument("--max-tick-fps", type=float, default=float(os.environ.get("CAMERA_MAX_TICK_FPS", "60")))
    parser.add_argument("--raw-publish-fps", type=float, default=float(os.environ.get("CAMERA_RAW_PUBLISH_FPS", "5")))
    parser.add_argument(
        "--compressed-jpeg-quality",
        type=int,
        default=int(os.environ.get("CAMERA_COMPRESSED_JPEG_QUALITY", "70")),
    )
    parser.add_argument(
        "--compressed-preview-width",
        type=int,
        default=int(os.environ.get("CAMERA_COMPRESSED_PREVIEW_WIDTH", "1280")),
    )
    parser.add_argument(
        "--mjpeg-passthrough",
        default=parse_bool(os.environ.get("CAMERA_MJPEG_PASSTHROUGH", "1"), True),
        action=argparse.BooleanOptionalAction,
        help="MJPG capture: record the camera's original JPEG bytes (CAP_PROP_CONVERT_RGB=0); falls back to decoded "
             "capture with the same sensor-orientation + declared-rotation recording contract",
    )
    parser.add_argument(
        "--preview-decode-reduction",
        default=os.environ.get("CAMERA_PREVIEW_DECODE_REDUCTION", "auto"),
        help="Passthrough preview JPEG scaled decode: auto|1|2|4 (auto = largest reduction whose width still "
             "covers --compressed-preview-width; a 1920 source with a 1280 preview decodes full size)",
    )
    parser.add_argument("--status-interval-s", type=float, default=float(os.environ.get("CAMERA_DRIVER_STATUS_INTERVAL_S", "0.5")))
    parser.add_argument("--open-retry-interval-s", type=float, default=float(os.environ.get("CAMERA_OPEN_RETRY_INTERVAL_S", "1.0")))
    parser.add_argument("--wait-log-interval-s", type=float, default=float(os.environ.get("CAMERA_WAIT_LOG_INTERVAL_S", "8.0")))
    parser.add_argument("--auto-usb-recover", default=parse_bool(os.environ.get("CAMERA_AUTO_USB_RECOVER", "1"), True), action=argparse.BooleanOptionalAction)
    parser.add_argument("--usb-recover-interval-s", type=float, default=float(os.environ.get("CAMERA_USB_RECOVER_INTERVAL_S", "12.0")))
    parser.add_argument("--usb-recover-max-interval-s", type=float, default=float(os.environ.get("CAMERA_USB_RECOVER_MAX_INTERVAL_S", "120.0")))
    parser.add_argument("--authorized-cycle-every", type=int, default=int(os.environ.get("CAMERA_AUTHORIZED_CYCLE_EVERY", "3")))
    args = parser.parse_args()
    args.raw_publish_fps = max(0.1, min(30.0, float(args.raw_publish_fps)))
    args.compressed_jpeg_quality = max(40, min(92, int(args.compressed_jpeg_quality)))
    args.compressed_preview_width = max(320, min(3840, int(args.compressed_preview_width)))
    args.preview_decode_reduction = str(args.preview_decode_reduction).strip().lower()
    if args.preview_decode_reduction not in PREVIEW_DECODE_REDUCTIONS:
        parser.error(f"--preview-decode-reduction must be one of {', '.join(PREVIEW_DECODE_REDUCTIONS)}")
    rclpy.init()
    node = UsbCameraDriver(args)
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
