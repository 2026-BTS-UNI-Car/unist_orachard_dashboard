#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, LaserScan

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_server.camera_analysis import CameraOverlayStabilizer, analyze_camera_image
from dashboard_server.lidar_analysis import ALGORITHM_NAME, LIDAR_COORDINATE_FRAME, analyze_points, empty_analysis as shared_empty_analysis


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"ts": round(time.time(), 3), "event": f"data_recorder.{event}", **fields}, ensure_ascii=False), flush=True)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def safe_id(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum() or ch in {"_", "-", "."})


class RecordingState:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.lock = threading.Lock()
        self.archive_dir = Path(args.archive_dir)
        self.status_file = Path(args.status_file)
        self.active: dict[str, Any] | None = None
        self.process: subprocess.Popen | None = None
        self.preview_scan: dict[str, Any] = {}
        self.analysis: dict[str, Any] = {}
        self.lidar_frames: list[dict[str, Any]] = []
        self.camera_frames: list[dict[str, Any]] = []
        self.tracks: dict[str, dict[str, Any]] = {}
        self.camera_stabilizer = CameraOverlayStabilizer()
        self.next_track_id = 1
        self.next_lidar_sample_at = 0.0
        self.next_camera_sample_at = 0.0
        self.write_status("start")

    def status(self) -> dict[str, Any]:
        with self.lock:
            active = dict(self.active or {})
            if active:
                active["duration_s"] = round(time.time() - float(active.get("started_monotonic", time.time())), 2)
            return {
                "ok": True,
                "state": "recording" if active else "idle",
                "active": bool(active),
                "recording_id": active.get("id"),
                "kind": active.get("kind"),
                "started_at": active.get("started_at"),
                "duration_s": active.get("duration_s"),
                "archive_dir": str(self.archive_dir),
                "archive_count": len(self.list_recordings()["recordings"]),
                "rosbag_pid": self.process.pid if self.process and self.process.poll() is None else None,
                "samples": {
                    "lidar_frames": len(self.lidar_frames),
                    "camera_frames": len(self.camera_frames),
                },
            }

    def write_status(self, event: str) -> None:
        payload = self.status()
        payload["event"] = event
        payload["updated_at"] = time.time()
        try:
            write_json_atomic(self.status_file, payload)
        except OSError as exc:
            log("status_write_error", error=str(exc), path=str(self.status_file))

    def list_recordings(self) -> dict[str, Any]:
        rows = []
        if self.archive_dir.exists():
            for path in sorted(self.archive_dir.iterdir(), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
                if not path.is_dir():
                    continue
                metadata = read_json(path / "metadata.json")
                rows.append(
                    {
                        "id": metadata.get("id") or path.name,
                        "kind": metadata.get("kind") or "unknown",
                        "state": metadata.get("state") or "archived",
                        "started_at": metadata.get("started_at"),
                        "finished_at": metadata.get("finished_at"),
                        "duration_s": metadata.get("duration_s"),
                    }
                )
        return {"ok": True, "recordings": rows, "archive_dir": str(self.archive_dir)}

    def detail(self, recording_id: str) -> dict[str, Any]:
        path = self.archive_dir / safe_id(recording_id)
        if not path.exists():
            return {"ok": False, "error": "recording_not_found", "id": recording_id}
        return {
            "ok": True,
            "recording": {
                "id": path.name,
                "metadata": read_json(path / "metadata.json"),
                "analysis": read_json(path / "analysis.json"),
                "preview_scan": read_json(path / "preview_scan.json"),
                "timeline_summary": self.timeline_summary(path),
            },
        }

    def timeline(self, recording_id: str) -> dict[str, Any]:
        path = self.archive_dir / safe_id(recording_id)
        if not path.exists():
            return {"ok": False, "error": "recording_not_found", "id": recording_id}
        timeline = read_json(path / "timeline.json")
        if not timeline:
            return {"ok": False, "error": "timeline_not_found", "id": recording_id}
        return {"ok": True, "timeline": timeline}

    def timeline_summary(self, path: Path) -> dict[str, Any]:
        timeline = read_json(path / "timeline.json")
        if not timeline:
            return {"ok": False, "lidar_frames": 0, "camera_frames": 0}
        return {
            "ok": bool(timeline.get("ok")),
            "duration_s": timeline.get("duration_s"),
            "lidar_frames": len(timeline.get("lidar", {}).get("frames", [])),
            "camera_frames": len(timeline.get("camera", {}).get("frames", [])),
            "algorithm": timeline.get("algorithm"),
            "coordinate_frame": timeline.get("coordinate_frame"),
        }

    def start(self, kind: str) -> dict[str, Any]:
        kind = kind.strip().lower()
        if kind not in {"lidar", "camera", "both"}:
            return {"ok": False, "state": "invalid_kind", "error": "kind must be lidar, camera, or both"}
        with self.lock:
            if self.active:
                already_active = True
            else:
                already_active = False
        if already_active:
            return {"ok": False, "state": "already_recording", "status": self.status()}
        with self.lock:
            recording_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{kind}"
            root = self.archive_dir / recording_id
            root.mkdir(parents=True, exist_ok=False)
            topics = self.topics_for(kind)
            metadata = {
                "id": recording_id,
                "kind": kind,
                "state": "recording",
                "started_at": iso_now(),
                "topics": topics,
                "archive_path": str(root),
                "algorithm": ALGORITHM_NAME,
                "timeline": {
                    "sample_hz": self.args.timeline_hz,
                    "camera_preview_width": self.args.camera_preview_width,
                    "max_frames": self.args.timeline_max_frames,
                },
            }
            write_json_atomic(root / "metadata.json", metadata)
            self.preview_scan = {}
            self.analysis = {}
            self.lidar_frames = []
            self.camera_frames = []
            self.tracks = {}
            self.camera_stabilizer = CameraOverlayStabilizer()
            self.next_track_id = 1
            self.next_lidar_sample_at = 0.0
            self.next_camera_sample_at = 0.0
            self.active = {**metadata, "path": str(root), "started_monotonic": time.time()}
            self.process = self.start_rosbag(root / "rosbag2", topics)
        log("start", id=recording_id, kind=kind, topics=topics, rosbag_pid=self.process.pid if self.process else None)
        self.write_status("start_recording")
        return {"ok": True, "state": "recording", "status": self.status()}

    def stop(self) -> dict[str, Any]:
        with self.lock:
            active = dict(self.active or {})
            proc = self.process
            self.active = None
            self.process = None
        if not active:
            self.write_status("stop_idle")
            return {"ok": True, "state": "idle", "status": self.status()}
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=self.args.rosbag_stop_timeout_s)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        root = Path(str(active["path"]))
        finished_at = iso_now()
        duration_s = round(time.time() - float(active.get("started_monotonic", time.time())), 2)
        metadata = read_json(root / "metadata.json")
        metadata.update(
            {
                "state": "archived",
                "finished_at": finished_at,
                "duration_s": duration_s,
                "timeline": {
                    "lidar_frames": len(self.lidar_frames),
                    "camera_frames": len(self.camera_frames),
                    "sample_hz": self.args.timeline_hz,
                },
            }
        )
        write_json_atomic(root / "metadata.json", metadata)
        write_json_atomic(root / "preview_scan.json", self.preview_scan or {"ok": False, "points": [], "message": "no lidar scan captured"})
        write_json_atomic(root / "analysis.json", self.analysis or self.empty_analysis())
        write_json_atomic(root / "timeline.json", self.timeline_payload(active, metadata, duration_s))
        log("stop", id=active.get("id"), duration_s=duration_s)
        self.write_status("stop_recording")
        return {"ok": True, "state": "archived", "recording_id": active.get("id"), "status": self.status()}

    def timeline_payload(self, active: dict[str, Any], metadata: dict[str, Any], duration_s: float) -> dict[str, Any]:
        return {
            "ok": True,
            "version": 1,
            "id": active.get("id") or metadata.get("id"),
            "kind": active.get("kind") or metadata.get("kind"),
            "algorithm": ALGORITHM_NAME,
            "coordinate_frame": LIDAR_COORDINATE_FRAME,
            "generated_at": iso_now(),
            "started_at": metadata.get("started_at"),
            "finished_at": metadata.get("finished_at"),
            "duration_s": duration_s,
            "sample_hz": self.args.timeline_hz,
            "lidar": {"frames": list(self.lidar_frames)},
            "camera": {"frames": list(self.camera_frames)},
            "sync": {
                "time_base": "seconds_since_recording_start",
                "lidar_frames": len(self.lidar_frames),
                "camera_frames": len(self.camera_frames),
            },
        }

    def start_rosbag(self, output_dir: Path, topics: list[str]) -> subprocess.Popen | None:
        if self.args.disable_rosbag:
            return None
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        log_path = output_dir.parent / "rosbag2.log"
        log_file = log_path.open("ab")
        cmd = ["ros2", "bag", "record", "-o", str(output_dir), *topics]
        try:
            return subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            log("rosbag_start_error", error=str(exc), cmd=cmd)
            return None

    def topics_for(self, kind: str) -> list[str]:
        topics = [self.args.cmd_topic, self.args.rover_status_topic]
        if kind in {"lidar", "both"}:
            topics.append(self.args.scan_topic)
        if kind in {"camera", "both"}:
            topics.extend([self.args.camera_topic, self.args.camera_info_topic])
        return topics

    def observe_scan(self, msg: LaserScan) -> None:
        with self.lock:
            if not self.active or self.active.get("kind") not in {"lidar", "both"}:
                return
            started_monotonic = float(self.active.get("started_monotonic", time.time()))
        points = []
        angle = float(msg.angle_min)
        max_points = max(50, int(self.args.preview_max_points))
        stride = max(1, math.ceil(len(msg.ranges) / max_points))
        for index, distance in enumerate(msg.ranges):
            if index % stride != 0:
                angle += float(msg.angle_increment)
                continue
            r = float(distance)
            if math.isfinite(r) and max(float(msg.range_min), 0.02) <= r <= float(msg.range_max):
                x = r * math.sin(angle)
                y = r * math.cos(angle)
                points.append(
                    {
                        "angle_deg": round((math.degrees(angle) + 360.0) % 360.0, 2),
                        "distance_m": round(r, 3),
                        "x_m": round(x, 3),
                        "y_m": round(y, 3),
                        "quality": 32,
                    }
                )
            angle += float(msg.angle_increment)
        preview = {
            "ok": True,
            "state": "archived_preview",
            "protocol": "ROS2 /scan",
            "frame_id": msg.header.frame_id or self.args.lidar_frame_id,
            "stamp": {"sec": int(msg.header.stamp.sec), "nanosec": int(msg.header.stamp.nanosec)},
            "tested_at": int(time.time()),
            "points": points,
            "coordinate_frame": LIDAR_COORDINATE_FRAME,
            "metrics": {
                "rendered_points": len(points),
                "source_points": len(msg.ranges),
                "coverage_deg": self.coverage(points),
                "range_limit_m": float(msg.range_max),
            },
            "source": "archive_recorder",
        }
        analysis = self.analyze(points)
        now = time.time()
        frame = None
        if now >= self.next_lidar_sample_at:
            self.next_lidar_sample_at = now + self.sample_interval_s()
            frame = {
                "t_s": round(now - started_monotonic, 3),
                "frame_id": preview["frame_id"],
                "stamp": preview["stamp"],
                "points": points,
                "coordinate_frame": LIDAR_COORDINATE_FRAME,
                "metrics": preview["metrics"],
                "overlay": analysis,
            }
        with self.lock:
            self.preview_scan = preview
            self.analysis = analysis
            if frame:
                self.append_limited(self.lidar_frames, frame)

    def observe_camera(self, msg: Image) -> None:
        with self.lock:
            if not self.active or self.active.get("kind") not in {"camera", "both"}:
                return
            started_monotonic = float(self.active.get("started_monotonic", time.time()))
            now = time.time()
            if now < self.next_camera_sample_at:
                return
            self.next_camera_sample_at = now + self.sample_interval_s()
        try:
            frame = self.image_to_bgr(msg)
            source_height, source_width = frame.shape[:2]
            preview_width = max(160, int(self.args.camera_preview_width))
            if source_width > preview_width:
                scale = preview_width / source_width
                preview_height = max(1, int(source_height * scale))
                frame = cv2.resize(frame, (preview_width, preview_height), interpolation=cv2.INTER_AREA)
            overlay = self.camera_stabilizer.update(analyze_camera_image(frame), now - started_monotonic)
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(self.args.camera_jpeg_quality)])
            if not ok:
                return
        except Exception as exc:
            log("camera_preview_error", error=str(exc))
            return
        payload = {
            "t_s": round(now - started_monotonic, 3),
            "frame_id": msg.header.frame_id or "camera_link_optical",
            "stamp": {"sec": int(msg.header.stamp.sec), "nanosec": int(msg.header.stamp.nanosec)},
            "mime": "image/jpeg",
            "data": base64.b64encode(encoded.tobytes()).decode("ascii"),
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
            "source_width": int(source_width),
            "source_height": int(source_height),
            "overlay": overlay,
        }
        with self.lock:
            self.append_limited(self.camera_frames, payload)

    def sample_interval_s(self) -> float:
        return 1.0 / max(0.1, float(self.args.timeline_hz))

    def append_limited(self, rows: list[dict[str, Any]], item: dict[str, Any]) -> None:
        rows.append(item)
        limit = max(1, int(self.args.timeline_max_frames))
        if len(rows) > limit:
            del rows[: len(rows) - limit]

    def image_to_bgr(self, msg: Image) -> Any:
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

    def coverage(self, points: list[dict[str, Any]]) -> float:
        if len(points) < 2:
            return 0.0
        angles = sorted(float(point["angle_deg"]) for point in points)
        gaps = [angles[i] - angles[i - 1] for i in range(1, len(angles))]
        gaps.append((angles[0] + 360.0) - angles[-1])
        return round(max(0.0, 360.0 - max(gaps)), 1)

    def analyze(self, points: list[dict[str, Any]]) -> dict[str, Any]:
        analysis, tracks, next_track_id = analyze_points(points, self.tracks, self.next_track_id)
        self.tracks = tracks
        self.next_track_id = next_track_id
        return analysis

    def cluster_points(self, points: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        clusters: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        for point in points:
            if previous:
                gap = math.hypot(float(point["x_m"]) - float(previous["x_m"]), float(point["y_m"]) - float(previous["y_m"]))
                threshold = max(0.18, 0.06 * max(float(point["distance_m"]), float(previous["distance_m"])))
                if gap > threshold:
                    if len(current) >= 4:
                        clusters.append(current)
                    current = []
            current.append(point)
            previous = point
        if len(current) >= 4:
            clusters.append(current)
        return clusters

    def fit_wall(self, cluster: list[dict[str, Any]]) -> dict[str, Any] | None:
        if len(cluster) < 8:
            return None
        xs = [float(point["x_m"]) for point in cluster]
        ys = [float(point["y_m"]) for point in cluster]
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs) / len(xs)
        syy = sum((y - my) ** 2 for y in ys) / len(ys)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
        theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
        dx = math.cos(theta)
        dy = math.sin(theta)
        projections = [(x - mx) * dx + (y - my) * dy for x, y in zip(xs, ys)]
        residuals = [abs(-(x - mx) * dy + (y - my) * dx) for x, y in zip(xs, ys)]
        length = max(projections) - min(projections)
        width = sorted(residuals)[int(0.9 * (len(residuals) - 1))]
        if length < 0.65 or width > 0.16 or length / max(width, 0.01) < 6.0:
            return None
        a = min(projections)
        b = max(projections)
        return {
            "label": "wall",
            "start": {"x_m": round(mx + a * dx, 3), "y_m": round(my + a * dy, 3)},
            "end": {"x_m": round(mx + b * dx, 3), "y_m": round(my + b * dy, 3)},
            "length_m": round(length, 3),
            "width_m": round(width, 3),
            "points": len(cluster),
        }

    def object_from_cluster(self, cluster: list[dict[str, Any]]) -> dict[str, Any] | None:
        xs = [float(point["x_m"]) for point in cluster]
        ys = [float(point["y_m"]) for point in cluster]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        radius = max(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys))
        if radius > 0.7:
            return None
        return {
            "label": "object",
            "center": {"x_m": round(cx, 3), "y_m": round(cy, 3)},
            "radius_m": round(max(0.08, radius), 3),
            "points": len(cluster),
        }

    def update_tracks(self, objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = time.time()
        matched: set[str] = set()
        for obj in objects:
            cx = float(obj["center"]["x_m"])
            cy = float(obj["center"]["y_m"])
            best_id = None
            best_dist = 0.55
            for track_id, track in self.tracks.items():
                if track_id in matched:
                    continue
                tx = float(track["last"]["x_m"])
                ty = float(track["last"]["y_m"])
                dist = math.hypot(cx - tx, cy - ty)
                if dist < best_dist:
                    best_id = track_id
                    best_dist = dist
            if best_id is None:
                best_id = f"obj-{self.next_track_id}"
                self.next_track_id += 1
                self.tracks[best_id] = {"history": [], "last_at": now, "last": {"x_m": cx, "y_m": cy}}
            track = self.tracks[best_id]
            dt = max(0.001, now - float(track.get("last_at", now)))
            last = track.get("last", {"x_m": cx, "y_m": cy})
            velocity = {"x_mps": round((cx - float(last["x_m"])) / dt, 3), "y_mps": round((cy - float(last["y_m"])) / dt, 3)}
            track["history"] = (track.get("history") or [])[-7:] + [{"x_m": round(cx, 3), "y_m": round(cy, 3)}]
            track["last"] = {"x_m": cx, "y_m": cy}
            track["last_at"] = now
            matched.add(best_id)
            obj["id"] = best_id
            obj["velocity"] = velocity
            obj["track"] = track["history"]
        for track_id in list(self.tracks):
            if now - float(self.tracks[track_id].get("last_at", 0.0)) > 2.0:
                self.tracks.pop(track_id, None)
        return objects

    def empty_analysis(self) -> dict[str, Any]:
        return shared_empty_analysis()


class RecorderNode(Node):
    def __init__(self, args: argparse.Namespace, state: RecordingState):
        super().__init__("ubts_data_recorder")
        self.state = state
        self.create_subscription(LaserScan, args.scan_topic, self.state.observe_scan, 10)
        self.create_subscription(Image, args.camera_topic, self.state.observe_camera, 10)


class Handler(BaseHTTPRequestHandler):
    state: RecordingState
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/status":
            self.send_json(self.state.status())
        elif self.path == "/recordings":
            self.send_json(self.state.list_recordings())
        elif self.path.startswith("/recordings/"):
            raw = self.path.removeprefix("/recordings/").strip("/")
            if raw.endswith("/timeline"):
                self.send_json(self.state.timeline(raw.removesuffix("/timeline").strip("/")))
            else:
                self.send_json(self.state.detail(raw))
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        if self.path == "/start":
            payload = self.read_json()
            self.send_json(self.state.start(str(payload.get("kind") or "")))
        elif self.path == "/stop":
            self.send_json(self.state.stop())
        else:
            self.send_error(404, "Not found")

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def log_message(self, fmt, *args):
        if os.environ.get("RECORDER_ACCESS_LOG", "0") == "1":
            super().log_message(fmt, *args)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive rover LiDAR/camera drive-test data.")
    parser.add_argument("--host", default=os.environ.get("RECORDER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RECORDER_PORT", "8790")))
    parser.add_argument("--archive-dir", default=os.environ.get("RECORDING_ARCHIVE_DIR", "/app/runtime/recordings"))
    parser.add_argument("--status-file", default=os.environ.get("DATA_RECORDER_STATUS_FILE", "/app/runtime/data_recorder_status.json"))
    parser.add_argument("--scan-topic", default=os.environ.get("LIDAR_SCAN_TOPIC", "/scan"))
    parser.add_argument("--camera-topic", default=os.environ.get("CAMERA_IMAGE_TOPIC", "/camera/image_raw"))
    parser.add_argument("--camera-info-topic", default=os.environ.get("CAMERA_INFO_TOPIC", "/camera/camera_info"))
    parser.add_argument("--cmd-topic", default=os.environ.get("ROVER_CMD_TOPIC", "/cmd_vel"))
    parser.add_argument("--rover-status-topic", default=os.environ.get("ROVER_STATUS_TOPIC", "/rover/status"))
    parser.add_argument("--lidar-frame-id", default=os.environ.get("LIDAR_FRAME_ID", "laser"))
    parser.add_argument("--preview-max-points", type=int, default=int(os.environ.get("RECORDER_PREVIEW_MAX_POINTS", "1200")))
    parser.add_argument("--timeline-hz", type=float, default=float(os.environ.get("RECORDER_TIMELINE_HZ", "2.0")))
    parser.add_argument("--timeline-max-frames", type=int, default=int(os.environ.get("RECORDER_TIMELINE_MAX_FRAMES", "1800")))
    parser.add_argument("--camera-preview-width", type=int, default=int(os.environ.get("RECORDER_CAMERA_PREVIEW_WIDTH", "640")))
    parser.add_argument("--camera-jpeg-quality", type=int, default=int(os.environ.get("RECORDER_CAMERA_JPEG_QUALITY", "68")))
    parser.add_argument("--rosbag-stop-timeout-s", type=float, default=float(os.environ.get("RECORDER_ROSBAG_STOP_TIMEOUT_S", "8.0")))
    parser.add_argument("--disable-rosbag", default=os.environ.get("RECORDER_DISABLE_ROSBAG", "0").strip().lower() in {"1", "true", "yes", "on"}, action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    state = RecordingState(args)
    Handler.state = state
    server = Server((args.host, args.port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log("listen", host=args.host, port=args.port, archive_dir=args.archive_dir)

    rclpy.init()
    node = RecorderNode(args, state)
    try:
        rclpy.spin(node)
    finally:
        state.stop()
        node.destroy_node()
        rclpy.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
