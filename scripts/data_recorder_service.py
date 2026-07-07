#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan


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
        self.tracks: dict[str, dict[str, Any]] = {}
        self.next_track_id = 1
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
            },
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
                "algorithm": "polar_gap_cluster_line_fit_nearest_neighbor_tracks_v1",
            }
            write_json_atomic(root / "metadata.json", metadata)
            self.preview_scan = {}
            self.analysis = {}
            self.tracks = {}
            self.next_track_id = 1
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
        metadata.update({"state": "archived", "finished_at": finished_at, "duration_s": duration_s})
        write_json_atomic(root / "metadata.json", metadata)
        write_json_atomic(root / "preview_scan.json", self.preview_scan or {"ok": False, "points": [], "message": "no lidar scan captured"})
        write_json_atomic(root / "analysis.json", self.analysis or self.empty_analysis())
        log("stop", id=active.get("id"), duration_s=duration_s)
        self.write_status("stop_recording")
        return {"ok": True, "state": "archived", "recording_id": active.get("id"), "status": self.status()}

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
                x = r * math.cos(angle)
                y = r * math.sin(angle)
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
            "metrics": {
                "rendered_points": len(points),
                "source_points": len(msg.ranges),
                "coverage_deg": self.coverage(points),
                "range_limit_m": float(msg.range_max),
            },
            "source": "archive_recorder",
        }
        analysis = self.analyze(points)
        with self.lock:
            self.preview_scan = preview
            self.analysis = analysis

    def coverage(self, points: list[dict[str, Any]]) -> float:
        if len(points) < 2:
            return 0.0
        angles = sorted(float(point["angle_deg"]) for point in points)
        gaps = [angles[i] - angles[i - 1] for i in range(1, len(angles))]
        gaps.append((angles[0] + 360.0) - angles[-1])
        return round(max(0.0, 360.0 - max(gaps)), 1)

    def analyze(self, points: list[dict[str, Any]]) -> dict[str, Any]:
        clusters = self.cluster_points(points)
        walls = []
        objects = []
        for cluster in clusters:
            wall = self.fit_wall(cluster)
            if wall:
                walls.append(wall)
                continue
            obj = self.object_from_cluster(cluster)
            if obj:
                objects.append(obj)
        objects = self.update_tracks(objects)
        return {
            "ok": True,
            "algorithm": "polar_gap_cluster_line_fit_nearest_neighbor_tracks_v1",
            "generated_at": iso_now(),
            "walls": walls,
            "objects": objects,
            "cluster_count": len(clusters),
        }

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
        return {"ok": False, "algorithm": "polar_gap_cluster_line_fit_nearest_neighbor_tracks_v1", "walls": [], "objects": [], "cluster_count": 0}


class RecorderNode(Node):
    def __init__(self, args: argparse.Namespace, state: RecordingState):
        super().__init__("ubts_data_recorder")
        self.state = state
        self.create_subscription(LaserScan, args.scan_topic, self.state.observe_scan, 10)


class Handler(BaseHTTPRequestHandler):
    state: RecordingState
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/status":
            self.send_json(self.state.status())
        elif self.path == "/recordings":
            self.send_json(self.state.list_recordings())
        elif self.path.startswith("/recordings/"):
            self.send_json(self.state.detail(self.path.removeprefix("/recordings/").strip("/")))
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
