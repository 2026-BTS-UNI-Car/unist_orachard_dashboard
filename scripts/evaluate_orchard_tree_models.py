#!/usr/bin/env python3
"""Evaluate public tree/trunk perception models on recorded orchard archives.

The script intentionally keeps API tokens out of files. Set these environment
variables only in the shell that runs the evaluation:

  ROBOFLOW_API_KEY=... HF_TOKEN=... python3 scripts/evaluate_orchard_tree_models.py

Default mode samples a small, evenly spaced set of frames from each recording.
Use --all-frames when you intentionally want a full archive pass.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any


DEFAULT_RECORDINGS = ("20260708T053333Z_both", "20260708T054036Z_both")

ROBOFLOW_MODELS = {
    "rf_yolo11_trunk_seg": "tree-trunk-detection-369pz-vm3hn/2",
    "rf_orchard_yolov5s_det": "orchard-trunk-yolov5s/3",
    "rf_trees_and_trunks_det": "trees-and-trunks/1",
    "rf_generic_tree_wall_seg": "object-detection-isoz2/1",
}

HF_SEGFORMER_MODEL = "nvidia/segformer-b0-finetuned-ade-512-512"
HF_ENDPOINT = f"https://router.huggingface.co/hf-inference/models/{HF_SEGFORMER_MODEL}"

SEMANTIC_POSITIVE_LABELS = {"tree", "plant", "grass", "earth", "field", "palm"}
SEMANTIC_NEGATIVE_LABELS = {
    "wall",
    "floor",
    "building",
    "road",
    "sidewalk",
    "table",
    "chair",
    "person",
    "pole",
    "column",
    "bench",
    "fence",
    "ceiling",
}


@dataclass(frozen=True)
class FrameSample:
    recording_id: str
    index: int
    t_s: float
    image_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recordings",
        nargs="*",
        default=list(DEFAULT_RECORDINGS),
        help="Recording ids under runtime/recordings.",
    )
    parser.add_argument("--recordings-root", default="runtime/recordings")
    parser.add_argument("--output", default="runtime/analysis/model_eval")
    parser.add_argument(
        "--frames-per-recording",
        type=int,
        default=8,
        help="Evenly spaced frames per recording when --all-frames is not set.",
    )
    parser.add_argument("--all-frames", action="store_true", help="Evaluate every camera frame.")
    parser.add_argument("--roboflow", action="store_true", help="Run Roboflow hosted models.")
    parser.add_argument("--hf-segformer", action="store_true", help="Run Hugging Face SegFormer ADE20K.")
    parser.add_argument(
        "--roboflow-model",
        action="append",
        choices=sorted(ROBOFLOW_MODELS),
        help="Roboflow model key to run. Repeat for multiple. Default: all Roboflow models.",
    )
    parser.add_argument(
        "--skip-existing-frames",
        action="store_true",
        help="Reuse extracted frame files if present.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.roboflow and not args.hf_segformer:
        args.roboflow = True
        args.hf_segformer = True

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    samples = extract_samples(
        recordings_root=Path(args.recordings_root),
        output=output,
        recording_ids=args.recordings,
        frames_per_recording=args.frames_per_recording,
        all_frames=args.all_frames,
        skip_existing=args.skip_existing_frames,
    )
    print(f"Prepared {len(samples)} frame samples")

    combined_summary: dict[str, Any] = {
        "ok": True,
        "generated_at": time.time(),
        "sample_count": len(samples),
        "recordings": list(args.recordings),
        "runs": {},
    }

    if args.roboflow:
        keys = args.roboflow_model or sorted(ROBOFLOW_MODELS)
        roboflow_result = evaluate_roboflow(samples, output, keys)
        combined_summary["runs"]["roboflow"] = roboflow_result["summary"]

    if args.hf_segformer:
        hf_result = evaluate_hf_segformer(samples, output)
        combined_summary["runs"]["hf_segformer"] = hf_result["summary"]

    (output / "model_evaluation_summary.json").write_text(
        json.dumps(combined_summary, ensure_ascii=False, indent=2)
    )
    print(json.dumps(combined_summary["runs"], ensure_ascii=False, indent=2))
    return 0


def extract_samples(
    recordings_root: Path,
    output: Path,
    recording_ids: list[str],
    frames_per_recording: int,
    all_frames: bool,
    skip_existing: bool,
) -> list[FrameSample]:
    samples: list[FrameSample] = []
    for recording_id in recording_ids:
        timeline_path = recordings_root / recording_id / "timeline.json"
        if not timeline_path.exists():
            raise FileNotFoundError(f"Missing timeline: {timeline_path}")
        timeline = json.loads(timeline_path.read_text())
        frames = ((timeline.get("camera") or {}).get("frames") or [])
        indexes = list(range(len(frames))) if all_frames else evenly_spaced_indexes(len(frames), frames_per_recording)
        frame_dir = output / "frames" / recording_id
        frame_dir.mkdir(parents=True, exist_ok=True)
        for index in indexes:
            frame = frames[index]
            t_s = float(frame.get("t_s") or 0.0)
            image_path = frame_dir / f"{index:04d}_{t_s:07.3f}.jpg"
            if not (skip_existing and image_path.exists()):
                write_camera_frame(frame, image_path)
            samples.append(FrameSample(recording_id=recording_id, index=index, t_s=t_s, image_path=image_path))
    return samples


def evenly_spaced_indexes(count: int, requested: int) -> list[int]:
    if count <= 0 or requested <= 0:
        return []
    if requested >= count:
        return list(range(count))
    if requested == 1:
        return [0]
    indexes = {round(i * (count - 1) / (requested - 1)) for i in range(requested)}
    return sorted(indexes)


def write_camera_frame(frame: dict[str, Any], image_path: Path) -> None:
    data = frame.get("data")
    if not data:
        raise ValueError(f"Camera frame has no data for {image_path}")
    if isinstance(data, str) and data.startswith("data:"):
        data = data.split(",", 1)[1]
    if isinstance(data, str) and len(data) < 512 and Path(data).exists():
        image_path.write_bytes(Path(data).read_bytes())
        return
    image_path.write_bytes(base64.b64decode(data))


def evaluate_roboflow(samples: list[FrameSample], output: Path, model_keys: list[str]) -> dict[str, Any]:
    api_key = os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        raise RuntimeError("Set ROBOFLOW_API_KEY before running --roboflow")
    try:
        from inference_sdk import InferenceHTTPClient
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError("Install optional deps: pip install inference-sdk pillow") from exc

    client = InferenceHTTPClient(api_url="https://serverless.roboflow.com", api_key=api_key)
    run_dir = output / "roboflow_public_models"
    overlay_dir = run_dir / "overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for key in model_keys:
        model_id = ROBOFLOW_MODELS[key]
        stats = new_model_stats(model_id)
        for sample in samples:
            started = time.time()
            try:
                response = client.infer(str(sample.image_path), model_id=model_id)
                elapsed = time.time() - started
                predictions = response.get("predictions") or []
                overlay_path = draw_roboflow_overlay(Image, ImageDraw, sample, predictions, key, overlay_dir)
                item = {
                    "model_key": key,
                    "model_id": model_id,
                    "recording": sample.recording_id,
                    "frame": sample.image_path.name,
                    "elapsed_s": round(elapsed, 3),
                    "overlay": str(overlay_path),
                    "predictions": [],
                }
                for prediction in predictions:
                    cls = str(prediction.get("class") or prediction.get("class_name") or "")
                    confidence = float(prediction.get("confidence") or 0.0)
                    box = prediction_box(prediction)
                    area_ratio = max(0.0, (box[2] - box[0]) * (box[3] - box[1]) / (640 * 360))
                    item["predictions"].append(
                        {
                            "class": cls,
                            "confidence": round(confidence, 4),
                            "area_ratio": round(area_ratio, 4),
                            "has_polygon": bool(prediction.get("points")),
                        }
                    )
                    add_prediction_stats(stats, cls, confidence, area_ratio, prediction)
                update_frame_stats(stats, predictions, elapsed)
                results.append(item)
            except Exception as exc:  # Hosted model availability differs by project.
                stats["errors"] += 1
                results.append(
                    {
                        "model_key": key,
                        "model_id": model_id,
                        "recording": sample.recording_id,
                        "frame": sample.image_path.name,
                        "error": str(exc)[:500],
                    }
                )
        summary[key] = finalize_model_stats(stats)
        make_contact_sheet(Image, output / f"contact_{key}.jpg", overlay_dir.glob(f"{key}_*.jpg"))

    (run_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return {"results": results, "summary": summary}


def new_model_stats(model_id: str) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "frames": 0,
        "errors": 0,
        "predictions": 0,
        "frames_with_prediction": 0,
        "class_counts": {},
        "confidence_values": [],
        "edge_touching_predictions": 0,
        "large_area_predictions": 0,
        "polygon_predictions": 0,
        "elapsed_s": 0.0,
    }


def add_prediction_stats(
    stats: dict[str, Any],
    cls: str,
    confidence: float,
    area_ratio: float,
    prediction: dict[str, Any],
) -> None:
    stats["class_counts"][cls] = stats["class_counts"].get(cls, 0) + 1
    stats["confidence_values"].append(confidence)
    box = prediction_box(prediction)
    if box[0] <= 2 or box[1] <= 2 or box[2] >= 638 or box[3] >= 358:
        stats["edge_touching_predictions"] += 1
    if area_ratio >= 0.08:
        stats["large_area_predictions"] += 1
    if prediction.get("points"):
        stats["polygon_predictions"] += 1


def update_frame_stats(stats: dict[str, Any], predictions: list[dict[str, Any]], elapsed: float) -> None:
    stats["frames"] += 1
    stats["predictions"] += len(predictions)
    stats["frames_with_prediction"] += 1 if predictions else 0
    stats["elapsed_s"] += elapsed


def finalize_model_stats(stats: dict[str, Any]) -> dict[str, Any]:
    confidence_values = stats.pop("confidence_values")
    frames = max(1, int(stats["frames"]))
    stats["avg_predictions_per_frame"] = round(stats["predictions"] / frames, 3)
    stats["avg_ms_per_frame"] = round(1000 * stats["elapsed_s"] / frames, 1)
    stats["elapsed_s"] = round(stats["elapsed_s"], 2)
    if confidence_values:
        stats["confidence"] = {
            "mean": round(mean(confidence_values), 3),
            "median": round(median(confidence_values), 3),
            "max": round(max(confidence_values), 3),
        }
    else:
        stats["confidence"] = {}
    return stats


def prediction_box(prediction: dict[str, Any]) -> list[float]:
    x = float(prediction.get("x") or 0.0)
    y = float(prediction.get("y") or 0.0)
    w = float(prediction.get("width") or 0.0)
    h = float(prediction.get("height") or 0.0)
    return [x - w / 2, y - h / 2, x + w / 2, y + h / 2]


def draw_roboflow_overlay(
    image_module: Any,
    draw_module: Any,
    sample: FrameSample,
    predictions: list[dict[str, Any]],
    model_key: str,
    overlay_dir: Path,
) -> Path:
    image = image_module.open(sample.image_path).convert("RGB")
    draw = draw_module.Draw(image)
    for prediction in predictions:
        cls = str(prediction.get("class") or prediction.get("class_name") or "")
        confidence = float(prediction.get("confidence") or 0.0)
        color = (80, 220, 166) if cls.lower() in {"tree", "trunk"} else (255, 194, 77)
        points = prediction.get("points") or []
        if points:
            polygon = [(float(point.get("x", 0)), float(point.get("y", 0))) for point in points]
            if len(polygon) >= 2:
                draw.line(polygon + [polygon[0]], fill=color, width=3)
        else:
            draw.rectangle(prediction_box(prediction), outline=color, width=3)
        x0, y0, x1, y1 = prediction_box(prediction)
        draw.rectangle([x0, y0, min(x0 + 104, x1), min(y0 + 18, y1)], fill=(0, 0, 0))
        draw.text((x0 + 2, y0 + 2), f"{cls} {confidence:.2f}", fill=(255, 255, 255))
    draw.rectangle([0, 0, image.width, 22], fill=(0, 0, 0))
    draw.text((4, 4), f"{model_key} {sample.recording_id} {sample.image_path.stem}", fill=(255, 255, 255))
    overlay_path = overlay_dir / f"{model_key}_{sample.recording_id}_{sample.image_path.stem}.jpg"
    image.save(overlay_path, quality=88)
    return overlay_path


def evaluate_hf_segformer(samples: list[FrameSample], output: Path) -> dict[str, Any]:
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not hf_token:
        raise RuntimeError("Set HF_TOKEN before running --hf-segformer")
    try:
        import requests
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError("Install optional deps: pip install requests pillow") from exc

    run_dir = output / "hf_segformer"
    overlay_dir = run_dir / "overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {hf_token}", "Content-Type": "image/jpeg"}
    results: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "model_id": HF_SEGFORMER_MODEL,
        "frames": 0,
        "errors": 0,
        "labels": {},
        "positive_mask_ratio_sum": 0.0,
        "negative_mask_ratio_sum": 0.0,
        "elapsed_s": 0.0,
    }

    for sample in samples:
        image = Image.open(sample.image_path).convert("RGB")
        started = time.time()
        response = requests.post(HF_ENDPOINT, headers=headers, data=sample.image_path.read_bytes(), timeout=60)
        elapsed = time.time() - started
        item: dict[str, Any] = {
            "recording": sample.recording_id,
            "frame": sample.image_path.name,
            "status": response.status_code,
            "elapsed_s": round(elapsed, 3),
            "labels": [],
        }
        if response.status_code != 200:
            summary["errors"] += 1
            item["error"] = response.text[:500]
            results.append(item)
            continue

        positive_ratio, negative_ratio, overlay_path, labels = draw_hf_semantic_overlay(
            Image,
            ImageDraw,
            image,
            response.json(),
            sample,
            overlay_dir,
        )
        for label in labels:
            summary["labels"][label["label"]] = summary["labels"].get(label["label"], 0) + 1
        item["positive_mask_ratio"] = round(positive_ratio, 4)
        item["negative_mask_ratio"] = round(negative_ratio, 4)
        item["overlay"] = str(overlay_path)
        item["labels"] = labels
        results.append(item)
        summary["frames"] += 1
        summary["positive_mask_ratio_sum"] += positive_ratio
        summary["negative_mask_ratio_sum"] += negative_ratio
        summary["elapsed_s"] += elapsed

    frames = max(1, int(summary["frames"]))
    summary["positive_mask_ratio_mean"] = round(summary.pop("positive_mask_ratio_sum") / frames, 4)
    summary["negative_mask_ratio_mean"] = round(summary.pop("negative_mask_ratio_sum") / frames, 4)
    summary["avg_ms_per_frame"] = round(1000 * summary["elapsed_s"] / frames, 1)
    summary["elapsed_s"] = round(summary["elapsed_s"], 2)
    make_contact_sheet(Image, output / "contact_hf_segformer_ade20k.jpg", overlay_dir.glob("*.jpg"))
    (run_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return {"results": results, "summary": summary}


def draw_hf_semantic_overlay(
    image_module: Any,
    draw_module: Any,
    image: Any,
    predictions: list[dict[str, Any]],
    sample: FrameSample,
    overlay_dir: Path,
) -> tuple[float, float, Path, list[dict[str, Any]]]:
    width, height = image.size
    base = image.copy().convert("RGBA")
    draw = draw_module.Draw(base)
    positive_ratio = 0.0
    negative_ratio = 0.0
    labels: list[dict[str, Any]] = []
    for prediction in predictions:
        label = str(prediction.get("label") or "")
        score = float(prediction.get("score") or 0.0)
        mask_b64 = prediction.get("mask") or ""
        if not mask_b64:
            continue
        mask = image_module.open(io.BytesIO(base64.b64decode(mask_b64))).convert("L").resize((width, height))
        bbox = mask.getbbox()
        pixels = sum(mask.histogram()[129:])
        ratio = pixels / float(width * height)
        labels.append(
            {
                "label": label,
                "score": round(score, 4),
                "mask_ratio": round(ratio, 4),
                "bbox": list(bbox) if bbox else None,
            }
        )
        if label in SEMANTIC_POSITIVE_LABELS:
            positive_ratio += ratio
            color = (62, 211, 164, 82)
            outline = (62, 211, 164, 255)
        elif label in SEMANTIC_NEGATIVE_LABELS:
            negative_ratio += ratio
            color = (255, 194, 77, 80)
            outline = (255, 194, 77, 255)
            if label in {"wall", "floor", "road", "building", "ceiling"}:
                color = (44, 128, 255, 70)
                outline = (44, 128, 255, 255)
        else:
            color = (180, 180, 220, 38) if ratio >= 0.04 else (255, 255, 255, 0)
            outline = (180, 180, 220, 255)
        if color[3] > 0:
            overlay = image_module.new("RGBA", (width, height), color)
            overlay.putalpha(mask.point(lambda value: min(color[3], int(value * color[3] / 255))))
            base.alpha_composite(overlay)
        if bbox and (label in SEMANTIC_POSITIVE_LABELS or label in SEMANTIC_NEGATIVE_LABELS):
            x0, y0, x1, y1 = bbox
            draw.rectangle([x0, y0, x1, y1], outline=outline, width=2)
            draw.text((max(2, x0 + 3), max(2, y0 + 3)), f"{label} {score:.2f}", fill=(255, 255, 255, 255))
    draw.rectangle([0, 0, width, 26], fill=(0, 0, 0, 170))
    draw.text(
        (6, 5),
        f"{sample.recording_id} {sample.image_path.stem} pos={positive_ratio:.2f} neg={negative_ratio:.2f}",
        fill=(255, 255, 255, 255),
    )
    overlay_path = overlay_dir / f"{sample.recording_id}_{sample.image_path.stem}_hf_segformer.jpg"
    base.convert("RGB").save(overlay_path, quality=88)
    return positive_ratio, negative_ratio, overlay_path, labels


def make_contact_sheet(image_module: Any, output_path: Path, image_paths: Any) -> None:
    paths = sorted(Path(path) for path in image_paths)
    if not paths:
        return
    images = [image_module.open(path).convert("RGB") for path in paths]
    thumb_width, thumb_height = 320, 180
    cols = 4
    rows = (len(images) + cols - 1) // cols
    sheet = image_module.new("RGB", (cols * thumb_width, rows * thumb_height), (8, 14, 32))
    for index, image in enumerate(images):
        image.thumbnail((thumb_width, thumb_height))
        x = (index % cols) * thumb_width + (thumb_width - image.width) // 2
        y = (index // cols) * thumb_height + (thumb_height - image.height) // 2
        sheet.paste(image, (x, y))
    sheet.save(output_path, quality=88)


if __name__ == "__main__":
    raise SystemExit(main())
