from __future__ import annotations

import math
from typing import Any

from dashboard_server.camera_analysis import estimate_camera_orchard_support
from dashboard_server.lidar_analysis import ALGORITHM_NAME, LIDAR_COORDINATE_FRAME, detect_orchard_trees, is_rear_person_zone
from dashboard_server.lidar_odometry import ODOMETRY_ALGORITHM, estimate_relative_transform


MAP_ALGORITHM_NAME = "orchard_three_layer_weighted_centerline_v8"
CAMERA_FOV_X_RAD = math.radians(78.0)
CAMERA_ASSUMED_VISIBLE_TRUNK_HEIGHT_M = 1.35
LIDAR_TREE_WEIGHT = 0.64
CAMERA_TREE_WEIGHT = 0.36


def build_orchard_map(timeline: dict[str, Any]) -> dict[str, Any]:
    lidar_frames = timeline.get("lidar", {}).get("frames", []) or []
    camera_frames = timeline.get("camera", {}).get("frames", []) or []
    camera_index = camera_observation_index(camera_frames)
    pose = {"x_m": 0.0, "y_m": 0.0, "theta_rad": 0.0, "theta_total_rad": 0.0}
    path: list[dict[str, Any]] = []
    previous_points: list[dict[str, Any]] = []
    previous_t_s: float | None = None
    motion_prior: dict[str, Any] | None = None
    lidar_hits: list[dict[str, Any]] = []
    frame_summaries: list[dict[str, Any]] = []

    for frame in lidar_frames:
        t_s = float(frame.get("t_s") or 0.0)
        points = frame.get("points") or []
        if previous_points:
            dt_s = max(0.05, t_s - float(previous_t_s if previous_t_s is not None else t_s - 0.6))
            transform = estimate_scan_transform(previous_points, points, prior=motion_prior, dt_s=dt_s)
            if transform.get("accepted"):
                pose = compose_pose(pose, transform)
                motion_prior = transform
        else:
            transform = {
                "dx_m": 0.0,
                "dy_m": 0.0,
                "dtheta_rad": 0.0,
                "score": 1.0,
                "pose_confidence": 1.0,
                "accepted": True,
                "turn_event": False,
                "method": "initial_frame",
            }
        orchard = detect_orchard_trees(points)
        local_trees = select_map_tree_candidates(orchard.get("trees") or [])
        path.append(
            {
                "t_s": round(t_s, 3),
                "x_m": round(pose["x_m"], 3),
                "y_m": round(pose["y_m"], 3),
                "heading_deg": round(math.degrees(pose["theta_rad"]), 2),
                "heading_unwrapped_deg": round(math.degrees(pose.get("theta_total_rad", pose["theta_rad"])), 2),
                "alignment_score": round(float(transform.get("score") or 0.0), 3),
                "pose_confidence": round(float(transform.get("pose_confidence") or 0.0), 3),
                "turn_event": bool(transform.get("turn_event")),
            }
        )
        camera_support = nearest_camera_support(camera_index, t_s)
        for tree in local_trees:
            lidar_hits.append(local_to_global_tree(tree, pose, t_s, camera_support=None))
        frame_summaries.append(
            {
                "t_s": round(t_s, 3),
                "pose": {
                    "x_m": round(pose["x_m"], 3),
                    "y_m": round(pose["y_m"], 3),
                    "heading_deg": round(math.degrees(pose["theta_rad"]), 2),
                    "heading_unwrapped_deg": round(math.degrees(pose.get("theta_total_rad", pose["theta_rad"])), 2),
                },
                "scan_match": transform,
                "local_tree_count": len(local_trees),
                "excluded_rear_candidates": orchard.get("excluded_rear_candidates", 0),
                "lidar_ood": {
                    "raw_candidates": orchard.get("raw_candidate_count", 0),
                    "wall_suppressed": orchard.get("wall_suppressed_candidates", 0),
                    "linear_context_suppressed": orchard.get("linear_context_suppressed_candidates", 0),
                    "rear_operator_suppressed": orchard.get("excluded_rear_candidates", 0),
                    "rejection_counts": orchard.get("rejection_counts", {}),
                },
                "camera_support": camera_support,
            }
        )
        previous_points = points
        previous_t_s = t_s

    if not path:
        path = [{"t_s": 0.0, "x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0, "heading_unwrapped_deg": 0.0, "alignment_score": 0.0, "pose_confidence": 0.0, "turn_event": False}]
    camera_hits = camera_global_tree_hits(camera_index, path)
    lidar_trees = spatial_thin_trees(merge_global_trees(lidar_hits, frame_count=len(lidar_frames)), min_spacing_m=0.82, max_count=36)
    camera_trees = spatial_thin_trees(merge_global_trees(camera_hits, frame_count=len(camera_index), source="camera"), min_spacing_m=1.05, max_count=42)
    fusion_trees = spatial_thin_trees(fuse_tree_maps(lidar_trees, camera_trees), min_spacing_m=0.95, max_count=48)
    lidar_map = build_map_layer(
        "lidar",
        "LiDAR-only",
        "2D LiDAR trunk candidates only",
        lidar_trees,
        path,
    )
    camera_map = build_map_layer(
        "camera",
        "Camera-only",
        "Monocular camera trunk contour/line detections projected with image geometry",
        camera_trees,
        path,
    )
    fusion_map = build_map_layer(
        "fusion",
        "Weighted Fusion",
        f"Weighted average of LiDAR ({LIDAR_TREE_WEIGHT:.2f}) and camera ({CAMERA_TREE_WEIGHT:.2f}) tree evidence",
        fusion_trees,
        path,
    )
    bounds = combined_map_bounds(path, [lidar_map, camera_map, fusion_map])
    return {
        "ok": True,
        "algorithm": MAP_ALGORITHM_NAME,
        "lidar_algorithm": ALGORITHM_NAME,
        "coordinate_frame": LIDAR_COORDINATE_FRAME,
        "duration_s": timeline.get("duration_s"),
        "tree_count": len(fusion_trees),
        "trees": fusion_trees,
        "path": path,
        "start": {**path[0], "t_s": 0.0} if path else None,
        "end": path[-1] if path else None,
        "bounds": bounds,
        "centerline": fusion_map["centerline"],
        "maps": {
            "lidar": lidar_map,
            "camera": camera_map,
            "fusion": fusion_map,
        },
        "frames": frame_summaries,
        "camera": {
            "frames_analyzed": len(camera_index),
            "method": camera_index[0]["method"] if camera_index else "unavailable",
        },
        "fusion": {
            "method": "Three-layer map: LiDAR-only, camera-only, and confidence-weighted landmark fusion; pseudo-odometry from 2D scan matching",
            "weights": {"lidar": LIDAR_TREE_WEIGHT, "camera": CAMERA_TREE_WEIGHT},
            "note": "The current camera-only layer uses monocular geometric depth approximation. Add calibrated camera intrinsics/extrinsics or visual odometry for metric publication results.",
        },
        "odometry": {
            "algorithm": ODOMETRY_ALGORITHM,
            "accepted_frames": sum(1 for frame in frame_summaries if frame.get("scan_match", {}).get("accepted")),
            "rejected_frames": sum(1 for frame in frame_summaries if not frame.get("scan_match", {}).get("accepted")),
            "turn_events": sum(1 for frame in frame_summaries if frame.get("scan_match", {}).get("turn_event")),
            "heading_change_deg": round(float(path[-1].get("heading_unwrapped_deg") or 0.0) - float(path[0].get("heading_unwrapped_deg") or 0.0), 2),
        },
        "lidar_ood": {
            "raw_candidates": sum(int(frame.get("lidar_ood", {}).get("raw_candidates") or 0) for frame in frame_summaries),
            "geometry_candidates": sum(int(frame.get("local_tree_count") or 0) for frame in frame_summaries),
            "confirmed_landmarks": len(lidar_trees),
            "wall_suppressed": sum(int(frame.get("lidar_ood", {}).get("wall_suppressed") or 0) for frame in frame_summaries),
            "linear_context_suppressed": sum(int(frame.get("lidar_ood", {}).get("linear_context_suppressed") or 0) for frame in frame_summaries),
            "rear_operator_suppressed": sum(int(frame.get("lidar_ood", {}).get("rear_operator_suppressed") or 0) for frame in frame_summaries),
            "temporal_policy": "minimum repeated global observations with confidence and time-span gate",
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
        if float(tree.get("structure_score") or 0.0) < 0.42:
            continue
        if tree.get("kind") != "trunk_like" and confidence < 0.78:
            continue
        if int(tree.get("points") or 0) < 5 and distance > 7.5:
            continue
        selected.append(tree)
    return selected[:18]


def estimate_scan_transform(
    previous_points: list[dict[str, Any]],
    current_points: list[dict[str, Any]],
    prior: dict[str, Any] | None = None,
    dt_s: float = 0.6,
) -> dict[str, Any]:
    return estimate_relative_transform(previous_points, current_points, prior=prior, dt_s=dt_s)


def compose_pose(pose: dict[str, float], transform: dict[str, Any]) -> dict[str, float]:
    dx = float(transform.get("dx_m") or 0.0)
    dy = float(transform.get("dy_m") or 0.0)
    dtheta = float(transform.get("dtheta_rad") or 0.0)
    heading = float(pose.get("theta_rad") or 0.0)
    cos_h = math.cos(heading)
    sin_h = math.sin(heading)
    next_heading = ((heading + dtheta + math.pi) % (math.tau)) - math.pi
    next_total_heading = float(pose.get("theta_total_rad", heading)) + dtheta
    return {
        "x_m": float(pose.get("x_m") or 0.0) + cos_h * dx - sin_h * dy,
        "y_m": float(pose.get("y_m") or 0.0) + sin_h * dx + cos_h * dy,
        "theta_rad": next_heading,
        "theta_total_rad": next_total_heading,
    }


def local_to_global_tree(tree: dict[str, Any], pose: dict[str, float], t_s: float, camera_support: dict[str, Any] | None = None) -> dict[str, Any]:
    local = tree["center"]
    lidar_score = float(tree.get("confidence") or 0.0)
    gx, gy = transform_local_point(float(local["x_m"]), float(local["y_m"]), pose)
    return {
        "x_m": round(gx, 3),
        "y_m": round(gy, 3),
        "radius_m": tree.get("radius_m", 0.2),
        "confidence": round(lidar_score, 3),
        "tree_probability": round(float(tree.get("tree_probability") or lidar_score), 3),
        "ood_score": round(float(tree.get("ood_score") or (1.0 - lidar_score)), 3),
        "structure_score": round(float(tree.get("structure_score") or 0.0), 3),
        "wall_distance_m": tree.get("wall_distance_m"),
        "nearest_wall_length_m": tree.get("nearest_wall_length_m"),
        "admission": "temporal_pending",
        "rejection_reasons": tree.get("rejection_reasons") or [],
        "lidar_confidence": round(lidar_score, 3),
        "camera_confidence": 0.0,
        "kind": tree.get("kind", "trunk_like"),
        "source": "lidar",
        "first_seen_s": round(t_s, 3),
        "last_seen_s": round(t_s, 3),
        "hits": 1,
    }


def transform_local_point(x: float, y: float, pose: dict[str, float]) -> tuple[float, float]:
    heading = float(pose.get("theta_rad") or 0.0)
    cos_h = math.cos(heading)
    sin_h = math.sin(heading)
    return cos_h * x - sin_h * y + float(pose.get("x_m") or 0.0), sin_h * x + cos_h * y + float(pose.get("y_m") or 0.0)


def camera_global_tree_hits(camera_index: list[dict[str, Any]], path: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for frame in camera_index:
        if not frame.get("ok"):
            continue
        t_s = float(frame.get("t_s") or 0.0)
        pose = nearest_path_pose(path, t_s)
        if not pose:
            continue
        for detection in camera_tree_local_candidates(frame):
            gx, gy = transform_local_point(detection["center"]["x_m"], detection["center"]["y_m"], pose)
            confidence = float(detection.get("confidence") or 0.0)
            hits.append(
                {
                    "x_m": round(gx, 3),
                    "y_m": round(gy, 3),
                    "radius_m": detection.get("radius_m", 0.22),
                    "confidence": round(confidence, 3),
                    "lidar_confidence": 0.0,
                    "camera_confidence": round(confidence, 3),
                    "kind": "camera_trunk_projection",
                    "source": "camera",
                    "bearing_deg": detection.get("bearing_deg"),
                    "distance_m": detection.get("distance_m"),
                    "projection": detection.get("projection"),
                    "first_seen_s": round(t_s, 3),
                    "last_seen_s": round(t_s, 3),
                    "hits": 1,
                }
            )
    return hits


def camera_tree_local_candidates(frame: dict[str, Any]) -> list[dict[str, Any]]:
    image = frame.get("image") if isinstance(frame.get("image"), dict) else {}
    width = int(image.get("width") or 1280)
    height = int(image.get("height") or 720)
    focal_px = width / (2.0 * math.tan(CAMERA_FOV_X_RAD / 2.0))
    rows: list[dict[str, Any]] = []
    for detection in frame.get("detections") or []:
        if str(detection.get("admission") or "accepted") == "rejected":
            continue
        if detection.get("map_admission") is not None and detection.get("map_admission") != "accepted":
            continue
        bbox = detection.get("bbox") or []
        if len(bbox) < 4:
            continue
        try:
            x, y, bw, bh = [float(value) for value in bbox[:4]]
        except (TypeError, ValueError):
            continue
        confidence = float(detection.get("tree_probability") or detection.get("confidence") or 0.0)
        if confidence < 0.62 or bw <= 0 or bh <= 0:
            continue
        bottom_ratio = max(0.0, min(1.0, (y + bh) / max(1.0, height)))
        if bottom_ratio < 0.45:
            continue
        center_x = x + bw / 2.0
        bearing = ((center_x / max(1.0, width)) - 0.5) * CAMERA_FOV_X_RAD
        height_depth = focal_px * CAMERA_ASSUMED_VISIBLE_TRUNK_HEIGHT_M / max(12.0, bh)
        bottom_depth = 1.15 + max(0.0, 1.0 - bottom_ratio) * 11.2
        distance = clamp(0.62 * height_depth + 0.38 * bottom_depth, 1.15, 13.5)
        lateral = math.tan(bearing) * distance
        radius = clamp(distance * (bw / max(1.0, focal_px)) * 0.34, 0.12, 0.55)
        geometry_confidence = confidence
        if bottom_ratio < 0.58:
            geometry_confidence *= 0.86
        if abs(math.degrees(bearing)) > 34:
            geometry_confidence *= 0.9
        rows.append(
            {
                "center": {"x_m": lateral, "y_m": distance},
                "radius_m": round(radius, 3),
                "confidence": round(max(0.05, min(0.96, geometry_confidence)), 3),
                "bearing_deg": round(math.degrees(bearing), 2),
                "distance_m": round(distance, 3),
                "projection": {
                    "method": "monocular_trunk_height_bottom_bearing",
                    "bbox": [round(x, 1), round(y, 1), round(bw, 1), round(bh, 1)],
                    "bottom_ratio": round(bottom_ratio, 3),
                    "fov_x_deg": round(math.degrees(CAMERA_FOV_X_RAD), 1),
                },
            }
        )
    rows.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return rows[:10]


def nearest_path_pose(path: list[dict[str, Any]], t_s: float) -> dict[str, float] | None:
    if not path:
        return None
    item = min(path, key=lambda row: abs(float(row.get("t_s") or 0.0) - t_s))
    theta = math.radians(float(item.get("heading_deg") or 0.0))
    return {"x_m": float(item.get("x_m") or 0.0), "y_m": float(item.get("y_m") or 0.0), "theta_rad": theta}


def merge_global_trees(hits: list[dict[str, Any]], frame_count: int | None = None, source: str = "lidar") -> list[dict[str, Any]]:
    trees: list[dict[str, Any]] = []
    for hit in sorted(hits, key=lambda item: item["first_seen_s"]):
        found = None
        for tree in trees:
            base_gate = 0.72 if source == "camera" else 0.48
            max_gate = 1.45 if source == "camera" else 1.05
            gate = max(base_gate, min(max_gate, 0.38 + float(hit.get("radius_m") or 0.2) + 0.026 * max(abs(hit["x_m"]), abs(hit["y_m"]))))
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
        found["structure_score"] = round(max(float(found.get("structure_score") or 0.0), float(hit.get("structure_score") or 0.0)), 3)
        wall_distances = [value for value in (found.get("wall_distance_m"), hit.get("wall_distance_m")) if value is not None]
        found["wall_distance_m"] = round(min(float(value) for value in wall_distances), 3) if wall_distances else None
        found["last_seen_s"] = max(float(found.get("last_seen_s") or 0.0), float(hit.get("last_seen_s") or 0.0))
        found["hits"] = hits_count
    filtered = []
    for tree in trees:
        hits = int(tree.get("hits") or 0)
        time_span = float(tree.get("last_seen_s") or 0.0) - float(tree.get("first_seen_s") or 0.0)
        confidence = float(tree.get("confidence") or 0.0)
        camera_confidence = float(tree.get("camera_confidence") or 0.0)
        if source == "camera" and hits >= 3 and confidence >= 0.66:
            filtered.append(tree)
        elif source == "camera" and hits >= 2 and time_span >= 0.5 and confidence >= 0.78:
            filtered.append(tree)
        elif hits >= 8 and time_span >= 2.0:
            filtered.append(tree)
        elif hits >= 5 and confidence >= 0.74:
            filtered.append(tree)
        elif (frame_count or 0) <= 8 and hits >= 3 and time_span >= 0.8 and confidence >= 0.62:
            filtered.append(tree)
        elif hits >= 3 and camera_confidence >= 0.62 and confidence >= 0.78:
            filtered.append(tree)
    filtered.sort(key=lambda item: (-(int(item.get("hits") or 0)), -float(item.get("confidence") or 0.0)))
    for tree in filtered:
        tree["temporal_hits"] = int(tree.get("hits") or 0)
        tree["temporal_persistence_s"] = round(float(tree.get("last_seen_s") or 0.0) - float(tree.get("first_seen_s") or 0.0), 3)
        tree["admission"] = "temporal_confirmed"
        tree["map_admission"] = "accepted"
        tree["rejection_reasons"] = []
    return filtered[:80]


def fuse_tree_maps(lidar_trees: list[dict[str, Any]], camera_trees: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fused: list[dict[str, Any]] = []
    used_camera: set[int] = set()
    for lidar in lidar_trees:
        best_index = -1
        best_distance = float("inf")
        for index, camera in enumerate(camera_trees):
            if index in used_camera:
                continue
            distance = math.hypot(float(lidar["x_m"]) - float(camera["x_m"]), float(lidar["y_m"]) - float(camera["y_m"]))
            if distance < best_distance:
                best_index = index
                best_distance = distance
        distance_from_origin = math.hypot(float(lidar["x_m"]), float(lidar["y_m"]))
        gate = max(0.9, min(2.2, 0.75 + 0.055 * distance_from_origin))
        camera = camera_trees[best_index] if best_index >= 0 and best_distance <= gate else None
        if camera:
            used_camera.add(best_index)
        fused.append(fuse_tree_pair(lidar, camera, best_distance if camera else None))

    for index, camera in enumerate(camera_trees):
        if index in used_camera:
            continue
        confidence = float(camera.get("confidence") or 0.0)
        if confidence < 0.9 or int(camera.get("hits") or 0) < 5:
            continue
        fused.append(
            {
                **camera,
                "confidence": round(min(0.84, confidence * CAMERA_TREE_WEIGHT + 0.38), 3),
                "fusion_source": "camera_only_high_confidence",
                "fusion_weight_lidar": 0.0,
                "fusion_weight_camera": CAMERA_TREE_WEIGHT,
                "match_error_m": None,
            }
        )
    fused.sort(key=lambda item: (-(int(item.get("hits") or 0)), -float(item.get("confidence") or 0.0)))
    return fused[:80]


def spatial_thin_trees(trees: list[dict[str, Any]], min_spacing_m: float, max_count: int) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for tree in sorted(trees, key=lambda item: (-(int(item.get("hits") or 0)), -float(item.get("confidence") or 0.0))):
        try:
            x = float(tree["x_m"])
            y = float(tree["y_m"])
        except (KeyError, TypeError, ValueError):
            continue
        if any(math.hypot(x - float(other["x_m"]), y - float(other["y_m"])) < min_spacing_m for other in kept):
            continue
        kept.append(tree)
        if len(kept) >= max_count:
            break
    kept.sort(key=lambda item: (float(item.get("first_seen_s") or 0.0), float(item.get("x_m") or 0.0)))
    return kept


def fuse_tree_pair(lidar: dict[str, Any], camera: dict[str, Any] | None, match_error_m: float | None) -> dict[str, Any]:
    lidar_conf = float(lidar.get("confidence") or lidar.get("lidar_confidence") or 0.0)
    if not camera:
        return {
            **lidar,
            "confidence": round(min(0.92, lidar_conf * 0.9), 3),
            "fusion_source": "lidar_only",
            "fusion_weight_lidar": LIDAR_TREE_WEIGHT,
            "fusion_weight_camera": 0.0,
            "match_error_m": None,
        }
    camera_conf = float(camera.get("confidence") or camera.get("camera_confidence") or 0.0)
    wl = LIDAR_TREE_WEIGHT * max(0.05, lidar_conf)
    wc = CAMERA_TREE_WEIGHT * max(0.05, camera_conf)
    total = max(0.001, wl + wc)
    x = (float(lidar["x_m"]) * wl + float(camera["x_m"]) * wc) / total
    y = (float(lidar["y_m"]) * wl + float(camera["y_m"]) * wc) / total
    radius = max(float(lidar.get("radius_m") or 0.2), float(camera.get("radius_m") or 0.2))
    confidence = min(0.98, 0.56 * lidar_conf + 0.34 * camera_conf + 0.12 * max(0.0, 1.0 - (match_error_m or 0.0) / 2.0))
    return {
        "x_m": round(x, 3),
        "y_m": round(y, 3),
        "radius_m": round(radius, 3),
        "confidence": round(confidence, 3),
        "lidar_confidence": round(lidar_conf, 3),
        "camera_confidence": round(camera_conf, 3),
        "kind": "fused_tree",
        "source": "weighted_lidar_camera",
        "fusion_source": "matched_weighted_average",
        "fusion_weight_lidar": round(wl / total, 3),
        "fusion_weight_camera": round(wc / total, 3),
        "match_error_m": round(match_error_m or 0.0, 3),
        "first_seen_s": min(float(lidar.get("first_seen_s") or 0.0), float(camera.get("first_seen_s") or 0.0)),
        "last_seen_s": max(float(lidar.get("last_seen_s") or 0.0), float(camera.get("last_seen_s") or 0.0)),
        "hits": int(lidar.get("hits") or 0) + int(camera.get("hits") or 0),
        "components": {
            "lidar": {"x_m": lidar.get("x_m"), "y_m": lidar.get("y_m"), "hits": lidar.get("hits")},
            "camera": {"x_m": camera.get("x_m"), "y_m": camera.get("y_m"), "hits": camera.get("hits")},
        },
    }


def build_map_layer(layer_id: str, label: str, method: str, trees: list[dict[str, Any]], path: list[dict[str, Any]]) -> dict[str, Any]:
    centerline = estimate_row_centerline(trees, path, layer_id)
    return {
        "id": layer_id,
        "label": label,
        "method": method,
        "tree_count": len(trees),
        "trees": trees,
        "path": path,
        "start": path[0] if path else None,
        "end": path[-1] if path else None,
        "bounds": map_bounds_for_items(path, trees, centerline.get("points") or []),
        "centerline": centerline,
    }


def estimate_row_centerline(trees: list[dict[str, Any]], path: list[dict[str, Any]], layer_id: str) -> dict[str, Any]:
    if len(path) < 2:
        return empty_centerline("path_unavailable", layer_id)
    origin, axis, normal = path_reference_axis(path)
    projected = []
    for tree in trees:
        try:
            dx = float(tree["x_m"]) - origin[0]
            dy = float(tree["y_m"]) - origin[1]
        except (KeyError, TypeError, ValueError):
            continue
        s = dx * axis[0] + dy * axis[1]
        lateral = dx * normal[0] + dy * normal[1]
        if abs(lateral) < 0.35:
            continue
        projected.append({"tree": tree, "s": s, "lateral": lateral, "weight": max(0.2, float(tree.get("confidence") or 0.5))})
    if len(projected) < 3:
        return empty_centerline("insufficient_tree_rows", layer_id)

    bin_size = 2.0
    bins: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for item in projected:
        key = int(math.floor(item["s"] / bin_size))
        side = "left" if item["lateral"] >= 0 else "right"
        bins.setdefault(key, {"left": [], "right": []})[side].append(item)

    paired_gaps = []
    side_centers: dict[int, dict[str, float | None]] = {}
    for key, sides in bins.items():
        left = weighted_average([row["lateral"] for row in sides["left"]], [row["weight"] for row in sides["left"]])
        right = weighted_average([row["lateral"] for row in sides["right"]], [row["weight"] for row in sides["right"]])
        side_centers[key] = {"left": left, "right": right}
        if left is not None and right is not None and left > right:
            gap = left - right
            if 1.0 <= gap <= 8.0:
                paired_gaps.append(gap)
    row_gap = median(paired_gaps) if paired_gaps else None
    points: list[dict[str, Any]] = []
    for key in sorted(side_centers):
        left = side_centers[key]["left"]
        right = side_centers[key]["right"]
        source = "paired_rows"
        if left is not None and right is not None:
            center_lateral = (left + right) / 2.0
        elif row_gap is not None and left is not None:
            center_lateral = left - row_gap / 2.0
            source = "left_row_gap_projection"
        elif row_gap is not None and right is not None:
            center_lateral = right + row_gap / 2.0
            source = "right_row_gap_projection"
        else:
            continue
        s = (key + 0.5) * bin_size
        x = origin[0] + axis[0] * s + normal[0] * center_lateral
        y = origin[1] + axis[1] * s + normal[1] * center_lateral
        points.append({"x_m": round(x, 3), "y_m": round(y, 3), "s_m": round(s, 3), "source": source})
    points = smooth_centerline_points(points)
    length = polyline_length(points)
    confidence = centerline_confidence(points, projected, paired_gaps)
    return {
        "ok": len(points) >= 2,
        "layer": layer_id,
        "method": "tree_row_lateral_midpoint_bins_v1",
        "points": points,
        "point_count": len(points),
        "length_m": round(length, 3),
        "row_gap_m": round(row_gap, 3) if row_gap is not None else None,
        "left_tree_count": sum(1 for item in projected if item["lateral"] >= 0),
        "right_tree_count": sum(1 for item in projected if item["lateral"] < 0),
        "confidence": confidence,
        "state": "ready" if len(points) >= 2 else "insufficient_tree_rows",
    }


def empty_centerline(reason: str, layer_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "layer": layer_id,
        "method": "tree_row_lateral_midpoint_bins_v1",
        "points": [],
        "point_count": 0,
        "length_m": 0.0,
        "row_gap_m": None,
        "left_tree_count": 0,
        "right_tree_count": 0,
        "confidence": 0.0,
        "state": reason,
    }


def path_reference_axis(path: list[dict[str, Any]]) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    start = path[0]
    end = path[-1]
    ox = float(start.get("x_m") or 0.0)
    oy = float(start.get("y_m") or 0.0)
    dx = float(end.get("x_m") or 0.0) - ox
    dy = float(end.get("y_m") or 0.0) - oy
    if math.hypot(dx, dy) < 0.5:
        heading = math.radians(float(start.get("heading_deg") or 0.0))
        dx = math.sin(heading)
        dy = math.cos(heading)
    norm = math.hypot(dx, dy) or 1.0
    axis = (dx / norm, dy / norm)
    normal = (-axis[1], axis[0])
    return (ox, oy), axis, normal


def weighted_average(values: list[float], weights: list[float]) -> float | None:
    if not values:
        return None
    total = sum(max(0.001, weight) for weight in weights)
    return sum(value * max(0.001, weight) for value, weight in zip(values, weights)) / total


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def smooth_centerline_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(points) < 3:
        return points
    rows = []
    for index, point in enumerate(points):
        neighbors = points[max(0, index - 1) : min(len(points), index + 2)]
        rows.append(
            {
                **point,
                "x_m": round(sum(float(row["x_m"]) for row in neighbors) / len(neighbors), 3),
                "y_m": round(sum(float(row["y_m"]) for row in neighbors) / len(neighbors), 3),
            }
        )
    return rows


def polyline_length(points: list[dict[str, Any]]) -> float:
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += math.hypot(float(a["x_m"]) - float(b["x_m"]), float(a["y_m"]) - float(b["y_m"]))
    return total


def centerline_confidence(points: list[dict[str, Any]], projected: list[dict[str, Any]], paired_gaps: list[float]) -> float:
    if len(points) < 2:
        return 0.0
    tree_term = min(1.0, len(projected) / 16.0)
    paired_term = min(1.0, len(paired_gaps) / 4.0)
    length_term = min(1.0, polyline_length(points) / 8.0)
    return round(0.25 + 0.32 * tree_term + 0.28 * paired_term + 0.15 * length_term, 3)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def camera_observation_index(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for frame in frames:
        rows.append({"t_s": float(frame.get("t_s") or 0.0), **estimate_camera_orchard_support(frame)})
    return rows


def nearest_camera_support(index: list[dict[str, Any]], t_s: float) -> dict[str, Any] | None:
    if not index:
        return None
    return min(index, key=lambda item: abs(float(item.get("t_s") or 0.0) - t_s))


def map_bounds(path: list[dict[str, Any]], trees: list[dict[str, Any]]) -> dict[str, float]:
    return map_bounds_for_items(path, trees, [])


def map_bounds_for_items(
    path: list[dict[str, Any]],
    trees: list[dict[str, Any]],
    extra_points: list[dict[str, Any]],
) -> dict[str, float]:
    xs = [float(item["x_m"]) for item in path] + [float(item["x_m"]) for item in trees] + [float(item["x_m"]) for item in extra_points]
    ys = [float(item["y_m"]) for item in path] + [float(item["y_m"]) for item in trees] + [float(item["y_m"]) for item in extra_points]
    if not xs or not ys:
        return {"min_x": -5.0, "max_x": 5.0, "min_y": -5.0, "max_y": 5.0}
    pad = 2.0
    return {"min_x": round(min(xs) - pad, 3), "max_x": round(max(xs) + pad, 3), "min_y": round(min(ys) - pad, 3), "max_y": round(max(ys) + pad, 3)}


def combined_map_bounds(path: list[dict[str, Any]], maps: list[dict[str, Any]]) -> dict[str, float]:
    trees: list[dict[str, Any]] = []
    points: list[dict[str, Any]] = []
    for layer in maps:
        trees.extend(layer.get("trees") or [])
        points.extend(layer.get("centerline", {}).get("points") or [])
    return map_bounds_for_items(path, trees, points)
