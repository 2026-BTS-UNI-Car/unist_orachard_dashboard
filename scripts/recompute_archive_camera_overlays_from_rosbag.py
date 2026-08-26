#!/usr/bin/env python3
"""Rebuild archived camera overlays from source-native ROS2 Image messages.

Legacy timelines contain a display JPEG and cannot prove that AI inference saw
the source image.  This tool stamp-matches each timeline preview to the raw
Image in its MCAP bag, runs the current local worker on that native image, and
stores explicit inference provenance while retaining the small display JPEG.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_server.camera_analysis import (
    CameraOverlayStabilizer,
    DEFAULT_VISION_INFERENCE_JPEG_QUALITY,
    LOCAL_VISION_ALGORITHM_NAME,
    LOCAL_VISION_ANALYSIS_REVISION,
    VISION_INFERENCE_INPUT_POLICY_REVISION,
    analyze_camera_image,
    current_source_native_camera_overlay,
    prepare_camera_inference_frame,
    scale_camera_overlay_geometry,
)

TOOL_REVISION = "rosbag_source_native_archive_reinference_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", default=os.environ.get("RECORDING_ARCHIVE_DIR", str(ROOT / "runtime" / "recordings")))
    parser.add_argument("--recording-id", action="append", required=True)
    parser.add_argument("--camera-topic", default="/camera/image_raw")
    parser.add_argument("--max-delta-s", type=float, default=0.12)
    parser.add_argument("--jpeg-quality", type=int, default=DEFAULT_VISION_INFERENCE_JPEG_QUALITY)
    parser.add_argument("--checkpoint-every", type=int, default=50)
    parser.add_argument("--backup-suffix", default=".before_source_native_reinference.bak")
    parser.add_argument("--drop-unmatched", action="store_true", help="Remove preview-only frames with no close raw bag frame.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def stamp_ns(stamp: Any) -> int:
    if not isinstance(stamp, dict):
        return 0
    try:
        return int(stamp.get("sec") or 0) * 1_000_000_000 + int(stamp.get("nanosec") or 0)
    except (TypeError, ValueError):
        return 0


def message_stamp_ns(message: Any) -> int:
    try:
        return int(message.header.stamp.sec) * 1_000_000_000 + int(message.header.stamp.nanosec)
    except (TypeError, ValueError, AttributeError):
        return 0


def image_message_to_bgr(message: Any, cv2: Any, np: Any) -> Any:
    height = int(getattr(message, "height", 0) or 0)
    width = int(getattr(message, "width", 0) or 0)
    step = int(getattr(message, "step", 0) or 0)
    encoding = str(getattr(message, "encoding", "") or "").lower()
    raw = bytes(getattr(message, "data", b"") or b"")
    channels = 4 if encoding in {"bgra8", "rgba8"} else 1 if encoding in {"mono8", "8uc1"} else 3
    if height <= 0 or width <= 0 or step < width * channels or len(raw) < height * step:
        raise ValueError("invalid or empty ROS Image message")
    rows = np.frombuffer(raw, dtype=np.uint8, count=height * step).reshape((height, step))
    pixels = rows[:, : width * channels]
    if channels == 1:
        return cv2.cvtColor(pixels.reshape((height, width)), cv2.COLOR_GRAY2BGR)
    image = pixels.reshape((height, width, channels))
    if encoding == "bgr8":
        return image.copy()
    if encoding == "rgb8":
        return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    if encoding == "bgra8":
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    if encoding == "rgba8":
        return cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
    raise ValueError(f"unsupported ROS Image encoding: {encoding}")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def backup_timeline(path: Path, suffix: str) -> Path:
    backup = path.with_name(path.name + suffix)
    if not backup.exists():
        shutil.copy2(path, backup)
    return backup


def sync_metadata_timeline(path: Path, timeline: dict[str, Any], suffix: str, dry_run: bool) -> bool:
    metadata_path = path / "metadata.json"
    if not metadata_path.is_file():
        return False
    metadata = load_json(metadata_path)
    if not metadata:
        return False
    timeline_metadata = metadata.get("timeline") if isinstance(metadata.get("timeline"), dict) else {}
    counts = {
        "lidar_frames": len(((timeline.get("lidar") or {}).get("frames") or [])),
        "camera_frames": len(((timeline.get("camera") or {}).get("frames") or [])),
        "imu_frames": len(((timeline.get("imu") or {}).get("frames") or [])),
    }
    if all(timeline_metadata.get(key) == value for key, value in counts.items()):
        return False
    if dry_run:
        return True
    backup_timeline(metadata_path, suffix)
    metadata["timeline"] = {**timeline_metadata, **counts}
    write_json_atomic(metadata_path, metadata)
    return True


def source_policy(source_width: int, source_height: int, jpeg_quality: int) -> dict[str, Any]:
    # Use the production helper so a changed contract fails validation instead
    # of being duplicated silently in this repair path.
    class ShapeOnly:
        shape = (source_height, source_width, 3)

    _, policy = prepare_camera_inference_frame(ShapeOnly(), requested_width=0)
    policy["jpeg_quality"] = max(40, min(95, int(jpeg_quality)))
    return policy


def update_frame_from_message(
    frame: dict[str, Any],
    message: Any,
    bag_time_ns: int,
    delta_s: float,
    bag_path: Path,
    camera_topic: str,
    jpeg_quality: int,
    max_delta_s: float,
    stabilizer: CameraOverlayStabilizer,
    cv2: Any,
    np: Any,
) -> tuple[bool, str]:
    source = image_message_to_bgr(message, cv2, np)
    source_height, source_width = (int(value) for value in source.shape[:2])
    if int(frame.get("source_width") or 0) not in {0, source_width} or int(frame.get("source_height") or 0) not in {0, source_height}:
        return False, "source_dimensions_mismatch"
    policy = source_policy(source_width, source_height, jpeg_quality)
    overlay = analyze_camera_image(source, jpeg_quality=jpeg_quality)
    if overlay.get("ok") is not True:
        return False, str(overlay.get("method") or overlay.get("error") or "vision_inference_failed")
    overlay["inference_input_policy"] = copy.deepcopy(policy)
    overlay = stabilizer.update(overlay, float(frame.get("t_s") or 0.0))
    preview_width = int(frame.get("width") or source_width)
    preview_height = int(frame.get("height") or source_height)
    overlay = scale_camera_overlay_geometry(overlay, preview_width, preview_height)
    candidate = {**frame, "inference_input_policy": policy, "overlay": overlay}
    if not current_source_native_camera_overlay(overlay, frame=candidate):
        return False, "generated_overlay_failed_source_native_validation"
    raw = bytes(getattr(message, "data", b"") or b"")
    frame["source_width"] = source_width
    frame["source_height"] = source_height
    frame["inference_input_policy"] = policy
    frame["overlay"] = overlay
    frame["vision_sync"] = {
        "matched": True,
        "delta_s": round(float(delta_s), 9),
        "max_delta_s": float(max_delta_s),
        "source": TOOL_REVISION,
        "independent_inference": True,
    }
    frame["archive_reprocessing"] = {
        "revision": TOOL_REVISION,
        "source": "rosbag2_sensor_msgs_image",
        "bag": bag_path.name,
        "topic": camera_topic,
        "bag_time_ns": int(bag_time_ns),
        "source_stamp_ns": message_stamp_ns(message),
        "timeline_stamp_ns": stamp_ns(frame.get("stamp")),
        "match_delta_s": round(float(delta_s), 9),
        "source_encoding": str(getattr(message, "encoding", "") or ""),
        "source_data_sha256": hashlib.sha256(raw).hexdigest(),
        "processed_at": time.time(),
    }
    return True, "ok"


def process_recording(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import rosbag2_py  # type: ignore
        from rclpy.serialization import deserialize_message  # type: ignore
        from rosidl_runtime_py.utilities import get_message  # type: ignore
    except Exception as exc:
        raise RuntimeError("ROS2, rosbag2_py, NumPy, and OpenCV are required") from exc

    timeline_path = path / "timeline.json"
    bag_path = path / "rosbag2"
    timeline = load_json(timeline_path)
    frames = ((timeline.get("camera") or {}).get("frames") or [])
    if not isinstance(frames, list):
        raise ValueError(f"camera frames are missing: {timeline_path}")
    targets = []
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            continue
        if current_source_native_camera_overlay(frame.get("overlay"), frame=frame) and not args.force:
            continue
        target_ns = stamp_ns(frame.get("stamp"))
        if target_ns > 0:
            targets.append((target_ns, index, frame))
    targets.sort(key=lambda row: row[0])
    summary = {
        "id": path.name,
        "camera_frames": len(frames),
        "targets": len(targets),
        "updated": 0,
        "unmatched": 0,
        "failed": 0,
        "dropped": 0,
        "errors": {},
        "max_match_delta_s": 0.0,
        "max_candidate_delta_s": 0.0,
        "dry_run": bool(args.dry_run),
        "metadata_updated": False,
    }
    if not targets:
        summary["metadata_updated"] = sync_metadata_timeline(path, timeline, args.backup_suffix, args.dry_run)
        return summary

    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(bag_path), storage_id="mcap"),
        rosbag2_py.ConverterOptions(input_serialization_format="cdr", output_serialization_format="cdr"),
    )
    reader.set_filter(rosbag2_py.StorageFilter(topics=[args.camera_topic]))
    topic_types = {item.name: item.type for item in reader.get_all_topics_and_types()}
    if args.camera_topic not in topic_types:
        raise ValueError(f"camera topic not present in bag: {args.camera_topic}")
    message_type = get_message(topic_types[args.camera_topic])
    target_index = 0
    previous: tuple[int, Any, int] | None = None
    matched_indices: set[int] = set()
    stabilizer = CameraOverlayStabilizer()
    backup_created = False

    def record_error(reason: str) -> None:
        summary["errors"][reason] = int(summary["errors"].get(reason) or 0) + 1

    def process_target(target: tuple[int, int, dict[str, Any]], candidate: tuple[int, Any, int] | None) -> None:
        nonlocal backup_created
        target_stamp, frame_index, frame = target
        if candidate is None:
            summary["unmatched"] += 1
            record_error("no_source_message")
            return
        source_stamp, message, bag_time_ns = candidate
        delta_s = abs(source_stamp - target_stamp) / 1_000_000_000
        summary["max_candidate_delta_s"] = max(float(summary["max_candidate_delta_s"]), delta_s)
        if delta_s > max(0.0, float(args.max_delta_s)):
            summary["unmatched"] += 1
            record_error("source_stamp_outside_gate")
            return
        summary["max_match_delta_s"] = max(float(summary["max_match_delta_s"]), delta_s)
        matched_indices.add(frame_index)
        if args.dry_run:
            summary["updated"] += 1
            return
        ok, reason = update_frame_from_message(
            frame,
            message,
            bag_time_ns,
            delta_s,
            bag_path,
            args.camera_topic,
            args.jpeg_quality,
            args.max_delta_s,
            stabilizer,
            cv2,
            np,
        )
        if ok:
            summary["updated"] += 1
            if not backup_created:
                backup_timeline(timeline_path, args.backup_suffix)
                backup_created = True
            if args.checkpoint_every > 0 and summary["updated"] % int(args.checkpoint_every) == 0:
                write_json_atomic(timeline_path, timeline)
            if summary["updated"] % 10 == 0:
                print(json.dumps({"id": path.name, "progress": summary["updated"], "targets": len(targets)}), flush=True)
        else:
            summary["failed"] += 1
            record_error(reason)

    while reader.has_next() and target_index < len(targets):
        topic, serialized, bag_time_ns = reader.read_next()
        if topic != args.camera_topic:
            continue
        message = deserialize_message(serialized, message_type)
        current_stamp = message_stamp_ns(message)
        if current_stamp <= 0:
            continue
        current = (current_stamp, message, int(bag_time_ns))
        while target_index < len(targets) and targets[target_index][0] <= current_stamp:
            target_stamp = targets[target_index][0]
            candidate = current
            if previous is not None and abs(previous[0] - target_stamp) <= abs(current_stamp - target_stamp):
                candidate = previous
            process_target(targets[target_index], candidate)
            target_index += 1
        previous = current

    while target_index < len(targets):
        process_target(targets[target_index], previous)
        target_index += 1

    if args.drop_unmatched and not args.dry_run:
        target_indices = {index for _, index, _ in targets}
        rejected = target_indices - matched_indices
        if rejected:
            if not backup_created:
                backup_timeline(timeline_path, args.backup_suffix)
                backup_created = True
            frames[:] = [frame for index, frame in enumerate(frames) if index not in rejected]
            summary["dropped"] = len(rejected)

    if backup_created and not args.dry_run:
        precompute = timeline.setdefault("precompute", {})
        precompute["camera_overlay"] = {
            "algorithm": LOCAL_VISION_ALGORITHM_NAME,
            "revision": LOCAL_VISION_ANALYSIS_REVISION,
            "tool_revision": TOOL_REVISION,
            "updated_at": time.time(),
            "updated_frames": summary["updated"],
            "unmatched_frames": summary["unmatched"],
            "failed_frames": summary["failed"],
            "dropped_frames": summary["dropped"],
            "required_input_policy": {
                "revision": VISION_INFERENCE_INPUT_POLICY_REVISION,
                "mode": "source_native",
                "requested_width": 0,
                "jpeg_quality": max(40, min(95, int(args.jpeg_quality))),
            },
            "source": "rosbag2_sensor_msgs_image",
        }
        write_json_atomic(timeline_path, timeline)
    summary["max_match_delta_s"] = round(float(summary["max_match_delta_s"]), 9)
    summary["max_candidate_delta_s"] = round(float(summary["max_candidate_delta_s"]), 9)
    summary["metadata_updated"] = sync_metadata_timeline(path, timeline, args.backup_suffix, args.dry_run)
    return summary


def main() -> int:
    args = parse_args()
    archive_dir = Path(args.archive_dir)
    started = time.time()
    results = []
    for recording_id in args.recording_id:
        path = archive_dir / recording_id
        try:
            results.append(process_recording(path, args))
        except Exception as exc:
            results.append({"id": recording_id, "ok": False, "error": str(exc)})
    payload = {
        "ok": all(item.get("ok", True) is not False and not item.get("failed") for item in results),
        "tool_revision": TOOL_REVISION,
        "elapsed_s": round(time.time() - started, 3),
        "recordings": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
