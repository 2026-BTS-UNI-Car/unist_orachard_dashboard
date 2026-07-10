#!/usr/bin/env python3
"""Render AIHub orchard tree SEG labels as a contact sheet for inspection."""

from __future__ import annotations

import argparse
import atexit
from dataclasses import dataclass
import json
import random
import zipfile
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


TREE_LABELS = {"사과나무", "배나무", "포도나무", "감귤나무"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
ZIP_CACHE: dict[Path, zipfile.ZipFile] = {}


@dataclass(frozen=True)
class SourceImage:
    origin: Path
    member: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label-zip", type=Path, required=True)
    parser.add_argument("--source-root", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-samples", type=int, default=24)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--tile-width", type=int, default=480)
    parser.add_argument("--max-bbox-area-ratio", type=float, default=0.60)
    parser.add_argument("--min-bbox-area-ratio", type=float, default=0.003)
    parser.add_argument("--min-mask-area-ratio", type=float, default=0.001)
    parser.add_argument("--include-full-frame", action="store_true")
    parser.add_argument("--shuffle-seed", type=int, default=17)
    return parser.parse_args()


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


def read_source_image(source: SourceImage) -> np.ndarray | None:
    if source.member:
        archive = ZIP_CACHE.get(source.origin)
        if archive is None:
            archive = ZIP_CACHE.setdefault(source.origin, zipfile.ZipFile(source.origin))
        payload = archive.read(source.member)
        encoded = np.frombuffer(payload, dtype=np.uint8)
        return cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    return cv2.imread(str(source.origin), cv2.IMREAD_COLOR)


def close_zip_cache() -> None:
    for archive in ZIP_CACHE.values():
        archive.close()


atexit.register(close_zip_cache)


def valid_polygon(points: Any, width: int, height: int) -> np.ndarray | None:
    if not isinstance(points, list) or len(points) < 6:
        return None
    pairs: list[list[int]] = []
    for idx in range(0, len(points) - 1, 2):
        x = int(round(min(max(float(points[idx]), 0.0), float(width - 1))))
        y = int(round(min(max(float(points[idx + 1]), 0.0), float(height - 1))))
        pairs.append([x, y])
    if len(pairs) < 3:
        return None
    return np.array(pairs, dtype=np.int32)


def overlay_annotation(image: np.ndarray, polygons: list[np.ndarray], label: str, confidence_text: str) -> np.ndarray:
    rendered = image.copy()
    mask = np.zeros_like(rendered)
    color = (54, 214, 255) if "감귤" in label else (80, 230, 154)
    for polygon in polygons:
        cv2.fillPoly(mask, [polygon], color)
        cv2.polylines(rendered, [polygon], True, color, 3, cv2.LINE_AA)
    rendered = cv2.addWeighted(mask, 0.36, rendered, 0.64, 0)
    xs = np.concatenate([poly[:, 0] for poly in polygons])
    ys = np.concatenate([poly[:, 1] for poly in polygons])
    x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    cv2.rectangle(rendered, (x1, y1), (x2, y2), (36, 129, 255), 2)
    text = f"{label} {confidence_text}"
    cv2.rectangle(rendered, (x1, max(0, y1 - 30)), (min(rendered.shape[1] - 1, x1 + 270), y1), (0, 0, 0), -1)
    cv2.putText(rendered, text, (x1 + 8, max(22, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
    return rendered


def collect_samples(args: argparse.Namespace) -> tuple[list[np.ndarray], list[dict[str, Any]]]:
    source_index = iter_source_images(args.source_root)
    tiles: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []
    with zipfile.ZipFile(args.label_zip) as archive:
        members = [m for m in archive.infolist() if "/SEG/" in m.filename and m.filename.endswith(".json")]
        if args.shuffle_seed >= 0:
            random.Random(args.shuffle_seed).shuffle(members)
        for member in members:
            data = json.loads(archive.read(member).decode("utf-8"))
            categories = {cat.get("id"): cat.get("name") for cat in data.get("categories", [])}
            image_info = (data.get("images") or [{}])[0]
            image_name = image_info.get("file_name") or image_info.get("filename") or ""
            source = source_index.get(image_name)
            if not source:
                continue
            image = read_source_image(source)
            if image is None:
                continue
            height, width = image.shape[:2]
            image_area = float(max(width * height, 1))
            for annotation in data.get("annotations", []) or []:
                label = categories.get(annotation.get("category_id"))
                if label not in TREE_LABELS:
                    continue
                bbox = annotation.get("bbox") or [0, 0, 0, 0]
                if len(bbox) < 4:
                    bbox = [0, 0, 0, 0]
                _, _, bbox_w, bbox_h = [float(value or 0) for value in bbox[:4]]
                bbox_ratio = max(bbox_w, 0.0) * max(bbox_h, 0.0) / image_area
                mask_ratio = float(annotation.get("area") or 0) / image_area
                if not args.include_full_frame:
                    if bbox_ratio > args.max_bbox_area_ratio:
                        continue
                    if bbox_ratio < args.min_bbox_area_ratio or mask_ratio < args.min_mask_area_ratio:
                        continue
                polygons = [
                    polygon
                    for polygon in (
                        valid_polygon(segment, width, height)
                        for segment in (annotation.get("segmentation") or [])
                    )
                    if polygon is not None
                ]
                if not polygons:
                    continue
                tile = overlay_annotation(image, polygons, label, f"bbox={bbox_ratio:.2f}")
                scale = args.tile_width / max(tile.shape[1], 1)
                tile = cv2.resize(tile, (args.tile_width, int(tile.shape[0] * scale)), interpolation=cv2.INTER_AREA)
                tiles.append(tile)
                metadata.append(
                    {
                        "image": image_name,
                        "label": label,
                        "source_label": member.filename,
                        "bbox_area_ratio": round(bbox_ratio, 6),
                        "mask_area_ratio": round(mask_ratio, 6),
                    }
                )
                if len(tiles) >= args.max_samples:
                    return tiles, metadata
    return tiles, metadata


def make_contact_sheet(tiles: list[np.ndarray], columns: int) -> np.ndarray:
    if not tiles:
        raise RuntimeError("No samples were rendered. Check source-root, label zip, and filters.")
    columns = max(1, columns)
    tile_h = max(tile.shape[0] for tile in tiles)
    tile_w = max(tile.shape[1] for tile in tiles)
    rows = int(np.ceil(len(tiles) / columns))
    sheet = np.zeros((rows * tile_h, columns * tile_w, 3), dtype=np.uint8)
    sheet[:] = (12, 17, 35)
    for idx, tile in enumerate(tiles):
        row, col = divmod(idx, columns)
        y, x = row * tile_h, col * tile_w
        sheet[y : y + tile.shape[0], x : x + tile.shape[1]] = tile
    return sheet


def main() -> int:
    args = parse_args()
    tiles, metadata = collect_samples(args)
    sheet = make_contact_sheet(tiles, args.columns)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.output), sheet)
    args.output.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(args.output), "samples": len(metadata)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
