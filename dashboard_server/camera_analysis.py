from __future__ import annotations

import base64
import copy
import json
import os
from functools import lru_cache
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


CAMERA_ALGORITHM_NAME = "opencv_orchard_scene_trunk_semantic_gate_v4"
LOCAL_VISION_ALGORITHM_NAME = "local_orchard_vision_ai_worker_v3"
LOCAL_VISION_ANALYSIS_REVISION = "roboflow_yolo11n_seg_local_cache_conf020_r2"
VISION_AI_URL = os.environ.get("ORCHARD_VISION_AI_URL", "").strip()
VISION_AI_TIMEOUT_S = float(os.environ.get("ORCHARD_VISION_AI_TIMEOUT_S", "6.0"))


def empty_camera_analysis(method: str = "unavailable", error: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "algorithm": LOCAL_VISION_ALGORITHM_NAME,
        "revision": LOCAL_VISION_ANALYSIS_REVISION,
        "method": method,
        "error": error,
        "ai_required": True,
        "confidence": 0.0,
        "detections": [],
    }


def analyze_camera_frame_payload(frame: dict[str, Any]) -> dict[str, Any]:
    existing = frame.get("overlay")
    if isinstance(existing, dict) and current_camera_overlay_algorithm(existing.get("algorithm"), existing.get("revision")):
        return existing
    data = frame.get("data")
    if not data:
        return empty_camera_analysis("no_frame_data")
    return copy.deepcopy(_analyze_camera_data_cached(data))


def current_camera_overlay_algorithm(algorithm: Any, revision: Any = None) -> bool:
    return algorithm == LOCAL_VISION_ALGORITHM_NAME and revision == LOCAL_VISION_ANALYSIS_REVISION


@lru_cache(maxsize=2048)
def _analyze_camera_data_cached(data: str) -> dict[str, Any]:
    return analyze_camera_data_with_local_worker(data)


def estimate_camera_orchard_support(frame: dict[str, Any]) -> dict[str, Any]:
    return analyze_camera_frame_payload(frame)


def analyze_camera_image(image_bgr: Any) -> dict[str, Any]:
    try:
        import cv2  # type: ignore
    except Exception:
        return empty_camera_analysis("vision_ai_encode_unavailable", "OpenCV is required only to JPEG-encode frames before local AI inference.")
    if image_bgr is None:
        return empty_camera_analysis("no_image")
    height, width = image_bgr.shape[:2]
    if height <= 0 or width <= 0:
        return empty_camera_analysis("empty_image")
    return analyze_camera_image_with_local_worker(cv2, image_bgr)


def analyze_camera_image_with_local_worker(cv2: Any, image_bgr: Any) -> dict[str, Any]:
    try:
        ok, encoded = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
    except Exception:
        return empty_camera_analysis("vision_ai_encode_failed", "Failed to JPEG-encode camera frame for local AI inference.")
    if not ok:
        return empty_camera_analysis("vision_ai_encode_failed", "Failed to JPEG-encode camera frame for local AI inference.")
    image_data = base64.b64encode(encoded.tobytes()).decode("ascii")
    return analyze_camera_data_with_local_worker(image_data)


def analyze_camera_data_with_local_worker(data: str) -> dict[str, Any]:
    if not VISION_AI_URL:
        return empty_camera_analysis(
            "vision_ai_url_required",
            "ORCHARD_VISION_AI_URL is not configured. Camera perception is AI-only and will not use OpenCV fallback.",
        )
    payload = {
        "image": data,
        "schema": "dashboard_camera_overlay_v1",
        "prefer": "tree_trunk_instance_segmentation",
    }
    try:
        raw = json.dumps(payload).encode("utf-8")
        request = Request(
            VISION_AI_URL,
            data=raw,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=VISION_AI_TIMEOUT_S) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return empty_camera_analysis(
            "vision_ai_worker_unavailable",
            f"Local vision AI worker did not return a valid response: {exc}",
        )
    if not isinstance(result, dict) or result.get("algorithm") != LOCAL_VISION_ALGORITHM_NAME:
        return empty_camera_analysis(
            "vision_ai_invalid_response",
            "Local vision AI worker response used an unexpected schema or algorithm.",
        )
    if result.get("revision") != LOCAL_VISION_ANALYSIS_REVISION:
        return empty_camera_analysis(
            "vision_ai_invalid_response",
            "Local vision AI worker response used an unexpected analysis revision.",
        )
    result.setdefault("image", {})
    result.setdefault("detections", [])
    result.setdefault("ai_required", True)
    result["source"] = "local_vision_ai_worker"
    return result


def camera_tree_masks(cv2: Any, np: Any, image_bgr: Any) -> dict[str, Any]:
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    equalized = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[:, :, 0])
    edges = cv2.Canny(cv2.GaussianBlur(equalized, (5, 5), 0), 48, 145)

    green_mask = cv2.inRange(hsv, (35, 30, 38), (105, 255, 255))
    bark_brown = cv2.inRange(hsv, (0, 16, 18), (34, 190, 215))
    low_sat_dark = cv2.inRange(hsv, (0, 0, 0), (180, 92, 132))
    dark = cv2.inRange(gray, 0, 118)
    trunk_mask = cv2.bitwise_or(bark_brown, cv2.bitwise_or(low_sat_dark, dark))
    trunk_mask = cv2.bitwise_and(trunk_mask, cv2.bitwise_not(green_mask))
    trunk_mask = cv2.morphologyEx(trunk_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5)))
    trunk_mask = cv2.morphologyEx(trunk_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 19)))
    return {
        "gray": gray,
        "edges": edges,
        "trunk_mask": trunk_mask,
        "green_mask": green_mask,
        "scene": scene_green_stats(cv2, green_mask, width=image_bgr.shape[1], height=image_bgr.shape[0]),
    }


def scene_green_stats(cv2: Any, green_mask: Any, width: int, height: int) -> dict[str, Any]:
    image_area = max(1, width * height)
    upper = green_mask[: max(1, int(height * 0.62)), :]
    mid = green_mask[int(height * 0.18) : max(int(height * 0.18) + 1, int(height * 0.78)), :]
    lower = green_mask[int(height * 0.55) :, :]
    green_ratio = cv2.countNonZero(green_mask) / image_area
    upper_green_ratio = cv2.countNonZero(upper) / max(1, upper.size)
    mid_green_ratio = cv2.countNonZero(mid) / max(1, mid.size)
    lower_green_ratio = cv2.countNonZero(lower) / max(1, lower.size)
    orchard_scene = green_ratio >= 0.035 and (upper_green_ratio >= 0.022 or mid_green_ratio >= 0.03)
    return {
        "green_ratio": round(float(green_ratio), 4),
        "upper_green_ratio": round(float(upper_green_ratio), 4),
        "mid_green_ratio": round(float(mid_green_ratio), 4),
        "lower_green_ratio": round(float(lower_green_ratio), 4),
        "orchard_scene": bool(orchard_scene),
        "gate": "vegetation_context_v1",
    }


def contour_detections(cv2: Any, masks: dict[str, Any], width: int, height: int) -> list[dict[str, Any]]:
    trunk_mask = masks["trunk_mask"]
    green_mask = masks["green_mask"]
    scene = masks.get("scene") or {}
    contours, _ = cv2.findContours(trunk_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_area = max(1, width * height)
    min_area = max(80, int(image_area * 0.00055))
    rows: list[dict[str, Any]] = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        area = bw * bh
        bottom = y + bh
        if area < min_area or not valid_camera_bbox([x, y, bw, bh], width, height, allow_low_score=False):
            continue
        if bottom < height * 0.48:
            continue
        aspect = bh / max(1, bw)
        fill = cv2.contourArea(contour) / max(1, area)
        if aspect < 1.05 and bw > width * 0.12:
            continue
        if y > height * 0.55 and aspect < 2.4:
            continue
        if fill < 0.08:
            continue
        roi_trunk = trunk_mask[y : y + bh, x : x + bw]
        trunk_ratio = cv2.countNonZero(roi_trunk) / max(1, area)
        if trunk_ratio < 0.22:
            continue
        context = tree_context_metrics(cv2, green_mask, x, y, bw, bh, width, height)
        gate = camera_candidate_gate(scene, context, [x, y, bw, bh], width, height, candidate_type="contour")
        if not gate["allow"]:
            continue
        canopy_ratio = context["canopy_ratio"]
        score = (
            0.28
            + 0.2 * min(1.0, aspect / 4.0)
            + 0.18 * min(1.0, bh / max(1.0, height * 0.7))
            + 0.14 * min(1.0, trunk_ratio * 2.2)
            + 0.16 * min(1.0, float(gate["support"]) * 3.0)
            - float(gate["penalty"])
        )
        if bottom > height * 0.58:
            score += 0.05
        if score < 0.68:
            continue
        contour_points = simplify_contour(cv2, contour, width, height)
        rows.append(
            {
                "bbox": [int(x), int(y), int(bw), int(bh)],
                "confidence": round(max(0.05, min(0.97, score)), 3),
                "label": "trunk_mask_candidate",
                "render_shape": "contour",
                "contour": contour_points,
                "trunk_ratio": round(float(trunk_ratio), 3),
                "canopy_ratio": round(float(canopy_ratio), 3),
                "semantic_gate": gate["reason"],
                "canopy_support": round(float(gate["support"]), 3),
                "shape": {"aspect": round(float(aspect), 3), "fill": round(float(fill), 3)},
            }
        )
    rows.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return rows


def hough_line_detections(cv2: Any, np: Any, masks: dict[str, Any], width: int, height: int) -> list[dict[str, Any]]:
    gray = masks["gray"]
    edges = masks["edges"]
    trunk_mask = masks["trunk_mask"]
    green_mask = masks["green_mask"]
    scene = masks.get("scene") or {}
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=34,
        minLineLength=max(34, int(height * 0.12)),
        maxLineGap=10,
    )
    if lines is None:
        return []
    rows: list[dict[str, Any]] = []
    for x1, y1, x2, y2 in lines[:, 0, :]:
        dx = int(x2) - int(x1)
        dy = int(y2) - int(y1)
        length = (dx * dx + dy * dy) ** 0.5
        if length < max(34, height * 0.12):
            continue
        verticality = abs(dy) / max(length, 1.0)
        if verticality < 0.68:
            continue
        pad = max(4, int(width * 0.012))
        x = max(0, min(int(x1), int(x2)) - pad)
        y = max(0, min(int(y1), int(y2)) - pad)
        bw = min(width - x, abs(dx) + 2 * pad)
        bh = min(height - y, abs(dy) + 2 * pad)
        if not valid_camera_bbox([x, y, bw, bh], width, height, allow_low_score=True):
            continue
        bottom = y + bh
        if bottom < height * 0.48 and bh < height * 0.42:
            continue
        if y > height * 0.63 and bh < height * 0.29:
            continue
        if min(int(y1), int(y2)) > height * 0.55:
            continue
        if y > height * 0.46 and verticality < 0.82:
            continue
        if bw / max(1, bh) > 1.2 and verticality < 0.76:
            continue

        line_mask = np.zeros((height, width), dtype=np.uint8)
        cv2.line(line_mask, (int(x1), int(y1)), (int(x2), int(y2)), 255, max(5, int(width * 0.009)))
        roi_line = line_mask[y : y + bh, x : x + bw]
        line_pixels = max(1, cv2.countNonZero(roi_line))
        trunk_line_ratio = cv2.countNonZero(cv2.bitwise_and(trunk_mask[y : y + bh, x : x + bw], roi_line)) / line_pixels
        green_line_ratio = cv2.countNonZero(cv2.bitwise_and(green_mask[y : y + bh, x : x + bw], roi_line)) / line_pixels
        gray_line = gray[line_mask > 0]
        dark_score = max(0.0, 1.0 - float(np.median(gray_line)) / 170.0) if gray_line.size else 0.0
        context = tree_context_metrics(cv2, green_mask, x, y, bw, bh, width, height)
        gate = camera_candidate_gate(scene, context, [x, y, bw, bh], width, height, candidate_type="line")
        if not gate["allow"]:
            continue
        canopy_ratio = context["canopy_ratio"]
        if y > height * 0.62 and verticality < 0.78 and float(gate["support"]) < 0.1:
            continue
        if trunk_line_ratio < 0.26 and dark_score < 0.32:
            continue
        if green_line_ratio > 0.62 and dark_score < 0.38 and trunk_line_ratio < 0.36:
            continue
        score = (
            0.1
            + 0.24 * min(1.0, length / max(1.0, height * 0.72))
            + 0.18 * verticality
            + 0.18 * dark_score
            + 0.14 * min(1.0, trunk_line_ratio * 1.6)
            + 0.16 * min(1.0, float(gate["support"]) * 3.0)
            - float(gate["penalty"])
        )
        if bottom > height * 0.58:
            score += 0.05
        if score < 0.74:
            continue
        rows.append(
            {
                "bbox": [int(x), int(y), int(bw), int(bh)],
                "confidence": round(max(0.05, min(0.96, score)), 3),
                "label": "trunk_line_candidate",
                "render_shape": "line",
                "line": [int(x1), int(y1), int(x2), int(y2)],
                "line_width_px": max(6, int(width * 0.012)),
                "trunk_ratio": round(float(trunk_line_ratio), 3),
                "canopy_ratio": round(float(canopy_ratio), 3),
                "semantic_gate": gate["reason"],
                "canopy_support": round(float(gate["support"]), 3),
                "shape": {
                    "length_px": round(float(length), 1),
                    "verticality": round(float(verticality), 3),
                    "dark_score": round(float(dark_score), 3),
                },
            }
        )
    rows.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return rows


def simplify_contour(cv2: Any, contour: Any, width: int, height: int) -> list[list[int]]:
    perimeter = cv2.arcLength(contour, True)
    epsilon = max(1.2, perimeter * 0.012)
    approx = cv2.approxPolyDP(contour, epsilon, True)
    points = approx.reshape(-1, 2).tolist()
    if len(points) > 36:
        step = max(1, len(points) // 36)
        points = points[::step]
    return [
        [
            max(0, min(width - 1, int(x))),
            max(0, min(height - 1, int(y))),
        ]
        for x, y in points
    ]


def context_canopy_ratio(cv2: Any, green_mask: Any, x: int, y: int, bw: int, bh: int, width: int, height: int) -> float:
    gx1 = max(0, x - int(width * 0.04))
    gx2 = min(width, x + bw + int(width * 0.04))
    gy1 = max(0, y - int(height * 0.16))
    gy2 = min(height, y + bh + int(height * 0.07))
    roi = green_mask[gy1:gy2, gx1:gx2]
    return cv2.countNonZero(roi) / max(1, roi.size)


def tree_context_metrics(cv2: Any, green_mask: Any, x: int, y: int, bw: int, bh: int, width: int, height: int) -> dict[str, float]:
    canopy_ratio = context_canopy_ratio(cv2, green_mask, x, y, bw, bh, width, height)
    pad_x = int(width * 0.055)
    above_x1 = max(0, x - pad_x)
    above_x2 = min(width, x + bw + pad_x)
    above_y1 = max(0, y - int(height * 0.24))
    above_y2 = max(above_y1 + 1, min(height, y + max(1, int(bh * 0.2))))
    above = green_mask[above_y1:above_y2, above_x1:above_x2]
    above_ratio = cv2.countNonZero(above) / max(1, above.size)
    side_x1 = max(0, x - int(width * 0.075))
    side_x2 = min(width, x + bw + int(width * 0.075))
    side_y1 = max(0, y + int(bh * 0.08))
    side_y2 = min(height, y + bh + int(height * 0.06))
    side = green_mask[side_y1:max(side_y1 + 1, side_y2), side_x1:side_x2]
    side_ratio = cv2.countNonZero(side) / max(1, side.size)
    return {
        "canopy_ratio": float(canopy_ratio),
        "above_ratio": float(above_ratio),
        "side_ratio": float(side_ratio),
        "support": max(float(canopy_ratio), float(above_ratio) * 1.15, float(side_ratio) * 0.75),
    }


def camera_candidate_gate(
    scene: dict[str, Any],
    context: dict[str, float],
    bbox: list[int],
    width: int,
    height: int,
    candidate_type: str = "contour",
) -> dict[str, Any]:
    x, y, bw, bh = [int(value) for value in bbox[:4]]
    green_ratio = float(scene.get("green_ratio") or 0.0)
    upper_green_ratio = float(scene.get("upper_green_ratio") or 0.0)
    support = float(context.get("support") or 0.0)
    edge_contact = x <= width * 0.018 or x + bw >= width * 0.982
    very_low_canopy = support < 0.055
    penalty = 0.0
    if green_ratio < 0.035 or upper_green_ratio < 0.018:
        return {"allow": False, "support": support, "penalty": 0.0, "reason": "scene_not_orchard"}
    min_support = 0.082 if green_ratio < 0.12 else 0.064
    if candidate_type == "line":
        min_support += 0.04
        if float(context.get("above_ratio") or 0.0) < 0.06 and float(context.get("canopy_ratio") or 0.0) < 0.09:
            return {"allow": False, "support": support, "penalty": 0.0, "reason": "line_without_canopy_above"}
    if support < min_support:
        return {"allow": False, "support": support, "penalty": 0.0, "reason": "no_local_canopy_context"}
    if edge_contact and support < 0.12:
        return {"allow": False, "support": support, "penalty": 0.0, "reason": "edge_without_canopy_context"}
    if edge_contact:
        penalty += 0.1
    if very_low_canopy:
        penalty += 0.08
    if y > height * 0.6 and support < 0.1:
        penalty += 0.08
    return {"allow": True, "support": support, "penalty": penalty, "reason": "canopy_context_supported"}


def valid_camera_bbox(bbox: list[int], width: int, height: int, allow_low_score: bool = False) -> bool:
    x, y, bw, bh = [int(value) for value in bbox[:4]]
    image_area = max(1, width * height)
    area = bw * bh
    if bw < 4 or bh < max(12, int(height * 0.08)):
        return False
    if area > image_area * (0.105 if allow_low_score else 0.12):
        return False
    if area < image_area * 0.00025:
        return False
    if bw > width * (0.34 if allow_low_score else 0.42):
        return False
    if y + bh < height * 0.42:
        return False
    if y > height * 0.68 and bh < height * 0.28:
        return False
    if x <= 1 and bw > width * 0.26 and bh > height * 0.65:
        return False
    return True


def merge_camera_detections(detections: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for item in detections:
        bbox = item.get("bbox") or [0, 0, 0, 0]
        if not valid_camera_bbox([int(value) for value in bbox[:4]], width, height, allow_low_score=True):
            continue
        found = None
        for existing in merged:
            union = union_bbox(existing.get("bbox") or [0, 0, 0, 0], bbox)
            if not valid_camera_bbox(union, width, height, allow_low_score=True):
                continue
            if bbox_iou(bbox, existing.get("bbox") or [0, 0, 0, 0]) >= 0.22 or bbox_center_distance_ratio(bbox, existing.get("bbox") or [0, 0, 0, 0], width, height) < 0.04:
                found = existing
                break
        if not found:
            merged.append(dict(item))
            continue
        item_confidence = float(item.get("confidence") or 0.0)
        found_confidence = float(found.get("confidence") or 0.0)
        found["bbox"] = union_bbox(found["bbox"], bbox)
        found["confidence"] = round(max(found_confidence, item_confidence), 3)
        found["trunk_ratio"] = round(max(float(found.get("trunk_ratio") or 0.0), float(item.get("trunk_ratio") or 0.0)), 3)
        found["canopy_ratio"] = round(max(float(found.get("canopy_ratio") or 0.0), float(item.get("canopy_ratio") or 0.0)), 3)
        if item_confidence > found_confidence:
            for key in ("render_shape", "contour", "line", "line_width_px", "shape"):
                if key in item:
                    found[key] = copy.deepcopy(item[key])
        if item.get("label") == "trunk_mask_candidate":
            found["label"] = "trunk_mask_candidate"
    filtered = [
        item for item in merged
        if float(item.get("confidence") or 0.0) >= 0.7 and valid_camera_bbox(item.get("bbox") or [0, 0, 0, 0], width, height, allow_low_score=True)
    ]
    filtered.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return filtered


def bbox_iou(a: list[int], b: list[int]) -> float:
    ax, ay, aw, ah = [float(v) for v in a[:4]]
    bx, by, bw, bh = [float(v) for v in b[:4]]
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return 0.0 if union <= 0 else inter / union


def bbox_center_distance_ratio(a: list[int], b: list[int], width: int, height: int) -> float:
    ax, ay, aw, ah = [float(v) for v in a[:4]]
    bx, by, bw, bh = [float(v) for v in b[:4]]
    distance = ((ax + aw / 2 - bx - bw / 2) ** 2 + (ay + ah / 2 - by - bh / 2) ** 2) ** 0.5
    diagonal = max(1.0, (width * width + height * height) ** 0.5)
    return distance / diagonal


def union_bbox(a: list[int], b: list[int]) -> list[int]:
    ax, ay, aw, ah = [int(v) for v in a[:4]]
    bx, by, bw, bh = [int(v) for v in b[:4]]
    x1 = min(ax, bx)
    y1 = min(ay, by)
    x2 = max(ax + aw, bx + bw)
    y2 = max(ay + ah, by + bh)
    return [x1, y1, x2 - x1, y2 - y1]


class CameraOverlayStabilizer:
    def __init__(self, max_age_s: float = 0.9) -> None:
        self.max_age_s = max_age_s
        self.tracks: list[dict[str, Any]] = []
        self.next_id = 1

    def update(self, overlay: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        if not overlay.get("ok"):
            return overlay
        now = 0.0 if now is None else float(now)
        detections = [copy.deepcopy(item) for item in overlay.get("detections") or []]
        matched: set[int] = set()
        rows: list[dict[str, Any]] = []
        for detection in sorted(detections, key=lambda item: float(item.get("confidence") or 0.0), reverse=True):
            bbox = detection.get("bbox") or [0, 0, 0, 0]
            best_index = None
            best_score = 0.0
            for index, track in enumerate(self.tracks):
                if index in matched:
                    continue
                last_seen = float(track.get("last_seen_s", now))
                age = now - last_seen
                if age > self.max_age_s:
                    continue
                track_bbox = track.get("bbox") or [0, 0, 0, 0]
                iou = bbox_iou(bbox, track_bbox)
                distance = bbox_center_distance_ratio(bbox, track_bbox, overlay.get("image", {}).get("width", 640), overlay.get("image", {}).get("height", 360))
                score = iou + max(0.0, 0.18 - distance) * 1.8
                if score > best_score:
                    best_index = index
                    best_score = score
            if best_index is None or best_score < 0.16:
                track = {
                    "id": f"cam-tree-{self.next_id}",
                    "bbox": bbox,
                    "confidence": float(detection.get("confidence") or 0.0),
                    "hits": 1,
                    "last_seen_s": now,
                }
                self.next_id += 1
                self.tracks.append(track)
            else:
                track = self.tracks[best_index]
                matched.add(best_index)
                previous_bbox = track.get("bbox") or bbox
                smooth_bbox = smooth_bbox_values(previous_bbox, bbox, alpha=0.42)
                detection = transform_detection_geometry(detection, bbox, smooth_bbox)
                track["bbox"] = smooth_bbox
                track["confidence"] = round(float(track.get("confidence") or 0.0) * 0.55 + float(detection.get("confidence") or 0.0) * 0.45, 3)
                track["hits"] = int(track.get("hits") or 1) + 1
                track["last_seen_s"] = now
            detection["bbox"] = [int(v) for v in track.get("bbox", bbox)]
            detection["confidence"] = round(max(float(detection.get("confidence") or 0.0), float(track.get("confidence") or 0.0)), 3)
            detection["track_id"] = track["id"]
            detection["stable_hits"] = int(track.get("hits") or 1)
            detection["stable"] = int(track.get("hits") or 1) >= 2
            if detection["stable"] or detection["confidence"] >= 0.2:
                rows.append(detection)
        self.tracks = [
            track for track in self.tracks
            if now - float(track.get("last_seen_s", now)) <= self.max_age_s
        ][-24:]
        output = copy.deepcopy(overlay)
        output["detections"] = rows[:8]
        output["stabilized"] = True
        output["stabilizer"] = "iou_bbox_exponential_smoothing_v1"
        output["confidence"] = max((float(item.get("confidence") or 0.0) for item in rows), default=0.0)
        return output


def smooth_bbox_values(previous: list[int], current: list[int], alpha: float) -> list[int]:
    return [
        int(round(float(previous[index]) * (1.0 - alpha) + float(current[index]) * alpha))
        for index in range(4)
    ]


def transform_detection_geometry(detection: dict[str, Any], old_bbox: list[int], new_bbox: list[int]) -> dict[str, Any]:
    output = copy.deepcopy(detection)
    if output.get("contour"):
        output["contour"] = transform_points(output["contour"], old_bbox, new_bbox)
    if output.get("line"):
        points = transform_points([[output["line"][0], output["line"][1]], [output["line"][2], output["line"][3]]], old_bbox, new_bbox)
        output["line"] = [points[0][0], points[0][1], points[1][0], points[1][1]]
    return output


def transform_points(points: list[list[int]], old_bbox: list[int], new_bbox: list[int]) -> list[list[int]]:
    ox, oy, ow, oh = [float(value) for value in old_bbox[:4]]
    nx, ny, nw, nh = [float(value) for value in new_bbox[:4]]
    rows = []
    for px, py in points:
        rx = 0.0 if ow <= 0 else (float(px) - ox) / ow
        ry = 0.0 if oh <= 0 else (float(py) - oy) / oh
        rows.append([int(round(nx + rx * nw)), int(round(ny + ry * nh))])
    return rows
