#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2  # type: ignore
import numpy as np  # type: ignore
import torch  # type: ignore
from PIL import Image  # type: ignore
from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor  # type: ignore
from ultralytics import SAM  # type: ignore


TEACHER_ALGORITHM = "grounding_dino_tree_trunk_sam2_ood_teacher_v1"


@dataclass
class SourceFrame:
    source: str
    source_kind: str
    source_index: int
    source_count: int
    t_s: float
    image: Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build conservative YOLO segmentation labels using Grounding DINO and SAM2 on Jetson.")
    parser.add_argument("--video", action="append", default=[], help="Video path. Repeat for multiple videos.")
    parser.add_argument("--timeline", action="append", default=[], help="Dashboard timeline.json path. Repeat for multiple archives.")
    parser.add_argument("--output", required=True, help="Output YOLO dataset directory.")
    parser.add_argument("--grounding-model", default="IDEA-Research/grounding-dino-tiny")
    parser.add_argument("--sam-model", default="/app/runtime/models/sam2.1_t.pt")
    parser.add_argument("--prompt", default="tree trunk.")
    parser.add_argument("--video-sample-fps", type=float, default=2.0)
    parser.add_argument("--timeline-stride", type=int, default=1)
    parser.add_argument("--max-frames-per-source", type=int, default=0)
    parser.add_argument("--box-threshold", type=float, default=0.12)
    parser.add_argument("--text-threshold", type=float, default=0.12)
    parser.add_argument("--score-threshold", type=float, default=0.18)
    parser.add_argument("--validation-ratio", type=float, default=0.2)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seed", type=int, default=71697)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    prepare_output(output)
    frames = load_all_frames(args)
    if not frames:
        raise SystemExit("No source frames were loaded.")

    processor = AutoProcessor.from_pretrained(args.grounding_model, local_files_only=True)
    detector = AutoModelForZeroShotObjectDetection.from_pretrained(args.grounding_model, local_files_only=True).to(args.device).eval()
    segmenter = SAM(args.sam_model)
    manifest_rows: list[dict[str, Any]] = []
    preview_rows: list[tuple[Any, str]] = []
    started = time.time()

    for frame_number, frame in enumerate(frames, start=1):
        result = analyze_frame(frame.image, processor, detector, segmenter, args)
        split = source_split(frame, args.validation_ratio)
        stem = safe_stem(f"{frame.source}_{frame.source_index:06d}_{int(round(frame.t_s * 1000)):09d}")
        image_path = output / "images" / split / f"{stem}.jpg"
        label_path = output / "labels" / split / f"{stem}.txt"
        metadata_path = output / "metadata" / split / f"{stem}.json"
        cv2.imwrite(str(image_path), frame.image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        label_path.write_text(yolo_labels(result["detections"], frame.image.shape[1], frame.image.shape[0]), encoding="utf-8")
        metadata = {
            "algorithm": TEACHER_ALGORITHM,
            "source": frame.source,
            "source_kind": frame.source_kind,
            "source_index": frame.source_index,
            "source_count": frame.source_count,
            "t_s": round(frame.t_s, 3),
            "split": split,
            **result,
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_rows.append({
            "image": str(image_path.relative_to(output)),
            "label": str(label_path.relative_to(output)),
            "metadata": str(metadata_path.relative_to(output)),
            "source": frame.source,
            "source_kind": frame.source_kind,
            "t_s": round(frame.t_s, 3),
            "split": split,
            "accepted": len(result["detections"]),
            "rejected": len(result["rejected"]),
            "elapsed_ms": result["elapsed_ms"],
        })
        if should_preview(frame_number, len(frames)):
            preview_rows.append((render_result(frame.image, result), f"{frame.source[:22]} {frame.t_s:.1f}s +{len(result['detections'])}/-{len(result['rejected'])}"))
        elapsed = time.time() - started
        print(
            json.dumps(
                {
                    "frame": frame_number,
                    "total": len(frames),
                    "source": frame.source,
                    "t_s": round(frame.t_s, 2),
                    "accepted": len(result["detections"]),
                    "rejected": len(result["rejected"]),
                    "elapsed_s": round(elapsed, 1),
                }
            ),
            flush=True,
        )

    manifest = build_manifest(args, manifest_rows, time.time() - started)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_data_yaml(output)
    write_contact_sheet(preview_rows, output / "teacher_contact_sheet.jpg")
    print(json.dumps(manifest["stats"], ensure_ascii=False, indent=2))
    return 0


def prepare_output(output: Path) -> None:
    for branch in ("images", "labels", "metadata"):
        for split in ("train", "val"):
            (output / branch / split).mkdir(parents=True, exist_ok=True)


def load_all_frames(args: argparse.Namespace) -> list[SourceFrame]:
    rows: list[SourceFrame] = []
    for path in [Path(value) for value in args.video]:
        source_rows = list(video_frames(path, max(0.1, args.video_sample_fps)))
        rows.extend(limit_source_frames(source_rows, args.max_frames_per_source))
    for path in [Path(value) for value in args.timeline]:
        source_rows = list(timeline_frames(path, max(1, args.timeline_stride)))
        rows.extend(limit_source_frames(source_rows, args.max_frames_per_source))
    return rows


def video_frames(path: Path, sample_fps: float) -> Iterable[SourceFrame]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_s = frame_count / max(0.1, fps)
    times = np.arange(0.0, duration_s, 1.0 / sample_fps).tolist()
    for index, t_s in enumerate(times):
        capture.set(cv2.CAP_PROP_POS_MSEC, float(t_s) * 1000.0)
        ok, image = capture.read()
        if ok and image is not None:
            yield SourceFrame(path.stem, "video", index, len(times), float(t_s), image)
    capture.release()


def timeline_frames(path: Path, stride: int) -> Iterable[SourceFrame]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    frames = payload.get("camera", {}).get("frames", []) or []
    selected = frames[::stride]
    source = str(payload.get("id") or path.parent.name)
    for index, frame in enumerate(selected):
        try:
            raw = base64.b64decode(frame.get("data") or "")
            image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            image = None
        if image is not None:
            yield SourceFrame(source, "timeline", index, len(selected), float(frame.get("t_s") or 0.0), image)


def limit_source_frames(frames: list[SourceFrame], limit: int) -> list[SourceFrame]:
    if limit <= 0 or len(frames) <= limit:
        return frames
    indexes = np.linspace(0, len(frames) - 1, limit, dtype=int).tolist()
    selected = [frames[index] for index in indexes]
    for index, frame in enumerate(selected):
        frame.source_index = index
        frame.source_count = len(selected)
    return selected


def analyze_frame(image_bgr: Any, processor: Any, detector: Any, segmenter: Any, args: argparse.Namespace) -> dict[str, Any]:
    height, width = image_bgr.shape[:2]
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(image_rgb)
    inputs = processor(images=pil_image, text=args.prompt, return_tensors="pt").to(args.device)
    started = time.time()
    with torch.no_grad():
        outputs = detector(**inputs)
    result = processor.post_process_grounded_object_detection(
        outputs,
        inputs.input_ids,
        threshold=args.box_threshold,
        text_threshold=args.text_threshold,
        target_sizes=[pil_image.size[::-1]],
    )[0]
    labels = result.get("text_labels", result.get("labels", []))
    candidates = []
    for label, score, box in zip(labels, result["scores"].detach().cpu().tolist(), result["boxes"].detach().cpu().tolist()):
        candidate = teacher_box_candidate(str(label), float(score), [float(value) for value in box], width, height, args.score_threshold)
        if candidate:
            candidates.append(candidate)
    candidates = nms_candidates(candidates, iou_threshold=0.45)
    detections: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if candidates:
        boxes = [item["bbox_xyxy"] for item in candidates]
        sam_results = segmenter.predict(source=image_bgr, bboxes=boxes, device=args.device, verbose=False)
        polygons = sam_results[0].masks.xy if sam_results and sam_results[0].masks is not None else []
        for index, candidate in enumerate(candidates):
            polygon = polygons[index] if index < len(polygons) else []
            evaluated = evaluate_mask(candidate, polygon, image_bgr)
            if evaluated["admission"] == "accepted":
                detections.append(evaluated)
            else:
                rejected.append(evaluated)
    return {
        "prompt": args.prompt,
        "grounding_model": args.grounding_model,
        "sam_model": str(args.sam_model),
        "image": {"width": width, "height": height},
        "detections": detections,
        "rejected": rejected,
        "elapsed_ms": round((time.time() - started) * 1000.0, 1),
    }


def teacher_box_candidate(label: str, score: float, box: list[float], width: int, height: int, score_threshold: float) -> dict[str, Any] | None:
    x1, y1, x2, y2 = box
    bw = max(0.0, x2 - x1)
    bh = max(0.0, y2 - y1)
    area_ratio = bw * bh / max(1.0, width * height)
    aspect = bh / max(1.0, bw)
    if "trunk" not in label.lower() or score < score_threshold:
        return None
    if aspect < 1.2 or area_ratio > 0.17 or bh < height * 0.12:
        return None
    return {
        "label": "tree_trunk",
        "teacher_label": label,
        "teacher_confidence": round(score, 4),
        "bbox_xyxy": [round(max(0.0, x1), 2), round(max(0.0, y1), 2), round(min(float(width), x2), 2), round(min(float(height), y2), 2)],
        "box_aspect": round(aspect, 4),
        "box_area_ratio": round(area_ratio, 6),
    }


def nms_candidates(candidates: list[dict[str, Any]], iou_threshold: float) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: float(item["teacher_confidence"]), reverse=True):
        if any(xyxy_iou(candidate["bbox_xyxy"], other["bbox_xyxy"]) >= iou_threshold for other in kept):
            continue
        kept.append(candidate)
    return kept[:16]


def evaluate_mask(candidate: dict[str, Any], polygon: Any, image_bgr: Any) -> dict[str, Any]:
    height, width = image_bgr.shape[:2]
    points = np.asarray(polygon, dtype=np.float32).reshape(-1, 2) if len(polygon) else np.empty((0, 2), dtype=np.float32)
    reasons: list[str] = []
    if len(points) < 3:
        reasons.append("sam_mask_missing")
        return {**candidate, "admission": "rejected", "tree_probability": 0.0, "ood_score": 1.0, "rejection_reasons": reasons, "contour": []}
    x, y, bw, bh = cv2.boundingRect(points.astype(np.int32))
    mask_area = float(abs(cv2.contourArea(points)))
    bbox_area = max(1.0, float(bw * bh))
    area_ratio = mask_area / max(1.0, width * height)
    fill_ratio = mask_area / bbox_area
    aspect = bh / max(1.0, bw)
    bottom_ratio = (y + bh) / max(1.0, height)
    edge_contact = x <= 2 or y <= 2 or x + bw >= width - 2 or y + bh >= height - 2
    green_support = canopy_green_support(image_bgr, x, y, bw, bh)
    score = float(candidate.get("teacher_confidence") or 0.0)

    if aspect < 1.12:
        reasons.append("mask_not_vertical")
    if area_ratio < 0.00018:
        reasons.append("mask_too_small")
    if area_ratio > 0.12:
        reasons.append("mask_too_large")
    if fill_ratio < 0.08:
        reasons.append("mask_fragmented")
    if bottom_ratio < 0.36:
        reasons.append("mask_floats_above_ground_region")
    if edge_contact and score < 0.24:
        reasons.append("low_confidence_edge_candidate")
    if green_support < 0.018 and score < 0.28:
        reasons.append("no_canopy_context")

    confidence_term = clamp((score - 0.14) / 0.28, 0.0, 1.0)
    vertical_term = clamp((aspect - 1.0) / 3.0, 0.0, 1.0)
    canopy_term = clamp(green_support / 0.18, 0.0, 1.0)
    fill_term = clamp(fill_ratio / 0.42, 0.0, 1.0)
    tree_probability = 0.48 * confidence_term + 0.2 * vertical_term + 0.17 * canopy_term + 0.15 * fill_term
    if reasons:
        tree_probability *= 0.42
    tree_probability = clamp(tree_probability, 0.0, 0.995)
    contour = simplify_polygon(points, width, height)
    return {
        **candidate,
        "bbox": [int(x), int(y), int(bw), int(bh)],
        "contour": contour,
        "render_shape": "contour",
        "confidence": round(tree_probability, 4),
        "tree_probability": round(tree_probability, 4),
        "ood_score": round(1.0 - tree_probability, 4),
        "admission": "accepted" if not reasons and tree_probability >= 0.34 else "rejected",
        "rejection_reasons": reasons,
        "features": {
            "mask_aspect": round(aspect, 4),
            "mask_area_ratio": round(area_ratio, 6),
            "mask_fill_ratio": round(fill_ratio, 4),
            "bottom_ratio": round(bottom_ratio, 4),
            "edge_contact": edge_contact,
            "canopy_green_support": round(green_support, 4),
        },
        "source": "grounding_dino_sam2_teacher",
        "model_provenance": {
            "candidate": "IDEA-Research/grounding-dino-tiny",
            "mask": "sam2.1_t",
            "policy": TEACHER_ALGORITHM,
        },
    }


def canopy_green_support(image_bgr: Any, x: int, y: int, bw: int, bh: int) -> float:
    height, width = image_bgr.shape[:2]
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (28, 22, 28), (110, 255, 255))
    x1 = max(0, x - int(width * 0.035))
    x2 = min(width, x + bw + int(width * 0.035))
    y1 = max(0, y - max(int(height * 0.16), int(bh * 0.4)))
    y2 = min(height, y + max(1, int(bh * 0.28)))
    region = green[y1:max(y1 + 1, y2), x1:max(x1 + 1, x2)]
    return float(cv2.countNonZero(region) / max(1, region.size))


def simplify_polygon(points: Any, width: int, height: int, max_points: int = 64) -> list[list[int]]:
    perimeter = cv2.arcLength(points.astype(np.float32), True)
    approx = cv2.approxPolyDP(points.astype(np.float32), max(1.0, perimeter * 0.004), True).reshape(-1, 2)
    rows = approx.tolist()
    if len(rows) > max_points:
        step = max(1, math.ceil(len(rows) / max_points))
        rows = rows[::step]
    return [[max(0, min(width - 1, int(round(x)))), max(0, min(height - 1, int(round(y))))] for x, y in rows]


def yolo_labels(detections: list[dict[str, Any]], width: int, height: int) -> str:
    lines = []
    for detection in detections:
        contour = detection.get("contour") or []
        if len(contour) < 3:
            continue
        values = []
        for x, y in contour:
            values.extend((clamp(float(x) / max(1, width), 0.0, 1.0), clamp(float(y) / max(1, height), 0.0, 1.0)))
        lines.append("0 " + " ".join(f"{value:.6f}" for value in values))
    return "\n".join(lines) + ("\n" if lines else "")


def source_split(frame: SourceFrame, validation_ratio: float) -> str:
    validation_count = max(1, int(round(frame.source_count * clamp(validation_ratio, 0.05, 0.45))))
    return "val" if frame.source_index >= frame.source_count - validation_count else "train"


def build_manifest(args: argparse.Namespace, rows: list[dict[str, Any]], elapsed_s: float) -> dict[str, Any]:
    return {
        "algorithm": TEACHER_ALGORITHM,
        "generated_at": time.time(),
        "grounding_model": args.grounding_model,
        "sam_model": args.sam_model,
        "prompt": args.prompt,
        "filters": {
            "box_threshold": args.box_threshold,
            "text_threshold": args.text_threshold,
            "score_threshold": args.score_threshold,
            "validation_ratio": args.validation_ratio,
        },
        "stats": {
            "frames": len(rows),
            "train_frames": sum(row["split"] == "train" for row in rows),
            "val_frames": sum(row["split"] == "val" for row in rows),
            "positive_frames": sum(row["accepted"] > 0 for row in rows),
            "hard_negative_frames": sum(row["accepted"] == 0 for row in rows),
            "accepted_instances": sum(int(row["accepted"]) for row in rows),
            "rejected_instances": sum(int(row["rejected"]) for row in rows),
            "elapsed_s": round(elapsed_s, 2),
            "mean_teacher_ms": round(sum(float(row["elapsed_ms"]) for row in rows) / max(1, len(rows)), 1),
        },
        "items": rows,
    }


def write_data_yaml(output: Path) -> None:
    text = f"path: {output.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: tree_trunk\n"
    (output / "data.yaml").write_text(text, encoding="utf-8")


def should_preview(index: int, total: int) -> bool:
    if total <= 40:
        return True
    stride = max(1, total // 40)
    return index == 1 or index == total or index % stride == 0


def render_result(image: Any, result: dict[str, Any]) -> Any:
    canvas = image.copy()
    for detection in result.get("rejected") or []:
        bbox = detection.get("bbox") or []
        if len(bbox) >= 4:
            x, y, bw, bh = [int(value) for value in bbox[:4]]
            cv2.rectangle(canvas, (x, y), (x + bw, y + bh), (30, 90, 240), 2)
    for detection in result.get("detections") or []:
        contour = detection.get("contour") or []
        if len(contour) >= 3:
            cv2.polylines(canvas, [np.asarray(contour, np.int32)], True, (40, 240, 90), 4, cv2.LINE_AA)
        bbox = detection.get("bbox") or []
        if len(bbox) >= 4:
            x, y, bw, bh = [int(value) for value in bbox[:4]]
            cv2.putText(canvas, f"tree {float(detection.get('tree_probability') or 0.0):.2f}", (x, max(18, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (40, 240, 90), 2, cv2.LINE_AA)
    return canvas


def write_contact_sheet(items: list[tuple[Any, str]], path: Path) -> None:
    if not items:
        return
    columns = 4
    cell_width, cell_height = 440, 270
    row_count = math.ceil(len(items) / columns)
    sheet = np.full((row_count * (cell_height + 30), columns * cell_width, 3), 16, np.uint8)
    for index, (image, label) in enumerate(items):
        height, width = image.shape[:2]
        scale = min(cell_width / max(1, width), cell_height / max(1, height))
        resized_width, resized_height = max(1, int(width * scale)), max(1, int(height * scale))
        resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
        x = (index % columns) * cell_width + (cell_width - resized_width) // 2
        y = (index // columns) * (cell_height + 30) + (cell_height - resized_height) // 2
        sheet[y : y + resized_height, x : x + resized_width] = resized
        cv2.putText(sheet, label, ((index % columns) * cell_width + 4, (index // columns) * (cell_height + 30) + cell_height + 21), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (90, 220, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 91])


def safe_stem(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)


def xyxy_iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    union = max(1.0, (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection)
    return intersection / union


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


if __name__ == "__main__":
    raise SystemExit(main())
