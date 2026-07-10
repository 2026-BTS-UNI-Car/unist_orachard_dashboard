#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import tarfile
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and package the orchard teacher dataset for manual mask review.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def priority(item: dict[str, Any]) -> tuple[int, float, str]:
    accepted = int(item.get("accepted") or 0)
    rejected = int(item.get("rejected") or 0)
    clip_rejected = int(item.get("clip_rejected") or 0)
    if rejected or clip_rejected:
        level = 0
    elif accepted == 0:
        level = 1
    elif accepted >= 6:
        level = 2
    else:
        level = 3
    return level, float(item.get("t_s") or 0.0), str(item.get("source") or "")


def validate_label(path: Path) -> tuple[int, list[str]]:
    errors = []
    instances = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        values = line.split()
        instances += 1
        if len(values) < 7 or (len(values) - 1) % 2:
            errors.append(f"{path}:{line_number}: invalid polygon token count")
            continue
        try:
            coordinates = [float(value) for value in values[1:]]
        except ValueError:
            errors.append(f"{path}:{line_number}: non-numeric coordinate")
            continue
        if any(value < 0.0 or value > 1.0 for value in coordinates):
            errors.append(f"{path}:{line_number}: coordinate outside [0,1]")
    return instances, errors


def guide_text(stats: dict[str, Any]) -> str:
    return f"""# Orchard trunk mask review package

Frames: {stats['frames']}
Pre-label instances: {stats['instances']}
Empty hard-negative frames: {stats['empty_frames']}

## Review order

Open `review_queue.csv` from rank 1. Priority 0 has rejected/ambiguous teacher candidates, priority 1 has no accepted mask, priority 2 has crowded masks, and priority 3 is ordinary confirmation.

## Required edits

1. Keep a polygon only when it follows a real tree stem or trunk visible in the image.
2. Add every missed visible tree stem, including thin, leaning, partially occluded and multi-stem trees.
3. Delete people, pavilion/building posts, utility poles, furniture legs, wall/road edges, plastic objects and foliage-only regions.
4. For a multi-stem tree, annotate each visible stem polygon. Record the shared root in the annotation tool notes; runtime grouping remains a post-process.
5. Keep a label file empty when no tree stem is visible. Empty frames are valuable hard negatives.
6. Do not move near-duplicate frames across train/val/test. For the final paper split, separate locations or recording sessions rather than random adjacent frames.

## Import

Use `data.yaml`, `images/train`, `images/val`, `labels/train`, and `labels/val` as a YOLO segmentation dataset. CVAT can import the images first; if direct YOLO polygon import is unavailable in the installed CVAT version, use Ultralytics/Roboflow only as a conversion tool and export final labels back to YOLO segmentation.

## Acceptance gate

- Every image reviewed once and priority 0 reviewed by a second person.
- No known person/post/pole/furniture false positive remains.
- Thin and split stems are included.
- Independent test split is never used to tune thresholds.
"""


def main() -> int:
    args = parse_args()
    dataset = Path(args.dataset).resolve()
    manifest_path = dataset / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = list(manifest.get("items") or [])
    rows = []
    errors = []
    total_instances = 0
    for item in items:
        image = dataset / str(item.get("image") or "")
        label = dataset / str(item.get("label") or "")
        metadata = dataset / str(item.get("metadata") or "")
        for path in (image, label, metadata):
            if not path.is_file():
                errors.append(f"missing: {path}")
        instances = 0
        if label.is_file():
            instances, label_errors = validate_label(label)
            errors.extend(label_errors)
        total_instances += instances
        level, stamp, source = priority(item)
        rows.append(
            {
                "priority": level,
                "source": source,
                "t_s": round(stamp, 3),
                "split": item.get("split"),
                "image": item.get("image"),
                "label": item.get("label"),
                "metadata": item.get("metadata"),
                "accepted_masks": int(item.get("accepted") or 0),
                "rejected_candidates": int(item.get("rejected") or 0),
                "clip_rejected": int(item.get("clip_rejected") or 0),
                "label_instances": instances,
            }
        )
    rows.sort(key=lambda row: (row["priority"], -row["rejected_candidates"], -row["clip_rejected"], row["source"], row["t_s"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    fieldnames = ["rank", "priority", "source", "t_s", "split", "image", "label", "metadata", "accepted_masks", "rejected_candidates", "clip_rejected", "label_instances"]
    with (dataset / "review_queue.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    stats = {
        "ok": not errors,
        "frames": len(rows),
        "instances": total_instances,
        "empty_frames": sum(1 for row in rows if row["label_instances"] == 0),
        "priority_counts": {str(level): sum(1 for row in rows if row["priority"] == level) for level in range(4)},
        "errors": errors,
    }
    (dataset / "ANNOTATION_GUIDE.md").write_text(guide_text(stats), encoding="utf-8")
    (dataset / "review_validation.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    if errors:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 1

    output = Path(args.output).resolve() if args.output else dataset.with_name(dataset.name + "_review.tar.gz")
    with tarfile.open(output, "w:gz", compresslevel=1) as archive:
        archive.add(dataset, arcname=dataset.name)
    stats["package"] = str(output)
    stats["package_bytes"] = output.stat().st_size
    (dataset / "review_validation.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
