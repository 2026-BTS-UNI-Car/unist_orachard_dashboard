#!/usr/bin/env python3
"""Run local vision worker against selected frames and render overlay previews."""

from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-url", default="http://127.0.0.1:8785/analyze")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("images", nargs="+", type=Path)
    return parser.parse_args()


def call_worker(worker_url: str, image_path: Path) -> tuple[dict[str, Any], float]:
    payload = json.dumps({"image": base64.b64encode(image_path.read_bytes()).decode("ascii")}).encode("utf-8")
    request = urllib.request.Request(
        worker_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result, round((time.time() - started) * 1000.0, 1)


def point_xy(point: Any, width: int, height: int) -> list[int]:
    if isinstance(point, dict):
        return [int(float(point.get("x", 0.0)) * width), int(float(point.get("y", 0.0)) * height)]
    return [int(float(point[0])), int(float(point[1]))]


def bbox_xywh(bbox: Any, width: int, height: int) -> list[int]:
    if isinstance(bbox, list):
        return [int(float(value)) for value in bbox[:4]]
    return [
        int(float(bbox.get("x", 0.0)) * width),
        int(float(bbox.get("y", 0.0)) * height),
        int(float(bbox.get("width", 0.0)) * width),
        int(float(bbox.get("height", 0.0)) * height),
    ]


def render_overlay(image_path: Path, result: dict[str, Any], output_path: Path) -> None:
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"Failed to read {image_path}")
    height, width = image.shape[:2]
    overlay = image.copy()
    mask = np.zeros_like(image)
    for detection in result.get("detections", []) or []:
        source = str(detection.get("source") or "")
        color = (80, 230, 154) if "roboflow" in source else (54, 214, 255)
        contour = detection.get("contour") or []
        if contour:
            points = np.array([point_xy(point, width, height) for point in contour], dtype=np.int32)
            if len(points) >= 3:
                cv2.fillPoly(mask, [points], color)
                cv2.polylines(overlay, [points], True, color, 4, cv2.LINE_AA)
        bbox = bbox_xywh(detection.get("bbox") or [0, 0, 0, 0], width, height)
        x, y, box_w, box_h = bbox
        if box_w > 0 and box_h > 0:
            cv2.rectangle(overlay, (x, y), (x + box_w, y + box_h), (255, 120, 30), 2)
            cv2.putText(
                overlay,
                f"{float(detection.get('confidence') or 0.0):.2f}",
                (x, max(24, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
    rendered = cv2.addWeighted(mask, 0.28, overlay, 0.72, 0)
    cv2.imwrite(str(output_path), rendered)


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, Any]] = []
    for image_path in args.images:
        if not image_path.exists():
            continue
        result, elapsed_ms = call_worker(args.worker_url, image_path)
        output_path = args.output_dir / f"worker_{image_path.stem}.jpg"
        render_overlay(image_path, result, output_path)
        summary.append(
            {
                "image": str(image_path),
                "output": str(output_path),
                "ok": result.get("ok"),
                "backend": result.get("backend"),
                "revision": result.get("revision"),
                "detections": len(result.get("detections", []) or []),
                "elapsed_ms_api": elapsed_ms,
                "elapsed_ms_worker": result.get("elapsed_ms"),
                "top_confidence": result.get("confidence"),
                "labels": [item.get("label") for item in (result.get("detections", []) or [])[:8]],
                "sources": [item.get("source") for item in (result.get("detections", []) or [])[:8]],
                "backend_errors": result.get("backend_errors", []),
            }
        )
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
