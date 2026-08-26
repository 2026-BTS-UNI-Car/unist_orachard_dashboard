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

from dashboard_server.camera_analysis import (
    CameraOverlayStabilizer,
    DEFAULT_VISION_INFERENCE_JPEG_QUALITY,
    LOCAL_VISION_ALGORITHM_NAME,
    LOCAL_VISION_ANALYSIS_REVISION,
    VISION_INFERENCE_INPUT_POLICY_REVISION,
    analyze_camera_frame_payload,
    current_source_native_camera_overlay,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Precompute camera AI overlays for archive timeline.json files so playback can stream cached frames "
            "without running local vision inference on every seek."
        )
    )
    parser.add_argument("--archive-dir", default=os.environ.get("RECORDING_ARCHIVE_DIR", str(ROOT / "runtime" / "recordings")))
    parser.add_argument("--recording-id", action="append", default=[], help="Recording id to process. Can be passed multiple times.")
    parser.add_argument("--force", action="store_true", help="Recompute even current ok overlays.")
    parser.add_argument("--dry-run", action="store_true", help="Report work without writing timeline.json.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of camera frames to process across all recordings.")
    parser.add_argument("--checkpoint-every", type=int, default=25, help="Write timeline.json every N updated frames.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def overlay_is_current_ok(frame: Any) -> bool:
    return isinstance(frame, dict) and current_source_native_camera_overlay(frame.get("overlay"), frame=frame)


def recording_paths(archive_dir: Path, ids: list[str]) -> list[Path]:
    if ids:
        return [archive_dir / rid for rid in ids]
    return sorted(path for path in archive_dir.iterdir() if path.is_dir())


def process_timeline(path: Path, args: argparse.Namespace, remaining: int | None) -> tuple[dict[str, Any], int | None]:
    timeline_path = path / "timeline.json"
    timeline = load_json(timeline_path)
    frames = (((timeline.get("camera") or {}).get("frames")) or [])
    summary = {
        "id": path.name,
        "ok": bool(timeline),
        "camera_frames": len(frames),
        "current_ok": 0,
        "updated": 0,
        "failed": 0,
        "provenance_rejected": 0,
        "skipped": 0,
        "elapsed_s": 0.0,
        "timeline": str(timeline_path),
    }
    if not timeline or not isinstance(frames, list):
        summary["ok"] = False
        return summary, remaining

    started = time.time()
    changed = False
    stabilizer = CameraOverlayStabilizer()
    for frame in frames:
        if remaining is not None and remaining <= 0:
            summary["skipped"] += 1
            continue
        if not isinstance(frame, dict):
            summary["skipped"] += 1
            continue
        if overlay_is_current_ok(frame) and not args.force:
            summary["current_ok"] += 1
            continue
        if not frame.get("data"):
            summary["skipped"] += 1
            continue
        if remaining is not None:
            remaining -= 1
        if args.dry_run:
            summary["updated"] += 1
            continue
        overlay = stabilizer.update(
            analyze_camera_frame_payload(frame, require_source_native_provenance=True),
            float(frame.get("t_s") or 0.0),
        )
        frame["overlay"] = overlay
        if overlay.get("ok"):
            summary["updated"] += 1
        else:
            summary["failed"] += 1
            if overlay.get("method") == "source_native_provenance_required":
                summary["provenance_rejected"] += 1
        changed = True
        if args.checkpoint_every > 0 and summary["updated"] and summary["updated"] % args.checkpoint_every == 0:
            write_json_atomic(timeline_path, timeline)

    if changed and not args.dry_run:
        metadata = timeline.setdefault("precompute", {})
        metadata["camera_overlay"] = {
            "algorithm": LOCAL_VISION_ALGORITHM_NAME,
            "revision": LOCAL_VISION_ANALYSIS_REVISION,
            "updated_at": time.time(),
            "updated_frames": summary["updated"],
            "failed_frames": summary["failed"],
            "provenance_rejected_frames": summary["provenance_rejected"],
            "required_input_policy": {
                "revision": VISION_INFERENCE_INPUT_POLICY_REVISION,
                "mode": "source_native",
                "requested_width": 0,
                "jpeg_quality": DEFAULT_VISION_INFERENCE_JPEG_QUALITY,
            },
            "source": "scripts/precompute_archive_camera_overlays.py",
        }
        write_json_atomic(timeline_path, timeline)
    summary["elapsed_s"] = round(time.time() - started, 3)
    return summary, remaining


def main() -> int:
    args = parse_args()
    archive_dir = Path(args.archive_dir)
    if not archive_dir.exists():
        print(json.dumps({"ok": False, "error": "archive_dir_not_found", "archive_dir": str(archive_dir)}), file=sys.stderr)
        return 2
    remaining = int(args.limit) if args.limit and args.limit > 0 else None
    results = []
    started = time.time()
    for path in recording_paths(archive_dir, args.recording_id):
        summary, remaining = process_timeline(path, args, remaining)
        results.append(summary)
    payload = {
        "ok": all(item.get("ok") for item in results) if results else True,
        "algorithm": LOCAL_VISION_ALGORITHM_NAME,
        "revision": LOCAL_VISION_ANALYSIS_REVISION,
        "dry_run": bool(args.dry_run),
        "recordings": results,
        "elapsed_s": round(time.time() - started, 3),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
