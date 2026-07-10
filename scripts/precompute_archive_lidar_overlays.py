#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_server.lidar_analysis import ALGORITHM_NAME, analyze_points, current_lidar_overlay_algorithm


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Precompute continuous LiDAR tracking and OOD overlays for archive playback."
    )
    parser.add_argument(
        "--archive-dir",
        default=os.environ.get("RECORDING_ARCHIVE_DIR", str(ROOT / "runtime" / "recordings")),
    )
    parser.add_argument("--recording-id", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--checkpoint-every", type=int, default=25)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def process_recording(recording: Path, args: argparse.Namespace) -> dict[str, Any]:
    timeline_path = recording / "timeline.json"
    timeline = load_json(timeline_path)
    frames = ((timeline.get("lidar") or {}).get("frames") or [])
    result = {
        "id": recording.name,
        "ok": bool(timeline),
        "lidar_frames": len(frames),
        "current_ok": 0,
        "updated": 0,
        "failed": 0,
        "elapsed_s": 0.0,
    }
    if not timeline or not isinstance(frames, list):
        result["ok"] = False
        return result

    # Keep tracking state continuous across the complete archive.
    tracks: dict[str, dict[str, Any]] = {}
    next_track_id = 1
    started = time.perf_counter()
    changed = False
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            result["failed"] += 1
            continue
        if current_lidar_overlay_algorithm(frame.get("overlay")) and not args.force:
            result["current_ok"] += 1
            continue
        try:
            overlay, tracks, next_track_id = analyze_points(
                frame.get("points") or [],
                tracks,
                next_track_id,
                now=float(frame.get("t_s") or 0.0),
            )
            frame["overlay"] = overlay
            result["updated"] += 1
            changed = True
        except Exception:
            result["failed"] += 1
        if changed and args.checkpoint_every > 0 and (index + 1) % args.checkpoint_every == 0:
            write_json_atomic(timeline_path, timeline)

    if changed:
        timeline.setdefault("precompute", {})["lidar_overlay"] = {
            "algorithm": ALGORITHM_NAME,
            "updated_at": time.time(),
            "updated_frames": result["updated"],
            "failed_frames": result["failed"],
            "source": "scripts/precompute_archive_lidar_overlays.py",
        }
        write_json_atomic(timeline_path, timeline)
    result["elapsed_s"] = round(time.perf_counter() - started, 3)
    return result


def main() -> int:
    args = parse_args()
    root = Path(args.archive_dir)
    if not root.exists():
        print(json.dumps({"ok": False, "error": "archive_dir_not_found", "archive_dir": str(root)}))
        return 2
    recordings = (
        [root / value for value in args.recording_id]
        if args.recording_id
        else sorted(path for path in root.iterdir() if path.is_dir())
    )
    results = [process_recording(recording, args) for recording in recordings]
    payload = {
        "ok": all(item.get("ok") and not item.get("failed") for item in results),
        "algorithm": ALGORITHM_NAME,
        "recordings": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
