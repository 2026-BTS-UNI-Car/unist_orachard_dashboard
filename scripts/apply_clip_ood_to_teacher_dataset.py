#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

import cv2  # type: ignore
import numpy as np  # type: ignore
import torch  # type: ignore
from PIL import Image  # type: ignore
from transformers import CLIPModel, CLIPProcessor  # type: ignore


OOD_ALGORITHM = "clip_semantic_tree_non_tree_gate_v1"
DEFAULT_PROMPTS = [
    "a natural tree trunk with rough bark",
    "an orchard fruit tree stem",
    "a person or human body",
    "a metal utility pole",
    "a concrete or wooden building column",
    "a furniture or table leg",
    "a wall or paved road",
    "leaves grass or bushes without a trunk",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a CLIP semantic OOD gate to a teacher-generated YOLO segmentation dataset.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--model", default="openai/clip-vit-base-patch32")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=48)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.dataset)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    processor = CLIPProcessor.from_pretrained(args.model, local_files_only=True)
    model = CLIPModel.from_pretrained(args.model, local_files_only=True).to(args.device).eval()
    jobs = collect_jobs(root, manifest)
    started = time.time()
    scores = score_jobs(jobs, processor, model, args.device, max(1, args.batch_size))
    metadata_cache: dict[Path, dict[str, Any]] = {}
    for job, score in zip(jobs, scores):
        metadata = metadata_cache.setdefault(job["metadata_path"], json.loads(job["metadata_path"].read_text(encoding="utf-8")))
        detection = metadata["detections"][job["detection_index"]]
        detection["clip_ood"] = score

    preview: list[tuple[Any, str]] = []
    for item_number, item in enumerate(manifest.get("items") or [], start=1):
        metadata_path = root / item["metadata"]
        metadata = metadata_cache.get(metadata_path) or json.loads(metadata_path.read_text(encoding="utf-8"))
        admitted: list[dict[str, Any]] = []
        rejected = list(metadata.get("rejected") or [])
        for detection in metadata.get("detections") or []:
            updated = apply_ood_policy(detection)
            if updated["admission"] == "accepted":
                admitted.append(updated)
            else:
                rejected.append(updated)
        metadata["detections"] = admitted
        metadata["rejected"] = rejected
        metadata["ood_algorithm"] = OOD_ALGORITHM
        metadata["clip_model"] = args.model
        metadata["clip_prompts"] = DEFAULT_PROMPTS
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        image_path = root / item["image"]
        image = cv2.imread(str(image_path))
        label_path = root / item["label"]
        label_path.write_text(yolo_labels(admitted, image.shape[1], image.shape[0]), encoding="utf-8")
        item["accepted_before_clip"] = int(item.get("accepted") or 0)
        item["accepted"] = len(admitted)
        item["rejected"] = len(rejected)
        item["clip_rejected"] = max(0, item["accepted_before_clip"] - len(admitted))
        if should_preview(item_number, len(manifest.get("items") or [])):
            preview.append((render(image, admitted, rejected), f"{item.get('source', '')[:22]} {float(item.get('t_s') or 0):.1f}s +{len(admitted)}/-{len(rejected)}"))

    stats = manifest.setdefault("stats", {})
    items = manifest.get("items") or []
    stats.update(
        {
            "positive_frames": sum(int(item.get("accepted") or 0) > 0 for item in items),
            "hard_negative_frames": sum(int(item.get("accepted") or 0) == 0 for item in items),
            "accepted_instances": sum(int(item.get("accepted") or 0) for item in items),
            "rejected_instances": sum(int(item.get("rejected") or 0) for item in items),
            "clip_rejected_instances": sum(int(item.get("clip_rejected") or 0) for item in items),
            "clip_elapsed_s": round(time.time() - started, 2),
        }
    )
    manifest["ood_algorithm"] = OOD_ALGORITHM
    manifest["clip_model"] = args.model
    manifest["clip_prompts"] = DEFAULT_PROMPTS
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_contact_sheet(preview, root / "teacher_clip_ood_contact_sheet.jpg")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def collect_jobs(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for item in manifest.get("items") or []:
        metadata_path = root / item["metadata"]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        image = cv2.imread(str(root / item["image"]))
        if image is None:
            continue
        for index, detection in enumerate(metadata.get("detections") or []):
            bbox = detection.get("bbox") or []
            if len(bbox) < 4:
                continue
            crop = contextual_crop(image, [int(value) for value in bbox[:4]])
            if crop is None or crop.size == 0:
                continue
            jobs.append(
                {
                    "metadata_path": metadata_path,
                    "detection_index": index,
                    "image": Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)),
                }
            )
    return jobs


def score_jobs(jobs: list[dict[str, Any]], processor: Any, model: Any, device: str, batch_size: int) -> list[dict[str, Any]]:
    if not jobs:
        return []
    text_inputs = processor(text=DEFAULT_PROMPTS, return_tensors="pt", padding=True)
    text_inputs = {key: value.to(device) for key, value in text_inputs.items()}
    with torch.no_grad():
        text_features = feature_tensor(model.get_text_features(**text_inputs))
    text_features = text_features / text_features.norm(dim=-1, keepdim=True)
    rows: list[dict[str, Any]] = []
    for start in range(0, len(jobs), batch_size):
        batch = jobs[start : start + batch_size]
        image_inputs = processor(images=[job["image"] for job in batch], return_tensors="pt")
        pixel_values = image_inputs["pixel_values"].to(device)
        with torch.no_grad():
            image_features = feature_tensor(model.get_image_features(pixel_values=pixel_values))
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        logits = model.logit_scale.exp() * image_features @ text_features.T
        probabilities = logits.softmax(dim=1).detach().cpu().numpy()
        for probability in probabilities:
            tree_probability = float(probability[0] + probability[1])
            negative_index = int(np.argmax(probability[2:])) + 2
            rows.append(
                {
                    "model": "openai/clip-vit-base-patch32",
                    "tree_probability": round(tree_probability, 5),
                    "ood_score": round(1.0 - tree_probability, 5),
                    "top_negative": DEFAULT_PROMPTS[negative_index],
                    "top_negative_probability": round(float(probability[negative_index]), 5),
                    "probabilities": {prompt: round(float(value), 5) for prompt, value in zip(DEFAULT_PROMPTS, probability)},
                }
            )
        print(json.dumps({"clip_scored": min(start + len(batch), len(jobs)), "total": len(jobs)}), flush=True)
    return rows


def feature_tensor(value: Any) -> Any:
    if hasattr(value, "pooler_output") and value.pooler_output is not None:
        return value.pooler_output
    if hasattr(value, "image_embeds") and value.image_embeds is not None:
        return value.image_embeds
    if hasattr(value, "text_embeds") and value.text_embeds is not None:
        return value.text_embeds
    if isinstance(value, (tuple, list)) and value:
        return value[0]
    return value


def apply_ood_policy(detection: dict[str, Any]) -> dict[str, Any]:
    updated = dict(detection)
    reasons = list(updated.get("rejection_reasons") or [])
    clip = updated.get("clip_ood") or {}
    clip_probability = float(clip.get("tree_probability") or 0.0)
    teacher_confidence = float(updated.get("teacher_confidence") or 0.0)
    features = updated.get("features") or {}
    canopy_support = float(features.get("canopy_green_support") or 0.0)
    edge_contact = bool(features.get("edge_contact"))
    geometry_probability = float(updated.get("tree_probability") or updated.get("confidence") or 0.0)

    if clip_probability < 0.075:
        reasons.append("clip_strong_non_tree")
    elif clip_probability < 0.28 and (teacher_confidence < 0.32 or canopy_support < 0.06):
        reasons.append("clip_ambiguous_without_detector_context")
    if edge_contact and clip_probability < 0.18:
        reasons.append("clip_ambiguous_edge_candidate")

    combined_probability = 0.44 * geometry_probability + 0.56 * clip_probability
    if teacher_confidence >= 0.38 and canopy_support >= 0.1:
        combined_probability = max(combined_probability, 0.38)
    combined_probability = max(0.0, min(0.995, combined_probability))
    admission = "accepted" if not reasons and combined_probability >= 0.28 else "rejected"
    updated.update(
        {
            "confidence": round(combined_probability, 4),
            "tree_probability": round(combined_probability, 4),
            "ood_score": round(1.0 - combined_probability, 4),
            "admission": admission,
            "rejection_reasons": sorted(set(reasons)),
            "ood_policy": OOD_ALGORITHM,
        }
    )
    return updated


def contextual_crop(image: Any, bbox: list[int]) -> Any | None:
    height, width = image.shape[:2]
    x, y, bw, bh = bbox
    pad_x = max(3, int(bw * 0.18))
    pad_y = max(3, int(bh * 0.08))
    x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
    x2, y2 = min(width, x + bw + pad_x), min(height, y + bh + pad_y)
    return image[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None


def yolo_labels(detections: list[dict[str, Any]], width: int, height: int) -> str:
    lines = []
    for detection in detections:
        contour = detection.get("contour") or []
        if len(contour) < 3:
            continue
        values = []
        for x, y in contour:
            values.extend((max(0.0, min(1.0, float(x) / max(1, width))), max(0.0, min(1.0, float(y) / max(1, height)))))
        lines.append("0 " + " ".join(f"{value:.6f}" for value in values))
    return "\n".join(lines) + ("\n" if lines else "")


def render(image: Any, admitted: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> Any:
    canvas = image.copy()
    for detection in rejected:
        bbox = detection.get("bbox") or []
        if len(bbox) >= 4:
            x, y, bw, bh = [int(value) for value in bbox[:4]]
            cv2.rectangle(canvas, (x, y), (x + bw, y + bh), (35, 75, 240), 2)
    for detection in admitted:
        contour = detection.get("contour") or []
        if len(contour) >= 3:
            cv2.polylines(canvas, [np.asarray(contour, np.int32)], True, (45, 240, 80), 4, cv2.LINE_AA)
        bbox = detection.get("bbox") or []
        if len(bbox) >= 4:
            x, y, _, _ = [int(value) for value in bbox[:4]]
            cv2.putText(canvas, f"tree {float(detection.get('tree_probability') or 0):.2f}", (x, max(18, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (45, 240, 80), 2, cv2.LINE_AA)
    return canvas


def should_preview(index: int, total: int) -> bool:
    if total <= 40:
        return True
    stride = max(1, total // 40)
    return index in {1, total} or index % stride == 0


def write_contact_sheet(items: list[tuple[Any, str]], path: Path) -> None:
    if not items:
        return
    columns = 4
    cell_width, cell_height = 440, 270
    rows = math.ceil(len(items) / columns)
    sheet = np.full((rows * (cell_height + 30), columns * cell_width, 3), 16, np.uint8)
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


if __name__ == "__main__":
    raise SystemExit(main())
