from __future__ import annotations

import base64
import math
from typing import Any

from dashboard_server.lidar_analysis import ALGORITHM_NAME, LIDAR_COORDINATE_FRAME, detect_orchard_trees, is_rear_person_zone


MAP_ALGORITHM_NAME = "orchard_lidar_camera_scanmatch_fusion_v3"
SCAN_MATCH_GRID_M = 0.28
SCAN_MATCH_FINE_GRID_M = 0.2


def build_orchard_map(timeline: dict[str, Any]) -> dict[str, Any]:
    lidar_frames = timeline.get("lidar", {}).get("frames", []) or []
    camera_frames = timeline.get("camera", {}).get("frames", []) or []
    camera_index = camera_observation_index(camera_frames)
    pose = {"x_m": 0.0, "y_m": 0.0, "theta_rad": 0.0}
    path = [{"t_s": 0.0, "x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0, "alignment_score": 1.0}]
    previous_points: list[dict[str, Any]] = []
    global_hits: list[dict[str, Any]] = []
    frame_summaries: list[dict[str, Any]] = []

    for frame in lidar_frames:
        t_s = float(frame.get("t_s") or 0.0)
        points = frame.get("points") or []
        if previous_points:
            transform = estimate_scan_transform(previous_points, points)
            pose = compose_pose(pose, transform)
        else:
            transform = {"dx_m": 0.0, "dy_m": 0.0, "dtheta_rad": 0.0, "score": 1.0, "method": "initial_frame"}
        orchard = detect_orchard_trees(points)
        local_trees = select_map_tree_candidates(orchard.get("trees") or [])
        path.append(
            {
                "t_s": round(t_s, 3),
                "x_m": round(pose["x_m"], 3),
                "y_m": round(pose["y_m"], 3),
                "heading_deg": round(math.degrees(pose["theta_rad"]), 2),
                "alignment_score": round(float(transform.get("score") or 0.0), 3),
            }
        )
        camera_support = nearest_camera_support(camera_index, t_s)
        for tree in local_trees:
            global_tree = local_to_global_tree(tree, pose, t_s, camera_support)
            global_hits.append(global_tree)
        frame_summaries.append(
            {
                "t_s": round(t_s, 3),
                "pose": {
                    "x_m": round(pose["x_m"], 3),
                    "y_m": round(pose["y_m"], 3),
                    "heading_deg": round(math.degrees(pose["theta_rad"]), 2),
                },
                "scan_match": transform,
                "local_tree_count": len(local_trees),
                "excluded_rear_candidates": orchard.get("excluded_rear_candidates", 0),
                "camera_support": camera_support,
            }
        )
        previous_points = points

    trees = merge_global_trees(global_hits, frame_count=len(lidar_frames))
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
            "method": "LiDAR trunk-like candidates + nearest camera trunk/canopy support; pseudo-odometry from 2D scan matching",
            "note": "Use wheel odometry or visual/LiDAR odometry when available to replace pseudo-odometry.",
        },
    }


def select_map_tree_candidates(trees: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    for tree in trees:
        center = tree.get("center") or {}
        x = float(center.get("x_m") or 0.0)
        y = float(center.get("y_m") or 0.0)
        distance = math.hypot(x, y)
        confidence = float(tree.get("confidence") or 0.0)
        if distance < 1.05 or distance > 11.5:
            continue
        if is_rear_person_zone(center):
            continue
        if confidence < 0.62:
            continue
        if tree.get("kind") != "trunk_like" and confidence < 0.78:
            continue
        if int(tree.get("points") or 0) < 5 and distance > 7.5:
            continue
        selected.append(tree)
    return selected[:18]


def estimate_scan_transform(previous_points: list[dict[str, Any]], current_points: list[dict[str, Any]]) -> dict[str, Any]:
    previous = scan_match_points(previous_points)
    current = scan_match_points(current_points)
    if len(previous) < 80 or len(current) < 80:
        return {"dx_m": 0.0, "dy_m": 0.0, "dtheta_rad": 0.0, "score": 0.0, "method": "insufficient_points"}
    best = search_scan_transform(previous, current, 0.0, 0.0, 0.0, SCAN_MATCH_GRID_M, 0.24, math.radians(2.5), 0.72, math.radians(2.5))
    best = search_scan_transform(previous, current, best["dx_m"], best["dy_m"], best["dtheta_rad"], SCAN_MATCH_FINE_GRID_M, 0.08, math.radians(0.5), 0.16, math.radians(0.5))
    if float(best["score"]) < 0.28:
        return {**best, "dx_m": 0.0, "dy_m": 0.0, "dtheta_rad": 0.0, "method": "scan_match_low_confidence"}
    return {**best, "method": "occupancy_scan_match"}


def scan_match_points(points: list[dict[str, Any]]) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    for point in points:
        try:
            x = float(point["x_m"])
            y = float(point["y_m"])
        except (KeyError, TypeError, ValueError):
            continue
        distance = math.hypot(x, y)
        if distance < 0.9 or distance > 12.0:
            continue
        if y < -0.3 and abs(x) < 4.8 and distance < 8.5:
            continue
        rows.append((x, y))
    if len(rows) > 620:
        step = max(1, len(rows) // 620)
        rows = rows[::step]
    return rows


def search_scan_transform(
    previous: list[tuple[float, float]],
    current: list[tuple[float, float]],
    center_dx: float,
    center_dy: float,
    center_theta: float,
    resolution: float,
    step_m: float,
    step_theta: float,
    span_m: float,
    span_theta: float,
) -> dict[str, float]:
    previous_grid = occupancy_grid(previous, resolution)
    best = {"dx_m": center_dx, "dy_m": center_dy, "dtheta_rad": center_theta, "score": -1.0}
    dx_steps = range(-int(round(span_m / step_m)), int(round(span_m / step_m)) + 1)
    theta_steps = range(-int(round(span_theta / step_theta)), int(round(span_theta / step_theta)) + 1)
    for ti in theta_steps:
        theta = center_theta + ti * step_theta
        if abs(theta) > math.radians(4.0):
            continue
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        rotated = [(cos_t * x - sin_t * y, sin_t * x + cos_t * y) for x, y in current]
        for xi in dx_steps:
            dx = center_dx + xi * step_m
            if abs(dx) > 1.0:
                continue
            for yi in dx_steps:
                dy = center_dy + yi * step_m
                if abs(dy) > 1.0:
                    continue
                score = occupancy_score(previous_grid, rotated, dx, dy, resolution)
                if score > best["score"]:
                    best = {"dx_m": dx, "dy_m": dy, "dtheta_rad": theta, "score": score}
    return {
        "dx_m": round(best["dx_m"], 3),
        "dy_m": round(best["dy_m"], 3),
        "dtheta_rad": round(best["dtheta_rad"], 5),
        "score": round(max(0.0, best["score"]), 3),
    }


def occupancy_grid(points: list[tuple[float, float]], resolution: float) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for x, y in points:
        ix = round(x / resolution)
        iy = round(y / resolution)
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                cells.add((ix + ox, iy + oy))
    return cells


def occupancy_score(previous_grid: set[tuple[int, int]], rotated_current: list[tuple[float, float]], dx: float, dy: float, resolution: float) -> float:
    if not rotated_current:
        return 0.0
    hits = 0
    for x, y in rotated_current:
        if (round((x + dx) / resolution), round((y + dy) / resolution)) in previous_grid:
            hits += 1
    return hits / len(rotated_current)


def compose_pose(pose: dict[str, float], transform: dict[str, Any]) -> dict[str, float]:
    dx = float(transform.get("dx_m") or 0.0)
    dy = float(transform.get("dy_m") or 0.0)
    dtheta = float(transform.get("dtheta_rad") or 0.0)
    heading = float(pose.get("theta_rad") or 0.0)
    cos_h = math.cos(heading)
    sin_h = math.sin(heading)
    next_heading = ((heading + dtheta + math.pi) % (math.tau)) - math.pi
    return {
        "x_m": float(pose.get("x_m") or 0.0) + cos_h * dx - sin_h * dy,
        "y_m": float(pose.get("y_m") or 0.0) + sin_h * dx + cos_h * dy,
        "theta_rad": next_heading,
    }


def local_to_global_tree(tree: dict[str, Any], pose: dict[str, float], t_s: float, camera_support: dict[str, Any] | None) -> dict[str, Any]:
    local = tree["center"]
    camera_score = float((camera_support or {}).get("confidence") or 0.0)
    lidar_score = float(tree.get("confidence") or 0.0)
    front_visible = float(local.get("y_m") or 0.0) > 0.3 and abs(math.atan2(float(local.get("x_m") or 0.0), max(0.1, float(local.get("y_m") or 0.0)))) < math.radians(42)
    fusion_score = min(0.99, lidar_score * 0.72 + (camera_score if front_visible else 0.0) * 0.28)
    gx, gy = transform_local_point(float(local["x_m"]), float(local["y_m"]), pose)
    return {
        "x_m": round(gx, 3),
        "y_m": round(gy, 3),
        "radius_m": tree.get("radius_m", 0.2),
        "confidence": round(fusion_score, 3),
        "lidar_confidence": round(lidar_score, 3),
        "camera_confidence": round(camera_score if front_visible else 0.0, 3),
        "kind": tree.get("kind", "trunk_like"),
        "first_seen_s": round(t_s, 3),
        "last_seen_s": round(t_s, 3),
        "hits": 1,
    }


def transform_local_point(x: float, y: float, pose: dict[str, float]) -> tuple[float, float]:
    heading = float(pose.get("theta_rad") or 0.0)
    cos_h = math.cos(heading)
    sin_h = math.sin(heading)
    return cos_h * x - sin_h * y + float(pose.get("x_m") or 0.0), sin_h * x + cos_h * y + float(pose.get("y_m") or 0.0)


def merge_global_trees(hits: list[dict[str, Any]], frame_count: int | None = None) -> list[dict[str, Any]]:
    trees: list[dict[str, Any]] = []
    for hit in sorted(hits, key=lambda item: item["first_seen_s"]):
        found = None
        for tree in trees:
            gate = max(0.48, min(1.05, 0.32 + float(hit.get("radius_m") or 0.2) + 0.018 * max(abs(hit["x_m"]), abs(hit["y_m"]))))
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
    filtered = []
    for tree in trees:
        hits = int(tree.get("hits") or 0)
        time_span = float(tree.get("last_seen_s") or 0.0) - float(tree.get("first_seen_s") or 0.0)
        confidence = float(tree.get("confidence") or 0.0)
        camera_confidence = float(tree.get("camera_confidence") or 0.0)
        if hits >= 8 and time_span >= 2.0:
            filtered.append(tree)
        elif hits >= 5 and confidence >= 0.74:
            filtered.append(tree)
        elif (frame_count or 0) <= 8 and hits >= 3 and time_span >= 0.8 and confidence >= 0.62:
            filtered.append(tree)
        elif hits >= 3 and camera_confidence >= 0.62 and confidence >= 0.78:
            filtered.append(tree)
    filtered.sort(key=lambda item: (-(int(item.get("hits") or 0)), -float(item.get("confidence") or 0.0)))
    return filtered[:80]


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
    green = cv2.inRange(hsv, (35, 30, 30), (95, 255, 255))
    brown = cv2.inRange(hsv, (5, 28, 20), (34, 230, 210))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark_vertical = cv2.inRange(gray, 18, 125)
    mask = cv2.morphologyEx(cv2.bitwise_or(cv2.bitwise_or(green, brown), dark_vertical), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    detections = []
    h, w = image.shape[:2]
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        area = bw * bh
        if area < max(90, 0.0007 * w * h):
            continue
        vertical = bh / max(1, bw)
        center_bias = 1.0 - min(1.0, abs((x + bw / 2) - w / 2) / (w / 2))
        lower_half = 1.0 if y + bh > h * 0.38 else 0.55
        score = min(0.95, 0.18 + min(0.36, area / max(1, w * h) * 7.0) + min(0.26, vertical / 9.0) + 0.1 * center_bias + 0.05 * lower_half)
        label = "trunk_or_tree" if vertical >= 1.35 and bh > h * 0.16 else "canopy_or_branch"
        detections.append({"bbox": [x, y, bw, bh], "confidence": round(score, 3), "label": label})
    confidence = max((item["confidence"] for item in detections), default=0.0)
    detections.sort(key=lambda item: float(item["confidence"]), reverse=True)
    return {"confidence": round(confidence, 3), "detections": detections[:12], "method": "opencv_hsv_dark_vertical_tree_support_v2"}


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
