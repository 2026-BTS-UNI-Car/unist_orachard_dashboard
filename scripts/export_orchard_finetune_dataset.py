#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE_DIR = ROOT / "runtime" / "recordings"
DEFAULT_OUTPUT_DIR = ROOT / "runtime" / "finetune_exports" / "orchard_trunk_dataset"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export archived camera preview frames and current AI contour overlays as a YOLO segmentation dataset. "
            "Use the exported labels as editable pre-labels for fine-tuning the orchard trunk detector."
        )
    )
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--recording-id", action="append", default=[], help="Recording id to export. Can be passed multiple times.")
    parser.add_argument("--min-confidence", type=float, default=0.22)
    parser.add_argument("--frame-step", type=int, default=1, help="Export every Nth camera frame after filtering.")
    parser.add_argument("--include-empty", action="store_true", help="Also export frames with no current trunk detections as hard negatives.")
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--class-name", default="trunk")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "recording"


def recording_paths(archive_dir: Path, ids: list[str]) -> list[Path]:
    if ids:
        return [archive_dir / rid for rid in ids]
    return sorted(path for path in archive_dir.iterdir() if path.is_dir())


def decode_frame_data(data: str) -> bytes:
    if data.startswith("data:"):
        data = data.split(",", 1)[1]
    return base64.b64decode(data)


def detection_polygon(det: dict[str, Any], width: int, height: int) -> list[tuple[float, float]]:
    contour = det.get("contour") or []
    points: list[tuple[float, float]] = []
    if isinstance(contour, list):
        for point in contour:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            points.append((float(point[0]), float(point[1])))
    if len(points) < 3:
        bbox = det.get("bbox") or []
        if isinstance(bbox, list) and len(bbox) >= 4:
            x, y, bw, bh = [float(value) for value in bbox[:4]]
            points = [(x, y), (x + bw, y), (x + bw, y + bh), (x, y + bh)]
    cleaned = []
    for x, y in points:
        cleaned.append((max(0.0, min(float(width - 1), x)), max(0.0, min(float(height - 1), y))))
    return cleaned


def yolo_segmentation_line(class_id: int, points: list[tuple[float, float]], width: int, height: int) -> str:
    values = [str(class_id)]
    for x, y in points:
        values.append(f"{x / max(1, width):.6f}")
        values.append(f"{y / max(1, height):.6f}")
    return " ".join(values)


def frame_detections(frame: dict[str, Any], min_confidence: float) -> list[dict[str, Any]]:
    overlay = frame.get("overlay") or {}
    rows = []
    for det in overlay.get("detections") or []:
        if not isinstance(det, dict):
            continue
        if float(det.get("confidence") or 0.0) < min_confidence:
            continue
        label = str(det.get("label") or "").lower()
        if label and not any(hint in label for hint in ("tree", "trunk", "stem", "branch", "canopy")):
            continue
        rows.append(det)
    return rows


def split_name(index: int, val_ratio: float) -> str:
    if val_ratio <= 0:
        return "train"
    if val_ratio >= 1:
        return "val"
    bucket = index % 10
    return "val" if bucket < round(val_ratio * 10) else "train"


def ensure_dirs(output_dir: Path, overwrite: bool) -> None:
    if output_dir.exists() and overwrite:
        import shutil

        shutil.rmtree(output_dir)
    for split in ("train", "val"):
        (output_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_dir / "labels" / split).mkdir(parents=True, exist_ok=True)


def export_dataset(args: argparse.Namespace) -> dict[str, Any]:
    archive_dir = Path(args.archive_dir)
    output_dir = Path(args.output_dir)
    ensure_dirs(output_dir, args.overwrite)
    manifest: list[dict[str, Any]] = []
    frame_index = 0
    exported = 0
    empty = 0
    labeled = 0

    for recording_path in recording_paths(archive_dir, args.recording_id):
        timeline = load_json(recording_path / "timeline.json")
        frames = (((timeline.get("camera") or {}).get("frames")) or [])
        for local_index, frame in enumerate(frames):
            if not isinstance(frame, dict) or not frame.get("data"):
                continue
            if args.frame_step > 1 and local_index % args.frame_step != 0:
                continue
            width = int(frame.get("width") or frame.get("source_width") or 0)
            height = int(frame.get("height") or frame.get("source_height") or 0)
            if width <= 0 or height <= 0:
                continue
            detections = frame_detections(frame, args.min_confidence)
            if not detections and not args.include_empty:
                continue
            split = split_name(frame_index, args.val_ratio)
            stem = f"{safe_name(recording_path.name)}_{float(frame.get('t_s') or 0.0):08.3f}_{frame_index:05d}"
            image_path = output_dir / "images" / split / f"{stem}.jpg"
            label_path = output_dir / "labels" / split / f"{stem}.txt"
            image_path.write_bytes(decode_frame_data(str(frame.get("data") or "")))
            label_lines = []
            for det in detections:
                points = detection_polygon(det, width, height)
                if len(points) < 3:
                    continue
                label_lines.append(yolo_segmentation_line(0, points, width, height))
            label_path.write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")
            exported += 1
            labeled += 1 if label_lines else 0
            empty += 0 if label_lines else 1
            manifest.append(
                {
                    "image": str(image_path.relative_to(output_dir)),
                    "label": str(label_path.relative_to(output_dir)),
                    "recording_id": recording_path.name,
                    "t_s": round(float(frame.get("t_s") or 0.0), 3),
                    "detections": len(label_lines),
                    "source_overlay_backend": (frame.get("overlay") or {}).get("backend"),
                    "source_overlay_revision": (frame.get("overlay") or {}).get("revision"),
                }
            )
            frame_index += 1

    (output_dir / "data.yaml").write_text(
        "\n".join(
            [
                f"path: {output_dir}",
                "train: images/train",
                "val: images/val",
                "names:",
                f"  0: {args.class_name}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "README.md").write_text(
        "\n".join(
            [
                "# Orchard Trunk Fine-Tuning Export",
                "",
                "This dataset contains archived camera preview frames and editable YOLO segmentation pre-labels.",
                "Review every label before training. Add missed trunks and remove false positives.",
                "",
                "Recommended training command:",
                "",
                "```bash",
                f"yolo task=segment mode=train model=yolo11n-seg.pt data={output_dir / 'data.yaml'} imgsz=640 epochs=100 batch=8 device=0",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "output_dir": str(output_dir),
        "exported_frames": exported,
        "labeled_frames": labeled,
        "empty_frames": empty,
        "classes": {0: args.class_name},
        "manifest": str(output_dir / "manifest.json"),
        "data_yaml": str(output_dir / "data.yaml"),
    }


def main() -> int:
    started = time.time()
    payload = export_dataset(parse_args())
    payload["elapsed_s"] = round(time.time() - started, 3)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
