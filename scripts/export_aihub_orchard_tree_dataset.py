#!/usr/bin/env python3
"""Export AIHub orchard tree SEG labels to a YOLO segmentation dataset.

The AIHub orchard SEG labels contain useful tree masks, but many annotations
cover nearly the whole frame. This exporter keeps the dashboard training path
focused on individual visible trees by filtering oversized masks by default.
"""

from __future__ import annotations

import argparse
import atexit
from dataclasses import dataclass
import hashlib
import json
import os
import random
import shutil
import zipfile
from pathlib import Path
from typing import Any, Iterable


TREE_LABELS = {"사과나무", "배나무", "포도나무", "감귤나무"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
ZIP_CACHE: dict[Path, zipfile.ZipFile] = {}


@dataclass(frozen=True)
class SourceImage:
    origin: Path
    member: str | None = None

    @property
    def description(self) -> str:
        if self.member:
            return f"{self.origin}!{self.member}"
        return str(self.origin)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label-zip", action="append", type=Path, required=True, help="AIHub TL/VL label zip. Repeatable.")
    parser.add_argument("--source-root", action="append", type=Path, required=True, help="Root containing AIHub source images. Repeatable.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--class-name", default="orchard_tree")
    parser.add_argument("--tree-label", action="append", default=[], help="Tree category name to include. Defaults to apple/pear/grape/citrus tree labels.")
    parser.add_argument("--max-bbox-area-ratio", type=float, default=0.60, help="Drop tree masks with bbox area ratio above this value.")
    parser.add_argument("--min-bbox-area-ratio", type=float, default=0.003, help="Drop tiny fragments below this bbox area ratio.")
    parser.add_argument("--min-mask-area-ratio", type=float, default=0.001, help="Drop tiny masks below this mask area ratio.")
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--limit", type=int, default=0, help="Maximum exported images after filtering. 0 means no limit.")
    parser.add_argument("--copy-mode", choices=("copy", "symlink"), default="copy")
    parser.add_argument("--shuffle-seed", type=int, default=17, help="Shuffle label files before applying --limit. Use -1 to keep archive order.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def stable_split(name: str, val_ratio: float) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "val" if bucket < val_ratio else "train"


def iter_source_images(roots: Iterable[Path]) -> dict[str, SourceImage]:
    index: dict[str, SourceImage] = {}
    for root in roots:
        if not root.exists():
            continue
        if root.is_file() and root.suffix.lower() == ".zip":
            archive = ZIP_CACHE.setdefault(root, zipfile.ZipFile(root))
            for member in archive.infolist():
                if member.is_dir():
                    continue
                member_name = Path(member.filename).name
                if Path(member_name).suffix.lower() in IMAGE_EXTS:
                    index.setdefault(member_name, SourceImage(root, member.filename))
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
                index.setdefault(path.name, SourceImage(path))
    return index


def normalize_polygon(points: list[float], width: float, height: float) -> list[float]:
    normalized: list[float] = []
    if len(points) < 6:
        return normalized
    for idx in range(0, len(points) - 1, 2):
        x = min(max(float(points[idx]), 0.0), width) / width
        y = min(max(float(points[idx + 1]), 0.0), height) / height
        normalized.extend((x, y))
    return normalized if len(normalized) >= 6 else []


def segmentation_polygons(segmentation: Any, width: float, height: float) -> list[list[float]]:
    polygons: list[list[float]] = []
    if not isinstance(segmentation, list):
        return polygons
    for segment in segmentation:
        if not isinstance(segment, list):
            continue
        poly = normalize_polygon(segment, width, height)
        if poly:
            polygons.append(poly)
    return polygons


def link_or_copy(src: SourceImage, dst: Path, mode: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if src.member:
        archive = ZIP_CACHE.get(src.origin)
        if archive is None:
            archive = ZIP_CACHE.setdefault(src.origin, zipfile.ZipFile(src.origin))
        dst.write_bytes(archive.read(src.member))
        return
    if mode == "symlink":
        os.symlink(src.origin, dst)
    else:
        shutil.copy2(src.origin, dst)


def close_zip_cache() -> None:
    for archive in ZIP_CACHE.values():
        archive.close()


atexit.register(close_zip_cache)


def export_dataset(args: argparse.Namespace) -> dict[str, Any]:
    labels = set(args.tree_label) if args.tree_label else set(TREE_LABELS)
    source_index = iter_source_images(args.source_root)
    stats: dict[str, Any] = {
        "source_images_indexed": len(source_index),
        "json_seen": 0,
        "tree_annotations_seen": 0,
        "images_exported": 0,
        "objects_exported": 0,
        "missing_source_images": 0,
        "filtered_too_large": 0,
        "filtered_too_small": 0,
        "filtered_empty_polygon": 0,
        "per_label": {},
        "splits": {"train": 0, "val": 0},
        "filters": {
            "tree_labels": sorted(labels),
            "max_bbox_area_ratio": args.max_bbox_area_ratio,
            "min_bbox_area_ratio": args.min_bbox_area_ratio,
            "min_mask_area_ratio": args.min_mask_area_ratio,
        },
    }
    if args.output_dir.exists() and args.overwrite and not args.dry_run:
        shutil.rmtree(args.output_dir)
    if args.output_dir.exists() and not args.overwrite and not args.dry_run:
        raise FileExistsError(f"{args.output_dir} exists; pass --overwrite")

    exported_names: set[str] = set()
    manifest_items: list[dict[str, Any]] = []

    for label_zip in args.label_zip:
        with zipfile.ZipFile(label_zip) as archive:
            seg_members = [m for m in archive.infolist() if "/SEG/" in m.filename and m.filename.endswith(".json")]
            if args.shuffle_seed >= 0:
                random.Random(args.shuffle_seed).shuffle(seg_members)
            for member in seg_members:
                stats["json_seen"] += 1
                data = json.loads(archive.read(member).decode("utf-8"))
                categories = {cat.get("id"): cat.get("name") for cat in data.get("categories", [])}
                image = (data.get("images") or [{}])[0]
                image_name = image.get("file_name") or image.get("filename") or ""
                if not image_name:
                    continue
                source_path = source_index.get(image_name)
                if not source_path:
                    stats["missing_source_images"] += 1
                    continue
                width = float(image.get("width") or 1920)
                height = float(image.get("height") or 1080)
                image_area = max(width * height, 1.0)
                yolo_lines: list[str] = []
                kept_labels: list[str] = []
                for annotation in data.get("annotations", []) or []:
                    label = categories.get(annotation.get("category_id"))
                    if label not in labels:
                        continue
                    stats["tree_annotations_seen"] += 1
                    stats["per_label"][label] = stats["per_label"].get(label, 0) + 1
                    bbox = annotation.get("bbox") or [0, 0, 0, 0]
                    if len(bbox) < 4:
                        bbox = [0, 0, 0, 0]
                    _, _, bbox_w, bbox_h = [float(value or 0) for value in bbox[:4]]
                    bbox_area_ratio = max(bbox_w, 0.0) * max(bbox_h, 0.0) / image_area
                    mask_area_ratio = float(annotation.get("area") or 0) / image_area
                    if bbox_area_ratio > args.max_bbox_area_ratio:
                        stats["filtered_too_large"] += 1
                        continue
                    if bbox_area_ratio < args.min_bbox_area_ratio or mask_area_ratio < args.min_mask_area_ratio:
                        stats["filtered_too_small"] += 1
                        continue
                    polygons = segmentation_polygons(annotation.get("segmentation"), width, height)
                    if not polygons:
                        stats["filtered_empty_polygon"] += 1
                        continue
                    for polygon in polygons:
                        coords = " ".join(f"{value:.6f}" for value in polygon)
                        yolo_lines.append(f"0 {coords}")
                    kept_labels.append(label)
                if not yolo_lines:
                    continue
                if image_name in exported_names:
                    continue
                split = stable_split(image_name, args.val_ratio)
                exported_names.add(image_name)
                stats["images_exported"] += 1
                stats["objects_exported"] += len(yolo_lines)
                stats["splits"][split] += 1
                relative_image = Path("images") / split / image_name
                relative_label = Path("labels") / split / f"{Path(image_name).stem}.txt"
                manifest_items.append(
                    {
                        "image": image_name,
                        "source_image": source_path.description,
                        "source_label": member.filename,
                        "split": split,
                        "objects": len(yolo_lines),
                        "labels": sorted(set(kept_labels)),
                    }
                )
                if not args.dry_run:
                    link_or_copy(source_path, args.output_dir / relative_image, args.copy_mode)
                    label_path = args.output_dir / relative_label
                    label_path.parent.mkdir(parents=True, exist_ok=True)
                    label_path.write_text("\n".join(yolo_lines) + "\n", encoding="utf-8")
                if args.limit and stats["images_exported"] >= args.limit:
                    break
        if args.limit and stats["images_exported"] >= args.limit:
            break

    if not args.dry_run:
        for split in ("train", "val"):
            (args.output_dir / "images" / split).mkdir(parents=True, exist_ok=True)
            (args.output_dir / "labels" / split).mkdir(parents=True, exist_ok=True)
        data_yaml = "\n".join(
            [
                f"path: {args.output_dir}",
                "train: images/train",
                "val: images/val",
                "names:",
                f"  0: {args.class_name}",
                "",
            ]
        )
        (args.output_dir / "data.yaml").write_text(data_yaml, encoding="utf-8")
        (args.output_dir / "manifest.json").write_text(
            json.dumps({"stats": stats, "items": manifest_items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return stats


def main() -> int:
    args = parse_args()
    stats = export_dataset(args)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
