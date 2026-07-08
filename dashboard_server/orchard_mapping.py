from __future__ import annotations

import base64
import math
from statistics import median
from typing import Any

from dashboard_server.lidar_analysis import ALGORITHM_NAME, LIDAR_COORDINATE_FRAME, detect_orchard_trees


MAP_ALGORITHM_NAME = "orchard_lidar_camera_fusion_pseudo_odometry_v1"


def build_orchard_map(timeline: dict[str, Any]) -> dict[str, Any]:
    lidar_frames = timeline.get("lidar", {}).get("frames", []) or []
    camera_frames = timeline.get("camera", {}).get("frames", []) or []
    camera_index = camera_observation_index(camera_frames)
    pose = {"x_m": 0.0, "y_m": 0.0}
    path = [{"t_s": 0.0, "x_m": 0.0, "y_m": 0.0}]
    previous_local: list[dict[str, Any]] = []
    global_hits: list[dict[str, Any]] = []
    frame_summaries: list[dict[str, Any]] = []

    for frame in lidar_frames:
        t_s = float(frame.get("t_s") or 0.0)
        points = frame.get("points") or []
        orchard = detect_orchard_trees(points)
        local_trees = orchard.get("trees") or []
        delta = estimate_sensor_delta(previous_local, local_trees)
        pose = {"x_m": pose["x_m"] + delta["x_m"], "y_m": pose["y_m"] + delta["y_m"]}
        path.append({"t_s": round(t_s, 3), "x_m": round(pose["x_m"], 3), "y_m": round(pose["y_m"], 3)})
        camera_support = nearest_camera_support(camera_index, t_s)
        for tree in local_trees:
            global_tree = local_to_global_tree(tree, pose, t_s, camera_support)
            global_hits.append(global_tree)
        frame_summaries.append(
            {
                "t_s": round(t_s, 3),
                "pose": {"x_m": round(pose["x_m"], 3), "y_m": round(pose["y_m"], 3)},
                "local_tree_count": len(local_trees),
                "excluded_rear_candidates": orchard.get("excluded_rear_candidates", 0),
                "camera_support": camera_support,
            }
        )
        previous_local = local_trees

    trees = merge_global_trees(global_hits)
    bounds = map_bounds(path, trees)
    return {
        "ok": True,
        "algorithm": MAP_ALGORITHM_NAME,
        "lidar_algorithm": ALGORITHM_NAME,
        "coordinate_frame": LIDAR_COORDINATE_FRAME,
        "duration_s": timeline.get("duration_s"),
        "tree_count": len(trees),
        "trees": trees,
        "path": path,
        "start": path[0] if path else None,
        "end": path[-1] if path else None,
        "bounds": bounds,
        "frames": frame_summaries,
        "camera": {
            "frames_analyzed": len(camera_index),
            "method": camera_index[0]["method"] if camera_index else "unavailable",
        },
        "fusion": {
            "method": "LiDAR tree candidates + nearest camera vegetation/trunk support; pseudo-odometry from inter-frame median candidate displacement",
            "note": "Use wheel odometry or visual/LiDAR odometry when available to replace pseudo-odometry.",
        },
    }


def estimate_sensor_delta(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> dict[str, float]:
    if not previous or not current:
        return {"x_m": 0.0, "y_m": 0.0}
    dxs = []
    dys = []
    for now in current:
        cx = float(now["center"]["x_m"])
        cy = float(now["center"]["y_m"])
        nearest = None
        nearest_dist = 0.95
        for old in previous:
            ox = float(old["center"]["x_m"])
            oy = float(old["center"]["y_m"])
            dist = math.hypot(cx - ox, cy - oy)
            if dist < nearest_dist:
                nearest = old
                nearest_dist = dist
        if nearest:
            dxs.append(float(nearest["center"]["x_m"]) - cx)
            dys.append(float(nearest["center"]["y_m"]) - cy)
    if len(dxs) < 2:
        return {"x_m": 0.0, "y_m": 0.0}
    dx = max(-0.7, min(0.7, median(dxs)))
    dy = max(-0.7, min(0.7, median(dys)))
    if math.hypot(dx, dy) > 0.9:
        return {"x_m": 0.0, "y_m": 0.0}
    return {"x_m": dx, "y_m": dy}


def local_to_global_tree(tree: dict[str, Any], pose: dict[str, float], t_s: float, camera_support: dict[str, Any] | None) -> dict[str, Any]:
    local = tree["center"]
    camera_score = float((camera_support or {}).get("confidence") or 0.0)
    lidar_score = float(tree.get("confidence") or 0.0)
    front_visible = float(local.get("y_m") or 0.0) > 0.3 and abs(math.atan2(float(local.get("x_m") or 0.0), max(0.1, float(local.get("y_m") or 0.0)))) < math.radians(42)
    fusion_score = min(0.99, lidar_score * 0.72 + (camera_score if front_visible else 0.0) * 0.28)
    return {
        "x_m": round(float(pose["x_m"]) + float(local["x_m"]), 3),
        "y_m": round(float(pose["y_m"]) + float(local["y_m"]), 3),
        "radius_m": tree.get("radius_m", 0.2),
        "confidence": round(fusion_score, 3),
        "lidar_confidence": round(lidar_score, 3),
        "camera_confidence": round(camera_score if front_visible else 0.0, 3),
        "first_seen_s": round(t_s, 3),
        "last_seen_s": round(t_s, 3),
        "hits": 1,
    }


def merge_global_trees(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trees: list[dict[str, Any]] = []
    for hit in sorted(hits, key=lambda item: item["first_seen_s"]):
        found = None
        for tree in trees:
            gate = max(0.45, min(0.95, 0.35 + 0.04 * max(abs(hit["x_m"]), abs(hit["y_m"]))))
            if math.hypot(hit["x_m"] - tree["x_m"], hit["y_m"] - tree["y_m"]) <= gate:
                found = tree
                break
        if not found:
            trees.append(dict(hit))
            continue
        hits_count = int(found.get("hits") or 1) + 1
        alpha = 1.0 / hits_count
        found["x_m"] = round(found["x_m"] * (1.0 - alpha) + hit["x_m"] * alpha, 3)
        found["y_m"] = round(found["y_m"] * (1.0 - alpha) + hit["y_m"] * alpha, 3)
        found["radius_m"] = round(max(float(found.get("radius_m") or 0.1), float(hit.get("radius_m") or 0.1)), 3)
        found["confidence"] = round(max(float(found.get("confidence") or 0.0), float(hit.get("confidence") or 0.0)), 3)
        found["lidar_confidence"] = round(max(float(found.get("lidar_confidence") or 0.0), float(hit.get("lidar_confidence") or 0.0)), 3)
        found["camera_confidence"] = round(max(float(found.get("camera_confidence") or 0.0), float(hit.get("camera_confidence") or 0.0)), 3)
        found["last_seen_s"] = max(float(found.get("last_seen_s") or 0.0), float(hit.get("last_seen_s") or 0.0))
        found["hits"] = hits_count
    return [tree for tree in trees if int(tree.get("hits") or 0) >= 2 or float(tree.get("confidence") or 0.0) >= 0.68]


def camera_observation_index(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for frame in frames:
        rows.append({"t_s": float(frame.get("t_s") or 0.0), **estimate_camera_orchard_support(frame)})
    return rows


def estimate_camera_orchard_support(frame: dict[str, Any]) -> dict[str, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return {"confidence": 0.0, "detections": [], "method": "opencv_unavailable"}
    data = frame.get("data")
    if not data:
        return {"confidence": 0.0, "detections": [], "method": "no_frame_data"}
    try:
        raw = base64.b64decode(data)
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return {"confidence": 0.0, "detections": [], "method": "decode_error"}
    if image is None:
        return {"confidence": 0.0, "detections": [], "method": "decode_error"}
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (35, 35, 35), (95, 255, 255))
    brown = cv2.inRange(hsv, (5, 35, 25), (32, 220, 190))
    mask = cv2.morphologyEx(cv2.bitwise_or(green, brown), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    detections = []
    h, w = image.shape[:2]
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        area = bw * bh
        if area < max(80, 0.0008 * w * h):
            continue
        vertical = bh / max(1, bw)
        score = min(0.95, 0.25 + min(0.45, area / max(1, w * h) * 8.0) + min(0.25, vertical / 8.0))
        detections.append({"bbox": [x, y, bw, bh], "confidence": round(score, 3), "label": "tree_or_canopy"})
    confidence = max((item["confidence"] for item in detections), default=0.0)
    return {"confidence": round(confidence, 3), "detections": detections[:12], "method": "opencv_hsv_vertical_blob_v1"}


def nearest_camera_support(index: list[dict[str, Any]], t_s: float) -> dict[str, Any] | None:
    if not index:
        return None
    return min(index, key=lambda item: abs(float(item.get("t_s") or 0.0) - t_s))


def map_bounds(path: list[dict[str, Any]], trees: list[dict[str, Any]]) -> dict[str, float]:
    xs = [float(item["x_m"]) for item in path] + [float(item["x_m"]) for item in trees]
    ys = [float(item["y_m"]) for item in path] + [float(item["y_m"]) for item in trees]
    if not xs or not ys:
        return {"min_x": -5.0, "max_x": 5.0, "min_y": -5.0, "max_y": 5.0}
    pad = 2.0
    return {"min_x": round(min(xs) - pad, 3), "max_x": round(max(xs) + pad, 3), "min_y": round(min(ys) - pad, 3), "max_y": round(max(ys) + pad, 3)}
