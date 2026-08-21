from __future__ import annotations

import math
from typing import Any

from dashboard_server.camera_analysis import estimate_camera_orchard_support
from dashboard_server.lidar_analysis import ALGORITHM_NAME, LIDAR_COORDINATE_FRAME, detect_orchard_trees, is_rear_person_zone
from dashboard_server.lidar_odometry import ODOMETRY_ALGORITHM, estimate_relative_transform


MAP_ALGORITHM_NAME = "orchard_metric_anchor_semantic_fusion_local_frenet_v9"
MAP_CACHE_REVISION = "v9_rep103_provenance_continuity_readiness_r3"
CAMERA_NOMINAL_FOV_X_DEG = 70.0
CAMERA_FOV_X_RAD = math.radians(CAMERA_NOMINAL_FOV_X_DEG)
CAMERA_INTRINSICS_CALIBRATED = False
CAMERA_ASSUMED_VISIBLE_TRUNK_HEIGHT_M = 1.35
LIDAR_TREE_WEIGHT = 0.64
CAMERA_TREE_WEIGHT = 0.36
CAMERA_SYNC_MAX_DELTA_S = 0.25
CAMERA_LIVE_RECOMMENDED_SYNC_MAX_DELTA_S = 0.12
CAMERA_BEARING_BASE_GATE_DEG = 2.5
CENTERLINE_MIN_CONFIDENCE = 0.45
CENTERLINE_ROW_GAP_MIN_M = 3.0
CENTERLINE_ROW_GAP_MAX_M = 7.0
CENTERLINE_MIN_SIDE_BINS = 3
CENTERLINE_MIN_SIDE_SPAN_M = 4.0
CENTERLINE_MIN_PAIRED_BINS = 2
CENTERLINE_MIN_BILATERAL_OVERLAP_M = 4.0
CENTERLINE_MIN_BILATERAL_COVERAGE = 0.5
CENTERLINE_MIN_PAIRED_COVERAGE = 0.35
CENTERLINE_MIN_PAIRED_STATION_SPAN_M = 4.0
CENTERLINE_MAX_HOLE_BINS = 1
CENTERLINE_MAX_CONSECUTIVE_PAIRED_BIN_GAP = CENTERLINE_MAX_HOLE_BINS + 1
CENTERLINE_MIN_CONTIGUOUS_PAIRED_BINS = 2
CENTERLINE_MIN_PAIRED_BIN_DENSITY = 0.50
MIN_DATA_LIDAR_FRAMES = 2
MIN_DATA_NONEMPTY_LIDAR_FRAMES = 2


def current_orchard_map_cache(payload: dict[str, Any]) -> bool:
    orchard_map = payload.get("map") if isinstance(payload.get("map"), dict) else {}
    return bool(
        payload.get("ok")
        and orchard_map.get("ok")
        and orchard_map.get("algorithm") == MAP_ALGORITHM_NAME
        and orchard_map.get("cache_revision") == MAP_CACHE_REVISION
    )


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
            semantic_support = lidar_camera_semantic_support(tree, camera_support)
            lidar_hits.append(local_to_global_tree(tree, pose, t_s, camera_support=semantic_support))
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
                "camera_support": compact_camera_sync_diagnostic(camera_support),
            }
        )
        previous_points = points
        previous_t_s = t_s

    if not path:
        path = [{"t_s": 0.0, "x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0, "heading_unwrapped_deg": 0.0, "alignment_score": 0.0, "pose_confidence": 0.0, "turn_event": False}]
    camera_hits = camera_global_tree_hits(camera_index, path)
    lidar_trees = spatial_thin_trees(merge_global_trees(lidar_hits, frame_count=len(lidar_frames)), min_spacing_m=0.82)
    camera_trees = spatial_thin_trees(merge_global_trees(camera_hits, frame_count=len(camera_index), source="camera"), min_spacing_m=1.05)
    fusion_trees = spatial_thin_trees(fuse_tree_maps(lidar_trees, camera_trees), min_spacing_m=0.82)
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
        "Camera trunk detections; monocular locations remain an explicitly uncalibrated research layer",
        camera_trees,
        path,
    )
    fusion_map = build_map_layer(
        "fusion",
        "Metric-anchor Fusion",
        "LiDAR metric anchors with time-gated camera bearing/contour semantic support",
        fusion_trees,
        path,
    )
    readiness = orchard_map_readiness(lidar_frames, camera_frames, fusion_trees, fusion_map["centerline"])
    bounds = combined_map_bounds(path, [lidar_map, camera_map, fusion_map])
    return {
        "ok": True,
        "algorithm": MAP_ALGORITHM_NAME,
        "cache_revision": MAP_CACHE_REVISION,
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
        "readiness": readiness,
        "data_sufficiency_ok": readiness["data_sufficiency_ok"],
        "navigation_ready": readiness["navigation_ready"],
        "camera": {
            "frames_analyzed": len(camera_index),
            "method": camera_index[0]["method"] if camera_index else "unavailable",
            "projection_model": {
                "model": "pinhole_u_equals_cx_minus_fx_tan_bearing",
                "nominal_fov_x_deg": CAMERA_NOMINAL_FOV_X_DEG,
                "nominal_intrinsics_calibrated": CAMERA_INTRINSICS_CALIBRATED,
                "policy": "use per-frame CameraInfo when present; otherwise nominal 70 degree FOV remains uncalibrated",
            },
            "sync": camera_sync_diagnostics(frame_summaries),
        },
        "fusion": {
            "method": "LiDAR metric-anchor fusion with nearest-frame time gate, y-forward bearing association, and uncertainty-aware camera semantic support",
            "position_policy": "LiDAR supplies metric x/y; camera changes semantic confidence only",
            "semantic_weight_policy": "dynamic_by_camera_quality_and_alignment; missing or low-quality camera evidence never penalizes LiDAR",
            "camera_only_metric_policy": "fail_closed_unless_metric_projection_is_explicitly_calibrated_and_validated",
            "tree_limit_policy": "spatial_spacing_only_no_fixed_recording_count_cap",
            "sync": camera_sync_diagnostics(frame_summaries),
            "metric_anchor_count": sum(1 for tree in fusion_trees if tree.get("metric_anchor") == "lidar"),
            "camera_only_promoted_count": sum(1 for tree in fusion_trees if tree.get("fusion_source") == "camera_only_calibrated"),
            "semantic_supported_count": sum(1 for tree in fusion_trees if int(tree.get("camera_support_hits") or 0) > 0),
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
    return selected


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
    support = camera_support or {}
    camera_confidence = float(support.get("semantic_probability") or 0.0)
    camera_quality = float(support.get("quality") or 0.0)
    semantic_support = camera_confidence * camera_quality if support.get("matched") else 0.0
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
        "camera_confidence": round(camera_confidence, 3),
        "camera_quality": round(camera_quality, 3),
        "camera_semantic_support": round(semantic_support, 3),
        "camera_support_hits": 1 if support.get("matched") else 0,
        "camera_sync_delta_s": support.get("sync_delta_s"),
        "camera_bearing_delta_deg": support.get("bearing_delta_deg"),
        "camera_detection_bbox": support.get("bbox"),
        "camera_detection_contour": support.get("contour"),
        "metric_anchor": "lidar",
        "position_source": "lidar_metric_anchor",
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


def lidar_camera_semantic_support(tree: dict[str, Any], camera_support: dict[str, Any] | None) -> dict[str, Any]:
    """Associate a y-forward LiDAR bearing with a camera contour without changing metric position."""
    if not camera_support or not camera_support.get("matched"):
        return {
            "matched": False,
            "state": str((camera_support or {}).get("state") or "camera_unavailable"),
            "quality": 0.0,
            "sync_delta_s": (camera_support or {}).get("sync_delta_s"),
        }
    if not camera_support.get("ok"):
        return {
            "matched": False,
            "state": "camera_analysis_unavailable",
            "quality": 0.0,
            "sync_delta_s": camera_support.get("sync_delta_s"),
        }
    center = tree.get("center") or {}
    try:
        lateral_x = float(center["x_m"])
        forward_y = float(center["y_m"])
    except (KeyError, TypeError, ValueError):
        return {"matched": False, "state": "invalid_lidar_center", "quality": 0.0}
    if forward_y <= 0.0:
        return {"matched": False, "state": "lidar_candidate_behind_camera", "quality": 0.0}

    image = camera_support.get("image") if isinstance(camera_support.get("image"), dict) else {}
    width = max(1, int(image.get("width") or camera_support.get("image_width") or 1280))
    fov_x_rad = float(camera_support.get("fov_x_rad") or CAMERA_FOV_X_RAD)
    focal_x_px = float(camera_support.get("fx_px") or width / (2.0 * math.tan(fov_x_rad / 2.0)))
    principal_x_px = float(camera_support.get("cx_px") if camera_support.get("cx_px") is not None else width / 2.0)
    bearing_deg = math.degrees(math.atan2(lateral_x, forward_y))
    # lidar_bridge_xy_v1 and REP-103 use left-positive lateral bearings, while
    # image u increases to the right. Keep this sign inversion explicit.
    predicted_u = principal_x_px - focal_x_px * math.tan(math.radians(bearing_deg))
    if predicted_u < 0.0 or predicted_u >= width:
        return {
            "matched": False,
            "state": "lidar_bearing_outside_camera_fov",
            "quality": 0.0,
            "sync_delta_s": camera_support.get("sync_delta_s"),
            "lidar_bearing_deg": round(bearing_deg, 3),
            "predicted_u_px": round(predicted_u, 2),
        }
    best: dict[str, Any] | None = None
    for detection in camera_support.get("detections") or []:
        if str(detection.get("admission") or "accepted") == "rejected":
            continue
        if detection.get("map_admission") is not None and detection.get("map_admission") != "accepted":
            continue
        extent = detection_horizontal_extent(detection)
        if extent is None:
            continue
        left, right, shape = extent
        center_u = (left + right) / 2.0
        left_bearing = math.degrees(-math.atan((left - principal_x_px) / focal_x_px))
        right_bearing = math.degrees(-math.atan((right - principal_x_px) / focal_x_px))
        detection_bearing = math.degrees(-math.atan((center_u - principal_x_px) / focal_x_px))
        half_width_deg = max(abs(detection_bearing - left_bearing), abs(right_bearing - detection_bearing))
        gate_deg = half_width_deg + CAMERA_BEARING_BASE_GATE_DEG
        bearing_delta = abs(wrapped_angle_deg(bearing_deg - detection_bearing))
        if bearing_delta > gate_deg:
            continue
        semantic_probability = clamp(float(detection.get("tree_probability") or detection.get("confidence") or 0.0), 0.0, 1.0)
        if semantic_probability <= 0.0:
            continue
        alignment = clamp(1.0 - bearing_delta / max(0.1, gate_deg), 0.0, 1.0)
        quality = camera_support_quality(camera_support, detection)
        score = semantic_probability * quality * (0.35 + 0.65 * alignment)
        if best is None or score > float(best.get("association_score") or 0.0):
            bbox = detection.get("bbox") if isinstance(detection.get("bbox"), list) else None
            contour = detection.get("contour") if isinstance(detection.get("contour"), list) else None
            best = {
                "matched": True,
                "state": "bearing_contour_associated",
                "semantic_probability": round(semantic_probability, 3),
                "quality": round(quality, 3),
                "association_score": round(score, 3),
                "alignment": round(alignment, 3),
                "sync_delta_s": camera_support.get("sync_delta_s"),
                "lidar_bearing_deg": round(bearing_deg, 3),
                "camera_bearing_deg": round(detection_bearing, 3),
                "bearing_delta_deg": round(bearing_delta, 3),
                "predicted_u_px": round(predicted_u, 2),
                "association_shape": shape,
                "bbox": bbox,
                "contour": contour,
            }
    return best or {
        "matched": False,
        "state": "no_camera_detection_at_lidar_bearing",
        "quality": 0.0,
        "sync_delta_s": camera_support.get("sync_delta_s"),
        "lidar_bearing_deg": round(bearing_deg, 3),
    }


def detection_horizontal_extent(detection: dict[str, Any]) -> tuple[float, float, str] | None:
    contour = detection.get("contour")
    contour_x = contour_x_coordinates(contour)
    if len(contour_x) >= 2:
        return min(contour_x), max(contour_x), "contour"
    bbox = detection.get("bbox") or []
    if len(bbox) < 4:
        return None
    try:
        x, _, width, _ = [float(value) for value in bbox[:4]]
    except (TypeError, ValueError):
        return None
    if width <= 0.0:
        return None
    return x, x + width, "bbox"


def contour_x_coordinates(value: Any) -> list[float]:
    if isinstance(value, dict) and "x" in value:
        try:
            return [float(value["x"])]
        except (TypeError, ValueError):
            return []
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and not isinstance(value[0], (list, tuple, dict)):
            try:
                return [float(value[0])]
            except (TypeError, ValueError):
                return []
        rows: list[float] = []
        for item in value:
            rows.extend(contour_x_coordinates(item))
        return rows
    return []


def camera_support_quality(camera_support: dict[str, Any], detection: dict[str, Any]) -> float:
    if not camera_support.get("matched") or not camera_support.get("ok"):
        return 0.0
    base = 0.82
    method = str(camera_support.get("method") or "")
    if "worker" in method or "tensorrt" in method.lower():
        base = 0.94
    sync_delta = abs(float(camera_support.get("sync_delta_s") or 0.0))
    sync_quality = clamp(1.0 - 0.35 * sync_delta / max(0.001, CAMERA_SYNC_MAX_DELTA_S), 0.65, 1.0)
    ood_score = clamp(float(detection.get("ood_score") or 0.0), 0.0, 1.0)
    admission = 1.0 if detection.get("map_admission") in {None, "accepted"} else 0.0
    return clamp(base * sync_quality * (1.0 - 0.35 * ood_score) * admission, 0.0, 1.0)


def camera_global_tree_hits(camera_index: list[dict[str, Any]], path: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for frame in camera_index:
        if not frame.get("ok"):
            continue
        t_s = float(frame.get("t_s") or 0.0)
        pose = nearest_path_pose(path, t_s, max_delta_s=CAMERA_SYNC_MAX_DELTA_S)
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
                    "metric_calibrated": bool(detection.get("metric_calibrated")),
                    "calibrated_hits": 1 if detection.get("metric_calibrated") else 0,
                    "uncalibrated_hits": 0 if detection.get("metric_calibrated") else 1,
                    "metric_provenance_consistent": True,
                    "metric_anchor": "camera_calibrated" if detection.get("metric_calibrated") else "none",
                    "position_source": "camera_calibrated_projection" if detection.get("metric_calibrated") else "camera_monocular_uncalibrated",
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
    fov_x_rad = float(frame.get("fov_x_rad") or CAMERA_FOV_X_RAD)
    focal_px = float(frame.get("fx_px") or width / (2.0 * math.tan(fov_x_rad / 2.0)))
    principal_x_px = float(frame.get("cx_px") if frame.get("cx_px") is not None else width / 2.0)
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
        bearing = -math.atan((center_x - principal_x_px) / max(1.0, focal_px))
        height_depth = focal_px * CAMERA_ASSUMED_VISIBLE_TRUNK_HEIGHT_M / max(12.0, bh)
        bottom_depth = 1.15 + max(0.0, 1.0 - bottom_ratio) * 11.2
        distance = clamp(0.62 * height_depth + 0.38 * bottom_depth, 1.15, 13.5)
        lateral = math.tan(bearing) * distance
        metric_calibrated = False
        projection_method = "monocular_trunk_height_bottom_bearing"
        metric_projection = detection.get("metric_projection") if isinstance(detection.get("metric_projection"), dict) else {}
        if (
            metric_projection.get("validated") is True
            and metric_projection.get("intrinsics_validated") is True
            and metric_projection.get("extrinsics_validated") is True
            and metric_projection.get("coordinate_frame") == LIDAR_COORDINATE_FRAME
        ):
            try:
                lateral = float(metric_projection["x_m"])
                distance = float(metric_projection["y_m"])
                metric_calibrated = math.isfinite(lateral) and math.isfinite(distance) and distance > 0.0
            except (KeyError, TypeError, ValueError):
                metric_calibrated = False
            if metric_calibrated:
                projection_method = "validated_camera_lidar_metric_projection"
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
                "metric_calibrated": metric_calibrated,
                "projection": {
                    "method": projection_method,
                    "bbox": [round(x, 1), round(y, 1), round(bw, 1), round(bh, 1)],
                    "bottom_ratio": round(bottom_ratio, 3),
                    "fov_x_deg": round(math.degrees(fov_x_rad), 2),
                    "intrinsics_calibrated": bool(frame.get("intrinsics_calibrated")),
                    "fx_px": round(focal_px, 3),
                    "cx_px": round(principal_x_px, 3),
                },
            }
        )
    rows.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return rows


def nearest_path_pose(path: list[dict[str, Any]], t_s: float, max_delta_s: float | None = None) -> dict[str, float] | None:
    if not path:
        return None
    item = min(path, key=lambda row: abs(float(row.get("t_s") or 0.0) - t_s))
    delta_s = abs(float(item.get("t_s") or 0.0) - t_s)
    if max_delta_s is not None and delta_s > max_delta_s:
        return None
    theta = math.radians(float(item.get("heading_deg") or 0.0))
    return {"x_m": float(item.get("x_m") or 0.0), "y_m": float(item.get("y_m") or 0.0), "theta_rad": theta, "sync_delta_s": delta_s}


def merge_global_trees(hits: list[dict[str, Any]], frame_count: int | None = None, source: str = "lidar") -> list[dict[str, Any]]:
    trees: list[dict[str, Any]] = []
    for hit in sorted(hits, key=lambda item: item["first_seen_s"]):
        hit_is_metric_calibrated = bool(hit.get("metric_calibrated"))
        found = None
        for tree in trees:
            # Never average validated metric camera coordinates with monocular
            # estimates. A single calibrated hit must not bless a mixed track.
            if source == "camera" and bool(tree.get("metric_calibrated")) != hit_is_metric_calibrated:
                continue
            base_gate = 0.72 if source == "camera" else 0.48
            max_gate = 1.45 if source == "camera" else 1.05
            gate = max(base_gate, min(max_gate, 0.38 + float(hit.get("radius_m") or 0.2) + 0.026 * max(abs(hit["x_m"]), abs(hit["y_m"]))))
            if math.hypot(hit["x_m"] - tree["x_m"], hit["y_m"] - tree["y_m"]) <= gate:
                found = tree
                break
        if not found:
            new_tree = dict(hit)
            if source == "camera":
                observation_count = max(1, int(new_tree.get("hits") or 1))
                new_tree["hits"] = observation_count
                new_tree["calibrated_hits"] = observation_count if hit_is_metric_calibrated else 0
                new_tree["uncalibrated_hits"] = 0 if hit_is_metric_calibrated else observation_count
                new_tree["metric_provenance_consistent"] = True
            trees.append(new_tree)
            continue
        added_hits = max(1, int(hit.get("hits") or 1)) if source == "camera" else 1
        hits_count = int(found.get("hits") or 1) + added_hits
        alpha = added_hits / hits_count
        found["x_m"] = round(found["x_m"] * (1.0 - alpha) + hit["x_m"] * alpha, 3)
        found["y_m"] = round(found["y_m"] * (1.0 - alpha) + hit["y_m"] * alpha, 3)
        found["radius_m"] = round(max(float(found.get("radius_m") or 0.1), float(hit.get("radius_m") or 0.1)), 3)
        found["confidence"] = round(max(float(found.get("confidence") or 0.0), float(hit.get("confidence") or 0.0)), 3)
        found["lidar_confidence"] = round(max(float(found.get("lidar_confidence") or 0.0), float(hit.get("lidar_confidence") or 0.0)), 3)
        found["camera_confidence"] = round(max(float(found.get("camera_confidence") or 0.0), float(hit.get("camera_confidence") or 0.0)), 3)
        found["camera_quality"] = round(max(float(found.get("camera_quality") or 0.0), float(hit.get("camera_quality") or 0.0)), 3)
        found["camera_semantic_support"] = round(max(float(found.get("camera_semantic_support") or 0.0), float(hit.get("camera_semantic_support") or 0.0)), 3)
        found["camera_support_hits"] = int(found.get("camera_support_hits") or 0) + int(hit.get("camera_support_hits") or 0)
        bearing_deltas = [value for value in (found.get("camera_bearing_delta_deg"), hit.get("camera_bearing_delta_deg")) if value is not None]
        found["camera_bearing_delta_deg"] = round(min(float(value) for value in bearing_deltas), 3) if bearing_deltas else None
        sync_deltas = [value for value in (found.get("camera_sync_delta_s"), hit.get("camera_sync_delta_s")) if value is not None]
        found["camera_sync_delta_s"] = round(min(float(value) for value in sync_deltas), 4) if sync_deltas else None
        if source == "camera":
            calibrated_hits = int(found.get("calibrated_hits") or 0) + (added_hits if hit_is_metric_calibrated else 0)
            uncalibrated_hits = int(found.get("uncalibrated_hits") or 0) + (0 if hit_is_metric_calibrated else added_hits)
            found["calibrated_hits"] = calibrated_hits
            found["uncalibrated_hits"] = uncalibrated_hits
            found["metric_provenance_consistent"] = calibrated_hits == hits_count or uncalibrated_hits == hits_count
            found["metric_calibrated"] = calibrated_hits == hits_count and uncalibrated_hits == 0
            found["metric_anchor"] = "camera_calibrated" if found["metric_calibrated"] else "none"
            found["position_source"] = (
                "camera_calibrated_projection" if found["metric_calibrated"] else "camera_monocular_uncalibrated"
            )
        else:
            found["metric_calibrated"] = bool(found.get("metric_calibrated") and hit.get("metric_calibrated"))
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
    return filtered


def fuse_tree_maps(lidar_trees: list[dict[str, Any]], camera_trees: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fused: list[dict[str, Any]] = []
    used_camera: set[int] = set()
    for lidar in lidar_trees:
        best_index = -1
        best_distance = float("inf")
        for index, camera in enumerate(camera_trees):
            if index in used_camera:
                continue
            if not camera_metric_track_is_valid(camera):
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
        if not camera_metric_track_is_valid(camera) or confidence < 0.9 or int(camera.get("hits") or 0) < 5:
            continue
        fused.append(
            {
                **camera,
                "confidence": round(confidence, 3),
                "fusion_source": "camera_only_calibrated",
                "fusion_weight_lidar": 0.0,
                "fusion_weight_camera": 1.0,
                "metric_anchor": "camera_calibrated",
                "position_source": "camera_calibrated_projection",
                "match_error_m": None,
            }
        )
    fused.sort(key=lambda item: (-(int(item.get("hits") or 0)), -float(item.get("confidence") or 0.0)))
    return fused


def camera_metric_track_is_valid(camera: dict[str, Any]) -> bool:
    """Require every observation in a camera-only metric track to be validated."""

    hits = int(camera.get("hits") or 0)
    calibrated_hits = int(camera.get("calibrated_hits") or 0)
    uncalibrated_hits = int(camera.get("uncalibrated_hits") or 0)
    return (
        hits > 0
        and bool(camera.get("metric_calibrated"))
        and bool(camera.get("metric_provenance_consistent"))
        and calibrated_hits == hits
        and uncalibrated_hits == 0
    )


def spatial_thin_trees(trees: list[dict[str, Any]], min_spacing_m: float, max_count: int | None = None) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for tree in sorted(trees, key=lambda item: (-(int(item.get("hits") or 0)), -float(item.get("confidence") or 0.0))):
        try:
            x = float(tree["x_m"])
            y = float(tree["y_m"])
        except (KeyError, TypeError, ValueError):
            continue
        if any(
            math.hypot(x - float(other["x_m"]), y - float(other["y_m"])) < min_spacing_m
            and not (
                tree.get("source") == "camera"
                and other.get("source") == "camera"
                and bool(tree.get("metric_calibrated")) != bool(other.get("metric_calibrated"))
            )
            for other in kept
        ):
            continue
        kept.append(tree)
        if max_count is not None and len(kept) >= max_count:
            break
    kept.sort(key=lambda item: (float(item.get("first_seen_s") or 0.0), float(item.get("x_m") or 0.0)))
    return kept


def fuse_tree_pair(lidar: dict[str, Any], camera: dict[str, Any] | None, match_error_m: float | None) -> dict[str, Any]:
    if camera is not None and not camera_metric_track_is_valid(camera):
        camera = None
        match_error_m = None
    lidar_conf = float(lidar.get("confidence") or lidar.get("lidar_confidence") or 0.0)
    camera_conf = float(lidar.get("camera_confidence") or 0.0)
    camera_quality = float(lidar.get("camera_quality") or 0.0)
    if camera:
        camera_conf = max(camera_conf, float(camera.get("confidence") or camera.get("camera_confidence") or 0.0))
        camera_quality = max(camera_quality, 0.9 if camera.get("metric_calibrated") else 0.0)
    support_hits = int(lidar.get("camera_support_hits") or 0) + (int(camera.get("hits") or 0) if camera else 0)
    semantic_support = clamp(max(float(lidar.get("camera_semantic_support") or 0.0), camera_conf * camera_quality), 0.0, 1.0)
    semantic_camera_weight = CAMERA_TREE_WEIGHT * semantic_support if support_hits > 0 else 0.0
    semantic_lidar_weight = 1.0 - semantic_camera_weight
    confidence = lidar_conf + (1.0 - lidar_conf) * 0.42 * semantic_support
    fusion_source = "lidar_metric_anchor_camera_semantic_support" if semantic_support > 0.0 and support_hits > 0 else "lidar_metric_anchor_only"
    return {
        **lidar,
        "x_m": round(float(lidar["x_m"]), 3),
        "y_m": round(float(lidar["y_m"]), 3),
        "confidence": round(confidence, 3),
        "lidar_confidence": round(lidar_conf, 3),
        "camera_confidence": round(camera_conf, 3),
        "camera_quality": round(camera_quality, 3),
        "camera_semantic_support": round(semantic_support, 3),
        "camera_support_hits": support_hits,
        "kind": "fused_tree",
        "source": "lidar_metric_camera_semantics",
        "fusion_source": fusion_source,
        "fusion_weight_lidar": 1.0,
        "fusion_weight_camera": 0.0,
        "semantic_weight_lidar": round(semantic_lidar_weight, 3),
        "semantic_weight_camera": round(semantic_camera_weight, 3),
        "metric_anchor": "lidar",
        "position_source": "lidar_metric_anchor",
        "match_error_m": round(match_error_m, 3) if match_error_m is not None else None,
        "components": {
            "lidar": {"x_m": lidar.get("x_m"), "y_m": lidar.get("y_m"), "hits": lidar.get("hits")},
            "camera": {"x_m": camera.get("x_m"), "y_m": camera.get("y_m"), "hits": camera.get("hits")} if camera else None,
        },
    }


def orchard_map_readiness(
    lidar_frames: list[dict[str, Any]],
    camera_frames: list[dict[str, Any]],
    fusion_trees: list[dict[str, Any]],
    centerline: dict[str, Any],
) -> dict[str, Any]:
    """Keep computation success separate from evidence and navigation readiness."""
    nonempty_lidar_frames = sum(1 for frame in lidar_frames if frame.get("points"))
    data_reasons: list[str] = []
    if len(lidar_frames) < MIN_DATA_LIDAR_FRAMES:
        data_reasons.append("insufficient_lidar_frames")
    if nonempty_lidar_frames < MIN_DATA_NONEMPTY_LIDAR_FRAMES:
        data_reasons.append("insufficient_nonempty_lidar_frames")
    data_sufficiency_ok = not data_reasons
    navigation_reasons = list(data_reasons)
    if not centerline.get("ok") or len(centerline.get("points") or []) < 2:
        navigation_reasons.append("centerline_not_ready")
    navigation_ready = data_sufficiency_ok and not navigation_reasons
    return {
        "policy": "implementation_success_is_not_research_or_navigation_readiness",
        "data_sufficiency_ok": data_sufficiency_ok,
        "navigation_ready": navigation_ready,
        "observed": {
            "lidar_frames": len(lidar_frames),
            "nonempty_lidar_frames": nonempty_lidar_frames,
            "camera_frames": len(camera_frames),
            "fusion_metric_landmarks": len(fusion_trees),
            "centerline_points": len(centerline.get("points") or []),
        },
        "minimum": {
            "lidar_frames": MIN_DATA_LIDAR_FRAMES,
            "nonempty_lidar_frames": MIN_DATA_NONEMPTY_LIDAR_FRAMES,
            "centerline_points_for_navigation": 2,
        },
        "data_sufficiency_reasons": data_reasons,
        "navigation_readiness_reasons": navigation_reasons,
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
    trajectory = trajectory_segments(path)
    assigned = assign_trees_to_trajectory_segments(trees, trajectory)
    segment_results = [
        estimate_segment_centerline(assigned.get(segment["id"], []), segment, layer_id)
        for segment in trajectory
    ]
    ready = [segment for segment in segment_results if segment.get("ok")]
    if not ready:
        result = empty_centerline("low_confidence_fail_closed", layer_id)
        result["segments"] = segment_results
        result["segment_count"] = len(segment_results)
        return result
    selected = max(
        ready,
        key=lambda item: (
            float(item.get("selection_score") or 0.0),
            float(item.get("confidence") or 0.0),
            float(item.get("length_m") or 0.0),
            int(item.get("projected_tree_count") or 0),
        ),
    )
    return {
        **selected,
        "layer": layer_id,
        "method": "trajectory_segmented_local_frenet_robust_rows_v9",
        "selected_segment_id": selected.get("segment_id"),
        "segment_count": len(segment_results),
        "segments": segment_results,
    }


def empty_centerline(reason: str, layer_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "layer": layer_id,
        "method": "trajectory_segmented_local_frenet_robust_rows_v9",
        "points": [],
        "point_count": 0,
        "length_m": 0.0,
        "row_gap_m": None,
        "left_tree_count": 0,
        "right_tree_count": 0,
        "confidence": 0.0,
        "confidence_threshold": CENTERLINE_MIN_CONFIDENCE,
        "state": reason,
        "selected_segment_id": None,
        "segment_count": 0,
        "segments": [],
    }


def trajectory_segments(path: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(path) < 2:
        return []
    split_indices: list[int] = []
    last_split = 0
    for index in range(1, len(path) - 1):
        before_index = max(last_split, index - 3)
        after_index = min(len(path) - 1, index + 3)
        before = path[before_index]
        pivot = path[index]
        after = path[after_index]
        before_dx = float(pivot.get("x_m") or 0.0) - float(before.get("x_m") or 0.0)
        before_dy = float(pivot.get("y_m") or 0.0) - float(before.get("y_m") or 0.0)
        after_dx = float(after.get("x_m") or 0.0) - float(pivot.get("x_m") or 0.0)
        after_dy = float(after.get("y_m") or 0.0) - float(pivot.get("y_m") or 0.0)
        before_distance = math.hypot(before_dx, before_dy)
        after_distance = math.hypot(after_dx, after_dy)
        turn_deg = 0.0
        if before_distance >= 0.35 and after_distance >= 0.35:
            turn_deg = abs(wrapped_angle_deg(math.degrees(math.atan2(after_dy, after_dx)) - math.degrees(math.atan2(before_dy, before_dx))))
        distance_since_split = path_distance(path[last_split : index + 1])
        explicit_turn = bool(pivot.get("turn_event")) and (turn_deg >= 25.0 or distance_since_split >= 5.0)
        geometric_turn = turn_deg >= 62.0
        if distance_since_split >= 3.5 and (explicit_turn or geometric_turn) and index - last_split >= 2:
            split_indices.append(index)
            last_split = index

    starts = [0] + split_indices
    ends = split_indices + [len(path) - 1]
    rows: list[dict[str, Any]] = []
    for start, end in zip(starts, ends):
        segment_path = path[start : end + 1]
        if len(segment_path) < 2:
            continue
        rows.append(
            {
                "id": f"trajectory_segment_{len(rows)}",
                "start_index": start,
                "end_index": end,
                "path": segment_path,
                "path_length_m": round(path_distance(segment_path), 3),
            }
        )
    return rows


def assign_trees_to_trajectory_segments(
    trees: list[dict[str, Any]], segments: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    assigned = {str(segment["id"]): [] for segment in segments}
    for tree in trees:
        try:
            x = float(tree["x_m"])
            y = float(tree["y_m"])
        except (KeyError, TypeError, ValueError):
            continue
        best_segment: dict[str, Any] | None = None
        best_projection: dict[str, float] | None = None
        for segment in segments:
            projection = project_point_to_path(x, y, segment["path"])
            if projection is None:
                continue
            if best_projection is None or projection["distance_m"] < best_projection["distance_m"]:
                best_segment = segment
                best_projection = projection
        if best_segment is None or best_projection is None or best_projection["distance_m"] > 8.5:
            continue
        assigned[str(best_segment["id"])].append({"tree": tree, **best_projection})
    return assigned


def estimate_segment_centerline(
    projected: list[dict[str, Any]], segment: dict[str, Any], layer_id: str
) -> dict[str, Any]:
    usable = [
        {**item, "weight": max(0.2, float(item["tree"].get("confidence") or 0.5))}
        for item in projected
        if 0.30 <= abs(float(item.get("lateral_m") or 0.0)) <= 8.5
    ]
    if len(usable) < 4:
        return segment_centerline_failure(segment, "insufficient_tree_rows", len(usable))

    bin_size = 2.5
    bins: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for item in usable:
        key = int(math.floor(float(item["s_m"]) / bin_size))
        side = "left" if float(item["lateral_m"]) >= 0.0 else "right"
        bins.setdefault(key, {"left": [], "right": []})[side].append(item)
    side_centers: dict[int, dict[str, float | None]] = {}
    for key, sides in bins.items():
        side_centers[key] = {
            "left": robust_nearest_row_location(sides["left"]),
            "right": robust_nearest_row_location(sides["right"]),
        }

    left_keys = sorted(key for key, sides in side_centers.items() if sides["left"] is not None and float(sides["left"]) > 0.0)
    right_keys = sorted(key for key, sides in side_centers.items() if sides["right"] is not None and float(sides["right"]) < 0.0)
    left_span = (left_keys[-1] - left_keys[0]) * bin_size if len(left_keys) >= 2 else 0.0
    right_span = (right_keys[-1] - right_keys[0]) * bin_size if len(right_keys) >= 2 else 0.0
    overlap_min_key = max(left_keys[0], right_keys[0]) if left_keys and right_keys else None
    overlap_max_key = min(left_keys[-1], right_keys[-1]) if left_keys and right_keys else None
    overlap_span = (
        max(0.0, float(overlap_max_key - overlap_min_key) * bin_size)
        if overlap_min_key is not None and overlap_max_key is not None
        else 0.0
    )
    bilateral_coverage = overlap_span / max(bin_size, min(left_span, right_span)) if left_span > 0.0 and right_span > 0.0 else 0.0
    bilateral_diagnostics = {
        "straddles_vehicle": bool(left_keys and right_keys),
        "left_occupied_bins": len(left_keys),
        "right_occupied_bins": len(right_keys),
        "left_longitudinal_span_m": round(left_span, 3),
        "right_longitudinal_span_m": round(right_span, 3),
        "bilateral_overlap_span_m": round(overlap_span, 3),
        "bilateral_coverage_ratio": round(bilateral_coverage, 3),
        "paired_gap_count": 0,
        "paired_candidate_count": 0,
        "paired_run_count": 0,
        "contiguous_paired_bin_count": 0,
        "max_consecutive_paired_bin_gap": None,
        "candidate_max_consecutive_paired_bin_gap": None,
        "paired_bin_density_ratio": 0.0,
        "paired_station_span_m": 0.0,
        "paired_station_coverage_ratio": 0.0,
        "paired_interval_s_m": None,
    }
    if len(left_keys) < CENTERLINE_MIN_SIDE_BINS or len(right_keys) < CENTERLINE_MIN_SIDE_BINS:
        return segment_centerline_failure(segment, "insufficient_bilateral_occupied_bins", len(usable), bilateral_diagnostics)
    if left_span < CENTERLINE_MIN_SIDE_SPAN_M or right_span < CENTERLINE_MIN_SIDE_SPAN_M:
        return segment_centerline_failure(segment, "insufficient_bilateral_longitudinal_span", len(usable), bilateral_diagnostics)
    if overlap_span < CENTERLINE_MIN_BILATERAL_OVERLAP_M or bilateral_coverage < CENTERLINE_MIN_BILATERAL_COVERAGE:
        return segment_centerline_failure(segment, "insufficient_bilateral_station_overlap", len(usable), bilateral_diagnostics)

    paired_candidates = match_bilateral_station_bins(side_centers, left_keys, right_keys, bin_size)
    paired_runs = split_paired_station_runs(
        paired_candidates,
        max_key_gap=CENTERLINE_MAX_CONSECUTIVE_PAIRED_BIN_GAP,
    )
    candidate_key_gaps = paired_station_key_gaps(paired_candidates)
    bilateral_diagnostics.update(
        {
            "paired_candidate_count": len(paired_candidates),
            "paired_run_count": len(paired_runs),
            "candidate_max_consecutive_paired_bin_gap": max(candidate_key_gaps) if candidate_key_gaps else 0,
        }
    )
    if len(paired_candidates) < CENTERLINE_MIN_PAIRED_BINS:
        bilateral_diagnostics["paired_gap_count"] = len(paired_candidates)
        bilateral_diagnostics["contiguous_paired_bin_count"] = len(paired_candidates)
        return segment_centerline_failure(segment, "insufficient_paired_bilateral_bins", len(usable), bilateral_diagnostics)
    paired = max(
        paired_runs,
        key=lambda run: (
            len(run),
            paired_run_density(run),
            float(run[-1]["station_key"]) - float(run[0]["station_key"]),
            -float(run[0]["station_key"]),
        ),
        default=[],
    )
    selected_key_gaps = paired_station_key_gaps(paired)
    contiguous_count = len(paired)
    max_selected_gap = max(selected_key_gaps) if selected_key_gaps else 0
    density = paired_run_density(paired)
    bilateral_diagnostics.update(
        {
            "paired_gap_count": contiguous_count,
            "contiguous_paired_bin_count": contiguous_count,
            "max_consecutive_paired_bin_gap": max_selected_gap,
            "paired_bin_density_ratio": round(density, 3),
        }
    )
    if contiguous_count < CENTERLINE_MIN_CONTIGUOUS_PAIRED_BINS:
        return segment_centerline_failure(
            segment,
            "insufficient_contiguous_paired_evidence",
            len(usable),
            bilateral_diagnostics,
        )
    paired_gaps = [float(item["gap_m"]) for item in paired]
    paired_stations = [float(item["station_m"]) for item in paired]
    paired_span = max(paired_stations) - min(paired_stations)
    # Density measures holes inside the selected run. Coverage is a separate
    # geometric quantity: how much of the bilateral overlap the paired run
    # actually supports. Conflating these allowed a short dense prefix to
    # authorize a long unsupported centerline.
    paired_coverage = paired_span / max(bin_size, overlap_span)
    bilateral_diagnostics.update(
        {
            "paired_station_span_m": round(paired_span, 3),
            "paired_station_coverage_ratio": round(paired_coverage, 3),
            "paired_interval_s_m": [round(min(paired_stations), 3), round(max(paired_stations), 3)],
        }
    )
    if (
        max_selected_gap > CENTERLINE_MAX_CONSECUTIVE_PAIRED_BIN_GAP
        or paired_span < CENTERLINE_MIN_PAIRED_STATION_SPAN_M
        or paired_coverage < CENTERLINE_MIN_PAIRED_COVERAGE
        or density < CENTERLINE_MIN_PAIRED_BIN_DENSITY
    ):
        return segment_centerline_failure(segment, "insufficient_paired_station_coverage", len(usable), bilateral_diagnostics)
    row_gap = robust_weighted_location(paired_gaps, [1.0] * len(paired_gaps))
    if row_gap is None:
        return segment_centerline_failure(segment, "row_gap_unavailable", len(usable), bilateral_diagnostics)
    gap_mad = median_absolute_deviation(paired_gaps, row_gap)

    candidate_points: list[dict[str, Any]] = []
    for item in paired:
        candidate_points.append(
            centerline_point_at_station(
                segment,
                float(item["station_m"]),
                (float(item["left_m"]) + float(item["right_m"])) / 2.0,
                "paired_rows",
            )
        )
    pair_station_keys = sorted(float(item["station_key"]) for item in paired)
    paired_left_keys = {int(item["left_key"]) for item in paired}
    paired_right_keys = {int(item["right_key"]) for item in paired}
    for key in sorted(side_centers):
        if key in paired_left_keys or key in paired_right_keys:
            continue
        station_key = float(key)
        before = [value for value in pair_station_keys if value < station_key]
        after = [value for value in pair_station_keys if value > station_key]
        if not before or not after or min(after) - max(before) > CENTERLINE_MAX_HOLE_BINS + 1:
            continue
        left = side_centers[key]["left"]
        right = side_centers[key]["right"]
        if (left is None) == (right is None):
            continue
        if left is not None and float(left) > 0.0:
            center_lateral = float(left) - row_gap / 2.0
            source = "left_small_hole_projection"
        elif right is not None and float(right) < 0.0:
            center_lateral = float(right) + row_gap / 2.0
            source = "right_small_hole_projection"
        else:
            continue
        station = (key + 0.5) * bin_size
        if station < min(paired_stations) or station > max(paired_stations):
            continue
        candidate_points.append(centerline_point_at_station(segment, station, center_lateral, source))
    candidate_points = [point for point in candidate_points if point]
    candidate_points.sort(key=lambda point: float(point["s_m"]))
    candidate_points = smooth_centerline_points(candidate_points)
    confidence = segment_centerline_confidence(candidate_points, usable, paired_gaps, row_gap, gap_mad, segment["path"])
    ready = len(candidate_points) >= 2 and confidence >= CENTERLINE_MIN_CONFIDENCE and gap_mad <= max(0.9, row_gap * 0.25)
    points = candidate_points if ready else []
    candidate_length = polyline_length(candidate_points)
    selection_score = confidence * (0.25 + 0.75 * min(1.0, candidate_length / 10.0)) if ready else 0.0
    return {
        "ok": ready,
        "layer": layer_id,
        "method": "trajectory_segmented_local_frenet_robust_rows_v9",
        "segment_id": segment["id"],
        "start_index": segment["start_index"],
        "end_index": segment["end_index"],
        "path_length_m": segment["path_length_m"],
        "points": points,
        "point_count": len(points),
        "candidate_point_count": len(candidate_points),
        "length_m": round(polyline_length(points), 3),
        "row_gap_m": round(row_gap, 3),
        "row_gap_mad_m": round(gap_mad, 3),
        "left_tree_count": sum(1 for item in usable if float(item["lateral_m"]) > 0.0),
        "right_tree_count": sum(1 for item in usable if float(item["lateral_m"]) < 0.0),
        "projected_tree_count": len(usable),
        **bilateral_diagnostics,
        "confidence": confidence,
        "selection_score": round(selection_score, 3),
        "confidence_threshold": CENTERLINE_MIN_CONFIDENCE,
        "state": "ready" if ready else "low_centerline_confidence",
        "fail_closed": not ready,
    }


def match_bilateral_station_bins(
    side_centers: dict[int, dict[str, float | None]],
    left_keys: list[int],
    right_keys: list[int],
    bin_size: float,
) -> list[dict[str, float | int]]:
    paired: list[dict[str, float | int]] = []
    used_right: set[int] = set()
    for left_key in left_keys:
        left = float(side_centers[left_key]["left"] or 0.0)
        candidates = [left_key] if left_key in right_keys and left_key not in used_right else []
        for right_key in candidates:
            right = float(side_centers[right_key]["right"] or 0.0)
            gap = left - right
            if left <= 0.0 or right >= 0.0 or not (CENTERLINE_ROW_GAP_MIN_M <= gap <= CENTERLINE_ROW_GAP_MAX_M):
                continue
            station_key = (left_key + right_key) / 2.0
            paired.append(
                {
                    "left_key": left_key,
                    "right_key": right_key,
                    "left_m": left,
                    "right_m": right,
                    "gap_m": gap,
                    "station_key": station_key,
                    "station_m": (station_key + 0.5) * bin_size,
                }
            )
            used_right.add(right_key)
            break
    return paired


def paired_station_key_gaps(paired: list[dict[str, float | int]]) -> list[int]:
    keys = sorted(int(round(float(item["station_key"]))) for item in paired)
    return [second - first for first, second in zip(keys, keys[1:])]


def paired_run_density(paired: list[dict[str, float | int]]) -> float:
    if not paired:
        return 0.0
    keys = sorted(int(round(float(item["station_key"]))) for item in paired)
    occupied_span_bins = keys[-1] - keys[0] + 1
    return len(keys) / max(1, occupied_span_bins)


def split_paired_station_runs(
    paired: list[dict[str, float | int]],
    max_key_gap: int,
) -> list[list[dict[str, float | int]]]:
    ordered = sorted(paired, key=lambda item: float(item["station_key"]))
    runs: list[list[dict[str, float | int]]] = []
    for item in ordered:
        if (
            not runs
            or float(item["station_key"]) - float(runs[-1][-1]["station_key"])
            > max_key_gap
        ):
            runs.append([item])
        else:
            runs[-1].append(item)
    return runs


def centerline_point_at_station(
    segment: dict[str, Any], station: float, center_lateral: float, source: str
) -> dict[str, Any] | None:
    station = clamp(station, 0.0, float(segment.get("path_length_m") or 0.0))
    basis = point_at_path_distance(segment["path"], station)
    if basis is None:
        return None
    x = basis["x_m"] + basis["normal_x"] * center_lateral
    y = basis["y_m"] + basis["normal_y"] * center_lateral
    return {
        "x_m": round(x, 3),
        "y_m": round(y, 3),
        "s_m": round(station, 3),
        "lateral_m": round(center_lateral, 3),
        "source": source,
        "segment_id": segment["id"],
    }


def segment_centerline_failure(
    segment: dict[str, Any], reason: str, projected_tree_count: int, diagnostics: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "ok": False,
        "method": "trajectory_segmented_local_frenet_robust_rows_v9",
        "segment_id": segment["id"],
        "start_index": segment["start_index"],
        "end_index": segment["end_index"],
        "path_length_m": segment["path_length_m"],
        "points": [],
        "point_count": 0,
        "candidate_point_count": 0,
        "length_m": 0.0,
        "row_gap_m": None,
        "row_gap_mad_m": None,
        "left_tree_count": 0,
        "right_tree_count": 0,
        "projected_tree_count": projected_tree_count,
        "paired_gap_count": 0,
        "paired_candidate_count": 0,
        "paired_run_count": 0,
        "contiguous_paired_bin_count": 0,
        "max_consecutive_paired_bin_gap": None,
        "candidate_max_consecutive_paired_bin_gap": None,
        "paired_bin_density_ratio": 0.0,
        "confidence": 0.0,
        "selection_score": 0.0,
        "confidence_threshold": CENTERLINE_MIN_CONFIDENCE,
        "state": reason,
        "fail_closed": True,
        **(diagnostics or {}),
    }


def project_point_to_path(x: float, y: float, path: list[dict[str, Any]]) -> dict[str, float] | None:
    best: dict[str, float] | None = None
    cumulative = 0.0
    for first, second in zip(path, path[1:]):
        ax = float(first.get("x_m") or 0.0)
        ay = float(first.get("y_m") or 0.0)
        bx = float(second.get("x_m") or 0.0)
        by = float(second.get("y_m") or 0.0)
        dx = bx - ax
        dy = by - ay
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        tangent_x = dx / length
        tangent_y = dy / length
        ratio = clamp(((x - ax) * dx + (y - ay) * dy) / (length * length), 0.0, 1.0)
        projected_x = ax + ratio * dx
        projected_y = ay + ratio * dy
        normal_x = -tangent_y
        normal_y = tangent_x
        lateral = (x - projected_x) * normal_x + (y - projected_y) * normal_y
        distance = math.hypot(x - projected_x, y - projected_y)
        candidate = {
            "s_m": cumulative + ratio * length,
            "lateral_m": lateral,
            "distance_m": distance,
            "path_x_m": projected_x,
            "path_y_m": projected_y,
            "tangent_x": tangent_x,
            "tangent_y": tangent_y,
        }
        if best is None or distance < best["distance_m"]:
            best = candidate
        cumulative += length
    return best


def point_at_path_distance(path: list[dict[str, Any]], station: float) -> dict[str, float] | None:
    cumulative = 0.0
    for first, second in zip(path, path[1:]):
        ax = float(first.get("x_m") or 0.0)
        ay = float(first.get("y_m") or 0.0)
        bx = float(second.get("x_m") or 0.0)
        by = float(second.get("y_m") or 0.0)
        dx = bx - ax
        dy = by - ay
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        if cumulative + length >= station:
            ratio = clamp((station - cumulative) / length, 0.0, 1.0)
            tangent_x = dx / length
            tangent_y = dy / length
            return {
                "x_m": ax + ratio * dx,
                "y_m": ay + ratio * dy,
                "normal_x": -tangent_y,
                "normal_y": tangent_x,
            }
        cumulative += length
    if not path:
        return None
    last = path[-1]
    heading = math.radians(float(last.get("heading_deg") or 0.0))
    tangent_x = math.sin(heading)
    tangent_y = math.cos(heading)
    return {
        "x_m": float(last.get("x_m") or 0.0),
        "y_m": float(last.get("y_m") or 0.0),
        "normal_x": -tangent_y,
        "normal_y": tangent_x,
    }


def path_distance(path: list[dict[str, Any]]) -> float:
    return sum(
        math.hypot(
            float(second.get("x_m") or 0.0) - float(first.get("x_m") or 0.0),
            float(second.get("y_m") or 0.0) - float(first.get("y_m") or 0.0),
        )
        for first, second in zip(path, path[1:])
    )


def wrapped_angle_deg(value: float) -> float:
    return ((value + 180.0) % 360.0) - 180.0


def weighted_average(values: list[float], weights: list[float]) -> float | None:
    if not values:
        return None
    total = sum(max(0.001, weight) for weight in weights)
    return sum(value * max(0.001, weight) for value, weight in zip(values, weights)) / total


def robust_weighted_location(values: list[float], weights: list[float]) -> float | None:
    if not values:
        return None
    estimate = weighted_median(values, weights)
    for _ in range(3):
        residuals = [abs(value - estimate) for value in values]
        scale = max(0.12, (median(residuals) or 0.0) * 1.4826)
        robust_weights = [
            max(0.001, weight) * min(1.0, (1.5 * scale) / max(1e-6, residual))
            for value, weight, residual in zip(values, weights, residuals)
        ]
        updated = weighted_average(values, robust_weights)
        if updated is None:
            break
        estimate = updated
    return estimate


def robust_nearest_row_location(items: list[dict[str, Any]], cluster_width_m: float = 1.25) -> float | None:
    if not items:
        return None
    nearest = min(abs(float(item["lateral_m"])) for item in items)
    cluster = [item for item in items if abs(abs(float(item["lateral_m"])) - nearest) <= cluster_width_m]
    return robust_weighted_location(
        [float(item["lateral_m"]) for item in cluster],
        [float(item.get("weight") or 0.2) for item in cluster],
    )


def weighted_median(values: list[float], weights: list[float]) -> float:
    ordered = sorted(zip(values, weights), key=lambda item: item[0])
    threshold = sum(max(0.001, weight) for _, weight in ordered) / 2.0
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += max(0.001, weight)
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def median_absolute_deviation(values: list[float], center: float | None = None) -> float:
    if not values:
        return float("inf")
    reference = float(center if center is not None else median(values) or 0.0)
    return float(median([abs(value - reference) for value in values]) or 0.0)


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


def segment_centerline_confidence(
    points: list[dict[str, Any]],
    projected: list[dict[str, Any]],
    paired_gaps: list[float],
    row_gap: float,
    gap_mad: float,
    path: list[dict[str, Any]],
) -> float:
    if len(points) < 2:
        return 0.0
    tree_term = min(1.0, len(projected) / 14.0)
    paired_term = min(1.0, len(paired_gaps) / 4.0)
    left = sum(1 for item in projected if float(item["lateral_m"]) >= 0.0)
    right = len(projected) - left
    balance_term = 2.0 * min(left, right) / max(1, left + right)
    consistency_term = clamp(1.0 - gap_mad / max(0.45, row_gap * 0.25), 0.0, 1.0)
    length_term = min(1.0, polyline_length(points) / 10.0)
    pose_values = [clamp(float(item.get("pose_confidence") or 0.0), 0.0, 1.0) for item in path]
    pose_term = sum(pose_values) / len(pose_values) if pose_values else 0.0
    return round(
        0.20 * tree_term
        + 0.24 * paired_term
        + 0.18 * balance_term
        + 0.18 * consistency_term
        + 0.12 * length_term
        + 0.08 * pose_term,
        3,
    )


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def camera_observation_index(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for frame in frames:
        support = estimate_camera_orchard_support(frame)
        fov_x_rad, intrinsics_calibrated, intrinsics_source = camera_horizontal_fov(frame)
        image = support.get("image") if isinstance(support.get("image"), dict) else {}
        if not image:
            frame_image = frame.get("image") if isinstance(frame.get("image"), dict) else {}
            image = {
                "width": int(frame_image.get("width") or frame.get("width") or 1280),
                "height": int(frame_image.get("height") or frame.get("height") or 720),
            }
        fx_px, cx_px, projection_source = camera_projection_intrinsics(frame, int(image.get("width") or 1280), fov_x_rad)
        rows.append(
            {
                "t_s": float(frame.get("t_s") or 0.0),
                **support,
                "image": image,
                "fov_x_rad": fov_x_rad,
                "fov_x_deg": round(math.degrees(fov_x_rad), 3),
                "intrinsics_calibrated": intrinsics_calibrated,
                "intrinsics_source": intrinsics_source,
                "fx_px": round(fx_px, 6),
                "cx_px": round(cx_px, 6),
                "projection_intrinsics_source": projection_source,
            }
        )
    return rows


def camera_horizontal_fov(frame: dict[str, Any]) -> tuple[float, bool, str]:
    camera_info = frame.get("camera_info") if isinstance(frame.get("camera_info"), dict) else {}
    matrix = camera_info.get("k") or camera_info.get("K") or []
    image = frame.get("image") if isinstance(frame.get("image"), dict) else {}
    width = float(camera_info.get("width") or image.get("width") or frame.get("width") or 0.0)
    try:
        focal_x = float(matrix[0]) if len(matrix) >= 1 else float(camera_info.get("fx") or 0.0)
        principal_x = float(matrix[2]) if len(matrix) >= 3 else float(camera_info.get("cx") or width / 2.0)
    except (TypeError, ValueError):
        focal_x = 0.0
        principal_x = width / 2.0
    if width > 0.0 and focal_x > 0.0:
        left = math.atan((0.0 - principal_x) / focal_x)
        right = math.atan((width - principal_x) / focal_x)
        validated = camera_info.get("calibration_valid") is True
        source = "timeline_camera_info_validated" if validated else "camera_info_nominal_or_unverified"
        return right - left, validated, source
    return CAMERA_FOV_X_RAD, CAMERA_INTRINSICS_CALIBRATED, "nominal_70deg_uncalibrated"


def camera_projection_intrinsics(frame: dict[str, Any], output_width: int, fov_x_rad: float) -> tuple[float, float, str]:
    camera_info = frame.get("camera_info") if isinstance(frame.get("camera_info"), dict) else {}
    matrix = camera_info.get("k") or camera_info.get("K") or []
    try:
        source_width = float(camera_info.get("width") or output_width)
        source_fx = float(matrix[0]) if len(matrix) >= 1 else float(camera_info.get("fx") or 0.0)
        source_cx = float(matrix[2]) if len(matrix) >= 3 else float(camera_info.get("cx") or source_width / 2.0)
    except (TypeError, ValueError):
        source_fx = 0.0
        source_cx = 0.0
        source_width = float(output_width)
    if source_fx > 0.0 and source_width > 0.0:
        scale = float(output_width) / source_width
        source = (
            "validated_camera_info_scaled_to_overlay"
            if camera_info.get("calibration_valid") is True
            else "camera_info_nominal_or_unverified_scaled_to_overlay"
        )
        return source_fx * scale, source_cx * scale, source
    focal_x = float(output_width) / (2.0 * math.tan(fov_x_rad / 2.0))
    return focal_x, float(output_width) / 2.0, "nominal_fov_center_principal_point"


def nearest_camera_support(
    index: list[dict[str, Any]], t_s: float, max_delta_s: float = CAMERA_SYNC_MAX_DELTA_S
) -> dict[str, Any]:
    if not index:
        return {
            "matched": False,
            "state": "camera_unavailable",
            "lidar_t_s": round(t_s, 3),
            "max_delta_s": max_delta_s,
            "sync_delta_s": None,
        }
    nearest = min(index, key=lambda item: abs(float(item.get("t_s") or 0.0) - t_s))
    camera_t_s = float(nearest.get("t_s") or 0.0)
    delta_s = abs(camera_t_s - t_s)
    matched = delta_s <= max_delta_s
    return {
        **nearest,
        "matched": matched,
        "state": "matched" if matched else "outside_sync_gate",
        "lidar_t_s": round(t_s, 3),
        "camera_t_s": round(camera_t_s, 3),
        "sync_delta_s": round(delta_s, 6),
        "max_delta_s": max_delta_s,
    }


def compact_camera_sync_diagnostic(camera_support: dict[str, Any] | None) -> dict[str, Any]:
    support = camera_support or {}
    return {
        "matched": bool(support.get("matched")),
        "state": str(support.get("state") or "camera_unavailable"),
        "camera_ok": bool(support.get("ok")),
        "camera_t_s": support.get("camera_t_s"),
        "sync_delta_s": support.get("sync_delta_s"),
        "max_delta_s": support.get("max_delta_s", CAMERA_SYNC_MAX_DELTA_S),
        "detection_count": len(support.get("detections") or []),
        "method": support.get("method"),
        "fov_x_deg": support.get("fov_x_deg", CAMERA_NOMINAL_FOV_X_DEG),
        "intrinsics_calibrated": bool(support.get("intrinsics_calibrated")),
        "intrinsics_source": support.get("intrinsics_source", "nominal_70deg_uncalibrated"),
        "fx_px": support.get("fx_px"),
        "cx_px": support.get("cx_px"),
        "projection_intrinsics_source": support.get("projection_intrinsics_source"),
    }


def camera_sync_diagnostics(frame_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    supports = [frame.get("camera_support") or {} for frame in frame_summaries]
    deltas = [float(item["sync_delta_s"]) for item in supports if item.get("sync_delta_s") is not None]
    matched_deltas = [float(item["sync_delta_s"]) for item in supports if item.get("matched") and item.get("sync_delta_s") is not None]
    matched = sum(1 for item in supports if item.get("matched"))
    outside = sum(1 for item in supports if item.get("state") == "outside_sync_gate")
    unavailable = sum(1 for item in supports if item.get("state") == "camera_unavailable")
    return {
        "policy": "nearest_camera_frame_with_max_delta_gate",
        "max_delta_s": CAMERA_SYNC_MAX_DELTA_S,
        "recommended_live_max_delta_s": CAMERA_LIVE_RECOMMENDED_SYNC_MAX_DELTA_S,
        "lidar_frames": len(supports),
        "matched_frames": matched,
        "outside_gate_frames": outside,
        "camera_unavailable_frames": unavailable,
        "match_ratio": round(matched / len(supports), 4) if supports else 0.0,
        "nearest_delta_mean_s": round(sum(deltas) / len(deltas), 6) if deltas else None,
        "nearest_delta_p95_s": round(percentile(deltas, 0.95), 6) if deltas else None,
        "nearest_delta_max_s": round(max(deltas), 6) if deltas else None,
        "matched_delta_p95_s": round(percentile(matched_deltas, 0.95), 6) if matched_deltas else None,
        "matched_delta_max_s": round(max(matched_deltas), 6) if matched_deltas else None,
    }


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = clamp(quantile, 0.0, 1.0) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    ratio = position - lower
    return ordered[lower] * (1.0 - ratio) + ordered[upper] * ratio


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
