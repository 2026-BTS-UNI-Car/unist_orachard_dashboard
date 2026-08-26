from __future__ import annotations

import math
import re
from typing import Any

from dashboard_server.camera_analysis import (
    DEFAULT_VISION_INFERENCE_JPEG_QUALITY,
    VISION_INFERENCE_INPUT_POLICY_REVISION,
    current_source_native_camera_overlay,
    estimate_camera_orchard_support,
)
from dashboard_server.lidar_analysis import ALGORITHM_NAME, LIDAR_COORDINATE_FRAME, detect_orchard_trees, is_rear_person_zone
from dashboard_server.lidar_odometry import ODOMETRY_ALGORITHM, estimate_relative_transform


MAP_ALGORITHM_NAME = "orchard_metric_anchor_semantic_fusion_local_frenet_v10"
MAP_CACHE_REVISION = "v10_rep103_robust_tracks_unique_semantics_row_model_native_input_r3"
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
LOCAL_ROW_MIN_SIDE_SUPPORT = 3
LOCAL_ROW_MIN_LONGITUDINAL_SPAN_M = 3.0
LOCAL_ROW_MAX_RESIDUAL_M = 0.45
LOCAL_ROW_MAX_ABS_SLOPE = 0.25
LOCAL_ROW_MAX_PARALLEL_SLOPE_DELTA = 0.16
CORRIDOR_CONSENSUS_BIN_SIZE_M = 2.0
CORRIDOR_CONSENSUS_MIN_BINS = 3
CORRIDOR_CONSENSUS_MIN_STATION_SPAN_M = 4.0
MIN_DATA_LIDAR_FRAMES = 2
MIN_DATA_NONEMPTY_LIDAR_FRAMES = 2
NAVIGATION_REQUIRED_VALIDATION_FLAGS = (
    "ground_truth_validated",
    "lidar_base_extrinsics_validated",
    "camera_intrinsics_validated",
    "camera_lidar_extrinsics_validated",
    "trajectory_pose_validated",
    "full_rate_replay_validated",
)
NAVIGATION_VALIDATION_CONTRACT = "externally_validated_self_attestation"
CAMERA_SEMANTIC_NEGATIVE_TOKENS = {
    "building",
    "column",
    "fence",
    "furniture",
    "human",
    "person",
    "pole",
    "post",
    "road",
    "wall",
}


def current_orchard_map_cache(payload: dict[str, Any]) -> bool:
    orchard_map = payload.get("map") if isinstance(payload.get("map"), dict) else {}
    camera = orchard_map.get("camera") if isinstance(orchard_map.get("camera"), dict) else {}
    provenance = camera.get("input_provenance") if isinstance(camera.get("input_provenance"), dict) else {}
    count_keys = ("frame_count", "accepted_frame_count", "rejected_frame_count")
    counts_are_integers = all(
        isinstance(provenance.get(key), int) and not isinstance(provenance.get(key), bool)
        for key in count_keys
    )
    total = int(provenance.get("frame_count") or 0) if counts_are_integers else -1
    accepted = int(provenance.get("accepted_frame_count") or 0) if counts_are_integers else -1
    rejected = int(provenance.get("rejected_frame_count") or 0) if counts_are_integers else -1
    return bool(
        payload.get("ok")
        and orchard_map.get("ok")
        and orchard_map.get("algorithm") == MAP_ALGORITHM_NAME
        and orchard_map.get("cache_revision") == MAP_CACHE_REVISION
        and provenance.get("enforced") is True
        and provenance.get("required_revision") == VISION_INFERENCE_INPUT_POLICY_REVISION
        and provenance.get("required_mode") == "source_native"
        and isinstance(provenance.get("required_requested_width"), int)
        and not isinstance(provenance.get("required_requested_width"), bool)
        and provenance.get("required_requested_width") == 0
        and isinstance(provenance.get("required_jpeg_quality"), int)
        and not isinstance(provenance.get("required_jpeg_quality"), bool)
        and provenance.get("required_jpeg_quality") == DEFAULT_VISION_INFERENCE_JPEG_QUALITY
        and provenance.get("legacy_preview_as_native_allowed") is False
        and counts_are_integers
        and total >= 0
        and accepted >= 0
        and rejected >= 0
        and accepted + rejected == total
    )


def build_orchard_map(timeline: dict[str, Any]) -> dict[str, Any]:
    lidar_frames = timeline.get("lidar", {}).get("frames", []) or []
    camera_frames = timeline.get("camera", {}).get("frames", []) or []
    camera_index = camera_observation_index(camera_frames)
    camera_input_provenance = camera_input_provenance_summary(camera_frames)
    camera_support_by_lidar_index = pair_lidar_camera_observations(
        lidar_frames, camera_index
    )
    pose = {"x_m": 0.0, "y_m": 0.0, "theta_rad": 0.0, "theta_total_rad": 0.0}
    path: list[dict[str, Any]] = []
    previous_points: list[dict[str, Any]] = []
    previous_t_s: float | None = None
    motion_prior: dict[str, Any] | None = None
    lidar_hits: list[dict[str, Any]] = []
    local_corridor_observations: list[dict[str, Any]] = []
    camera_semantic_observation_count = 0
    camera_stable_pending_semantic_count = 0
    frame_summaries: list[dict[str, Any]] = []

    for lidar_frame_index, frame in enumerate(lidar_frames):
        t_s = float(frame.get("t_s") or 0.0)
        sensor_stamp_s = sensor_timestamp_s(frame)
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
        local_corridor = estimate_local_bilateral_corridor(
            orchard.get("trees") or [], pose, t_s
        )
        if local_corridor:
            local_corridor_observations.append(local_corridor)
        local_trees = select_map_tree_candidates(orchard.get("trees") or [])
        path.append(
            {
                "t_s": round(t_s, 3),
                "sensor_stamp_s": sensor_stamp_s,
                "x_m": round(pose["x_m"], 3),
                "y_m": round(pose["y_m"], 3),
                "heading_deg": round(math.degrees(pose["theta_rad"]), 2),
                "heading_unwrapped_deg": round(math.degrees(pose.get("theta_total_rad", pose["theta_rad"])), 2),
                "alignment_score": round(float(transform.get("score") or 0.0), 3),
                "pose_confidence": round(float(transform.get("pose_confidence") or 0.0), 3),
                "turn_event": bool(transform.get("turn_event")),
            }
        )
        camera_support = camera_support_by_lidar_index.get(lidar_frame_index)
        if camera_support is None:
            camera_support = nearest_camera_support(
                camera_index,
                t_s,
                sensor_stamp_s=sensor_stamp_s,
            )
        semantic_supports = associate_lidar_camera_semantics(local_trees, camera_support)
        camera_semantic_observation_count += sum(
            1 for support in semantic_supports if support.get("matched")
        )
        camera_stable_pending_semantic_count += sum(
            1
            for support in semantic_supports
            if support.get("semantic_admission") == "stable_pending_semantic_only"
        )
        for tree, semantic_support in zip(local_trees, semantic_supports):
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
                "local_bilateral_corridor": compact_local_corridor_diagnostic(local_corridor),
            }
        )
        previous_points = points
        previous_t_s = t_s

    if not path:
        path = [{"t_s": 0.0, "x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0, "heading_unwrapped_deg": 0.0, "alignment_score": 0.0, "pose_confidence": 0.0, "turn_event": False}]
    camera_hits = camera_global_tree_hits(
        camera_index,
        path,
        paired_support_by_lidar_index=camera_support_by_lidar_index,
    )
    lidar_trees = spatial_thin_trees(merge_global_trees(lidar_hits, frame_count=len(lidar_frames)), min_spacing_m=0.82)
    camera_trees = spatial_thin_trees(merge_global_trees(camera_hits, frame_count=len(camera_index), source="camera"), min_spacing_m=1.05)
    fusion_trees = spatial_thin_trees(fuse_tree_maps(lidar_trees, camera_trees), min_spacing_m=0.82)
    lidar_map = build_map_layer(
        "lidar",
        "LiDAR-only",
        "2D LiDAR trunk candidates only",
        lidar_trees,
        path,
        corridor_observations=local_corridor_observations,
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
        corridor_observations=local_corridor_observations,
    )
    sync_diagnostics = camera_sync_diagnostics(frame_summaries)
    readiness = orchard_map_readiness(
        lidar_frames,
        camera_frames,
        fusion_trees,
        fusion_map["centerline"],
        navigation_validation=(
            timeline.get("navigation_validation")
            if isinstance(timeline.get("navigation_validation"), dict)
            else None
        ),
        sync_diagnostics=sync_diagnostics,
    )
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
            "input_provenance": camera_input_provenance,
            "method": camera_index[0]["method"] if camera_index else "unavailable",
            "projection_model": {
                "model": "pinhole_u_equals_cx_minus_fx_tan_bearing",
                "nominal_fov_x_deg": CAMERA_NOMINAL_FOV_X_DEG,
                "nominal_intrinsics_calibrated": CAMERA_INTRINSICS_CALIBRATED,
                "policy": "use per-frame CameraInfo when present; otherwise nominal 70 degree FOV remains uncalibrated",
            },
            "sync": sync_diagnostics,
        },
        "fusion": {
            "method": "LiDAR metric-anchor fusion with original-order common-header one-to-one sync, y-forward bearing association, and uncertainty-aware camera semantic support",
            "position_policy": "LiDAR supplies metric x/y; camera changes semantic confidence only",
            "semantic_weight_policy": "dynamic_by_camera_quality_and_alignment; missing or low-quality camera evidence never penalizes LiDAR",
            "camera_only_metric_policy": "fail_closed_unless_metric_projection_is_explicitly_calibrated_and_validated",
            "tree_limit_policy": "spatial_spacing_only_no_fixed_recording_count_cap",
            "camera_association_policy": "per_frame_maximum_cardinality_minimum_cost_one_to_one",
            "sync": sync_diagnostics,
            "metric_anchor_count": sum(1 for tree in fusion_trees if tree.get("metric_anchor") == "lidar"),
            "camera_only_promoted_count": sum(1 for tree in fusion_trees if tree.get("fusion_source") == "camera_only_calibrated"),
            "semantic_supported_count": sum(1 for tree in fusion_trees if int(tree.get("camera_support_hits") or 0) > 0),
            "semantic_observation_count": camera_semantic_observation_count,
            "stable_pending_semantic_observation_count": camera_stable_pending_semantic_count,
            "raw_rejected_camera_policy": "unused_without_temporal_identity; never_metric",
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
            "temporal_policy": "one-observation-per-frame robust static tracks with spatial residual, confidence, and time-span gates",
            "local_bilateral_corridor_frames": len(local_corridor_observations),
            "landmark_quality": {
                "policy": "self_consistency_only_not_accuracy",
                "spatial_residual_p90_median": round(
                    float(median([float(tree.get("spatial_residual_p90_m") or 0.0) for tree in lidar_trees]) or 0.0),
                    3,
                ),
                "spatial_residual_p90_p95": round(
                    percentile([float(tree.get("spatial_residual_p90_m") or 0.0) for tree in lidar_trees], 0.95),
                    3,
                ),
                "spatial_residual_p90_max": round(
                    max((float(tree.get("spatial_residual_p90_m") or 0.0) for tree in lidar_trees), default=0.0),
                    3,
                ),
                "observation_hits_median": float(median([float(tree.get("hits") or 0) for tree in lidar_trees]) or 0.0),
            },
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


def estimate_local_bilateral_corridor(
    trees: list[dict[str, Any]], pose: dict[str, float], t_s: float
) -> dict[str, Any] | None:
    """Fit two independent, parallel tree rows in the current LiDAR frame.

    The global landmark map deliberately uses strict temporal confirmation and
    spatial thinning.  Those operations can erase staggered left/right row
    evidence, so this estimator keeps a separate, time-local corridor channel
    based only on wall-suppressed geometry candidates.  It never creates a tree
    landmark and never changes a LiDAR anchor position.
    """

    left_models = fit_local_row_models(trees, side=1)
    right_models = fit_local_row_models(trees, side=-1)
    pairs: list[dict[str, Any]] = []
    for left in left_models:
        for right in right_models:
            overlap_min = max(float(left["min_forward_m"]), float(right["min_forward_m"]))
            overlap_max = min(float(left["max_forward_m"]), float(right["max_forward_m"]))
            overlap_span = overlap_max - overlap_min
            if overlap_span < 2.8 or overlap_min > 2.5:
                continue
            slope_delta = abs(float(left["slope"]) - float(right["slope"]))
            if slope_delta > LOCAL_ROW_MAX_PARALLEL_SLOPE_DELTA:
                continue
            sample_forward = [0.0, overlap_min, (overlap_min + overlap_max) / 2.0, overlap_max]
            gaps = [row_model_x(left, forward) - row_model_x(right, forward) for forward in sample_forward]
            if min(gaps) < CENTERLINE_ROW_GAP_MIN_M or max(gaps) > CENTERLINE_ROW_GAP_MAX_M:
                continue
            left_at_sensor = row_model_x(left, 0.0)
            right_at_sensor = row_model_x(right, 0.0)
            if left_at_sensor <= 0.0 or right_at_sensor >= 0.0:
                continue
            center_at_sensor = (left_at_sensor + right_at_sensor) / 2.0
            row_gap = float(median(gaps) or 0.0)
            evidence_score = (
                float(left["score"])
                + float(right["score"])
                + min(8.0, overlap_span) * 0.35
                - slope_delta * 4.0
            )
            pairs.append(
                {
                    "left": left,
                    "right": right,
                    "center_at_sensor_m": center_at_sensor,
                    "row_gap_m": row_gap,
                    "overlap_span_m": overlap_span,
                    "parallel_slope_delta": slope_delta,
                    "evidence_score": evidence_score,
                }
            )
    if not pairs:
        return None
    # Prefer the nearest pair that actually bounds the sensor.  Evidence score
    # breaks ties; this prevents an outer orchard row from replacing the aisle
    # the rover currently occupies.
    selected = min(
        pairs,
        key=lambda item: (
            abs(float(item["center_at_sensor_m"])),
            float(item["row_gap_m"]),
            -float(item["evidence_score"]),
        ),
    )
    local_x = float(selected["center_at_sensor_m"])
    global_x, global_y = transform_local_point(local_x, 0.0, pose)
    left = selected["left"]
    right = selected["right"]
    residual = max(float(left["residual_p90_m"]), float(right["residual_p90_m"]))
    support = min(int(left["support_count"]), int(right["support_count"]))
    confidence = clamp(
        0.34
        + 0.20 * min(1.0, support / 6.0)
        + 0.18 * min(1.0, float(selected["overlap_span_m"]) / 7.0)
        + 0.16 * (1.0 - residual / max(LOCAL_ROW_MAX_RESIDUAL_M, 1e-6))
        + 0.12 * (1.0 - float(selected["parallel_slope_delta"]) / LOCAL_ROW_MAX_PARALLEL_SLOPE_DELTA),
        0.0,
        0.99,
    )
    return {
        "t_s": round(float(t_s), 3),
        "x_m": round(global_x, 3),
        "y_m": round(global_y, 3),
        "local_center_lateral_m": round(local_x, 3),
        "row_gap_m": round(float(selected["row_gap_m"]), 3),
        "confidence": round(confidence, 3),
        "left_support_count": int(left["support_count"]),
        "right_support_count": int(right["support_count"]),
        "left_longitudinal_span_m": round(float(left["span_m"]), 3),
        "right_longitudinal_span_m": round(float(right["span_m"]), 3),
        "bilateral_overlap_span_m": round(float(selected["overlap_span_m"]), 3),
        "parallel_slope_delta": round(float(selected["parallel_slope_delta"]), 4),
        "max_row_residual_p90_m": round(residual, 3),
        "method": "time_local_parallel_tree_rows_ransac_v1",
        "metric_anchor": "lidar_local_geometry",
    }


def fit_local_row_models(trees: list[dict[str, Any]], side: int) -> list[dict[str, Any]]:
    points: list[dict[str, float]] = []
    for tree in trees:
        if tree.get("kind") not in {"trunk_like", "foliage_cluster"}:
            continue
        center = tree.get("center") or {}
        try:
            x = float(center["x_m"])
            y = float(center["y_m"])
            confidence = float(tree.get("confidence") or 0.0)
            structure = float(tree.get("structure_score") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if (
            side * x <= 0.65
            or abs(x) > 6.5
            or y < 0.4
            or y > 11.5
            or confidence < 0.62
            or structure < 0.42
        ):
            continue
        kind_weight = 1.08 if tree.get("kind") == "trunk_like" else 0.92
        points.append(
            {
                "x_m": x,
                "y_m": y,
                "weight": confidence * max(0.45, structure) * kind_weight,
            }
        )
    if len(points) < LOCAL_ROW_MIN_SIDE_SUPPORT:
        return []
    hypotheses: list[tuple[float, float]] = [(0.0, point["x_m"]) for point in points]
    for index, first in enumerate(points):
        for second in points[index + 1 :]:
            delta_y = second["y_m"] - first["y_m"]
            if abs(delta_y) < 1.8:
                continue
            slope = (second["x_m"] - first["x_m"]) / delta_y
            if abs(slope) <= LOCAL_ROW_MAX_ABS_SLOPE:
                hypotheses.append((slope, first["x_m"] - slope * first["y_m"]))

    candidates: list[dict[str, Any]] = []
    for slope, intercept in hypotheses:
        inliers = local_row_inliers(points, slope, intercept)
        if len(inliers) < LOCAL_ROW_MIN_SIDE_SUPPORT:
            continue
        for _ in range(2):
            fitted = weighted_line_fit_y_to_x(inliers)
            if fitted is None:
                break
            slope, intercept = fitted
            if abs(slope) > LOCAL_ROW_MAX_ABS_SLOPE:
                inliers = []
                break
            inliers = local_row_inliers(points, slope, intercept)
        if len(inliers) < LOCAL_ROW_MIN_SIDE_SUPPORT:
            continue
        forward_values = [point["y_m"] for point in inliers]
        span = max(forward_values) - min(forward_values)
        if span < LOCAL_ROW_MIN_LONGITUDINAL_SPAN_M:
            continue
        residuals = sorted(abs(point["x_m"] - (intercept + slope * point["y_m"])) for point in inliers)
        residual_p90 = percentile(residuals, 0.9)
        if residual_p90 > LOCAL_ROW_MAX_RESIDUAL_M:
            continue
        residual_median = float(median(residuals) or 0.0)
        candidates.append(
            {
                "slope": slope,
                "intercept_m": intercept,
                "support_count": len(inliers),
                "min_forward_m": min(forward_values),
                "max_forward_m": max(forward_values),
                "span_m": span,
                "residual_median_m": residual_median,
                "residual_p90_m": residual_p90,
                "score": sum(point["weight"] for point in inliers)
                + min(8.0, span) * 0.3
                - residual_median * 2.0,
            }
        )
    candidates.sort(
        key=lambda item: (
            -float(item["score"]),
            -int(item["support_count"]),
            float(item["residual_p90_m"]),
        )
    )
    models: list[dict[str, Any]] = []
    for candidate in candidates:
        if any(
            abs(row_model_x(candidate, 5.0) - row_model_x(existing, 5.0)) < 0.35
            and abs(float(candidate["slope"]) - float(existing["slope"])) < 0.08
            for existing in models
        ):
            continue
        models.append(candidate)
        if len(models) >= 8:
            break
    return models


def local_row_inliers(
    points: list[dict[str, float]], slope: float, intercept: float
) -> list[dict[str, float]]:
    return [
        point
        for point in points
        if abs(point["x_m"] - (intercept + slope * point["y_m"]))
        <= LOCAL_ROW_MAX_RESIDUAL_M
    ]


def weighted_line_fit_y_to_x(points: list[dict[str, float]]) -> tuple[float, float] | None:
    total_weight = sum(max(0.001, point["weight"]) for point in points)
    if total_weight <= 0.0:
        return None
    mean_y = sum(point["y_m"] * max(0.001, point["weight"]) for point in points) / total_weight
    mean_x = sum(point["x_m"] * max(0.001, point["weight"]) for point in points) / total_weight
    denominator = sum(max(0.001, point["weight"]) * (point["y_m"] - mean_y) ** 2 for point in points)
    if denominator <= 1e-9:
        return 0.0, mean_x
    slope = sum(
        max(0.001, point["weight"])
        * (point["y_m"] - mean_y)
        * (point["x_m"] - mean_x)
        for point in points
    ) / denominator
    return slope, mean_x - slope * mean_y


def row_model_x(model: dict[str, Any], forward_m: float) -> float:
    return float(model["intercept_m"]) + float(model["slope"]) * forward_m


def compact_local_corridor_diagnostic(corridor: dict[str, Any] | None) -> dict[str, Any]:
    if not corridor:
        return {"ok": False, "state": "no_valid_parallel_bilateral_rows"}
    return {
        "ok": True,
        "method": corridor.get("method"),
        "row_gap_m": corridor.get("row_gap_m"),
        "local_center_lateral_m": corridor.get("local_center_lateral_m"),
        "confidence": corridor.get("confidence"),
        "left_support_count": corridor.get("left_support_count"),
        "right_support_count": corridor.get("right_support_count"),
        "bilateral_overlap_span_m": corridor.get("bilateral_overlap_span_m"),
        "max_row_residual_p90_m": corridor.get("max_row_residual_p90_m"),
    }


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


def lidar_camera_semantic_support(
    tree: dict[str, Any],
    camera_support: dict[str, Any] | None,
    detection_indexes: set[int] | None = None,
) -> dict[str, Any]:
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
    for detection_index, detection in enumerate(camera_support.get("detections") or []):
        if detection_indexes is not None and detection_index not in detection_indexes:
            continue
        semantic_admission = camera_detection_semantic_admission(detection)
        if semantic_admission is None:
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
                "state": (
                    "bearing_contour_associated"
                    if semantic_admission == "accepted"
                    else "bearing_contour_associated_stable_pending_semantic_only"
                ),
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
                "semantic_admission": semantic_admission,
                "camera_detection_index": detection_index,
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


def camera_detection_semantic_admission(detection: dict[str, Any]) -> str | None:
    """Allow stable OOD-edge proposals only as down-weighted semantics.

    A pending proposal can never enter the camera metric map.  It may support
    an already admitted LiDAR anchor when temporal stability, bounded OOD, and
    a minimum tree probability all agree.
    """

    if str(detection.get("admission") or "accepted") == "rejected":
        return None
    map_admission = detection.get("map_admission")
    if map_admission in {None, "accepted"}:
        return "accepted"
    if (
        map_admission == "pending"
        and detection.get("stable") is True
        and int(detection.get("stable_hits") or 0) >= 3
        and bool(str(detection.get("track_id") or "").strip())
        and float(detection.get("tree_probability") or detection.get("confidence") or 0.0) >= 0.24
        and float(detection.get("ood_score") or 0.0) <= 0.78
        and not camera_semantic_negative_context(detection)
    ):
        return "stable_pending_semantic_only"
    return None


def camera_semantic_negative_context(detection: dict[str, Any]) -> bool:
    """Reject known structural/scene negatives from the low-trust pending path."""

    clip = detection.get("clip_ood") if isinstance(detection.get("clip_ood"), dict) else {}
    rejection_reasons = detection.get("rejection_reasons") or []
    if not isinstance(rejection_reasons, (list, tuple, set)):
        rejection_reasons = [rejection_reasons]
    values = [
        detection.get("label"),
        detection.get("class"),
        detection.get("class_name"),
        clip.get("top_negative"),
        *rejection_reasons,
    ]
    words: set[str] = set()
    for value in values:
        normalized = "".join(
            character.lower() if character.isalnum() else " "
            for character in str(value or "")
        )
        words.update(normalized.split())
    return bool(words & CAMERA_SEMANTIC_NEGATIVE_TOKENS)


def associate_lidar_camera_semantics(
    trees: list[dict[str, Any]], camera_support: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Associate a camera detection with at most one LiDAR candidate.

    A per-tree nearest-bearing lookup can accidentally use the same semantic
    contour more than once when two LiDAR clusters overlap in bearing.  The
    metric coordinates remain LiDAR-owned; this assignment only allocates
    independent semantic evidence.  Maximum cardinality is optimized before
    association cost so a locally attractive edge cannot hide another valid
    one-to-one pairing.
    """

    if not trees:
        return []
    detections = (camera_support or {}).get("detections") or []
    pair_support: dict[tuple[int, int], dict[str, Any]] = {}
    edges: list[tuple[int, int, float]] = []
    for tree_index, tree in enumerate(trees):
        for detection_index in range(len(detections)):
            support = lidar_camera_semantic_support(
                tree,
                camera_support,
                detection_indexes={detection_index},
            )
            if not support.get("matched"):
                continue
            score = clamp(float(support.get("association_score") or 0.0), 0.0, 1.0)
            bearing_delta = abs(float(support.get("bearing_delta_deg") or 0.0))
            # Cost is deliberately dimensionless and deterministic.  Semantic
            # probability/alignment dominates; bearing resolves close ties.
            cost = (1.0 - score) + min(1.0, bearing_delta / 90.0) * 0.01
            support["camera_detection_index"] = detection_index
            pair_support[(tree_index, detection_index)] = support
            edges.append((tree_index, detection_index, cost))

    assignments = minimum_cost_maximum_cardinality_assignment(
        len(trees), len(detections), edges
    )
    assigned_by_tree = {left: right for left, right in assignments}
    rows: list[dict[str, Any]] = []
    candidate_tree_indexes = {left for left, _, _ in edges}
    for tree_index, tree in enumerate(trees):
        detection_index = assigned_by_tree.get(tree_index)
        if detection_index is not None:
            rows.append(pair_support[(tree_index, detection_index)])
        elif tree_index in candidate_tree_indexes:
            rows.append(
                {
                    "matched": False,
                    "state": "camera_detection_reserved_by_one_to_one_assignment",
                    "quality": 0.0,
                    "sync_delta_s": (camera_support or {}).get("sync_delta_s"),
                }
            )
        else:
            rows.append(lidar_camera_semantic_support(tree, camera_support))
    return rows


def minimum_cost_maximum_cardinality_assignment(
    left_count: int,
    right_count: int,
    edges: list[tuple[int, int, float]],
) -> list[tuple[int, int]]:
    """Return a deterministic min-cost maximum bipartite matching.

    This small dependency-free successive-shortest-path solver is shared by
    camera association and static-landmark tracking.  Invalid/non-finite edges
    are ignored instead of being coerced into a match.
    """

    if left_count <= 0 or right_count <= 0:
        return []
    source = 0
    left_offset = 1
    right_offset = left_offset + left_count
    sink = right_offset + right_count
    node_count = sink + 1
    graph: list[list[list[float | int]]] = [[] for _ in range(node_count)]

    def add_edge(start: int, end: int, capacity: int, cost: float) -> int:
        forward_index = len(graph[start])
        reverse_index = len(graph[end])
        graph[start].append([end, reverse_index, capacity, cost])
        graph[end].append([start, forward_index, 0, -cost])
        return forward_index

    for left in range(left_count):
        add_edge(source, left_offset + left, 1, 0.0)
    for right in range(right_count):
        add_edge(right_offset + right, sink, 1, 0.0)

    tracked_edges: list[tuple[int, int, int, int]] = []
    for left, right, cost in sorted(edges, key=lambda item: (item[0], item[1], item[2])):
        if not (0 <= left < left_count and 0 <= right < right_count):
            continue
        try:
            numeric_cost = float(cost)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric_cost):
            continue
        node = left_offset + left
        edge_index = add_edge(
            node,
            right_offset + right,
            1,
            numeric_cost + left * 1e-10 + right * 1e-12,
        )
        tracked_edges.append((left, right, node, edge_index))

    while True:
        distances = [float("inf")] * node_count
        previous: list[tuple[int, int] | None] = [None] * node_count
        distances[source] = 0.0
        # Bellman-Ford is sufficient for these small graphs and correctly
        # handles negative residual edges after the first augmentation.
        for _ in range(node_count - 1):
            changed = False
            for node, node_edges in enumerate(graph):
                if not math.isfinite(distances[node]):
                    continue
                for edge_index, edge in enumerate(node_edges):
                    end, _, capacity, cost = edge
                    if int(capacity) <= 0:
                        continue
                    candidate = distances[node] + float(cost)
                    if candidate + 1e-12 < distances[int(end)]:
                        distances[int(end)] = candidate
                        previous[int(end)] = (node, edge_index)
                        changed = True
            if not changed:
                break
        if previous[sink] is None:
            break
        node = sink
        while node != source:
            prior_node, edge_index = previous[node] or (source, 0)
            edge = graph[prior_node][edge_index]
            edge[2] = int(edge[2]) - 1
            reverse_index = int(edge[1])
            graph[node][reverse_index][2] = int(graph[node][reverse_index][2]) + 1
            node = prior_node

    matches = [
        (left, right)
        for left, right, node, edge_index in tracked_edges
        if int(graph[node][edge_index][2]) == 0
    ]
    matches.sort()
    return matches


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
    semantic_admission = camera_detection_semantic_admission(detection)
    if semantic_admission == "accepted":
        admission = 1.0
    elif semantic_admission == "stable_pending_semantic_only":
        admission = 0.45 * min(1.0, int(detection.get("stable_hits") or 0) / 4.0)
    else:
        admission = 0.0
    return clamp(base * sync_quality * (1.0 - 0.35 * ood_score) * admission, 0.0, 1.0)


def camera_global_tree_hits(
    camera_index: list[dict[str, Any]],
    path: list[dict[str, Any]],
    paired_support_by_lidar_index: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    frame_poses: list[tuple[dict[str, Any], dict[str, float]]] = []
    if paired_support_by_lidar_index is not None:
        for lidar_index, support in sorted(paired_support_by_lidar_index.items()):
            if not support.get("matched") or not (0 <= lidar_index < len(path)):
                continue
            camera_index_value = support.get("observation_index")
            if not isinstance(camera_index_value, int) or not (
                0 <= camera_index_value < len(camera_index)
            ):
                continue
            path_row = path[lidar_index]
            frame_poses.append(
                (
                    camera_index[camera_index_value],
                    {
                        "x_m": float(path_row.get("x_m") or 0.0),
                        "y_m": float(path_row.get("y_m") or 0.0),
                        "theta_rad": math.radians(
                            float(path_row.get("heading_deg") or 0.0)
                        ),
                        "sync_delta_s": float(support.get("sync_delta_s") or 0.0),
                    },
                )
            )
    else:
        use_header_stamp = bool(camera_index and path) and all(
            _finite_number(frame.get("sensor_stamp_s")) for frame in camera_index
        ) and all(_finite_number(row.get("sensor_stamp_s")) for row in path)
        for frame in camera_index:
            pose = nearest_path_pose(
                path,
                float(frame.get("t_s") or 0.0),
                max_delta_s=CAMERA_SYNC_MAX_DELTA_S,
                sensor_stamp_s=(
                    float(frame["sensor_stamp_s"])
                    if use_header_stamp
                    else None
                ),
            )
            if pose:
                frame_poses.append((frame, pose))

    for frame, pose in frame_poses:
        if not frame.get("ok"):
            continue
        t_s = float(frame.get("t_s") or 0.0)
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


def nearest_path_pose(
    path: list[dict[str, Any]],
    t_s: float,
    max_delta_s: float | None = None,
    sensor_stamp_s: float | None = None,
) -> dict[str, float] | None:
    if not path:
        return None
    header_candidates = [row for row in path if _finite_number(row.get("sensor_stamp_s"))]
    if sensor_stamp_s is not None and math.isfinite(sensor_stamp_s) and header_candidates:
        item = min(
            header_candidates,
            key=lambda row: abs(float(row["sensor_stamp_s"]) - sensor_stamp_s),
        )
        delta_s = abs(float(item["sensor_stamp_s"]) - sensor_stamp_s)
    else:
        item = min(path, key=lambda row: abs(float(row.get("t_s") or 0.0) - t_s))
        delta_s = abs(float(item.get("t_s") or 0.0) - t_s)
    if max_delta_s is not None and delta_s > max_delta_s:
        return None
    theta = math.radians(float(item.get("heading_deg") or 0.0))
    return {"x_m": float(item.get("x_m") or 0.0), "y_m": float(item.get("y_m") or 0.0), "theta_rad": theta, "sync_delta_s": delta_s}


def merge_global_trees(hits: list[dict[str, Any]], frame_count: int | None = None, source: str = "lidar") -> list[dict[str, Any]]:
    valid_hits = [
        dict(hit)
        for hit in hits
        if _finite_number(hit.get("x_m"))
        and _finite_number(hit.get("y_m"))
        and _finite_number(hit.get("first_seen_s"))
    ]
    valid_hits.sort(
        key=lambda item: (
            float(item["first_seen_s"]),
            float(item["x_m"]),
            float(item["y_m"]),
        )
    )
    observation_groups: list[list[dict[str, Any]]] = []
    for hit in valid_hits:
        timestamp = float(hit["first_seen_s"])
        if not observation_groups or abs(timestamp - float(observation_groups[-1][0]["first_seen_s"])) > 1e-6:
            observation_groups.append([hit])
        else:
            observation_groups[-1].append(hit)

    tracks: list[dict[str, Any]] = []
    for frame_hits in observation_groups:
        edges: list[tuple[int, int, float]] = []
        for track_index, track in enumerate(tracks):
            observations = track["observations"]
            center_x, center_y = robust_track_center(observations)
            spread = track_spatial_residuals(observations, center_x, center_y)["p90_m"]
            for hit_index, hit in enumerate(frame_hits):
                hit_is_calibrated = bool(hit.get("metric_calibrated"))
                if source == "camera" and bool(track["metric_calibrated"]) != hit_is_calibrated:
                    continue
                distance_from_origin = math.hypot(float(hit["x_m"]), float(hit["y_m"]))
                if source == "camera":
                    gate = clamp(0.62 + 0.035 * distance_from_origin + 0.25 * spread, 0.72, 1.18)
                else:
                    gate = clamp(
                        0.34 + float(hit.get("radius_m") or 0.2) + 0.018 * distance_from_origin + 0.2 * spread,
                        0.44,
                        0.82,
                    )
                distance = math.hypot(float(hit["x_m"]) - center_x, float(hit["y_m"]) - center_y)
                if distance > gate:
                    continue
                kind_penalty = 0.04 if str(hit.get("kind")) != str(track.get("kind")) else 0.0
                edges.append((track_index, hit_index, distance / max(gate, 1e-6) + kind_penalty))

        assignments = minimum_cost_maximum_cardinality_assignment(
            len(tracks), len(frame_hits), edges
        )
        assigned_hits: set[int] = set()
        for track_index, hit_index in assignments:
            tracks[track_index]["observations"].append(frame_hits[hit_index])
            assigned_hits.add(hit_index)
        for hit_index, hit in enumerate(frame_hits):
            if hit_index in assigned_hits:
                continue
            tracks.append(
                {
                    "observations": [hit],
                    "metric_calibrated": bool(hit.get("metric_calibrated")),
                    "kind": hit.get("kind"),
                }
            )

    summarized = [summarize_static_landmark_track(track["observations"], source) for track in tracks]
    filtered: list[dict[str, Any]] = []
    for tree in summarized:
        hit_count = int(tree.get("hits") or 0)
        time_span = float(tree.get("temporal_persistence_s") or 0.0)
        confidence = float(tree.get("confidence") or 0.0)
        camera_confidence = float(tree.get("camera_confidence") or 0.0)
        residual_p90 = float(tree.get("spatial_residual_p90_m") or 0.0)
        stable = residual_p90 <= (0.9 if source == "camera" else 0.58)
        admitted = False
        if source == "camera":
            admitted = stable and (
                (hit_count >= 3 and time_span >= 0.45 and confidence >= 0.66)
                or (hit_count >= 2 and time_span >= 0.5 and confidence >= 0.78)
            )
        elif stable:
            admitted = (
                (hit_count >= 8 and time_span >= 2.0)
                or (hit_count >= 5 and time_span >= 1.0 and confidence >= 0.74)
                or (
                    (frame_count or 0) <= 8
                    and hit_count >= 3
                    and time_span >= 0.8
                    and confidence >= 0.62
                )
                or (
                    hit_count >= 3
                    and time_span >= 0.5
                    and int(tree.get("camera_support_hits") or 0) >= 2
                    and camera_confidence >= 0.62
                    and confidence >= 0.78
                )
            )
        if not admitted:
            continue
        tree["admission"] = "temporal_confirmed"
        tree["map_admission"] = "accepted"
        tree["rejection_reasons"] = []
        filtered.append(tree)

    filtered.sort(
        key=lambda item: (
            -(int(item.get("hits") or 0)),
            -float(item.get("confidence") or 0.0),
            float(item.get("first_seen_s") or 0.0),
            float(item.get("x_m") or 0.0),
        )
    )
    return filtered


def robust_track_center(observations: list[dict[str, Any]]) -> tuple[float, float]:
    weights = [max(0.1, float(item.get("confidence") or 0.5)) for item in observations]
    x = robust_weighted_location([float(item["x_m"]) for item in observations], weights)
    y = robust_weighted_location([float(item["y_m"]) for item in observations], weights)
    return float(x or 0.0), float(y or 0.0)


def track_spatial_residuals(
    observations: list[dict[str, Any]], center_x: float, center_y: float
) -> dict[str, float]:
    residuals = sorted(
        math.hypot(float(item["x_m"]) - center_x, float(item["y_m"]) - center_y)
        for item in observations
    )
    return {
        "median_m": float(median(residuals) or 0.0),
        "p90_m": percentile(residuals, 0.9) if residuals else 0.0,
        "max_m": max(residuals, default=0.0),
    }


def summarize_static_landmark_track(
    observations: list[dict[str, Any]], source: str
) -> dict[str, Any]:
    center_x, center_y = robust_track_center(observations)
    residuals = track_spatial_residuals(observations, center_x, center_y)
    exemplar = max(
        observations,
        key=lambda item: (
            float(item.get("confidence") or 0.0),
            float(item.get("structure_score") or 0.0),
        ),
    )
    row = dict(exemplar)
    first_times = [float(item.get("first_seen_s") or 0.0) for item in observations]
    last_times = [float(item.get("last_seen_s") or item.get("first_seen_s") or 0.0) for item in observations]
    observation_multiplicities = [max(1, int(item.get("hits") or 1)) for item in observations]
    hit_count = sum(observation_multiplicities)
    confidences = [float(item.get("confidence") or 0.0) for item in observations]
    confidence_weights = [max(0.1, float(item.get("structure_score") or 0.5)) for item in observations]
    lidar_confidences = [float(item.get("lidar_confidence") or 0.0) for item in observations]
    structure_scores = [float(item.get("structure_score") or 0.0) for item in observations]
    row.update(
        {
            "x_m": round(center_x, 3),
            "y_m": round(center_y, 3),
            "radius_m": round(float(median([float(item.get("radius_m") or 0.1) for item in observations]) or 0.1), 3),
            "confidence": round(float(robust_weighted_location(confidences, confidence_weights) or 0.0), 3),
            "lidar_confidence": round(float(median(lidar_confidences) or 0.0), 3),
            "camera_confidence": round(max((float(item.get("camera_confidence") or 0.0) for item in observations), default=0.0), 3),
            "camera_quality": round(max((float(item.get("camera_quality") or 0.0) for item in observations), default=0.0), 3),
            "camera_semantic_support": round(max((float(item.get("camera_semantic_support") or 0.0) for item in observations), default=0.0), 3),
            "camera_support_hits": sum(int(item.get("camera_support_hits") or 0) for item in observations),
            "structure_score": round(float(median(structure_scores) or 0.0), 3),
            "first_seen_s": round(min(first_times), 3),
            "last_seen_s": round(max(last_times), 3),
            "hits": hit_count,
            "temporal_hits": hit_count,
            "temporal_persistence_s": round(max(last_times) - min(first_times), 3),
            "distinct_observation_times": len(set(first_times)),
            "spatial_residual_median_m": round(residuals["median_m"], 3),
            "spatial_residual_p90_m": round(residuals["p90_m"], 3),
            "spatial_residual_max_m": round(residuals["max_m"], 3),
            "temporal_policy": "one_observation_per_frame_robust_static_track",
        }
    )
    bearing_deltas = [float(item["camera_bearing_delta_deg"]) for item in observations if item.get("camera_bearing_delta_deg") is not None]
    sync_deltas = [float(item["camera_sync_delta_s"]) for item in observations if item.get("camera_sync_delta_s") is not None]
    wall_distances = [float(item["wall_distance_m"]) for item in observations if item.get("wall_distance_m") is not None]
    row["camera_bearing_delta_deg"] = round(min(bearing_deltas), 3) if bearing_deltas else None
    row["camera_sync_delta_s"] = round(min(sync_deltas), 4) if sync_deltas else None
    row["wall_distance_m"] = round(min(wall_distances), 3) if wall_distances else None
    if source == "camera":
        calibrated_hits = sum(
            multiplicity
            for item, multiplicity in zip(observations, observation_multiplicities)
            if bool(item.get("metric_calibrated"))
        )
        uncalibrated_hits = hit_count - calibrated_hits
        row["calibrated_hits"] = calibrated_hits
        row["uncalibrated_hits"] = uncalibrated_hits
        row["metric_provenance_consistent"] = calibrated_hits == hit_count or uncalibrated_hits == hit_count
        row["metric_calibrated"] = calibrated_hits == hit_count and uncalibrated_hits == 0
        row["metric_anchor"] = "camera_calibrated" if row["metric_calibrated"] else "none"
        row["position_source"] = "camera_calibrated_projection" if row["metric_calibrated"] else "camera_monocular_uncalibrated"
    return row


def _finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def sensor_timestamp_s(frame: dict[str, Any]) -> float | None:
    stamp = frame.get("stamp") if isinstance(frame.get("stamp"), dict) else {}
    try:
        seconds = float(stamp["sec"])
        nanoseconds = float(stamp["nanosec"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or not math.isfinite(nanoseconds) or nanoseconds < 0.0 or nanoseconds >= 1e9:
        return None
    value = seconds + nanoseconds * 1e-9
    return value if math.isfinite(value) else None


def fuse_tree_maps(lidar_trees: list[dict[str, Any]], camera_trees: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fused: list[dict[str, Any]] = []
    pair_distances: dict[tuple[int, int], float] = {}
    edges: list[tuple[int, int, float]] = []
    for lidar_index, lidar in enumerate(lidar_trees):
        distance_from_origin = math.hypot(float(lidar["x_m"]), float(lidar["y_m"]))
        gate = max(0.9, min(2.2, 0.75 + 0.055 * distance_from_origin))
        for camera_index, camera in enumerate(camera_trees):
            if not camera_metric_track_is_valid(camera):
                continue
            distance = math.hypot(float(lidar["x_m"]) - float(camera["x_m"]), float(lidar["y_m"]) - float(camera["y_m"]))
            if distance <= gate:
                pair_distances[(lidar_index, camera_index)] = distance
                edges.append((lidar_index, camera_index, distance / max(gate, 1e-6)))
    assignments = minimum_cost_maximum_cardinality_assignment(
        len(lidar_trees),
        len(camera_trees),
        edges,
    )
    camera_by_lidar = {lidar_index: camera_index for lidar_index, camera_index in assignments}
    used_camera = set(camera_by_lidar.values())
    for lidar_index, lidar in enumerate(lidar_trees):
        camera_index = camera_by_lidar.get(lidar_index)
        camera = camera_trees[camera_index] if camera_index is not None else None
        fused.append(
            fuse_tree_pair(
                lidar,
                camera,
                pair_distances.get((lidar_index, camera_index))
                if camera_index is not None
                else None,
            )
        )

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
    navigation_validation: dict[str, Any] | None = None,
    sync_diagnostics: dict[str, Any] | None = None,
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
    sync = sync_diagnostics or {}
    if sync.get("clock_integrity_ok") is not True:
        navigation_reasons.append("sensor_header_clock_integrity_not_validated")
    validation = navigation_validation or {}
    validation_provenance_reasons: list[str] = []
    if validation.get("contract") != NAVIGATION_VALIDATION_CONTRACT:
        validation_provenance_reasons.append("navigation_validation_contract_missing")
    fingerprint = str(validation.get("manifest_fingerprint") or "")
    if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", fingerprint) is None:
        validation_provenance_reasons.append("navigation_validation_manifest_fingerprint_missing")
    if len(str(validation.get("reviewer") or "").strip()) < 3:
        validation_provenance_reasons.append("navigation_validation_reviewer_missing")
    if len(str(validation.get("source") or "").strip()) < 3:
        validation_provenance_reasons.append("navigation_validation_source_missing")
    navigation_reasons.extend(validation_provenance_reasons)
    missing_validation = [
        key
        for key in NAVIGATION_REQUIRED_VALIDATION_FLAGS
        if validation.get(key) is not True
    ]
    if missing_validation:
        navigation_reasons.extend(f"{key}_missing" for key in missing_validation)
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
            "centerline_evidence_ready": bool(centerline.get("ok")),
            "sensor_header_clock_integrity_ok": sync.get("clock_integrity_ok") is True,
            "lidar_header_non_monotonic_count": int(sync.get("lidar_header_non_monotonic_count") or 0),
            "camera_header_non_monotonic_count": int(sync.get("camera_header_non_monotonic_count") or 0),
            "navigation_validation": {
                key: validation.get(key) is True
                for key in NAVIGATION_REQUIRED_VALIDATION_FLAGS
            },
            "navigation_validation_provenance": {
                "contract": validation.get("contract"),
                "required_contract": NAVIGATION_VALIDATION_CONTRACT,
                "manifest_fingerprint": fingerprint or None,
                "reviewer": str(validation.get("reviewer") or "").strip() or None,
                "source": str(validation.get("source") or "").strip() or None,
                "valid": not validation_provenance_reasons,
            },
        },
        "minimum": {
            "lidar_frames": MIN_DATA_LIDAR_FRAMES,
            "nonempty_lidar_frames": MIN_DATA_NONEMPTY_LIDAR_FRAMES,
            "centerline_points_for_navigation": 2,
        },
        "data_sufficiency_reasons": data_reasons,
        "navigation_readiness_reasons": navigation_reasons,
    }


def build_map_layer(
    layer_id: str,
    label: str,
    method: str,
    trees: list[dict[str, Any]],
    path: list[dict[str, Any]],
    corridor_observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    centerline = estimate_row_centerline(
        trees,
        path,
        layer_id,
        corridor_observations=corridor_observations,
    )
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


def estimate_row_centerline(
    trees: list[dict[str, Any]],
    path: list[dict[str, Any]],
    layer_id: str,
    corridor_observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if len(path) < 2:
        return empty_centerline("path_unavailable", layer_id)
    trajectory = trajectory_segments(path)
    assigned = assign_trees_to_trajectory_segments(trees, trajectory)
    segment_results = [
        estimate_segment_centerline(assigned.get(segment["id"], []), segment, layer_id)
        for segment in trajectory
    ]
    corridor_results = estimate_corridor_observation_segments(
        corridor_observations or [], trajectory, layer_id
    )
    ready = [segment for segment in segment_results + corridor_results if segment.get("ok")]
    if not ready:
        result = empty_centerline("low_confidence_fail_closed", layer_id)
        result["segments"] = segment_results
        result["corridor_segments"] = corridor_results
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
        "method": "trajectory_segmented_hybrid_bilateral_rows_v10",
        "evidence_mode": selected.get("evidence_mode", "global_landmark_bilateral_rows"),
        "selected_segment_id": selected.get("segment_id"),
        "segment_count": len(segment_results),
        "segments": segment_results,
        "corridor_segments": corridor_results,
    }


def estimate_corridor_observation_segments(
    observations: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    layer_id: str,
) -> list[dict[str, Any]]:
    assigned = {str(segment["id"]): [] for segment in segments}
    for observation in observations:
        t_s = float(observation.get("t_s") or 0.0)
        candidates = []
        for segment in segments:
            segment_path = segment.get("path") or []
            if not segment_path:
                continue
            start_t = float(segment_path[0].get("t_s") or 0.0)
            end_t = float(segment_path[-1].get("t_s") or 0.0)
            if start_t - 1e-6 <= t_s <= end_t + 1e-6:
                time_distance = min(abs(t_s - start_t), abs(t_s - end_t))
                candidates.append((time_distance, segment))
        if not candidates:
            continue
        segment = min(candidates, key=lambda item: (item[0], str(item[1]["id"])))[1]
        projection = project_point_to_path(
            float(observation["x_m"]),
            float(observation["y_m"]),
            segment["path"],
        )
        if projection is None or float(projection["distance_m"]) > 4.0:
            continue
        assigned[str(segment["id"])].append({**observation, **projection})
    return [
        estimate_corridor_observation_segment(
            assigned.get(str(segment["id"]), []), segment, layer_id
        )
        for segment in segments
    ]


def estimate_corridor_observation_segment(
    observations: list[dict[str, Any]],
    segment: dict[str, Any],
    layer_id: str,
) -> dict[str, Any]:
    unique_by_time: dict[float, dict[str, Any]] = {}
    for observation in observations:
        key = round(float(observation.get("t_s") or 0.0), 6)
        existing = unique_by_time.get(key)
        if existing is None or float(observation.get("confidence") or 0.0) > float(existing.get("confidence") or 0.0):
            unique_by_time[key] = observation
    observations = [unique_by_time[key] for key in sorted(unique_by_time)]
    diagnostics = {
        "evidence_mode": "time_local_bilateral_corridor_consensus",
        "bilateral_frame_count": len(observations),
        "corridor_occupied_bins": 0,
        "corridor_station_span_m": 0.0,
        "corridor_time_span_s": 0.0,
        "corridor_row_gap_mad_m": None,
    }
    if len(observations) < 6:
        return corridor_centerline_failure(
            segment,
            "insufficient_time_local_bilateral_frames",
            len(observations),
            diagnostics,
        )
    times = [float(item["t_s"]) for item in observations]
    time_span = max(times) - min(times)
    diagnostics["corridor_time_span_s"] = round(time_span, 3)
    if time_span < 2.0:
        return corridor_centerline_failure(
            segment,
            "insufficient_time_local_bilateral_duration",
            len(observations),
            diagnostics,
        )

    bins: dict[int, list[dict[str, Any]]] = {}
    for observation in observations:
        key = int(math.floor(float(observation["s_m"]) / CORRIDOR_CONSENSUS_BIN_SIZE_M))
        bins.setdefault(key, []).append(observation)
    supported_bins = {key: rows for key, rows in bins.items() if len(rows) >= 2}
    diagnostics["corridor_occupied_bins"] = len(supported_bins)
    if len(supported_bins) < CORRIDOR_CONSENSUS_MIN_BINS:
        return corridor_centerline_failure(
            segment,
            "insufficient_time_local_corridor_bins",
            len(observations),
            diagnostics,
        )
    station_centers = [
        float(median([float(item["s_m"]) for item in rows]) or 0.0)
        for _, rows in sorted(supported_bins.items())
    ]
    station_span = max(station_centers) - min(station_centers)
    diagnostics["corridor_station_span_m"] = round(station_span, 3)
    if station_span < CORRIDOR_CONSENSUS_MIN_STATION_SPAN_M:
        return corridor_centerline_failure(
            segment,
            "insufficient_time_local_corridor_span",
            len(observations),
            diagnostics,
        )

    row_gaps = [float(item["row_gap_m"]) for item in observations]
    row_gap = float(robust_weighted_location(row_gaps, [float(item.get("confidence") or 0.2) for item in observations]) or 0.0)
    row_gap_mad = median_absolute_deviation(row_gaps, row_gap)
    diagnostics["corridor_row_gap_mad_m"] = round(row_gap_mad, 3)
    if (
        not CENTERLINE_ROW_GAP_MIN_M <= row_gap <= CENTERLINE_ROW_GAP_MAX_M
        or row_gap_mad > max(0.65, row_gap * 0.16)
    ):
        return corridor_centerline_failure(
            segment,
            "inconsistent_time_local_row_gap",
            len(observations),
            diagnostics,
        )

    points: list[dict[str, Any]] = []
    lateral_residuals: list[float] = []
    for key, rows in sorted(supported_bins.items()):
        weights = [float(item.get("confidence") or 0.2) for item in rows]
        station = float(robust_weighted_location([float(item["s_m"]) for item in rows], weights) or 0.0)
        lateral = float(robust_weighted_location([float(item["lateral_m"]) for item in rows], weights) or 0.0)
        lateral_residuals.extend(abs(float(item["lateral_m"]) - lateral) for item in rows)
        point = centerline_point_at_station(
            segment,
            station,
            lateral,
            "time_local_bilateral_corridor_consensus",
        )
        if point:
            point["support_frames"] = len(rows)
            point["station_bin"] = key
            points.append(point)
    points = smooth_centerline_points(points)
    lateral_p90 = percentile(sorted(lateral_residuals), 0.9) if lateral_residuals else float("inf")
    diagnostics["corridor_lateral_residual_p90_m"] = round(lateral_p90, 3)
    if lateral_p90 > 0.75:
        return corridor_centerline_failure(
            segment,
            "inconsistent_time_local_corridor_lateral_offset",
            len(observations),
            diagnostics,
        )

    mean_local_confidence = sum(float(item.get("confidence") or 0.0) for item in observations) / len(observations)
    pose_confidences = [clamp(float(item.get("pose_confidence") or 0.0), 0.0, 1.0) for item in segment["path"]]
    pose_confidence = sum(pose_confidences) / len(pose_confidences) if pose_confidences else 0.0
    confidence = round(
        0.22 * min(1.0, len(observations) / 12.0)
        + 0.20 * min(1.0, len(supported_bins) / 5.0)
        + 0.18 * min(1.0, station_span / 10.0)
        + 0.18 * clamp(1.0 - row_gap_mad / max(0.65, row_gap * 0.16), 0.0, 1.0)
        + 0.14 * clamp(mean_local_confidence, 0.0, 1.0)
        + 0.08 * pose_confidence,
        3,
    )
    ready = len(points) >= CORRIDOR_CONSENSUS_MIN_BINS and confidence >= max(0.5, CENTERLINE_MIN_CONFIDENCE)
    length = polyline_length(points) if ready else 0.0
    return {
        "ok": ready,
        "layer": layer_id,
        "method": "trajectory_segmented_time_local_bilateral_corridor_v10",
        "evidence_mode": "time_local_bilateral_corridor_consensus",
        "segment_id": segment["id"],
        "start_index": segment["start_index"],
        "end_index": segment["end_index"],
        "path_length_m": segment["path_length_m"],
        "points": points if ready else [],
        "point_count": len(points) if ready else 0,
        "candidate_point_count": len(points),
        "length_m": round(length, 3),
        "row_gap_m": round(row_gap, 3),
        "row_gap_mad_m": round(row_gap_mad, 3),
        "projected_tree_count": len(observations),
        "confidence": confidence,
        "selection_score": round(confidence * (0.25 + 0.75 * min(1.0, length / 10.0)), 3) if ready else 0.0,
        "confidence_threshold": max(0.5, CENTERLINE_MIN_CONFIDENCE),
        "state": "ready" if ready else "low_time_local_corridor_confidence",
        "fail_closed": not ready,
        **diagnostics,
    }


def corridor_centerline_failure(
    segment: dict[str, Any],
    reason: str,
    observation_count: int,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    row = segment_centerline_failure(
        segment,
        reason,
        observation_count,
        diagnostics,
    )
    row.update(
        {
            "method": "trajectory_segmented_time_local_bilateral_corridor_v10",
            "evidence_mode": "time_local_bilateral_corridor_consensus",
            "confidence_threshold": max(0.5, CENTERLINE_MIN_CONFIDENCE),
        }
    )
    return row


def empty_centerline(reason: str, layer_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "layer": layer_id,
        "method": "trajectory_segmented_hybrid_bilateral_rows_v10",
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
        "method": "trajectory_segmented_global_landmark_rows_v10",
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
        "method": "trajectory_segmented_global_landmark_rows_v10",
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
        if index == 0 or index == len(points) - 1:
            rows.append(dict(point))
            continue
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
                "observation_index": len(rows),
                "t_s": float(frame.get("t_s") or 0.0),
                **support,
                "sensor_stamp_s": sensor_timestamp_s(frame),
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


def camera_input_provenance_summary(frames: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = sum(
        1
        for frame in frames
        if isinstance(frame, dict)
        and current_source_native_camera_overlay(frame.get("overlay"), frame=frame)
    )
    total = len(frames)
    return {
        "enforced": True,
        "required_revision": VISION_INFERENCE_INPUT_POLICY_REVISION,
        "required_mode": "source_native",
        "required_requested_width": 0,
        "required_jpeg_quality": DEFAULT_VISION_INFERENCE_JPEG_QUALITY,
        "frame_count": total,
        "accepted_frame_count": accepted,
        "rejected_frame_count": total - accepted,
        "legacy_preview_as_native_allowed": False,
    }


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


def pair_lidar_camera_observations(
    lidar_frames: list[dict[str, Any]],
    camera_index: list[dict[str, Any]],
    max_delta_s: float = CAMERA_SYNC_MAX_DELTA_S,
) -> dict[int, dict[str, Any]]:
    """Pair streams in acquisition order without reusing sensor frames.

    The sparse chain solver maximizes in-gate cardinality and then minimizes
    total timestamp error.  It never sorts frames by a regressing header stamp:
    preserving archive order prevents a clock glitch from silently changing
    trajectory chronology.  ROS header stamps are used only when every frame
    supplies one; the whole archive otherwise uses the legacy relative timebase
    so clocks are never mixed silently.
    """

    if not lidar_frames:
        return {}
    use_header = bool(camera_index) and all(
        sensor_timestamp_s(frame) is not None for frame in lidar_frames
    ) and all(
        _finite_number(frame.get("sensor_stamp_s")) for frame in camera_index
    )
    timebase = "ros_header_stamp" if use_header else "timeline_relative_fallback"
    lidar_header_times = [sensor_timestamp_s(frame) for frame in lidar_frames]
    camera_header_times = [
        float(frame["sensor_stamp_s"])
        if _finite_number(frame.get("sensor_stamp_s"))
        else None
        for frame in camera_index
    ]
    lidar_clock = timestamp_stream_diagnostics(lidar_header_times)
    camera_clock = timestamp_stream_diagnostics(camera_header_times)
    pairing_metadata = {
        "header_stamp_complete": use_header,
        "clock_integrity_ok": bool(
            use_header
            and lidar_clock["clock_integrity_ok"]
            and camera_clock["clock_integrity_ok"]
        ),
        "lidar_header_non_monotonic_count": lidar_clock["non_monotonic_count"],
        "camera_header_non_monotonic_count": camera_clock["non_monotonic_count"],
        "lidar_header_duplicate_count": lidar_clock["duplicate_count"],
        "camera_header_duplicate_count": camera_clock["duplicate_count"],
        "lidar_header_max_regression_s": lidar_clock["max_regression_s"],
        "camera_header_max_regression_s": camera_clock["max_regression_s"],
        "sync_order_policy": "original_archive_order_no_header_stamp_resort",
        "sync_solver": "sparse_max_cardinality_minimum_cost_monotonic_chain",
    }
    if not camera_index:
        output = {
            index: nearest_camera_support(
                [],
                float(frame.get("t_s") or 0.0),
                max_delta_s,
                sensor_stamp_s=sensor_timestamp_s(frame),
            )
            for index, frame in enumerate(lidar_frames)
        }
        for support in output.values():
            support.update(pairing_metadata)
        return output

    lidar_times = [
        float(lidar_header_times[index] if use_header else frame.get("t_s") or 0.0)
        for index, frame in enumerate(lidar_frames)
    ]
    camera_times = [
        float(camera_header_times[index] if use_header else frame.get("t_s") or 0.0)
        for index, frame in enumerate(camera_index)
    ]
    matched_camera_by_lidar = dict(
        minimum_cost_maximum_cardinality_monotonic_pairs(
            lidar_times,
            camera_times,
            max_delta_s,
        )
    )

    output: dict[int, dict[str, Any]] = {}
    for lidar_index, lidar_frame in enumerate(lidar_frames):
        t_s = float(lidar_frame.get("t_s") or 0.0)
        sensor_stamp = sensor_timestamp_s(lidar_frame)
        camera_position = matched_camera_by_lidar.get(lidar_index)
        if camera_position is not None:
            camera = camera_index[camera_position]
            output[lidar_index] = camera_support_payload(
                camera,
                t_s,
                sensor_stamp,
                max_delta_s,
                timebase,
                matched=True,
                state="matched",
            )
            output[lidar_index]["sync_assignment"] = "global_monotonic_max_cardinality_minimum_cost_one_to_one"
            output[lidar_index].update(pairing_metadata)
            continue
        nearest = nearest_camera_support(
            camera_index,
            t_s,
            max_delta_s=max_delta_s,
            sensor_stamp_s=sensor_stamp if use_header else None,
        )
        was_in_gate = bool(nearest.get("matched"))
        nearest["matched"] = False
        nearest["state"] = "camera_reserved_by_one_to_one_sync" if was_in_gate else "outside_sync_gate"
        nearest["sync_assignment"] = "global_monotonic_max_cardinality_minimum_cost_one_to_one"
        nearest.update(pairing_metadata)
        output[lidar_index] = nearest
    return output


def timestamp_stream_diagnostics(values: list[float | None]) -> dict[str, Any]:
    non_monotonic: list[float] = []
    duplicates = 0
    previous: float | None = None
    for value in values:
        if value is None or not math.isfinite(float(value)):
            previous = None
            continue
        current = float(value)
        if previous is not None:
            delta = current - previous
            if delta < -1e-9:
                non_monotonic.append(-delta)
            elif abs(delta) <= 1e-9:
                duplicates += 1
        previous = current
    complete = bool(values) and all(value is not None and math.isfinite(float(value)) for value in values)
    return {
        "complete": complete,
        "clock_integrity_ok": complete and not non_monotonic and duplicates == 0,
        "non_monotonic_count": len(non_monotonic),
        "duplicate_count": duplicates,
        "max_regression_s": round(max(non_monotonic), 6) if non_monotonic else 0.0,
    }


def minimum_cost_maximum_cardinality_monotonic_pairs(
    left_times: list[float],
    right_times: list[float],
    max_delta_s: float,
) -> list[tuple[int, int]]:
    """Return an optimal order-preserving timestamp matching from sparse edges.

    Candidate generation is bucketed by timestamp and the matching is a
    maximum-cardinality, minimum-cost increasing chain.  Complexity is
    O((N + M + E) log M), where E is the number of in-gate timestamp pairs,
    instead of allocating an N-by-M dynamic-programming matrix.
    """

    if not left_times or not right_times or max_delta_s < 0.0:
        return []
    bucket_width = max(float(max_delta_s), 1e-9)
    right_buckets: dict[int, list[int]] = {}
    for right_index, timestamp in enumerate(right_times):
        if not math.isfinite(float(timestamp)):
            continue
        key = int(math.floor(float(timestamp) / bucket_width))
        right_buckets.setdefault(key, []).append(right_index)

    # State tuple: match count, cumulative absolute error, last chain-node id.
    State = tuple[int, float, int | None]
    empty_state: State = (0, 0.0, None)
    fenwick: list[State] = [empty_state for _ in range(len(right_times) + 1)]
    nodes: list[tuple[int, int, int | None]] = []

    def better(candidate: State, current: State) -> bool:
        if candidate[0] != current[0]:
            return candidate[0] > current[0]
        if abs(candidate[1] - current[1]) > 1e-12:
            return candidate[1] < current[1]
        candidate_node = candidate[2] if candidate[2] is not None else -1
        current_node = current[2] if current[2] is not None else -1
        return candidate_node < current_node

    def prefix_best(exclusive_right_index: int) -> State:
        best = empty_state
        index = exclusive_right_index
        while index > 0:
            if better(fenwick[index], best):
                best = fenwick[index]
            index -= index & -index
        return best

    def update(right_index: int, state: State) -> None:
        index = right_index + 1
        while index < len(fenwick):
            if better(state, fenwick[index]):
                fenwick[index] = state
            index += index & -index

    for left_index, timestamp in enumerate(left_times):
        if not math.isfinite(float(timestamp)):
            continue
        bucket = int(math.floor(float(timestamp) / bucket_width))
        candidate_right_indexes = sorted(
            {
                right_index
                for nearby in (bucket - 1, bucket, bucket + 1)
                for right_index in right_buckets.get(nearby, [])
                if abs(float(timestamp) - float(right_times[right_index]))
                <= max_delta_s + 1e-12
            }
        )
        pending_updates: list[tuple[int, State]] = []
        for right_index in candidate_right_indexes:
            predecessor = prefix_best(right_index)
            node_id = len(nodes)
            nodes.append((left_index, right_index, predecessor[2]))
            pending_updates.append(
                (
                    right_index,
                    (
                        predecessor[0] + 1,
                        predecessor[1]
                        + abs(float(timestamp) - float(right_times[right_index])),
                        node_id,
                    ),
                )
            )
        # Batch updates prevent two edges from the same left observation from
        # appearing in one chain.
        for right_index, state in pending_updates:
            update(right_index, state)

    best = prefix_best(len(right_times))
    pairs: list[tuple[int, int]] = []
    node_id = best[2]
    while node_id is not None:
        left_index, right_index, predecessor = nodes[node_id]
        pairs.append((left_index, right_index))
        node_id = predecessor
    pairs.reverse()
    return pairs


def camera_support_payload(
    camera: dict[str, Any],
    lidar_t_s: float,
    lidar_sensor_stamp_s: float | None,
    max_delta_s: float,
    sync_timebase: str,
    matched: bool,
    state: str,
) -> dict[str, Any]:
    camera_t_s = float(camera.get("t_s") or 0.0)
    timeline_delta_s = abs(camera_t_s - lidar_t_s)
    if sync_timebase == "ros_header_stamp" and lidar_sensor_stamp_s is not None:
        delta_s = abs(float(camera["sensor_stamp_s"]) - lidar_sensor_stamp_s)
    else:
        delta_s = timeline_delta_s
    return {
        **camera,
        "matched": matched,
        "state": state,
        "lidar_t_s": round(lidar_t_s, 3),
        "camera_t_s": round(camera_t_s, 3),
        "lidar_sensor_stamp_s": lidar_sensor_stamp_s,
        "camera_sensor_stamp_s": camera.get("sensor_stamp_s"),
        "timeline_delta_s": round(timeline_delta_s, 6),
        "sync_delta_s": round(delta_s, 6),
        "sync_timebase": sync_timebase,
        "max_delta_s": max_delta_s,
    }


def nearest_camera_support(
    index: list[dict[str, Any]],
    t_s: float,
    max_delta_s: float = CAMERA_SYNC_MAX_DELTA_S,
    sensor_stamp_s: float | None = None,
) -> dict[str, Any]:
    if not index:
        return {
            "matched": False,
            "state": "camera_unavailable",
            "lidar_t_s": round(t_s, 3),
            "lidar_sensor_stamp_s": sensor_stamp_s,
            "max_delta_s": max_delta_s,
            "sync_delta_s": None,
            "sync_timebase": "unavailable",
        }
    header_candidates = [item for item in index if _finite_number(item.get("sensor_stamp_s"))]
    use_header_stamp = sensor_stamp_s is not None and math.isfinite(sensor_stamp_s) and bool(header_candidates)
    if use_header_stamp:
        nearest = min(
            header_candidates,
            key=lambda item: abs(float(item["sensor_stamp_s"]) - sensor_stamp_s),
        )
        delta_s = abs(float(nearest["sensor_stamp_s"]) - sensor_stamp_s)
        sync_timebase = "ros_header_stamp"
    else:
        nearest = min(index, key=lambda item: abs(float(item.get("t_s") or 0.0) - t_s))
        delta_s = abs(float(nearest.get("t_s") or 0.0) - t_s)
        sync_timebase = "timeline_relative_fallback"
    camera_t_s = float(nearest.get("t_s") or 0.0)
    timeline_delta_s = abs(camera_t_s - t_s)
    matched = delta_s <= max_delta_s
    return camera_support_payload(
        nearest,
        t_s,
        sensor_stamp_s,
        max_delta_s,
        sync_timebase,
        matched=matched,
        state="matched" if matched else "outside_sync_gate",
    )


def compact_camera_sync_diagnostic(camera_support: dict[str, Any] | None) -> dict[str, Any]:
    support = camera_support or {}
    return {
        "matched": bool(support.get("matched")),
        "state": str(support.get("state") or "camera_unavailable"),
        "camera_ok": bool(support.get("ok")),
        "camera_t_s": support.get("camera_t_s"),
        "sync_timebase": support.get("sync_timebase"),
        "timeline_delta_s": support.get("timeline_delta_s"),
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
        "header_stamp_complete": bool(support.get("header_stamp_complete")),
        "clock_integrity_ok": bool(support.get("clock_integrity_ok")),
        "lidar_header_non_monotonic_count": int(support.get("lidar_header_non_monotonic_count") or 0),
        "camera_header_non_monotonic_count": int(support.get("camera_header_non_monotonic_count") or 0),
        "lidar_header_duplicate_count": int(support.get("lidar_header_duplicate_count") or 0),
        "camera_header_duplicate_count": int(support.get("camera_header_duplicate_count") or 0),
        "lidar_header_max_regression_s": float(support.get("lidar_header_max_regression_s") or 0.0),
        "camera_header_max_regression_s": float(support.get("camera_header_max_regression_s") or 0.0),
        "sync_order_policy": support.get("sync_order_policy"),
        "sync_solver": support.get("sync_solver"),
    }


def camera_sync_diagnostics(frame_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    supports = [frame.get("camera_support") or {} for frame in frame_summaries]
    deltas = [float(item["sync_delta_s"]) for item in supports if item.get("sync_delta_s") is not None]
    matched_deltas = [float(item["sync_delta_s"]) for item in supports if item.get("matched") and item.get("sync_delta_s") is not None]
    matched = sum(1 for item in supports if item.get("matched"))
    outside = sum(
        1
        for item in supports
        if not item.get("matched") and item.get("state") != "camera_unavailable"
    )
    unavailable = sum(1 for item in supports if item.get("state") == "camera_unavailable")
    first = supports[0] if supports else {}
    return {
        "policy": "original_order_common_ros_header_stamp_one_to_one_with_bounded_gate_and_archive_relative_compatibility_fallback",
        "solver": first.get("sync_solver", "unavailable"),
        "order_policy": first.get("sync_order_policy", "unavailable"),
        "max_delta_s": CAMERA_SYNC_MAX_DELTA_S,
        "recommended_live_max_delta_s": CAMERA_LIVE_RECOMMENDED_SYNC_MAX_DELTA_S,
        "lidar_frames": len(supports),
        "matched_frames": matched,
        "outside_gate_frames": outside,
        "camera_unavailable_frames": unavailable,
        "one_to_one_conflict_frames": sum(
            1 for item in supports if item.get("state") == "camera_reserved_by_one_to_one_sync"
        ),
        "ros_header_stamp_frames": sum(1 for item in supports if item.get("sync_timebase") == "ros_header_stamp"),
        "timeline_relative_fallback_frames": sum(1 for item in supports if item.get("sync_timebase") == "timeline_relative_fallback"),
        "header_stamp_complete": bool(first.get("header_stamp_complete")),
        "clock_integrity_ok": bool(first.get("clock_integrity_ok")),
        "lidar_header_non_monotonic_count": int(first.get("lidar_header_non_monotonic_count") or 0),
        "camera_header_non_monotonic_count": int(first.get("camera_header_non_monotonic_count") or 0),
        "lidar_header_duplicate_count": int(first.get("lidar_header_duplicate_count") or 0),
        "camera_header_duplicate_count": int(first.get("camera_header_duplicate_count") or 0),
        "lidar_header_max_regression_s": float(first.get("lidar_header_max_regression_s") or 0.0),
        "camera_header_max_regression_s": float(first.get("camera_header_max_regression_s") or 0.0),
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
