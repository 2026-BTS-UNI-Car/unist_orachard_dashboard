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
import time
from pathlib import Path
from typing import Any, Iterable

import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image


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
    if normalized_width <= 0 or normalized_height <= 0 or normalized_fps < 1:
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
        rows.append(
            {
                "resolution": resolution,
                "width": width,
                "height": height,
                "fps": sorted(fps_values),
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
        self.settings_mtime_ns: int | None = None
        self.next_settings_check_at = 0.0
        self.image_pub = self.create_publisher(Image, args.image_topic, 10)
        self.info_pub = self.create_publisher(CameraInfo, args.camera_info_topic, 10)
        self.cap: cv2.VideoCapture | None = None
        self.device = ""
        self.actual_width: int | None = None
        self.actual_height: int | None = None
        self.actual_fps: float | None = None
        self.fail_count = 0
        self.frame_count = 0
        self.last_error: str | None = None
        self.last_recovery: dict[str, Any] | None = None
        self.recovery_count = 0
        self.supported_modes: list[dict[str, Any]] = []
        self.next_open_attempt_at = 0.0
        self.next_recovery_at = 0.0
        self.last_wait_log_at = 0.0
        self.last_status_write_at = 0.0
        self.apply_settings_file(force=True)
        self.timer = self.create_timer(1.0 / max(1.0, args.max_tick_fps), self.tick)
        log(
            "start",
            image_topic=args.image_topic,
            camera_info_topic=args.camera_info_topic,
            width=self.request_width,
            height=self.request_height,
            fps=self.request_fps,
            frame_id=args.frame_id,
            auto_usb_recover=args.auto_usb_recover,
            status_file=args.status_file,
            settings_file=args.settings_file,
        )
        self.write_status("start", force=True)

    def state_name(self) -> str:
        if self.cap is not None:
            return "streaming"
        if usb_video_devices() and not video_nodes():
            return "usb_detected_video_node_missing"
        if video_nodes():
            return "waiting_for_open"
        return "not_found"

    def write_status(self, event: str, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_status_write_at < self.args.status_interval_s:
            return
        self.last_status_write_at = now
        nodes = video_nodes()
        usb_devices = usb_video_devices()
        payload = {
            "type": "camera_driver_status",
            "updated_at": now,
            "event": event,
            "state": self.state_name(),
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
            log("settings_changed", **self.current_mode())
            self.close_camera()
            self.next_open_attempt_at = 0.0
            self.write_status("settings_changed", force=True)
        return changed

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
        seen: set[str] = set()
        for path in candidate_devices(self.args.device):
            resolved = str(Path(path).resolve()) if Path(path).exists() else path
            if resolved in seen:
                continue
            seen.add(resolved)
            cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.request_fourcc))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.request_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.request_height)
            cap.set(cv2.CAP_PROP_FPS, self.request_fps)
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                continue
            self.cap = cap
            self.device = path
            self.fail_count = 0
            self.frame_count = 0
            self.last_error = None
            self.actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or frame.shape[1])
            self.actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or frame.shape[0])
            self.actual_fps = round(float(cap.get(cv2.CAP_PROP_FPS) or self.request_fps), 2)
            self.supported_modes = list_camera_modes(path)
            log(
                "opened",
                device=path,
                width=self.actual_width,
                height=self.actual_height,
                fps=self.actual_fps,
                fourcc=self.request_fourcc,
                supported_modes=self.supported_modes,
            )
            self.publish_frame(frame)
            self.write_status("opened", force=True)
            return True
        self.last_error = "USB camera video node not found" if not video_nodes() else "Unable to open USB camera video node"
        if now - self.last_wait_log_at >= self.args.wait_log_interval_s:
            self.last_wait_log_at = now
            log("waiting", message=self.last_error, video_nodes=video_nodes(), usb_devices=usb_video_devices())
        self.maybe_recover_usb()
        self.write_status("waiting", force=True)
        return False

    def close_camera(self):
        if self.cap is not None:
            self.cap.release()
        self.cap = None
        self.device = ""
        self.actual_width = None
        self.actual_height = None
        self.actual_fps = None

    def tick(self):
        if self.apply_settings_file():
            return
        if self.cap is None:
            self.open_camera()
            return
        ok, frame = self.cap.read()
        if not ok or frame is None:
            self.fail_count += 1
            if self.fail_count >= 5:
                self.last_error = "Camera read failed repeatedly; rescanning"
                log("reopen", message=self.last_error)
                self.close_camera()
                self.write_status("reopen", force=True)
            return
        self.fail_count = 0
        self.publish_frame(frame)
        self.write_status("streaming")

    def publish_frame(self, frame):
        stamp = self.get_clock().now().to_msg()
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
        self.image_pub.publish(image)
        self.info_pub.publish(info)
        self.frame_count += 1

    def destroy_node(self):
        self.write_status("shutdown", force=True)
        self.close_camera()
        super().destroy_node()


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a UVC USB camera as ROS2 Image and CameraInfo.")
    parser.add_argument("--device", default=os.environ.get("CAMERA_DEVICE", "auto"))
    parser.add_argument("--image-topic", default=os.environ.get("CAMERA_IMAGE_TOPIC", "/camera/image_raw"))
    parser.add_argument("--camera-info-topic", default=os.environ.get("CAMERA_INFO_TOPIC", "/camera/camera_info"))
    parser.add_argument("--frame-id", default=os.environ.get("CAMERA_FRAME_ID", "camera_link_optical"))
    parser.add_argument("--width", type=int, default=int(os.environ.get("CAMERA_WIDTH", "1280")))
    parser.add_argument("--height", type=int, default=int(os.environ.get("CAMERA_HEIGHT", "720")))
    parser.add_argument("--fps", type=float, default=float(os.environ.get("CAMERA_FPS", "60")))
    parser.add_argument("--fourcc", default=os.environ.get("CAMERA_FOURCC", "MJPG"))
    parser.add_argument("--fov-deg", type=float, default=float(os.environ.get("CAMERA_FOV_DEG", "70")))
    parser.add_argument("--status-file", default=os.environ.get("CAMERA_DRIVER_STATUS_FILE", "/app/runtime/camera_driver_status.json"))
    parser.add_argument("--settings-file", default=os.environ.get("CAMERA_SETTINGS_FILE", "/app/runtime/camera_settings.json"))
    parser.add_argument("--max-tick-fps", type=float, default=float(os.environ.get("CAMERA_MAX_TICK_FPS", "60")))
    parser.add_argument("--status-interval-s", type=float, default=float(os.environ.get("CAMERA_DRIVER_STATUS_INTERVAL_S", "0.5")))
    parser.add_argument("--open-retry-interval-s", type=float, default=float(os.environ.get("CAMERA_OPEN_RETRY_INTERVAL_S", "1.0")))
    parser.add_argument("--wait-log-interval-s", type=float, default=float(os.environ.get("CAMERA_WAIT_LOG_INTERVAL_S", "8.0")))
    parser.add_argument("--auto-usb-recover", default=parse_bool(os.environ.get("CAMERA_AUTO_USB_RECOVER", "1"), True), action=argparse.BooleanOptionalAction)
    parser.add_argument("--usb-recover-interval-s", type=float, default=float(os.environ.get("CAMERA_USB_RECOVER_INTERVAL_S", "12.0")))
    parser.add_argument("--usb-recover-max-interval-s", type=float, default=float(os.environ.get("CAMERA_USB_RECOVER_MAX_INTERVAL_S", "120.0")))
    parser.add_argument("--authorized-cycle-every", type=int, default=int(os.environ.get("CAMERA_AUTHORIZED_CYCLE_EVERY", "3")))
    args = parser.parse_args()
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
