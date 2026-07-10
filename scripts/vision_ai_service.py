#!/usr/bin/env python3
"""Local vision inference worker for orchard tree extraction.

This service is intentionally independent from the dashboard process so that
PyTorch/TensorRT/Roboflow-Inference dependencies can live in a dedicated
container. Camera perception is AI-only: if this worker is unavailable, the
dashboard reports an explicit camera AI error instead of using an OpenCV
heuristic result as a silent fallback.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ALGORITHM_NAME = "local_orchard_vision_ai_worker_v4"
ANALYSIS_REVISION = os.environ.get("ORCHARD_VISION_REVISION", "orchard_trunk_yolo11_clip_ood_v1")
OOD_POLICY_NAME = "orchard_trunk_yolo11_clip_ood_v1"
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
TREE_CLASS_HINTS = {
    "tree",
    "trunk",
    "stem",
    "stems",
    "trunk_base",
    "split_stem",
    "branch",
    "canopy",
    "orchard_tree",
    "tree_trunk",
    "orchard_trunk",
    "fruit_tree",
}
NEGATIVE_CLASS_HINTS = {
    "person",
    "post",
    "pole",
    "sprinkler",
    "wall",
    "chair",
    "table",
    "fence",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("VISION_AI_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VISION_AI_PORT", "8785")))
    parser.add_argument("--backend", default=os.environ.get("ORCHARD_VISION_BACKEND", "auto"))
    parser.add_argument("--yolo-model", default=os.environ.get("ORCHARD_YOLO_MODEL", ""))
    parser.add_argument("--segformer-model", default=os.environ.get("ORCHARD_SEGFORMER_MODEL", ""))
    parser.add_argument("--roboflow-model", default=os.environ.get("ORCHARD_ROBOFLOW_MODEL_ID", ""))
    parser.add_argument("--clip-model", default=os.environ.get("ORCHARD_CLIP_MODEL", ""))
    parser.add_argument("--confidence", type=float, default=float(os.environ.get("ORCHARD_VISION_CONFIDENCE", "0.35")))
    parser.add_argument("--device", default=os.environ.get("ORCHARD_VISION_DEVICE", ""))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    analyzer = VisionAnalyzer(args)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/health":
                self.write_json(analyzer.health())
                return
            self.write_json({"ok": False, "error": "not_found"}, status=404)

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/analyze":
                self.write_json({"ok": False, "error": "not_found"}, status=404)
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                result = analyzer.analyze_payload(payload)
                self.write_json(result)
            except Exception as exc:
                self.write_json(empty_analysis("request_error", str(exc)), status=500)

        def write_json(self, payload: dict[str, Any], status: int = 200) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: Any) -> None:
            if os.environ.get("VISION_AI_HTTP_LOG", "0") == "1":
                super().log_message(format, *args)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({"event": "vision_ai_worker_started", "host": args.host, "port": args.port, **analyzer.health()}), flush=True)
    server.serve_forever()
    return 0


class VisionAnalyzer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.backend_name = "unavailable"
        self.backend: Any | None = None
        self.backend_error = ""
        self.inference_lock = threading.Lock()
        self.result_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._load_backend()

    def _load_backend(self) -> None:
        requested = str(self.args.backend or "auto").lower()
        if requested in {"operational", "orchard_ood", "yolo_clip"}:
            try:
                self.backend = OperationalOrchardBackend(
                    yolo_model=self.args.yolo_model,
                    clip_model=self.args.clip_model,
                    confidence=self.args.confidence,
                    device=self.args.device,
                )
                self.backend_name = self.backend.name
                return
            except Exception as exc:
                self.backend_error = f"operational: {exc}"
                return
        if requested in {"hybrid", "ensemble"}:
            try:
                self.backend = HybridVisionBackend(
                    yolo_model=self.args.yolo_model,
                    roboflow_model=self.args.roboflow_model,
                    confidence=self.args.confidence,
                    device=self.args.device,
                )
                self.backend_name = self.backend.name
                return
            except Exception as exc:
                self.backend_error = f"hybrid: {exc}"
                return
        if requested in {"auto", "ultralytics", "yolo"} and self.args.yolo_model:
            try:
                self.backend = UltralyticsBackend(
                    self.args.yolo_model,
                    confidence=self.args.confidence,
                    device=self.args.device,
                )
                self.backend_name = self.backend.name
                return
            except Exception as exc:
                self.backend_error = f"ultralytics: {exc}"
                if requested in {"ultralytics", "yolo"}:
                    return
        if requested in {"auto", "roboflow", "roboflow_cache"} and self.args.roboflow_model:
            try:
                self.backend = RoboflowInferenceBackend(
                    self.args.roboflow_model,
                    confidence=self.args.confidence,
                )
                self.backend_name = self.backend.name
                return
            except Exception as exc:
                self.backend_error = f"{self.backend_error}; roboflow: {exc}".strip("; ")
                if requested in {"roboflow", "roboflow_cache"}:
                    return
        if requested in {"auto", "segformer"} and self.args.segformer_model:
            try:
                self.backend = SegFormerBackend(self.args.segformer_model, confidence=self.args.confidence, device=self.args.device)
                self.backend_name = self.backend.name
                return
            except Exception as exc:
                self.backend_error = f"{self.backend_error}; segformer: {exc}".strip("; ")
        if not self.backend_error:
            self.backend_error = (
                "No local AI model configured. Set ORCHARD_YOLO_MODEL to a local .pt/.onnx/.engine file "
                "or ORCHARD_SEGFORMER_MODEL to a local model directory."
            )

    def health(self) -> dict[str, Any]:
        payload = {
            "ok": self.backend is not None,
            "algorithm": ALGORITHM_NAME,
            "revision": ANALYSIS_REVISION,
            "backend": self.backend_name,
            "model": getattr(self.backend, "model_path", "") if self.backend else "",
            "error": self.backend_error,
        }
        if self.backend and hasattr(self.backend, "health_details"):
            payload.update(self.backend.health_details())
        return payload

    def analyze_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        image_data = payload.get("image") or payload.get("data") or ""
        if not image_data:
            return empty_analysis("no_image_data")
        image_bgr = decode_image(image_data)
        if image_bgr is None:
            return empty_analysis("decode_error")
        if not self.backend:
            return empty_analysis("backend_unavailable", self.backend_error)
        cache_key = hashlib.sha1(image_data.encode("ascii", errors="ignore")).hexdigest()
        cached = self.result_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] <= 0.8:
            result = copy.deepcopy(cached[1])
            result["cache_hit"] = True
            return result
        started = time.time()
        with self.inference_lock:
            result = self.backend.analyze(image_bgr)
        result["algorithm"] = ALGORITHM_NAME
        result["revision"] = ANALYSIS_REVISION
        result["backend"] = self.backend_name
        result["elapsed_ms"] = round((time.time() - started) * 1000.0, 1)
        result["cache_hit"] = False
        self.result_cache[cache_key] = (time.monotonic(), copy.deepcopy(result))
        if len(self.result_cache) > 8:
            oldest = min(self.result_cache, key=lambda key: self.result_cache[key][0])
            self.result_cache.pop(oldest, None)
        return result


class UltralyticsBackend:
    name = "ultralytics_yolo_seg_local"

    def __init__(self, model_path: str, confidence: float, device: str = "") -> None:
        self.model_path = str(model_path)
        if not Path(self.model_path).exists():
            raise FileNotFoundError(self.model_path)
        from ultralytics import YOLO  # type: ignore

        self.model = YOLO(self.model_path, task="segment")
        self.confidence = confidence
        self.device = device or None
        self.names = getattr(self.model, "names", {}) or {}

    def analyze(self, image_bgr: Any) -> dict[str, Any]:
        height, width = image_bgr.shape[:2]
        kwargs: dict[str, Any] = {"conf": self.confidence, "verbose": False}
        if self.device:
            kwargs["device"] = self.device
        results = self.model.predict(image_bgr, **kwargs)
        detections: list[dict[str, Any]] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            masks = getattr(result, "masks", None)
            if boxes is None:
                continue
            xyxy = boxes.xyxy.cpu().numpy().tolist() if getattr(boxes, "xyxy", None) is not None else []
            confs = boxes.conf.cpu().numpy().tolist() if getattr(boxes, "conf", None) is not None else []
            classes = boxes.cls.cpu().numpy().tolist() if getattr(boxes, "cls", None) is not None else []
            polygons = masks.xy if masks is not None and getattr(masks, "xy", None) is not None else []
            for index, box in enumerate(xyxy):
                cls_id = int(classes[index]) if index < len(classes) else -1
                label = normalize_label(str(self.names.get(cls_id, cls_id)))
                confidence = float(confs[index]) if index < len(confs) else 0.0
                if not should_keep_yolo_label(label, confidence, minimum=self.confidence):
                    continue
                x1, y1, x2, y2 = [float(value) for value in box[:4]]
                bbox = xyxy_to_xywh([x1, y1, x2, y2], width, height)
                contour = []
                if index < len(polygons):
                    contour = simplify_polygon(polygons[index], width, height)
                render_shape = "contour" if contour else "bbox"
                detections.append(
                    {
                        "bbox": bbox,
                        "confidence": round(max(0.0, min(0.99, confidence)), 3),
                        "label": label,
                        "render_shape": render_shape,
                        "contour": contour,
                        "source": "local_yolo_segmentation",
                        "model": Path(self.model_path).name,
                    }
                )
        detections = group_multi_stem_roots(detections, width, height)
        top_confidence = max((float(item.get("confidence") or 0.0) for item in detections), default=0.0)
        return {
            "ok": True,
            "method": "local_ultralytics_segmentation_with_multi_stem_grouping",
            "confidence": round(top_confidence, 3),
            "detections": detections[:24],
            "image": {"width": int(width), "height": int(height)},
            "legend": {
                "contour": "local segmentation contour for trunk/stem/tree classes",
                "root_group_id": "nearby split stems grouped into one tree root candidate",
            },
        }


class OperationalOrchardBackend:
    """Individual trunk segmentation followed by a local semantic OOD gate."""

    name = "operational_yolo11_seg_clip_ood"

    def __init__(self, yolo_model: str, clip_model: str, confidence: float, device: str = "") -> None:
        if not yolo_model:
            raise ValueError("ORCHARD_YOLO_MODEL is required for operational backend")
        if not clip_model:
            raise ValueError("ORCHARD_CLIP_MODEL is required for operational backend")
        import torch  # type: ignore
        from transformers import CLIPModel, CLIPProcessor  # type: ignore

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available")
        self.detector = UltralyticsBackend(yolo_model, confidence=confidence, device=self.device)
        self.yolo_model_path = str(yolo_model)
        self.clip_model_path = str(clip_model)
        self.model_path = f"{self.yolo_model_path} + {self.clip_model_path}"
        self.confidence = float(confidence)
        self.clip_processor = CLIPProcessor.from_pretrained(self.clip_model_path, local_files_only=True)
        self.clip_model = CLIPModel.from_pretrained(self.clip_model_path, local_files_only=True).to(self.device).eval()
        self.clip_dtype = torch.float32
        if self.device.startswith("cuda") and os.environ.get("ORCHARD_CLIP_FP16", "1") == "1":
            self.clip_model.half()
            self.clip_dtype = torch.float16
        text_inputs = self.clip_processor(text=CLIP_PROMPTS, return_tensors="pt", padding=True)
        text_inputs = {key: value.to(self.device) for key, value in text_inputs.items()}
        with torch.no_grad():
            text_features = clip_feature_tensor(self.clip_model.get_text_features(**text_inputs))
        self.text_features = text_features / text_features.norm(dim=-1, keepdim=True)

    def health_details(self) -> dict[str, Any]:
        return {
            "ood_policy": OOD_POLICY_NAME,
            "clip_model": self.clip_model_path,
            "device": self.device,
            "candidate_confidence": round(self.confidence, 3),
            "camera_policy": "AI-only; no OpenCV or remote API fallback",
        }

    def analyze(self, image_bgr: Any) -> dict[str, Any]:
        import cv2  # type: ignore
        from PIL import Image  # type: ignore

        height, width = image_bgr.shape[:2]
        detector_started = time.perf_counter()
        detector_result = self.detector.analyze(image_bgr)
        detector_ms = (time.perf_counter() - detector_started) * 1000.0
        green_mask, scene = orchard_scene_context(cv2, image_bgr)
        candidates = []
        crops = []
        crop_indexes = []
        for detection in detector_result.get("detections", []) or []:
            row = copy.deepcopy(detection)
            row["model_confidence"] = round(float(row.get("confidence") or 0.0), 5)
            row["features"] = runtime_candidate_features(cv2, image_bgr, green_mask, row)
            row["source"] = "local_orchard_trunk_yolo11_seg"
            row["model"] = Path(self.yolo_model_path).name
            candidates.append(row)
            crop = runtime_contextual_crop(image_bgr, row.get("bbox") or [])
            if crop is not None and crop.size:
                crop_indexes.append(len(candidates) - 1)
                crops.append(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))

        ood_started = time.perf_counter()
        clip_rows = self._score_clip(crops)
        for index, clip_row in zip(crop_indexes, clip_rows):
            candidates[index]["clip_ood"] = clip_row
        for candidate in candidates:
            candidate.setdefault("clip_ood", empty_clip_ood())

        admitted = []
        rejected = []
        for candidate in candidates:
            evaluated = apply_runtime_ood_policy(candidate)
            if evaluated["admission"] == "accepted":
                admitted.append(evaluated)
            else:
                rejected.append(evaluated)
        admitted = group_multi_stem_roots(admitted, width, height)
        admitted.sort(key=lambda item: float(item.get("tree_probability") or 0.0), reverse=True)
        rejected.sort(key=lambda item: float(item.get("model_confidence") or 0.0), reverse=True)
        ood_ms = (time.perf_counter() - ood_started) * 1000.0
        return {
            "ok": True,
            "method": "individual_trunk_segmentation_with_offline_clip_ood",
            "confidence": round(max((float(item.get("tree_probability") or 0.0) for item in admitted), default=0.0), 3),
            "detections": admitted[:24],
            "rejected_detections": rejected[:24],
            "image": {"width": int(width), "height": int(height)},
            "scene": scene,
            "candidate_count": len(candidates),
            "accepted_count": len(admitted),
            "rejected_count": len(rejected),
            "timing": {"detector_ms": round(detector_ms, 1), "ood_ms": round(ood_ms, 1)},
            "ood_policy": OOD_POLICY_NAME,
            "model_provenance": {
                "segmentation": Path(self.yolo_model_path).name,
                "semantic_ood": "openai_clip_vit_b32_offline",
                "remote_api": False,
            },
            "legend": {
                "contour": "instance segmentation contour for a locally admitted orchard trunk",
                "tree_probability": "combined segmentation and CLIP semantic ranking score",
                "rejected_detections": "audit-only OOD candidates; never rendered or added to the map",
            },
        }

    def _score_clip(self, images: list[Any]) -> list[dict[str, Any]]:
        if not images:
            return []
        inputs = self.clip_processor(images=images, return_tensors="pt")
        pixel_values = inputs["pixel_values"].to(device=self.device, dtype=self.clip_dtype)
        with self.torch.no_grad():
            image_features = clip_feature_tensor(self.clip_model.get_image_features(pixel_values=pixel_values))
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        probabilities = (self.clip_model.logit_scale.exp() * image_features @ self.text_features.T).softmax(dim=1).detach().float().cpu().numpy()
        rows = []
        for probability in probabilities:
            tree_probability = float(probability[0] + probability[1])
            negative_index = int(probability[2:].argmax()) + 2
            rows.append(
                {
                    "tree_probability": round(tree_probability, 5),
                    "ood_score": round(1.0 - tree_probability, 5),
                    "top_negative": CLIP_PROMPTS[negative_index],
                    "top_negative_probability": round(float(probability[negative_index]), 5),
                }
            )
        return rows


class SegFormerBackend:
    name = "segformer_ade20k_local_semantic_gate"

    def __init__(self, model_path: str, confidence: float, device: str = "") -> None:
        self.model_path = str(model_path)
        if not Path(self.model_path).exists():
            raise FileNotFoundError(self.model_path)
        import torch  # type: ignore
        from transformers import AutoImageProcessor, SegformerForSemanticSegmentation  # type: ignore

        self.torch = torch
        self.processor = AutoImageProcessor.from_pretrained(self.model_path, local_files_only=True)
        self.model = SegformerForSemanticSegmentation.from_pretrained(self.model_path, local_files_only=True)
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        self.model.eval()
        self.confidence = confidence
        self.id2label = {int(k): normalize_label(v) for k, v in self.model.config.id2label.items()}

    def analyze(self, image_bgr: Any) -> dict[str, Any]:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore

        height, width = image_bgr.shape[:2]
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(image_rgb)
        inputs = self.processor(images=pil_image, return_tensors="pt")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self.torch.no_grad():
            outputs = self.model(**inputs)
        logits = self.torch.nn.functional.interpolate(
            outputs.logits,
            size=(height, width),
            mode="bilinear",
            align_corners=False,
        )
        probabilities = self.torch.softmax(logits, dim=1)[0]
        labels = probabilities.argmax(dim=0).cpu().numpy().astype("int32")
        detections: list[dict[str, Any]] = []
        for class_id, label in self.id2label.items():
            if label not in TREE_CLASS_HINTS:
                continue
            mask = (labels == int(class_id)).astype("uint8") * 255
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = float(cv2.contourArea(contour))
                if area < max(180.0, width * height * 0.0018):
                    continue
                x, y, bw, bh = cv2.boundingRect(contour)
                if bw * bh > width * height * 0.42:
                    continue
                contour_points = simplify_contour_cv2(cv2, contour, width, height)
                confidence = float(probabilities[int(class_id), y : y + bh, x : x + bw].mean().cpu().item())
                detections.append(
                    {
                        "bbox": [int(x), int(y), int(bw), int(bh)],
                        "confidence": round(max(0.05, min(0.92, confidence)), 3),
                        "label": label,
                        "render_shape": "contour",
                        "contour": contour_points,
                        "source": "local_segformer_semantic",
                        "semantic_gate": "positive_tree_mask",
                    }
                )
        detections = group_multi_stem_roots(detections, width, height)
        top_confidence = max((float(item.get("confidence") or 0.0) for item in detections), default=0.0)
        return {
            "ok": True,
            "method": "local_segformer_tree_semantic_components",
            "confidence": round(top_confidence, 3),
            "detections": detections[:10],
            "image": {"width": int(width), "height": int(height)},
            "legend": {
                "contour": "semantic tree/plant components; use mainly as support gate, not final trunk instance detector",
            },
        }


class RoboflowInferenceBackend:
    name = "roboflow_inference_local_cache"

    def __init__(self, model_id: str, confidence: float) -> None:
        self.model_path = str(model_id)
        self.model_id = str(model_id)
        self.confidence = confidence
        from inference import get_model  # type: ignore

        api_key = os.environ.get("ROBOFLOW_API_KEY") or None
        kwargs = {"model_id": self.model_id}
        if api_key:
            kwargs["api_key"] = api_key
        self.model = get_model(**kwargs)

    def analyze(self, image_bgr: Any) -> dict[str, Any]:
        import cv2  # type: ignore

        height, width = image_bgr.shape[:2]
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            ok, encoded = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if not ok:
                raise RuntimeError("failed to encode image for Roboflow local inference")
            tmp.write(encoded.tobytes())
            tmp.flush()
            raw_result = self.model.infer(tmp.name, confidence=max(0.05, min(0.99, float(self.confidence))))
        detections = []
        for prediction in roboflow_predictions(raw_result):
            label = normalize_label(prediction_value(prediction, "class") or prediction_value(prediction, "class_name") or "")
            confidence = float(prediction_value(prediction, "confidence") or 0.0)
            if not should_keep_yolo_label(label, confidence):
                continue
            bbox = roboflow_bbox(prediction, width, height)
            if not bbox:
                continue
            contour = roboflow_contour(prediction, width, height)
            detections.append(
                {
                    "bbox": bbox,
                    "confidence": round(max(0.0, min(0.99, confidence)), 3),
                    "label": label or "tree",
                    "render_shape": "contour" if contour else "bbox",
                    "contour": contour,
                    "source": "roboflow_inference_local_cache",
                    "model": self.model_id,
                }
            )
        detections = group_multi_stem_roots(detections, width, height)
        top_confidence = max((float(item.get("confidence") or 0.0) for item in detections), default=0.0)
        return {
            "ok": True,
            "revision": ANALYSIS_REVISION,
            "method": "roboflow_inference_local_cache_with_multi_stem_grouping",
            "confidence": round(top_confidence, 3),
            "detections": detections[:12],
            "image": {"width": int(width), "height": int(height)},
            "legend": {
                "contour": "local Roboflow Inference prediction converted to dashboard camera overlay schema",
                "root_group_id": "nearby split stems grouped into one tree root candidate",
            },
        }


class HybridVisionBackend:
    name = "hybrid_ultralytics_aihub_and_roboflow_trunk"

    def __init__(self, yolo_model: str, roboflow_model: str, confidence: float, device: str = "") -> None:
        if not yolo_model:
            raise ValueError("ORCHARD_YOLO_MODEL is required for hybrid backend")
        if not roboflow_model:
            raise ValueError("ORCHARD_ROBOFLOW_MODEL_ID is required for hybrid backend")
        self.aihub = UltralyticsBackend(yolo_model, confidence=confidence, device=device)
        self.roboflow = RoboflowInferenceBackend(roboflow_model, confidence=confidence)
        self.model_path = f"{self.aihub.model_path} + {self.roboflow.model_path}"

    def analyze(self, image_bgr: Any) -> dict[str, Any]:
        height, width = image_bgr.shape[:2]
        detections: list[dict[str, Any]] = []
        backend_errors: list[str] = []
        for label, backend in (("aihub_yolo", self.aihub), ("roboflow_trunk", self.roboflow)):
            try:
                result = backend.analyze(image_bgr)
            except Exception as exc:
                backend_errors.append(f"{label}: {exc}")
                continue
            for detection in result.get("detections", []) or []:
                row = dict(detection)
                row["source"] = f"hybrid_{label}:{row.get('source', '')}"
                detections.append(row)
        detections = dedupe_detections(detections, iou_threshold=0.52)
        detections = group_multi_stem_roots(detections, width, height)
        top_confidence = max((float(item.get("confidence") or 0.0) for item in detections), default=0.0)
        return {
            "ok": True,
            "method": "hybrid_aihub_canopy_tree_and_roboflow_trunk_segmentation",
            "confidence": round(top_confidence, 3),
            "detections": detections[:12],
            "image": {"width": int(width), "height": int(height)},
            "backend_errors": backend_errors,
            "legend": {
                "aihub_yolo": "YOLO segmentation fine-tuned from AIHub 71697 orchard tree masks",
                "roboflow_trunk": "local Roboflow cache tree-trunk segmentation for current camera domain",
                "dedupe": "overlapping boxes are merged by confidence before root grouping",
            },
        }


def orchard_scene_context(cv2: Any, image_bgr: Any) -> tuple[Any, dict[str, Any]]:
    import numpy as np  # type: ignore

    height, width = image_bgr.shape[:2]
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([28, 28, 24], np.uint8), np.array([95, 255, 255], np.uint8))
    area = max(1, width * height)
    upper = green[: max(1, int(height * 0.68)), :]
    green_ratio = cv2.countNonZero(green) / area
    upper_ratio = cv2.countNonZero(upper) / max(1, upper.size)
    return green, {
        "green_ratio": round(float(green_ratio), 4),
        "upper_green_ratio": round(float(upper_ratio), 4),
        "orchard_context": bool(green_ratio >= 0.012 or upper_ratio >= 0.018),
        "method": "vegetation_context_for_ood_only",
    }


def runtime_candidate_features(cv2: Any, image_bgr: Any, green_mask: Any, detection: dict[str, Any]) -> dict[str, Any]:
    import numpy as np  # type: ignore

    height, width = image_bgr.shape[:2]
    bbox = detection.get("bbox") or [0, 0, 0, 0]
    x, y, box_width, box_height = [int(value) for value in bbox[:4]]
    image_area = max(1, width * height)
    edge_contact = x <= width * 0.012 or x + box_width >= width * 0.988
    pad_x = max(4, int(box_width * 0.65))
    pad_up = max(4, int(box_height * 0.7))
    context_x1 = max(0, x - pad_x)
    context_x2 = min(width, x + box_width + pad_x)
    context_y1 = max(0, y - pad_up)
    context_y2 = min(height, y + max(2, int(box_height * 0.55)))
    context = green_mask[context_y1:context_y2, context_x1:context_x2]
    canopy_support = cv2.countNonZero(context) / max(1, context.size)
    contour = detection.get("contour") or []
    contour_fill = 0.0
    if len(contour) >= 3:
        contour_fill = abs(cv2.contourArea(np.asarray(contour, np.float32))) / max(1.0, box_width * box_height)
    return {
        "aspect_h_over_w": round(box_height / max(1.0, box_width), 4),
        "height_ratio": round(box_height / max(1.0, height), 4),
        "area_ratio": round(box_width * box_height / image_area, 6),
        "top_ratio": round(y / max(1.0, height), 4),
        "bottom_ratio": round((y + box_height) / max(1.0, height), 4),
        "edge_contact": bool(edge_contact),
        "contour_fill": round(float(contour_fill), 4),
        "scene_green_ratio": round(cv2.countNonZero(green_mask) / image_area, 4),
        "canopy_green_support": round(float(canopy_support), 4),
    }


def runtime_contextual_crop(image_bgr: Any, bbox: list[int]) -> Any | None:
    if len(bbox) < 4:
        return None
    height, width = image_bgr.shape[:2]
    x, y, box_width, box_height = [int(value) for value in bbox[:4]]
    pad_x = max(4, int(box_width * 0.2))
    pad_y = max(4, int(box_height * 0.08))
    x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
    x2, y2 = min(width, x + box_width + pad_x), min(height, y + box_height + pad_y)
    return image_bgr[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None


def clip_feature_tensor(value: Any) -> Any:
    for name in ("pooler_output", "image_embeds", "text_embeds"):
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    if isinstance(value, (tuple, list)) and value:
        return value[0]
    return value


def empty_clip_ood() -> dict[str, Any]:
    return {
        "tree_probability": 0.0,
        "ood_score": 1.0,
        "top_negative": "invalid crop",
        "top_negative_probability": 1.0,
    }


def apply_runtime_ood_policy(detection: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(detection)
    reasons: list[str] = []
    features = output.get("features") or {}
    clip = output.get("clip_ood") or empty_clip_ood()
    model_probability = float(output.get("model_confidence") or output.get("confidence") or 0.0)
    clip_probability = float(clip.get("tree_probability") or 0.0)
    aspect = float(features.get("aspect_h_over_w") or 0.0)
    area_ratio = float(features.get("area_ratio") or 0.0)
    height_ratio = float(features.get("height_ratio") or 0.0)
    bottom_ratio = float(features.get("bottom_ratio") or 0.0)
    scene_green = float(features.get("scene_green_ratio") or 0.0)
    canopy_support = float(features.get("canopy_green_support") or 0.0)
    edge_contact = bool(features.get("edge_contact"))
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
    if edge_contact and clip_probability < 0.18:
        reasons.append("clip_ambiguous_edge")
    if scene_green < 0.012 and canopy_support < 0.025 and clip_probability < 0.7:
        reasons.append("out_of_orchard_scene")
    if top_negative in structural_negatives and canopy_support < 0.06 and model_probability < 0.35 and clip_probability < 0.75:
        reasons.append("structural_post_without_canopy")

    tree_probability = 0.7 * model_probability + 0.3 * clip_probability
    if canopy_support >= 0.12:
        tree_probability += 0.04
    if aspect >= 1.5:
        tree_probability += 0.025
    tree_probability = max(0.0, min(0.995, tree_probability))
    if tree_probability < 0.2:
        reasons.append("low_combined_tree_probability")
    output.update(
        {
            "label": "orchard_tree_trunk",
            "confidence": round(tree_probability, 4),
            "tree_probability": round(tree_probability, 4),
            "ood_score": round(1.0 - tree_probability, 4),
            "admission": "accepted" if not reasons else "rejected",
            "rejection_reasons": sorted(set(reasons)),
            "ood_policy": OOD_POLICY_NAME,
            "model_provenance": {
                "segmentation": "orchard_trunk_teacher_ood_yolo11_seg",
                "semantic_ood": "openai_clip_vit_b32_offline",
                "remote_api": False,
            },
        }
    )
    return output


def decode_image(image_data: str) -> Any | None:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        if image_data.startswith("data:"):
            image_data = image_data.split(",", 1)[1]
        raw = base64.b64decode(image_data)
        return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


def empty_analysis(method: str, error: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "algorithm": ALGORITHM_NAME,
        "revision": ANALYSIS_REVISION,
        "backend": "unavailable",
        "method": method,
        "error": error,
        "confidence": 0.0,
        "detections": [],
    }


def normalize_label(label: str) -> str:
    return label.strip().lower().replace(" ", "_").replace("-", "_")


def should_keep_yolo_label(label: str, confidence: float, minimum: float = 0.22) -> bool:
    if label in NEGATIVE_CLASS_HINTS:
        return False
    if label in TREE_CLASS_HINTS:
        return confidence >= max(0.01, float(minimum))
    return confidence >= max(0.5, float(minimum)) and any(hint in label for hint in TREE_CLASS_HINTS)


def xyxy_to_xywh(box: list[float], width: int, height: int) -> list[int]:
    x1, y1, x2, y2 = box
    x1 = max(0.0, min(float(width - 1), x1))
    y1 = max(0.0, min(float(height - 1), y1))
    x2 = max(x1 + 1.0, min(float(width), x2))
    y2 = max(y1 + 1.0, min(float(height), y2))
    return [int(round(x1)), int(round(y1)), int(round(x2 - x1)), int(round(y2 - y1))]


def simplify_polygon(points: Any, width: int, height: int, max_points: int = 48) -> list[list[int]]:
    rows = []
    for point in points:
        if len(point) < 2:
            continue
        rows.append(
            [
                max(0, min(width - 1, int(round(float(point[0]))))),
                max(0, min(height - 1, int(round(float(point[1]))))),
            ]
        )
    if len(rows) > max_points:
        step = max(1, len(rows) // max_points)
        rows = rows[::step]
    return rows


def simplify_contour_cv2(cv2: Any, contour: Any, width: int, height: int) -> list[list[int]]:
    perimeter = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, max(1.2, perimeter * 0.012), True)
    return simplify_polygon(approx.reshape(-1, 2).tolist(), width, height)


def roboflow_predictions(result: Any) -> list[Any]:
    if isinstance(result, list):
        rows: list[Any] = []
        for item in result:
            rows.extend(roboflow_predictions(item))
        return rows
    if isinstance(result, dict):
        predictions = result.get("predictions") or result.get("objects") or []
        return list(predictions) if isinstance(predictions, list) else []
    predictions = getattr(result, "predictions", None)
    if predictions is not None:
        return list(predictions)
    return []


def prediction_value(prediction: Any, key: str) -> Any:
    if isinstance(prediction, dict):
        return prediction.get(key)
    return getattr(prediction, key, None)


def roboflow_bbox(prediction: Any, width: int, height: int) -> list[int]:
    x = prediction_value(prediction, "x")
    y = prediction_value(prediction, "y")
    bw = prediction_value(prediction, "width")
    bh = prediction_value(prediction, "height")
    if None not in {x, y, bw, bh}:
        return xyxy_to_xywh(
            [
                float(x) - float(bw) / 2.0,
                float(y) - float(bh) / 2.0,
                float(x) + float(bw) / 2.0,
                float(y) + float(bh) / 2.0,
            ],
            width,
            height,
        )
    x1 = prediction_value(prediction, "x_min")
    y1 = prediction_value(prediction, "y_min")
    x2 = prediction_value(prediction, "x_max")
    y2 = prediction_value(prediction, "y_max")
    if None not in {x1, y1, x2, y2}:
        return xyxy_to_xywh([float(x1), float(y1), float(x2), float(y2)], width, height)
    return []


def roboflow_contour(prediction: Any, width: int, height: int) -> list[list[int]]:
    points = prediction_value(prediction, "points") or prediction_value(prediction, "polygon") or []
    rows = []
    for point in points or []:
        if isinstance(point, dict):
            px = point.get("x")
            py = point.get("y")
        elif hasattr(point, "x") and hasattr(point, "y"):
            px = getattr(point, "x", None)
            py = getattr(point, "y", None)
        else:
            try:
                px = point[0] if len(point) > 0 else None
                py = point[1] if len(point) > 1 else None
            except TypeError:
                px = None
                py = None
        if px is None or py is None:
            continue
        rows.append([float(px), float(py)])
    return simplify_polygon(rows, width, height)


def dedupe_detections(detections: list[dict[str, Any]], iou_threshold: float = 0.5) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for detection in sorted(detections, key=lambda item: float(item.get("confidence") or 0.0), reverse=True):
        bbox = detection.get("bbox") or []
        if len(bbox) < 4:
            continue
        if any(bbox_iou(bbox, item.get("bbox") or []) >= iou_threshold for item in kept):
            continue
        kept.append(detection)
    return kept


def bbox_iou(a: list[int], b: list[int]) -> float:
    if len(a) < 4 or len(b) < 4:
        return 0.0
    ax1, ay1, aw, ah = [float(value) for value in a[:4]]
    bx1, by1, bw, bh = [float(value) for value in b[:4]]
    ax2, ay2 = ax1 + max(0.0, aw), ay1 + max(0.0, ah)
    bx2, by2 = bx1 + max(0.0, bw), by1 + max(0.0, bh)
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = max(0.0, aw) * max(0.0, ah) + max(0.0, bw) * max(0.0, bh) - intersection
    return intersection / union if union > 0.0 else 0.0


def group_multi_stem_roots(detections: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for detection in sorted(detections, key=lambda item: float(item.get("confidence") or 0.0), reverse=True):
        bbox = detection.get("bbox") or [0, 0, 0, 0]
        bottom = bbox_bottom_center(bbox)
        best_group = None
        best_distance = 10**9
        for group in groups:
            gx, gy = group["bottom"]
            distance_px = ((bottom[0] - gx) ** 2 + (bottom[1] - gy) ** 2) ** 0.5
            gate_px = max(28.0, min(96.0, 0.085 * max(width, height) + 0.08 * max(bottom[1], gy)))
            if abs(bottom[1] - gy) <= height * 0.16 and distance_px < gate_px and distance_px < best_distance:
                best_group = group
                best_distance = distance_px
        if best_group is None:
            best_group = {
                "id": f"root-{len(groups) + 1}",
                "bottom": bottom,
                "members": [],
            }
            groups.append(best_group)
        best_group["members"].append(detection)
        count = len(best_group["members"])
        bx = sum(bbox_bottom_center(item.get("bbox") or [0, 0, 0, 0])[0] for item in best_group["members"]) / count
        by = sum(bbox_bottom_center(item.get("bbox") or [0, 0, 0, 0])[1] for item in best_group["members"]) / count
        best_group["bottom"] = [bx, by]

    output: list[dict[str, Any]] = []
    for group in groups:
        members = group["members"]
        for member in members:
            row = dict(member)
            row["root_group_id"] = group["id"]
            row["stem_count"] = len(members)
            row["multi_stem_root"] = len(members) > 1
            output.append(row)
    output.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return output


def bbox_bottom_center(bbox: list[int]) -> list[float]:
    x, y, w, h = [float(value) for value in bbox[:4]]
    return [x + w / 2.0, y + h]


if __name__ == "__main__":
    raise SystemExit(main())
