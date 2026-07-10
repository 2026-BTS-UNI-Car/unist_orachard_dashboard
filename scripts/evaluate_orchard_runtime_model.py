#!/usr/bin/env python3
"""Evaluate a local orchard segmentation model on Jetson camera sources.

The evaluator intentionally runs independently from the HTTP worker. It keeps
raw model candidates and OOD rejections in the artifact so deployment
thresholds can be audited instead of being inferred from a rendered overlay.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2  # type: ignore
import numpy as np  # type: ignore
import torch  # type: ignore
from PIL import Image  # type: ignore
from transformers import CLIPModel, CLIPProcessor  # type: ignore
from ultralytics import YOLO  # type: ignore


ALGORITHM = "orchard_trunk_segmentation_clip_ood_eval_v1"
CLIP_PROMPTS = [
    "a natural tree trunk with rough bark",
    "an orchard fruit tree stem",
    "a person or human body",
    "a metal utility pole",
    "a concrete or wooden building column",
    "a furniture or table leg",
    "a wall or paved road",
    "leaves grass or bushes without a trunk",
]


@dataclass
class Frame:
    source: str
    source_kind: str
    source_index: int
    t_s: float
    image: Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--clip-model", required=True)
    parser.add_argument("--video", action="append", default=[])
    parser.add_argument("--timeline", action="append", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--video-fps", type=float, default=2.0)
    parser.add_argument("--video-offset-s", type=float, default=0.25)
    parser.add_argument("--timeline-stride", type=int, default=1)
    parser.add_argument("--model-confidence", type=float, default=0.08)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--clip-batch-size", type=int, default=64)
    parser.add_argument("--preview-count", type=int, default=64)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    frames = load_frames(args)
    if not frames:
        raise SystemExit("No frames loaded")

    model = YOLO(args.model, task="segment")
    all_candidates: list[dict[str, Any]] = []
    frame_rows: list[dict[str, Any]] = []
    started = time.time()
    for number, frame in enumerate(frames, start=1):
        infer_started = time.time()
        results = model.predict(
            frame.image,
            conf=max(0.01, min(0.99, args.model_confidence)),
            device=args.device,
            verbose=False,
        )
        candidates = extract_candidates(results, frame)
        elapsed_ms = (time.time() - infer_started) * 1000.0
        frame_row = {
            "source": frame.source,
            "source_kind": frame.source_kind,
            "source_index": frame.source_index,
            "t_s": round(frame.t_s, 3),
            "image_width": int(frame.image.shape[1]),
            "image_height": int(frame.image.shape[0]),
            "raw_candidates": len(candidates),
            "inference_ms": round(elapsed_ms, 2),
            "candidates": candidates,
        }
        frame_rows.append(frame_row)
        for index, candidate in enumerate(candidates):
            crop = contextual_crop(frame.image, candidate["bbox"])
            if crop is None:
                candidate["clip_ood"] = empty_clip_result()
                continue
            all_candidates.append(
                {
                    "frame": frame,
                    "frame_row": frame_row,
                    "candidate_index": index,
                    "crop": Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)),
                }
            )
        if number == 1 or number % 25 == 0 or number == len(frames):
            print(json.dumps({"model_frames": number, "total": len(frames), "candidates": len(all_candidates)}), flush=True)

    clip_started = time.time()
    clip_results = score_clip(all_candidates, args.clip_model, args.device, max(1, args.clip_batch_size))
    for job, clip_result in zip(all_candidates, clip_results):
        job["frame_row"]["candidates"][job["candidate_index"]]["clip_ood"] = clip_result

    for row in frame_rows:
        admitted = []
        rejected = []
        for candidate in row["candidates"]:
            evaluated = apply_ood_policy(candidate, row)
            (admitted if evaluated["admission"] == "accepted" else rejected).append(evaluated)
        row["detections"] = admitted
        row["rejected_detections"] = rejected
        row["accepted"] = len(admitted)
        row["rejected"] = len(rejected)
        del row["candidates"]

    summary = summarize(frame_rows, args, time.time() - started, time.time() - clip_started)
    artifact = {
        "algorithm": ALGORITHM,
        "model": str(args.model),
        "clip_model": str(args.clip_model),
        "clip_prompts": CLIP_PROMPTS,
        "policy": policy_description(),
        "summary": summary,
        "frames": frame_rows,
    }
    (output / "evaluation.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    write_contact_sheets(frames, frame_rows, output, max(8, args.preview_count))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def load_frames(args: argparse.Namespace) -> list[Frame]:
    rows: list[Frame] = []
    for raw in args.video:
        rows.extend(video_frames(Path(raw), max(0.1, args.video_fps), max(0.0, args.video_offset_s)))
    for raw in args.timeline:
        rows.extend(timeline_frames(Path(raw), max(1, args.timeline_stride)))
    return rows


def video_frames(path: Path, sample_fps: float, offset_s: float) -> list[Frame]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return []
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = count / max(0.1, fps)
    rows = []
    for index, t_s in enumerate(np.arange(offset_s, duration, 1.0 / sample_fps).tolist()):
        capture.set(cv2.CAP_PROP_POS_MSEC, float(t_s) * 1000.0)
        ok, image = capture.read()
        if ok and image is not None:
            rows.append(Frame(path.stem, "video_offset", index, float(t_s), image))
    capture.release()
    return rows


def timeline_frames(path: Path, stride: int) -> list[Frame]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = str(payload.get("id") or path.parent.name)
    rows = []
    for index, item in enumerate((payload.get("camera", {}).get("frames") or [])[::stride]):
        try:
            data = base64.b64decode(item.get("data") or "")
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            image = None
        if image is not None:
            rows.append(Frame(source, "archive_timeline", index, float(item.get("t_s") or 0.0), image))
    return rows


def extract_candidates(results: Any, frame: Frame) -> list[dict[str, Any]]:
    height, width = frame.image.shape[:2]
    rows: list[dict[str, Any]] = []
    for result in results:
        boxes = getattr(result, "boxes", None)
        masks = getattr(result, "masks", None)
        if boxes is None:
            continue
        xyxy = boxes.xyxy.detach().cpu().numpy().tolist()
        confidences = boxes.conf.detach().cpu().numpy().tolist()
        polygons = masks.xy if masks is not None and getattr(masks, "xy", None) is not None else []
        for index, box in enumerate(xyxy):
            x1, y1, x2, y2 = [float(value) for value in box[:4]]
            bbox = [
                int(round(max(0.0, x1))),
                int(round(max(0.0, y1))),
                int(round(max(1.0, min(float(width), x2) - max(0.0, x1)))),
                int(round(max(1.0, min(float(height), y2) - max(0.0, y1)))),
            ]
            contour = []
            if index < len(polygons):
                contour = simplify_polygon(polygons[index], width, height)
            features = candidate_features(frame.image, bbox, contour)
            rows.append(
                {
                    "bbox": bbox,
                    "contour": contour,
                    "model_confidence": round(float(confidences[index]), 5),
                    "features": features,
                    "source": "orchard_trunk_teacher_ood_yolo11_seg",
                }
            )
    return rows


def simplify_polygon(points: Any, width: int, height: int, max_points: int = 64) -> list[list[int]]:
    rows = [[max(0, min(width - 1, int(round(float(p[0]))))), max(0, min(height - 1, int(round(float(p[1])))))] for p in points if len(p) >= 2]
    if len(rows) > max_points:
        rows = rows[:: max(1, math.ceil(len(rows) / max_points))]
    return rows


def candidate_features(image: Any, bbox: list[int], contour: list[list[int]]) -> dict[str, Any]:
    height, width = image.shape[:2]
    x, y, bw, bh = bbox
    image_area = max(1, width * height)
    area_ratio = bw * bh / image_area
    edge_contact = x <= width * 0.012 or x + bw >= width * 0.988
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([28, 28, 24], np.uint8), np.array([95, 255, 255], np.uint8))
    scene_green = cv2.countNonZero(green) / image_area
    pad_x = max(4, int(bw * 0.65))
    pad_up = max(4, int(bh * 0.7))
    cx1, cx2 = max(0, x - pad_x), min(width, x + bw + pad_x)
    cy1, cy2 = max(0, y - pad_up), min(height, y + max(2, int(bh * 0.55)))
    context = green[cy1:cy2, cx1:cx2]
    canopy_support = cv2.countNonZero(context) / max(1, context.size)
    contour_fill = 0.0
    if len(contour) >= 3:
        contour_fill = abs(cv2.contourArea(np.asarray(contour, np.float32))) / max(1.0, bw * bh)
    return {
        "aspect_h_over_w": round(bh / max(1.0, bw), 4),
        "height_ratio": round(bh / max(1.0, height), 4),
        "area_ratio": round(area_ratio, 6),
        "top_ratio": round(y / max(1.0, height), 4),
        "bottom_ratio": round((y + bh) / max(1.0, height), 4),
        "edge_contact": bool(edge_contact),
        "contour_fill": round(float(contour_fill), 4),
        "scene_green_ratio": round(float(scene_green), 4),
        "canopy_green_support": round(float(canopy_support), 4),
    }


def contextual_crop(image: Any, bbox: list[int]) -> Any | None:
    height, width = image.shape[:2]
    x, y, bw, bh = bbox
    pad_x = max(4, int(bw * 0.2))
    pad_y = max(4, int(bh * 0.08))
    x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
    x2, y2 = min(width, x + bw + pad_x), min(height, y + bh + pad_y)
    return image[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None


def score_clip(jobs: list[dict[str, Any]], model_path: str, device: str, batch_size: int) -> list[dict[str, Any]]:
    if not jobs:
        return []
    processor = CLIPProcessor.from_pretrained(model_path, local_files_only=True)
    model = CLIPModel.from_pretrained(model_path, local_files_only=True).to(device).eval()
    text_inputs = processor(text=CLIP_PROMPTS, return_tensors="pt", padding=True)
    text_inputs = {key: value.to(device) for key, value in text_inputs.items()}
    with torch.no_grad():
        text_features = feature_tensor(model.get_text_features(**text_inputs))
    text_features = text_features / text_features.norm(dim=-1, keepdim=True)
    rows = []
    for start in range(0, len(jobs), batch_size):
        batch = jobs[start : start + batch_size]
        inputs = processor(images=[item["crop"] for item in batch], return_tensors="pt")
        with torch.no_grad():
            image_features = feature_tensor(model.get_image_features(pixel_values=inputs["pixel_values"].to(device)))
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        probabilities = (model.logit_scale.exp() * image_features @ text_features.T).softmax(dim=1).detach().cpu().numpy()
        for probability in probabilities:
            tree_probability = float(probability[0] + probability[1])
            negative_index = int(np.argmax(probability[2:])) + 2
            rows.append(
                {
                    "tree_probability": round(tree_probability, 5),
                    "ood_score": round(1.0 - tree_probability, 5),
                    "top_negative": CLIP_PROMPTS[negative_index],
                    "top_negative_probability": round(float(probability[negative_index]), 5),
                    "probabilities": {prompt: round(float(value), 5) for prompt, value in zip(CLIP_PROMPTS, probability)},
                }
            )
        print(json.dumps({"clip_candidates": min(start + len(batch), len(jobs)), "total": len(jobs)}), flush=True)
    return rows


def feature_tensor(value: Any) -> Any:
    for name in ("pooler_output", "image_embeds", "text_embeds"):
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    if isinstance(value, (tuple, list)) and value:
        return value[0]
    return value


def empty_clip_result() -> dict[str, Any]:
    return {"tree_probability": 0.0, "ood_score": 1.0, "top_negative": "invalid crop", "top_negative_probability": 1.0}


def apply_ood_policy(candidate: dict[str, Any], frame: dict[str, Any]) -> dict[str, Any]:
    output = dict(candidate)
    reasons: list[str] = []
    features = output.get("features") or {}
    clip = output.get("clip_ood") or empty_clip_result()
    model_probability = float(output.get("model_confidence") or 0.0)
    clip_probability = float(clip.get("tree_probability") or 0.0)
    aspect = float(features.get("aspect_h_over_w") or 0.0)
    area_ratio = float(features.get("area_ratio") or 0.0)
    height_ratio = float(features.get("height_ratio") or 0.0)
    bottom_ratio = float(features.get("bottom_ratio") or 0.0)
    scene_green = float(features.get("scene_green_ratio") or 0.0)
    canopy = float(features.get("canopy_green_support") or 0.0)
    edge = bool(features.get("edge_contact"))
    top_negative = str(clip.get("top_negative") or "")
    structural_negatives = {
        "a metal utility pole",
        "a concrete or wooden building column",
        "a furniture or table leg",
    }

    if aspect < 0.82:
        reasons.append("non_vertical_shape")
    if area_ratio > 0.2:
        reasons.append("oversized_region")
    if height_ratio < 0.075:
        reasons.append("undersized_region")
    if bottom_ratio < 0.32:
        reasons.append("floating_above_ground")
    if clip_probability < 0.075:
        reasons.append("clip_strong_non_tree")
    elif clip_probability < 0.24 and model_probability < 0.58:
        reasons.append("clip_ambiguous_low_model_support")
    if edge and clip_probability < 0.18:
        reasons.append("clip_ambiguous_edge")
    if scene_green < 0.012 and canopy < 0.025 and clip_probability < 0.7:
        reasons.append("out_of_orchard_scene")
    if top_negative in structural_negatives and canopy < 0.06 and model_probability < 0.35 and clip_probability < 0.75:
        reasons.append("structural_post_without_canopy")

    tree_probability = 0.7 * model_probability + 0.3 * clip_probability
    if canopy >= 0.12:
        tree_probability += 0.04
    if aspect >= 1.5:
        tree_probability += 0.025
    tree_probability = max(0.0, min(0.995, tree_probability))
    if tree_probability < 0.2:
        reasons.append("low_combined_tree_probability")
    output.update(
        {
            "confidence": round(tree_probability, 4),
            "tree_probability": round(tree_probability, 4),
            "ood_score": round(1.0 - tree_probability, 4),
            "admission": "accepted" if not reasons else "rejected",
            "rejection_reasons": sorted(set(reasons)),
            "ood_policy": ALGORITHM,
            "model_provenance": {
                "segmentation": "orchard_trunk_teacher_ood_yolo11_seg",
                "semantic_ood": "openai_clip_vit_b32_offline",
            },
        }
    )
    return output


def policy_description() -> dict[str, Any]:
    return {
        "segmentation_candidate_confidence": "0.08 for evaluation recall",
        "strong_clip_non_tree": "tree_probability < 0.075",
        "ambiguous_clip": "tree_probability < 0.24 requires model_probability >= 0.58",
        "shape": "height/width >= 0.82, area <= 20%, bottom >= 32% image height",
        "scene_ood": "low vegetation scene requires CLIP tree_probability >= 0.70",
        "combined_probability": "0.70 * segmentation + 0.30 * CLIP with small canopy/vertical bonuses",
    }


def summarize(rows: list[dict[str, Any]], args: argparse.Namespace, elapsed_s: float, clip_elapsed_s: float) -> dict[str, Any]:
    by_source: dict[str, dict[str, Any]] = {}
    rejection_reasons: dict[str, int] = {}
    top_negatives: dict[str, int] = {}
    for row in rows:
        bucket = by_source.setdefault(row["source"], {"frames": 0, "raw_candidates": 0, "accepted": 0, "rejected": 0, "zero_accepted_frames": 0})
        bucket["frames"] += 1
        bucket["raw_candidates"] += int(row["raw_candidates"])
        bucket["accepted"] += int(row["accepted"])
        bucket["rejected"] += int(row["rejected"])
        bucket["zero_accepted_frames"] += int(row["accepted"] == 0)
        for detection in row["rejected_detections"]:
            for reason in detection.get("rejection_reasons") or []:
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            negative = str((detection.get("clip_ood") or {}).get("top_negative") or "unknown")
            top_negatives[negative] = top_negatives.get(negative, 0) + 1
    inference = [float(row["inference_ms"]) for row in rows]
    return {
        "frames": len(rows),
        "raw_candidates": sum(int(row["raw_candidates"]) for row in rows),
        "accepted": sum(int(row["accepted"]) for row in rows),
        "rejected": sum(int(row["rejected"]) for row in rows),
        "zero_accepted_frames": sum(int(row["accepted"] == 0) for row in rows),
        "mean_inference_ms": round(statistics.fmean(inference), 2) if inference else 0.0,
        "p95_inference_ms": round(float(np.percentile(inference, 95)), 2) if inference else 0.0,
        "elapsed_s": round(elapsed_s, 2),
        "clip_elapsed_s": round(clip_elapsed_s, 2),
        "by_source": by_source,
        "rejection_reasons": dict(sorted(rejection_reasons.items(), key=lambda item: (-item[1], item[0]))),
        "rejected_top_negative": dict(sorted(top_negatives.items(), key=lambda item: (-item[1], item[0]))),
        "arguments": vars(args),
    }


def write_contact_sheets(frames: list[Frame], rows: list[dict[str, Any]], output: Path, count: int) -> None:
    indexes = np.linspace(0, len(frames) - 1, min(count, len(frames)), dtype=int).tolist()
    accepted_tiles = []
    audit_tiles = []
    for index in indexes:
        frame = frames[index]
        row = rows[index]
        accepted_tiles.append((render(frame.image, row["detections"], []), tile_label(row)))
        audit_tiles.append((render(frame.image, row["detections"], row["rejected_detections"]), tile_label(row)))
    write_contact_sheet(accepted_tiles, output / "accepted_contact_sheet.jpg")
    write_contact_sheet(audit_tiles, output / "ood_audit_contact_sheet.jpg")


def tile_label(row: dict[str, Any]) -> str:
    return f"{row['source'][:20]} {float(row['t_s']):.2f}s +{row['accepted']}/-{row['rejected']}"


def render(image: Any, accepted: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> Any:
    canvas = image.copy()
    for detection in rejected:
        draw_detection(canvas, detection, (40, 70, 235), "reject")
    for detection in accepted:
        draw_detection(canvas, detection, (55, 235, 95), "tree")
    return canvas


def draw_detection(canvas: Any, detection: dict[str, Any], color: tuple[int, int, int], prefix: str) -> None:
    contour = detection.get("contour") or []
    if len(contour) >= 3:
        cv2.polylines(canvas, [np.asarray(contour, np.int32)], True, color, 3, cv2.LINE_AA)
    x, y, bw, bh = [int(value) for value in (detection.get("bbox") or [0, 0, 0, 0])[:4]]
    cv2.rectangle(canvas, (x, y), (x + bw, y + bh), color, 1)
    label = f"{prefix} {float(detection.get('tree_probability') or 0):.2f}"
    cv2.putText(canvas, label, (x, max(18, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.43, color, 2, cv2.LINE_AA)


def write_contact_sheet(items: list[tuple[Any, str]], path: Path) -> None:
    if not items:
        return
    columns = 4
    cell_width, cell_height = 480, 300
    rows = math.ceil(len(items) / columns)
    sheet = np.full((rows * (cell_height + 32), columns * cell_width, 3), 15, np.uint8)
    for index, (image, label) in enumerate(items):
        height, width = image.shape[:2]
        scale = min(cell_width / max(1, width), cell_height / max(1, height))
        resized = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
        tile_x = (index % columns) * cell_width
        tile_y = (index // columns) * (cell_height + 32)
        x = tile_x + (cell_width - resized.shape[1]) // 2
        y = tile_y + (cell_height - resized.shape[0]) // 2
        sheet[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
        cv2.putText(sheet, label, (tile_x + 5, tile_y + cell_height + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (95, 225, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 91])


if __name__ == "__main__":
    raise SystemExit(main())
