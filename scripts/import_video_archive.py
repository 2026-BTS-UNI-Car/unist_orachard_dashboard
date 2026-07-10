#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cv2


IMPORT_ALGORITHM = "native_h264_video_archive_import_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import H.264 videos as dashboard camera-only archives with a sampled AI overlay timeline."
    )
    parser.add_argument("--input", action="append", required=True, help="Source video. Repeat for multiple files.")
    parser.add_argument(
        "--recording-id",
        action="append",
        default=[],
        help="Archive id matching each --input. Defaults to sample_<stem>_camera.",
    )
    parser.add_argument("--archive-dir", default=os.environ.get("RECORDING_ARCHIVE_DIR", "/app/runtime/recordings"))
    parser.add_argument("--sample-hz", type=float, default=4.0)
    parser.add_argument("--preview-max-dimension", type=int, default=960)
    parser.add_argument("--jpeg-quality", type=int, default=82)
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]", "", value)
    if not normalized or normalized in {".", ".."}:
        raise ValueError(f"invalid recording id: {value!r}")
    return normalized


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def link_or_copy_exact(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    if target.stat().st_size != source.stat().st_size:
        raise RuntimeError("original video copy size mismatch")


def transcode_stream_proxy(source: Path, target: Path) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vf",
        "scale=960:960:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "25",
        "-maxrate",
        "1500k",
        "-bufsize",
        "3M",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-movflags",
        "+faststart",
        str(target),
    ]
    subprocess.run(command, check=True)
    if not target.exists() or target.stat().st_size <= 0:
        raise RuntimeError("ffmpeg produced an empty streaming proxy")


def inspect_video(path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not inspect {path}")
    if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
        capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        capture.release()
    return {
        "fps": round(fps, 6),
        "frame_count": frame_count,
        "duration_s": round(frame_count / fps, 3) if fps > 0 and frame_count > 0 else 0.0,
        "display_width": width,
        "display_height": height,
    }


def sampled_frames(
    source: Path,
    sample_hz: float,
    preview_max_dimension: int,
    jpeg_quality: int,
    started_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open {source}")
    if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
        capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if not math.isfinite(fps) or fps <= 0:
        capture.release()
        raise RuntimeError(f"invalid video fps: {fps}")
    duration_s = frame_count / fps if frame_count > 0 else 0.0
    interval_s = 1.0 / max(0.1, sample_hz)
    next_sample_s = 0.0
    frames: list[dict[str, Any]] = []
    source_width = 0
    source_height = 0
    decoded_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            t_s = decoded_index / fps
            decoded_index += 1
            if t_s + (0.5 / fps) < next_sample_s:
                continue
            source_height, source_width = frame.shape[:2]
            longest = max(source_width, source_height)
            if preview_max_dimension > 0 and longest > preview_max_dimension:
                scale = preview_max_dimension / longest
                frame = cv2.resize(
                    frame,
                    (max(1, round(source_width * scale)), max(1, round(source_height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), max(40, min(95, int(jpeg_quality)))],
            )
            if not ok:
                raise RuntimeError(f"JPEG encode failed at {t_s:.3f}s")
            stamp = started_at + timedelta(seconds=t_s)
            stamp_seconds = stamp.timestamp()
            sec = int(stamp_seconds)
            nanosec = int(round((stamp_seconds - sec) * 1_000_000_000))
            if nanosec >= 1_000_000_000:
                sec += 1
                nanosec -= 1_000_000_000
            frames.append(
                {
                    "t_s": round(t_s, 3),
                    "frame_id": "imported_camera_optical",
                    "stamp": {"sec": sec, "nanosec": nanosec},
                    "mime": "image/jpeg",
                    "data": base64.b64encode(encoded.tobytes()).decode("ascii"),
                    "width": int(frame.shape[1]),
                    "height": int(frame.shape[0]),
                    "source_width": int(source_width),
                    "source_height": int(source_height),
                    "source_frame_index": decoded_index - 1,
                }
            )
            next_sample_s += interval_s
    finally:
        capture.release()
    if not frames:
        raise RuntimeError(f"no frames decoded from {source}")
    if duration_s <= 0:
        duration_s = float(frames[-1]["t_s"])
    return frames, {
        "fps": round(fps, 6),
        "frame_count": frame_count or decoded_index,
        "duration_s": round(duration_s, 3),
        "display_width": source_width,
        "display_height": source_height,
    }


def import_video(source: Path, recording_id: str, args: argparse.Namespace) -> dict[str, Any]:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    archive_dir = Path(args.archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / safe_id(recording_id)
    staging = archive_dir / f".{target.name}.importing"
    if target.exists():
        if not args.replace:
            raise FileExistsError(target)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.perf_counter()
    try:
        original_video_path = staging / "video_original.mov"
        video_path = staging / "video.mp4"
        link_or_copy_exact(source, original_video_path)
        transcode_stream_proxy(source, video_path)
        frames, original_video_info = sampled_frames(
            source,
            sample_hz=max(0.1, float(args.sample_hz)),
            preview_max_dimension=max(160, int(args.preview_max_dimension)),
            jpeg_quality=int(args.jpeg_quality),
            started_at=started_at,
        )
        proxy_video_info = inspect_video(video_path)
        duration_s = float(original_video_info["duration_s"])
        finished_at = started_at + timedelta(seconds=duration_s)
        source_sha256 = sha256_file(source)
        metadata = {
            "id": target.name,
            "kind": "camera",
            "state": "archived",
            "started_at": iso(started_at),
            "finished_at": iso(finished_at),
            "duration_s": duration_s,
            "topics": ["imported_video"],
            "archive_path": f"/app/runtime/recordings/{target.name}",
            "algorithm": IMPORT_ALGORITHM,
            "timeline": {
                "lidar_frames": 0,
                "camera_frames": len(frames),
                "sample_hz": round(float(args.sample_hz), 3),
            },
            "source_video": {
                "file": "video.mp4",
                "mime": "video/mp4",
                "original_name": source.name,
                "original_sha256": source_sha256,
                "original_file": "video_original.mov",
                "original_bytes": original_video_path.stat().st_size,
                "original_fps": original_video_info["fps"],
                "original_frame_count": original_video_info["frame_count"],
                "original_display_width": original_video_info["display_width"],
                "original_display_height": original_video_info["display_height"],
                "bytes": video_path.stat().st_size,
                **proxy_video_info,
                "stream_profile": "h264_960px_30fps_crf25_max1.5mbps_faststart",
            },
            "import": {
                "algorithm": IMPORT_ALGORITHM,
                "source_path": str(source),
                "elapsed_s": round(time.perf_counter() - started_monotonic, 3),
            },
        }
        timeline = {
            "ok": True,
            "version": 2,
            "id": target.name,
            "kind": "camera",
            "algorithm": IMPORT_ALGORITHM,
            "coordinate_frame": "imported_camera_optical",
            "generated_at": iso(datetime.now(timezone.utc)),
            "started_at": metadata["started_at"],
            "finished_at": metadata["finished_at"],
            "duration_s": duration_s,
            "sample_hz": round(float(args.sample_hz), 3),
            "lidar": {"frames": []},
            "camera": {"frames": frames},
            "video": {key: value for key, value in metadata["source_video"].items() if key != "original_sha256"},
            "sync": {
                "time_base": "seconds_since_recording_start",
                "lidar_frames": 0,
                "camera_frames": len(frames),
                "native_video": True,
            },
        }
        write_json(staging / "metadata.json", metadata)
        write_json(
            staging / "preview_scan.json",
            {"ok": False, "points": [], "state": "video_only", "message": "No LiDAR data in imported video archive."},
        )
        write_json(
            staging / "analysis.json",
            {"ok": True, "algorithm": IMPORT_ALGORITHM, "walls": [], "objects": [], "state": "video_only"},
        )
        write_json(staging / "timeline.json", timeline)
        backup = archive_dir / f".{target.name}.replaced"
        shutil.rmtree(backup, ignore_errors=True)
        if target.exists():
            target.replace(backup)
        try:
            staging.replace(target)
        except Exception:
            if backup.exists() and not target.exists():
                backup.replace(target)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "id": target.name,
        "ok": True,
        "kind": "camera",
        "duration_s": duration_s,
        "camera_frames": len(frames),
        "sample_hz": round(float(args.sample_hz), 3),
        "source_sha256": source_sha256,
        "video_bytes": (target / "video.mp4").stat().st_size,
        "original_video_bytes": (target / "video_original.mov").stat().st_size,
        "timeline_bytes": (target / "timeline.json").stat().st_size,
        "elapsed_s": round(time.perf_counter() - started_monotonic, 3),
    }


def main() -> int:
    args = parse_args()
    inputs = [Path(value) for value in args.input]
    ids = list(args.recording_id)
    if ids and len(ids) != len(inputs):
        raise SystemExit("--recording-id count must match --input count")
    if not ids:
        ids = [f"sample_{path.stem}_camera" for path in inputs]
    results = []
    for source, recording_id in zip(inputs, ids, strict=True):
        results.append(import_video(source, recording_id, args))
    print(json.dumps({"ok": True, "algorithm": IMPORT_ALGORITHM, "recordings": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
