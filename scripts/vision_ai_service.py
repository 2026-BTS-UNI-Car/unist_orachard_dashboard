#!/usr/bin/env python3
"""Local vision inference worker for orchard tree extraction.

This service is intentionally independent from the dashboard process so that
PyTorch/TensorRT dependencies can live in a dedicated container. The dashboard
can call POST /analyze and fall back to OpenCV when this worker is unavailable.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ALGORITHM_NAME = "local_orchard_vision_ai_worker_v1"
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
        self._load_backend()

    def _load_backend(self) -> None:
        requested = str(self.args.backend or "auto").lower()
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
        if requested in {"auto", "segformer"} and self.args.segformer_model:
            try:
                self.backend = SegFormerBackend(self.args.segformer_model, confidence=self.args.confidence, device=self.args.device)
                self.backend_name = self.backend.name
                return
            except Exception as exc:
                self.backend_error = f"{self.backend_error}; segformer: {exc}".strip("; ")

    def health(self) -> dict[str, Any]:
        return {
            "ok": self.backend is not None,
            "algorithm": ALGORITHM_NAME,
            "backend": self.backend_name,
            "model": getattr(self.backend, "model_path", "") if self.backend else "",
            "error": self.backend_error,
        }

    def analyze_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        image_data = payload.get("image") or payload.get("data") or ""
        if not image_data:
            return empty_analysis("no_image_data")
        image_bgr = decode_image(image_data)
        if image_bgr is None:
            return empty_analysis("decode_error")
        if not self.backend:
            return empty_analysis("backend_unavailable", self.backend_error)
        started = time.time()
        result = self.backend.analyze(image_bgr)
        result["algorithm"] = ALGORITHM_NAME
        result["backend"] = self.backend_name
        result["elapsed_ms"] = round((time.time() - started) * 1000.0, 1)
        return result


class UltralyticsBackend:
    name = "ultralytics_yolo_seg_local"

    def __init__(self, model_path: str, confidence: float, device: str = "") -> None:
        self.model_path = str(model_path)
        if not Path(self.model_path).exists():
            raise FileNotFoundError(self.model_path)
        from ultralytics import YOLO  # type: ignore

        self.model = YOLO(self.model_path)
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
                if not should_keep_yolo_label(label, confidence):
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
            "detections": detections[:12],
            "image": {"width": int(width), "height": int(height)},
            "legend": {
                "contour": "local segmentation contour for trunk/stem/tree classes",
                "root_group_id": "nearby split stems grouped into one tree root candidate",
            },
        }


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
        "backend": "unavailable",
        "method": method,
        "error": error,
        "confidence": 0.0,
        "detections": [],
    }


def normalize_label(label: str) -> str:
    return label.strip().lower().replace(" ", "_").replace("-", "_")


def should_keep_yolo_label(label: str, confidence: float) -> bool:
    if label in NEGATIVE_CLASS_HINTS:
        return False
    if label in TREE_CLASS_HINTS:
        return confidence >= 0.22
    return confidence >= 0.5 and any(hint in label for hint in TREE_CLASS_HINTS)


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
