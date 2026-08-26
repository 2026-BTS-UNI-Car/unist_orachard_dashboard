#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import copy
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
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image, Imu, LaserScan, MagneticField
from std_msgs.msg import String

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_server.camera_analysis import (
    DEFAULT_VISION_INFERENCE_JPEG_QUALITY,
    LOCAL_VISION_ALGORITHM_NAME,
    LOCAL_VISION_ANALYSIS_REVISION,
    VISION_INFERENCE_INPUT_POLICY_REVISION,
    current_source_native_inference_policy,
    empty_camera_analysis,
    scale_camera_overlay_geometry,
)
from dashboard_server.lidar_analysis import ALGORITHM_NAME, LIDAR_COORDINATE_FRAME, analyze_points, empty_analysis as shared_empty_analysis
from dashboard_server.imu_processing import (
    IMU_CALIBRATION_REVISION,
    IMU_CALIBRATION_SCHEMA,
    IMU_TIMELINE_SCHEMA,
    ImuCalibrationConfig,
    StartReferenceCalibrator,
    quaternion_to_euler_deg,
)


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
        self.next_track_id = 1
        self.next_lidar_sample_at = 0.0
        self.next_camera_sample_at = 0.0
        self.camera_pending_sample: dict[str, Any] | None = None
        self.camera_pending_event = threading.Event()
        self.camera_idle_event = threading.Event()
        self.camera_idle_event.set()
        self.vision_condition = threading.Condition(self.lock)
        self.shared_vision_payloads: list[dict[str, Any]] = []
        self.shared_vision_received_count = 0
        self.shared_vision_last_received_monotonic: float | None = None
        self.shared_vision_provenance_rejected_count = 0
        self.camera_enqueued_count = 0
        self.camera_coalesced_frame_count = 0
        self.camera_archive_count = 0
        self.camera_shared_vision_match_count = 0
        self.camera_shared_vision_miss_count = 0
        self.camera_last_archive_duration_ms: float | None = None
        self.camera_last_vision_sync_delta_s: float | None = None
        self.camera_last_input_policy: dict[str, Any] = {}
        self.imu_frames: list[dict[str, Any]] = []
        self.imu_calibrator = StartReferenceCalibrator(
            ImuCalibrationConfig(
                stationary_window_s=float(args.imu_calibration_window_s),
                min_samples=int(args.imu_calibration_min_samples),
                max_gyro_norm_rad_s=float(args.imu_stationary_max_gyro_rad_s),
                gravity_tolerance_m_s2=float(args.imu_stationary_gravity_tolerance_m_s2),
                max_accel_norm_std_m_s2=float(args.imu_stationary_max_accel_std_m_s2),
            )
        )
        self.imu_latest_raw: dict[str, Any] = {}
        self.imu_latest_corrected: dict[str, Any] = {}
        self.imu_latest_magnetic: dict[str, Any] = {}
        self.imu_last_received_monotonic: float | None = None
        self.imu_raw_received_count = 0
        self.imu_recording_raw_count = 0
        self.imu_corrected_published_count = 0
        self.imu_timeline_count = 0
        self.next_imu_sample_at = 0.0
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
                "include_imu": bool(active.get("include_imu", True)) if active else True,
                "started_at": active.get("started_at"),
                "duration_s": active.get("duration_s"),
                "rosbag_topics": list(active.get("topics") or []),
                "archive_dir": str(self.archive_dir),
                "archive_count": len(self.list_recordings()["recordings"]),
                "rosbag_pid": self.process.pid if self.process and self.process.poll() is None else None,
                "samples": {
                    "lidar_frames": len(self.lidar_frames),
                    "camera_frames": len(self.camera_frames),
                    "imu_frames": len(self.imu_frames),
                },
                "imu_pipeline": {
                    "schema": IMU_TIMELINE_SCHEMA,
                    "raw_topic": self.args.imu_raw_topic,
                    "corrected_topic": self.args.imu_corrected_topic,
                    "mag_topic": self.args.imu_mag_topic,
                    "calibration_topic": self.args.imu_calibration_topic,
                    "default_recording_enabled": True,
                    "recording_enabled": bool(active.get("include_imu", True)) if active else False,
                    "raw_topic_alive": (
                        self.imu_last_received_monotonic is not None
                        and time.monotonic() - self.imu_last_received_monotonic < 1.0
                    ),
                    "last_raw_age_s": (
                        round(time.monotonic() - self.imu_last_received_monotonic, 4)
                        if self.imu_last_received_monotonic is not None
                        else None
                    ),
                    "raw_received_count": self.imu_raw_received_count,
                    "recording_raw_count": self.imu_recording_raw_count,
                    "corrected_published_count": self.imu_corrected_published_count,
                    "timeline_frame_count": self.imu_timeline_count,
                    "timeline_hz": float(self.args.imu_timeline_hz),
                    "latest_raw": dict(self.imu_latest_raw),
                    "latest_corrected": dict(self.imu_latest_corrected),
                    "latest_magnetic": dict(self.imu_latest_magnetic),
                    "calibration": self.imu_calibrator.status(),
                },
                "camera_pipeline": {
                    "input_policy_revision": VISION_INFERENCE_INPUT_POLICY_REVISION,
                    "required_input_mode": "source_native",
                    "required_requested_width": 0,
                    "required_jpeg_quality": DEFAULT_VISION_INFERENCE_JPEG_QUALITY,
                    "inference_owner": "camera_ws_bridge",
                    "independent_inference": False,
                    "preview_width": int(self.args.camera_preview_width),
                    "preview_jpeg_quality": int(self.args.camera_jpeg_quality),
                    "preview_decoupled": True,
                    "latest_only": True,
                    "coalescing": "single_slot_archive_worker_and_dds_best_effort_keep_last_1",
                    "vision_sync_max_delta_s": float(self.args.camera_vision_sync_max_delta_s),
                    "vision_wait_timeout_s": float(self.args.camera_vision_wait_timeout_s),
                    "enqueued_frame_count": self.camera_enqueued_count,
                    "coalesced_frame_count": self.camera_coalesced_frame_count,
                    "archived_frame_count": self.camera_archive_count,
                    "shared_vision_received_count": self.shared_vision_received_count,
                    "shared_vision_received_age_s": (
                        round(time.monotonic() - self.shared_vision_last_received_monotonic, 3)
                        if self.shared_vision_last_received_monotonic is not None
                        else None
                    ),
                    "shared_vision_provenance_rejected_count": self.shared_vision_provenance_rejected_count,
                    "shared_vision_match_count": self.camera_shared_vision_match_count,
                    "shared_vision_miss_count": self.camera_shared_vision_miss_count,
                    "last_duration_ms": self.camera_last_archive_duration_ms,
                    "last_vision_sync_delta_s": self.camera_last_vision_sync_delta_s,
                    "last_actual_input": dict(self.camera_last_input_policy),
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
            return {"ok": False, "lidar_frames": 0, "camera_frames": 0, "imu_frames": 0}
        return {
            "ok": bool(timeline.get("ok")),
            "duration_s": timeline.get("duration_s"),
            "lidar_frames": len(timeline.get("lidar", {}).get("frames", [])),
            "camera_frames": len(timeline.get("camera", {}).get("frames", [])),
            "imu_frames": len(timeline.get("imu", {}).get("frames", [])),
            "imu_calibration_valid": bool(
                timeline.get("imu", {}).get("calibration", {}).get("valid")
                or timeline.get("imu", {}).get("calibration", {}).get("calibration", {}).get("valid")
            ),
            "algorithm": timeline.get("algorithm"),
            "coordinate_frame": timeline.get("coordinate_frame"),
        }

    def start(self, kind: str, include_imu: bool = True) -> dict[str, Any]:
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
            include_imu = bool(include_imu)
            topics = self.topics_for(kind, include_imu=include_imu)
            metadata = {
                "id": recording_id,
                "kind": kind,
                "include_imu": include_imu,
                "state": "recording",
                "started_at": iso_now(),
                "topics": topics,
                "archive_path": str(root),
                "algorithm": ALGORITHM_NAME,
                "timeline": {
                    "sample_hz": self.args.timeline_hz,
                    "camera_preview_width": self.args.camera_preview_width,
                    "camera_input_policy_revision": VISION_INFERENCE_INPUT_POLICY_REVISION,
                    "camera_inference_owner": "camera_ws_bridge",
                    "camera_independent_inference": False,
                    "camera_preview_decoupled": True,
                    "camera_latest_only": True,
                    "max_frames": self.args.timeline_max_frames,
                    "imu_enabled": include_imu,
                    "imu_schema": IMU_TIMELINE_SCHEMA,
                    "imu_calibration_schema": IMU_CALIBRATION_SCHEMA,
                    "imu_calibration_revision": IMU_CALIBRATION_REVISION,
                    "imu_timeline_hz": self.args.imu_timeline_hz,
                    "imu_max_frames": self.args.imu_timeline_max_frames,
                },
            }
            write_json_atomic(root / "metadata.json", metadata)
            self.preview_scan = {}
            self.analysis = {}
            self.lidar_frames = []
            self.camera_frames = []
            self.tracks = {}
            self.next_track_id = 1
            self.next_lidar_sample_at = 0.0
            self.next_camera_sample_at = 0.0
            self.camera_pending_sample = None
            self.camera_pending_event.clear()
            self.camera_idle_event.set()
            self.shared_vision_payloads = []
            self.shared_vision_received_count = 0
            self.shared_vision_last_received_monotonic = None
            self.shared_vision_provenance_rejected_count = 0
            self.camera_enqueued_count = 0
            self.camera_coalesced_frame_count = 0
            self.camera_archive_count = 0
            self.camera_shared_vision_match_count = 0
            self.camera_shared_vision_miss_count = 0
            self.camera_last_archive_duration_ms = None
            self.camera_last_vision_sync_delta_s = None
            self.camera_last_input_policy = {}
            self.imu_frames = []
            self.imu_latest_corrected = {}
            self.imu_recording_raw_count = 0
            self.imu_corrected_published_count = 0
            self.imu_timeline_count = 0
            self.next_imu_sample_at = 0.0
            started_steady = time.monotonic()
            if include_imu:
                self.imu_calibrator.begin(recording_id, now_monotonic=started_steady)
            else:
                self.imu_calibrator.reset()
            self.active = {
                **metadata,
                "path": str(root),
                "started_monotonic": time.time(),
                "started_steady": started_steady,
            }
            self.process = self.start_rosbag(root / "rosbag2", topics)
        log("start", id=recording_id, kind=kind, include_imu=include_imu, topics=topics, rosbag_pid=self.process.pid if self.process else None)
        self.write_status("start_recording")
        return {"ok": True, "state": "recording", "status": self.status()}

    def stop(self) -> dict[str, Any]:
        with self.lock:
            active = dict(self.active or {})
            proc = self.process
            if self.active:
                self.active["stopping"] = True
        if not active:
            self.write_status("stop_idle")
            return {"ok": True, "state": "idle", "status": self.status()}
        # Let the non-ROS archive worker finish the already sampled newest
        # frame. New camera samples are rejected once `stopping` is set.
        self.camera_idle_event.wait(timeout=max(0.1, float(self.args.camera_archive_flush_timeout_s)))
        with self.lock:
            self.active = None
            self.process = None
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
                    "imu_frames": len(self.imu_frames),
                    "sample_hz": self.args.timeline_hz,
                    "camera_preview_width": self.args.camera_preview_width,
                    "camera_input_policy_revision": VISION_INFERENCE_INPUT_POLICY_REVISION,
                    "camera_inference_owner": "camera_ws_bridge",
                    "camera_independent_inference": False,
                    "camera_preview_decoupled": True,
                    "camera_latest_only": True,
                    "imu_enabled": bool(active.get("include_imu", True)),
                    "imu_schema": IMU_TIMELINE_SCHEMA,
                    "imu_calibration_revision": IMU_CALIBRATION_REVISION,
                    "imu_calibration_valid": bool(self.imu_calibrator.ready),
                    "imu_timeline_hz": self.args.imu_timeline_hz,
                },
            }
        )
        write_json_atomic(root / "metadata.json", metadata)
        write_json_atomic(root / "preview_scan.json", self.preview_scan or {"ok": False, "points": [], "message": "no lidar scan captured"})
        write_json_atomic(root / "analysis.json", self.analysis or self.empty_analysis())
        write_json_atomic(root / "imu_calibration.json", self.imu_calibrator.status())
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
            "camera": {
                "frames": list(self.camera_frames),
                "input_policy_revision": VISION_INFERENCE_INPUT_POLICY_REVISION,
                "inference_owner": "camera_ws_bridge",
                "independent_inference": False,
                "preview_width": int(self.args.camera_preview_width),
                "preview_jpeg_quality": int(self.args.camera_jpeg_quality),
                "preview_decoupled": True,
                "latest_only": True,
                "coalescing": "single_slot_archive_worker_and_dds_best_effort_keep_last_1",
                "shared_vision_sync_max_delta_s": float(self.args.camera_vision_sync_max_delta_s),
                "shared_vision_wait_timeout_s": float(self.args.camera_vision_wait_timeout_s),
                "enqueued_frame_count": self.camera_enqueued_count,
                "coalesced_frame_count": self.camera_coalesced_frame_count,
                "shared_vision_match_count": self.camera_shared_vision_match_count,
                "shared_vision_miss_count": self.camera_shared_vision_miss_count,
                "shared_vision_provenance_rejected_count": self.shared_vision_provenance_rejected_count,
            },
            "imu": {
                "schema": IMU_TIMELINE_SCHEMA,
                "enabled": bool(active.get("include_imu", True)),
                "raw_topic": self.args.imu_raw_topic,
                "corrected_topic": self.args.imu_corrected_topic,
                "mag_topic": self.args.imu_mag_topic,
                "calibration_topic": self.args.imu_calibration_topic,
                "sample_hz": float(self.args.imu_timeline_hz),
                "frames": list(self.imu_frames),
                "calibration": self.imu_calibrator.status(),
                "raw_received_count": self.imu_recording_raw_count,
                "corrected_published_count": self.imu_corrected_published_count,
            },
            "sync": {
                "time_base": "seconds_since_recording_start",
                "lidar_frames": len(self.lidar_frames),
                "camera_frames": len(self.camera_frames),
                "imu_frames": len(self.imu_frames),
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

    def topics_for(self, kind: str, *, include_imu: bool = True) -> list[str]:
        topics = [
            self.args.cmd_topic,
            self.args.rover_status_topic,
            self.args.odom_topic,
            self.args.tf_topic,
            self.args.tf_static_topic,
            self.args.vision_topic,
        ]
        if include_imu:
            topics.extend(
                [
                    self.args.imu_raw_topic,
                    self.args.imu_corrected_topic,
                    self.args.imu_mag_topic,
                    self.args.imu_driver_status_topic,
                    self.args.imu_calibration_topic,
                ]
            )
        if kind in {"lidar", "both"}:
            topics.append(self.args.scan_topic)
        if kind in {"camera", "both"}:
            topics.extend([self.args.camera_topic, self.args.camera_info_topic])
        return list(dict.fromkeys(topic for topic in topics if str(topic or "").strip()))

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

    def observe_imu(self, msg: Imu) -> dict[str, Any] | None:
        """Consume only the ROS 2 raw IMU topic and return a corrected sample.

        The Dashboard never opens I2C.  A corrected sample is returned only
        while an IMU-enabled recording is active and the start-stationary gate
        has completed successfully.
        """

        now_epoch = time.time()
        now_steady = time.monotonic()
        try:
            orientation_values = (
                float(msg.orientation.x),
                float(msg.orientation.y),
                float(msg.orientation.z),
                float(msg.orientation.w),
            )
            raw = {
                "frame_id": str(msg.header.frame_id or "imu_link"),
                "stamp": {"sec": int(msg.header.stamp.sec), "nanosec": int(msg.header.stamp.nanosec)},
                "orientation": {
                    "x": orientation_values[0],
                    "y": orientation_values[1],
                    "z": orientation_values[2],
                    "w": orientation_values[3],
                },
                "euler_deg": quaternion_to_euler_deg(orientation_values),
                "angular_velocity": {
                    "x": float(msg.angular_velocity.x),
                    "y": float(msg.angular_velocity.y),
                    "z": float(msg.angular_velocity.z),
                },
                "linear_acceleration": {
                    "x": float(msg.linear_acceleration.x),
                    "y": float(msg.linear_acceleration.y),
                    "z": float(msg.linear_acceleration.z),
                },
                "covariance": {
                    "orientation": [float(value) for value in msg.orientation_covariance],
                    "angular_velocity": [float(value) for value in msg.angular_velocity_covariance],
                    "linear_acceleration": [float(value) for value in msg.linear_acceleration_covariance],
                },
                "source": "ros2_sensor_msgs_imu_raw",
            }
            if not all(
                math.isfinite(value)
                for group in (raw["orientation"], raw["angular_velocity"], raw["linear_acceleration"])
                for value in group.values()
            ):
                raise ValueError("non-finite IMU message")
        except (TypeError, ValueError, AttributeError) as exc:
            log("imu_message_rejected", error=str(exc))
            return None

        with self.lock:
            self.imu_latest_raw = raw
            self.imu_last_received_monotonic = now_steady
            self.imu_raw_received_count += 1
            active = dict(self.active or {})
            if not active or not active.get("include_imu", True) or active.get("stopping"):
                return None
            self.imu_recording_raw_count += 1
            self.imu_calibrator.observe(raw, now_monotonic=now_steady)
            corrected = self.imu_calibrator.correct(raw)
            if corrected:
                self.imu_latest_corrected = corrected
                self.imu_corrected_published_count += 1
            if now_steady >= self.next_imu_sample_at:
                self.next_imu_sample_at = now_steady + 1.0 / max(0.1, float(self.args.imu_timeline_hz))
                magnetic = dict(self.imu_latest_magnetic)
                magnetic_age_s = None
                if magnetic.get("received_monotonic") is not None:
                    magnetic_age_s = max(0.0, now_steady - float(magnetic["received_monotonic"]))
                magnetic.pop("received_monotonic", None)
                corrected_magnetic = None
                if magnetic:
                    corrected_magnetic = self.imu_calibrator.correct_magnetic(magnetic.get("magnetic_field_t") or {})
                frame = {
                    "t_s": round(now_epoch - float(active.get("started_monotonic", now_epoch)), 6),
                    "stamp": dict(raw["stamp"]),
                    "raw": copy.deepcopy(raw),
                    "corrected": copy.deepcopy(corrected),
                    "magnetic": magnetic if magnetic and magnetic_age_s is not None and magnetic_age_s <= 0.25 else {},
                    "corrected_magnetic_field_t": (
                        corrected_magnetic
                        if corrected_magnetic is not None and magnetic_age_s is not None and magnetic_age_s <= 0.25
                        else {}
                    ),
                    "magnetic_age_s": round(magnetic_age_s, 6) if magnetic_age_s is not None else None,
                    "calibration_state": self.imu_calibrator.status(now_steady)["state"],
                }
                self.append_imu_limited(frame)
                self.imu_timeline_count += 1
            return copy.deepcopy(corrected)

    def observe_magnetic(self, msg: MagneticField) -> None:
        try:
            magnetic_t = {
                "x": float(msg.magnetic_field.x),
                "y": float(msg.magnetic_field.y),
                "z": float(msg.magnetic_field.z),
            }
            if not all(math.isfinite(value) for value in magnetic_t.values()):
                return
            row = {
                "frame_id": str(msg.header.frame_id or "imu_link"),
                "stamp": {"sec": int(msg.header.stamp.sec), "nanosec": int(msg.header.stamp.nanosec)},
                "magnetic_field_t": magnetic_t,
                "magnetic_field_uT": {key: value * 1e6 for key, value in magnetic_t.items()},
                "source": "ros2_sensor_msgs_magnetic_field",
                "received_monotonic": time.monotonic(),
            }
        except (TypeError, ValueError, AttributeError):
            return
        with self.lock:
            self.imu_latest_magnetic = row

    def observe_camera(self, msg: Image) -> None:
        """Copy the newest sampled ROS image into a single archive slot.

        No JPEG encoding, HTTP inference, or vision wait is allowed in this
        ROS callback.  The bridge is the sole AI inference owner; a dedicated
        archive worker stamp-matches its published result later.
        """

        with self.lock:
            if (
                not self.active
                or self.active.get("kind") not in {"camera", "both"}
                or self.active.get("stopping")
            ):
                return
            started_monotonic = float(self.active.get("started_monotonic", time.time()))
            recording_id = str(self.active.get("id") or "")
            now = time.time()
            if now < self.next_camera_sample_at:
                return
            self.next_camera_sample_at = now + self.sample_interval_s()
        try:
            sample = {
                "recording_id": recording_id,
                "started_monotonic": started_monotonic,
                "received_at": now,
                "frame_id": str(msg.header.frame_id or "camera_link_optical"),
                "stamp": {"sec": int(msg.header.stamp.sec), "nanosec": int(msg.header.stamp.nanosec)},
                "height": int(getattr(msg, "height", 0) or 0),
                "width": int(getattr(msg, "width", 0) or 0),
                "encoding": str(getattr(msg, "encoding", "") or ""),
                "data": bytes(getattr(msg, "data", b"") or b""),
            }
            if sample["height"] <= 0 or sample["width"] <= 0 or not sample["data"]:
                raise ValueError("empty image message")
        except Exception as exc:
            log("camera_enqueue_error", error=str(exc))
            return
        with self.lock:
            if (
                not self.active
                or str(self.active.get("id") or "") != recording_id
                or self.active.get("stopping")
            ):
                return
            if self.camera_pending_sample is not None:
                self.camera_coalesced_frame_count += 1
            self.camera_pending_sample = sample
            self.camera_enqueued_count += 1
            self.camera_idle_event.clear()
            self.camera_pending_event.set()

    def observe_vision(self, msg: String) -> None:
        try:
            payload = json.loads(str(getattr(msg, "data", "") or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        if (
            payload.get("schema") != "orchard.camera_bearings.v1"
            or payload.get("algorithm") != LOCAL_VISION_ALGORITHM_NAME
            or payload.get("revision") != LOCAL_VISION_ANALYSIS_REVISION
        ):
            return
        if not current_source_native_inference_policy(payload.get("inference_input_policy")):
            with self.lock:
                self.shared_vision_provenance_rejected_count += 1
            return
        stamp = payload.get("stamp") if isinstance(payload.get("stamp"), dict) else {}
        try:
            stamp_ns = int(payload.get("stamp_ns") or (int(stamp.get("sec") or 0) * 1_000_000_000 + int(stamp.get("nanosec") or 0)))
        except (TypeError, ValueError):
            return
        if stamp_ns <= 0:
            return
        row = {**copy.deepcopy(payload), "_stamp_ns": stamp_ns, "_received_monotonic": time.monotonic()}
        with self.vision_condition:
            self.shared_vision_payloads.append(row)
            self.shared_vision_payloads = self.shared_vision_payloads[-32:]
            self.shared_vision_received_count += 1
            self.shared_vision_last_received_monotonic = time.monotonic()
            self.vision_condition.notify_all()

    def camera_archive_worker(self, shutdown: threading.Event) -> None:
        while not shutdown.is_set():
            self.camera_pending_event.wait(timeout=0.1)
            if shutdown.is_set():
                break
            while not shutdown.is_set():
                with self.lock:
                    sample = self.camera_pending_sample
                    self.camera_pending_sample = None
                    if sample is None:
                        self.camera_pending_event.clear()
                        self.camera_idle_event.set()
                        break
                self.process_camera_sample(sample)
                with self.lock:
                    if self.camera_pending_sample is None:
                        self.camera_pending_event.clear()
                        self.camera_idle_event.set()
                        break

    def shared_vision_for_stamp(self, stamp_ns: int) -> tuple[dict[str, Any] | None, float | None]:
        deadline = time.monotonic() + max(0.0, float(self.args.camera_vision_wait_timeout_s))
        best: dict[str, Any] | None = None
        best_delta_s: float | None = None
        while True:
            with self.vision_condition:
                for payload in self.shared_vision_payloads:
                    delta_s = abs(int(payload.get("_stamp_ns") or 0) - stamp_ns) / 1_000_000_000
                    if best_delta_s is None or delta_s < best_delta_s:
                        best = payload
                        best_delta_s = delta_s
                if best_delta_s is not None and best_delta_s <= 0.005:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                self.vision_condition.wait(timeout=min(remaining, 0.05))
        max_delta = max(0.0, float(self.args.camera_vision_sync_max_delta_s))
        if best is None or best_delta_s is None or best_delta_s > max_delta:
            return None, best_delta_s
        return copy.deepcopy(best), best_delta_s

    def shared_vision_overlay(
        self,
        payload: dict[str, Any] | None,
        source_width: int,
        source_height: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if payload is None or payload.get("ok") is not True:
            overlay = empty_camera_analysis(
                "shared_vision_sync_unavailable",
                "No current camera bridge inference was available inside the bounded sensor-stamp gate.",
            )
            overlay["image"] = {"width": source_width, "height": source_height}
            return overlay, {
                "revision": VISION_INFERENCE_INPUT_POLICY_REVISION,
                "mode": "unavailable",
                "source_width": source_width,
                "source_height": source_height,
                "preview_decoupled": True,
            }
        policy = payload.get("inference_input_policy") if isinstance(payload.get("inference_input_policy"), dict) else {}
        inference_width = int(policy.get("inference_width") or 0)
        inference_height = int(policy.get("inference_height") or 0)
        if not current_source_native_inference_policy(
            policy,
            source_width=source_width,
            source_height=source_height,
        ):
            overlay = empty_camera_analysis(
                "shared_vision_input_policy_invalid",
                "The shared inference payload did not provide current input-resolution provenance.",
            )
            overlay["image"] = {"width": source_width, "height": source_height}
            return overlay, {
                "revision": VISION_INFERENCE_INPUT_POLICY_REVISION,
                "mode": "unavailable",
                "source_width": source_width,
                "source_height": source_height,
                "preview_decoupled": True,
            }
        detections = []
        for item in payload.get("detections") or []:
            if not isinstance(item, dict):
                continue
            detection = copy.deepcopy(item)
            detection["admission"] = "accepted"
            detection["map_admission"] = "accepted"
            detection["stable"] = True
            detection["stable_hits"] = max(2, int(detection.get("stable_hits") or 2))
            detections.append(detection)
        overlay = {
            "ok": True,
            "algorithm": LOCAL_VISION_ALGORITHM_NAME,
            "revision": LOCAL_VISION_ANALYSIS_REVISION,
            "image": {"width": inference_width, "height": inference_height},
            "detections": detections,
            "stabilized": True,
            "source": "camera_ws_bridge_stamp_matched_ros_bearings",
            "inference_input_policy": copy.deepcopy(policy),
        }
        return overlay, copy.deepcopy(policy)

    def process_camera_sample(self, sample: dict[str, Any]) -> None:
        started = time.perf_counter()
        stamp = sample.get("stamp") or {}
        stamp_ns = int(stamp.get("sec") or 0) * 1_000_000_000 + int(stamp.get("nanosec") or 0)
        vision_payload, sync_delta_s = self.shared_vision_for_stamp(stamp_ns)
        try:
            source_frame = self.image_sample_to_bgr(sample)
            source_height, source_width = source_frame.shape[:2]
            overlay, input_policy = self.shared_vision_overlay(vision_payload, source_width, source_height)
            vision_matched = vision_payload is not None and overlay.get("ok") is True
            preview_width = max(160, int(self.args.camera_preview_width))
            if source_width > preview_width:
                scale = preview_width / source_width
                preview_height = max(1, int(round(source_height * scale)))
                preview_frame = cv2.resize(source_frame, (preview_width, preview_height), interpolation=cv2.INTER_AREA)
            else:
                preview_frame = source_frame
            overlay = scale_camera_overlay_geometry(overlay, int(preview_frame.shape[1]), int(preview_frame.shape[0]))
            ok, encoded = cv2.imencode(
                ".jpg",
                preview_frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), int(self.args.camera_jpeg_quality)],
            )
            if not ok:
                return
        except Exception as exc:
            log("camera_archive_error", error=str(exc))
            return
        payload = {
            "t_s": round(float(sample["received_at"]) - float(sample["started_monotonic"]), 3),
            "frame_id": str(sample.get("frame_id") or "camera_link_optical"),
            "stamp": {"sec": int(stamp.get("sec") or 0), "nanosec": int(stamp.get("nanosec") or 0)},
            "mime": "image/jpeg",
            "data": base64.b64encode(encoded.tobytes()).decode("ascii"),
            "width": int(preview_frame.shape[1]),
            "height": int(preview_frame.shape[0]),
            "source_width": int(source_width),
            "source_height": int(source_height),
            "inference_input_policy": dict(input_policy),
            "vision_sync": {
                "matched": vision_matched,
                "delta_s": round(sync_delta_s, 6) if sync_delta_s is not None else None,
                "max_delta_s": float(self.args.camera_vision_sync_max_delta_s),
                "source": "camera_ws_bridge_ros_bearings",
                "independent_inference": False,
            },
            "overlay": overlay,
        }
        with self.lock:
            active_id = str((self.active or {}).get("id") or "")
            if active_id != str(sample.get("recording_id") or ""):
                return
            self.camera_archive_count += 1
            if not vision_matched:
                self.camera_shared_vision_miss_count += 1
            else:
                self.camera_shared_vision_match_count += 1
            self.camera_last_archive_duration_ms = round((time.perf_counter() - started) * 1000.0, 1)
            self.camera_last_vision_sync_delta_s = round(sync_delta_s, 6) if sync_delta_s is not None else None
            self.camera_last_input_policy = dict(input_policy)
            self.append_limited(self.camera_frames, payload)

    def sample_interval_s(self) -> float:
        return 1.0 / max(0.1, float(self.args.timeline_hz))

    def append_limited(self, rows: list[dict[str, Any]], item: dict[str, Any]) -> None:
        rows.append(item)
        limit = max(1, int(self.args.timeline_max_frames))
        if len(rows) > limit:
            del rows[: len(rows) - limit]

    def append_imu_limited(self, item: dict[str, Any]) -> None:
        self.imu_frames.append(item)
        limit = max(1, int(self.args.imu_timeline_max_frames))
        if len(self.imu_frames) > limit:
            del self.imu_frames[: len(self.imu_frames) - limit]

    def image_sample_to_bgr(self, sample: dict[str, Any]) -> Any:
        height = int(sample.get("height") or 0)
        width = int(sample.get("width") or 0)
        encoding = str(sample.get("encoding") or "").lower()
        data = bytes(sample.get("data") or b"")
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
        self.args = args
        self.last_calibration_publish_at = 0.0
        self.create_subscription(LaserScan, args.scan_topic, self.state.observe_scan, 10)
        camera_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.create_subscription(Image, args.camera_topic, self.state.observe_camera, camera_qos)
        self.create_subscription(String, args.vision_topic, self.state.observe_vision, 10)
        self.corrected_imu_publisher = self.create_publisher(Imu, args.imu_corrected_topic, camera_qos)
        self.calibration_publisher = self.create_publisher(String, args.imu_calibration_topic, 10)
        self.create_subscription(Imu, args.imu_raw_topic, self.observe_imu, camera_qos)
        self.create_subscription(MagneticField, args.imu_mag_topic, self.state.observe_magnetic, camera_qos)

    def observe_imu(self, msg: Imu) -> None:
        corrected = self.state.observe_imu(msg)
        if corrected:
            output = Imu()
            output.header.stamp.sec = int((corrected.get("stamp") or {}).get("sec") or 0)
            output.header.stamp.nanosec = int((corrected.get("stamp") or {}).get("nanosec") or 0)
            output.header.frame_id = str(corrected.get("frame_id") or "imu_link_recording_start")
            orientation = corrected.get("orientation") or {}
            output.orientation.x = float(orientation.get("x") or 0.0)
            output.orientation.y = float(orientation.get("y") or 0.0)
            output.orientation.z = float(orientation.get("z") or 0.0)
            output.orientation.w = float(orientation.get("w") or 1.0)
            angular = corrected.get("angular_velocity") or {}
            output.angular_velocity.x = float(angular.get("x") or 0.0)
            output.angular_velocity.y = float(angular.get("y") or 0.0)
            output.angular_velocity.z = float(angular.get("z") or 0.0)
            acceleration = corrected.get("linear_acceleration") or {}
            output.linear_acceleration.x = float(acceleration.get("x") or 0.0)
            output.linear_acceleration.y = float(acceleration.get("y") or 0.0)
            output.linear_acceleration.z = float(acceleration.get("z") or 0.0)
            covariance = corrected.get("covariance") or {}
            output.orientation_covariance[0] = -1.0
            for offset, axis in zip((0, 4, 8), ("x", "y", "z")):
                output.angular_velocity_covariance[offset] = float(
                    (covariance.get("angular_velocity_diagonal") or {}).get(axis) or 0.0
                )
                output.linear_acceleration_covariance[offset] = float(
                    (covariance.get("linear_acceleration_diagonal") or {}).get(axis) or 0.0
                )
            self.corrected_imu_publisher.publish(output)
        now = time.monotonic()
        if now - self.last_calibration_publish_at >= 0.5:
            calibration = String()
            calibration.data = json.dumps(self.state.imu_calibrator.status(now), ensure_ascii=False, separators=(",", ":"))
            self.calibration_publisher.publish(calibration)
            self.last_calibration_publish_at = now


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
            raw_include_imu = payload.get("include_imu", True)
            include_imu = raw_include_imu if isinstance(raw_include_imu, bool) else str(raw_include_imu).strip().lower() in {"1", "true", "yes", "on"}
            self.send_json(self.state.start(str(payload.get("kind") or ""), include_imu=include_imu))
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
    parser.add_argument("--odom-topic", default=os.environ.get("ODOM_TOPIC", "/odom"))
    parser.add_argument("--imu-raw-topic", default=os.environ.get("IMU_RAW_TOPIC", "/imu/data_raw"))
    parser.add_argument("--imu-corrected-topic", "--imu-topic", dest="imu_corrected_topic", default=os.environ.get("IMU_CORRECTED_TOPIC", os.environ.get("IMU_TOPIC", "/imu/data")))
    parser.add_argument("--imu-mag-topic", default=os.environ.get("IMU_MAG_TOPIC", "/imu/mag"))
    parser.add_argument("--imu-driver-status-topic", default=os.environ.get("IMU_DRIVER_STATUS_TOPIC", "/orchard/imu/driver_status"))
    parser.add_argument("--imu-calibration-topic", default=os.environ.get("IMU_CALIBRATION_TOPIC", "/orchard/imu/calibration"))
    parser.add_argument("--tf-topic", default=os.environ.get("TF_TOPIC", "/tf"))
    parser.add_argument("--tf-static-topic", default=os.environ.get("TF_STATIC_TOPIC", "/tf_static"))
    parser.add_argument("--vision-topic", default=os.environ.get("ORCHARD_VISION_BEARINGS_TOPIC", "/orchard/vision/tree_bearings"))
    parser.add_argument("--lidar-frame-id", default=os.environ.get("LIDAR_FRAME_ID", "laser"))
    parser.add_argument("--preview-max-points", type=int, default=int(os.environ.get("RECORDER_PREVIEW_MAX_POINTS", "1200")))
    parser.add_argument("--timeline-hz", type=float, default=float(os.environ.get("RECORDER_TIMELINE_HZ", "2.0")))
    parser.add_argument("--timeline-max-frames", type=int, default=int(os.environ.get("RECORDER_TIMELINE_MAX_FRAMES", "1800")))
    parser.add_argument("--imu-timeline-hz", type=float, default=float(os.environ.get("RECORDER_IMU_TIMELINE_HZ", "10.0")))
    parser.add_argument("--imu-timeline-max-frames", type=int, default=int(os.environ.get("RECORDER_IMU_TIMELINE_MAX_FRAMES", "18000")))
    parser.add_argument("--imu-calibration-window-s", type=float, default=float(os.environ.get("RECORDER_IMU_CALIBRATION_WINDOW_S", "1.5")))
    parser.add_argument("--imu-calibration-min-samples", type=int, default=int(os.environ.get("RECORDER_IMU_CALIBRATION_MIN_SAMPLES", "20")))
    parser.add_argument("--imu-stationary-max-gyro-rad-s", type=float, default=float(os.environ.get("RECORDER_IMU_STATIONARY_MAX_GYRO_RAD_S", "0.08")))
    parser.add_argument("--imu-stationary-gravity-tolerance-m-s2", type=float, default=float(os.environ.get("RECORDER_IMU_STATIONARY_GRAVITY_TOLERANCE_M_S2", "2.0")))
    parser.add_argument("--imu-stationary-max-accel-std-m-s2", type=float, default=float(os.environ.get("RECORDER_IMU_STATIONARY_MAX_ACCEL_STD_M_S2", "0.35")))
    parser.add_argument("--camera-preview-width", type=int, default=int(os.environ.get("RECORDER_CAMERA_PREVIEW_WIDTH", "640")))
    parser.add_argument("--camera-jpeg-quality", type=int, default=int(os.environ.get("RECORDER_CAMERA_JPEG_QUALITY", "68")))
    parser.add_argument(
        "--camera-vision-sync-max-delta-s",
        type=float,
        default=float(os.environ.get("RECORDER_SHARED_VISION_SYNC_MAX_DELTA_S", "0.12")),
    )
    parser.add_argument(
        "--camera-vision-wait-timeout-s",
        type=float,
        default=float(os.environ.get("RECORDER_SHARED_VISION_WAIT_TIMEOUT_S", "0.35")),
    )
    parser.add_argument(
        "--camera-archive-flush-timeout-s",
        type=float,
        default=float(os.environ.get("RECORDER_CAMERA_ARCHIVE_FLUSH_TIMEOUT_S", "0.75")),
    )
    parser.add_argument("--rosbag-stop-timeout-s", type=float, default=float(os.environ.get("RECORDER_ROSBAG_STOP_TIMEOUT_S", "8.0")))
    parser.add_argument("--disable-rosbag", default=os.environ.get("RECORDER_DISABLE_ROSBAG", "0").strip().lower() in {"1", "true", "yes", "on"}, action=argparse.BooleanOptionalAction)
    args = parser.parse_args()
    args.imu_timeline_hz = max(0.5, min(100.0, float(args.imu_timeline_hz)))
    args.imu_timeline_max_frames = max(100, min(360_000, int(args.imu_timeline_max_frames)))
    args.imu_calibration_window_s = max(0.5, min(10.0, float(args.imu_calibration_window_s)))
    args.imu_calibration_min_samples = max(5, min(1000, int(args.imu_calibration_min_samples)))
    args.camera_vision_sync_max_delta_s = max(0.01, min(0.5, args.camera_vision_sync_max_delta_s))
    args.camera_vision_wait_timeout_s = max(0.0, min(2.0, args.camera_vision_wait_timeout_s))
    args.camera_archive_flush_timeout_s = max(0.1, min(3.0, args.camera_archive_flush_timeout_s))
    args.camera_preview_width = max(160, min(3840, args.camera_preview_width))
    args.camera_jpeg_quality = max(40, min(95, args.camera_jpeg_quality))

    state = RecordingState(args)
    Handler.state = state
    server = Server((args.host, args.port), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    camera_shutdown = threading.Event()
    camera_thread = threading.Thread(target=state.camera_archive_worker, args=(camera_shutdown,), daemon=True)
    camera_thread.start()
    log("listen", host=args.host, port=args.port, archive_dir=args.archive_dir)

    rclpy.init()
    node = RecorderNode(args, state)
    try:
        rclpy.spin(node)
    finally:
        state.stop()
        camera_shutdown.set()
        state.camera_pending_event.set()
        camera_thread.join(timeout=2)
        node.destroy_node()
        rclpy.shutdown()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
