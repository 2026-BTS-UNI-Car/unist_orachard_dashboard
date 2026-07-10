from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any


ALGORITHM_NAME = "polar_gap_cluster_wall_context_tree_ood_predictive_tracks_v6"
LIDAR_COORDINATE_FRAME = "lidar_bridge_xy_v1"


def current_lidar_overlay_algorithm(overlay: Any) -> bool:
    return isinstance(overlay, dict) and overlay.get("ok") is True and overlay.get("algorithm") == ALGORITHM_NAME


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def coverage(points: list[dict[str, Any]]) -> float:
    if len(points) < 2:
        return 0.0
    angles = sorted(float(point["angle_deg"]) for point in points if _finite(point.get("angle_deg")))
    if len(angles) < 2:
        return 0.0
    gaps = [angles[i] - angles[i - 1] for i in range(1, len(angles))]
    gaps.append((angles[0] + 360.0) - angles[-1])
    return round(max(0.0, 360.0 - max(gaps)), 1)


def analyze_points(
    points: list[dict[str, Any]],
    tracks: dict[str, dict[str, Any]] | None = None,
    next_track_id: int = 1,
    now: float | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], int]:
    now = time.time() if now is None else now
    tracks = dict(tracks or {})
    clusters = cluster_points(points)
    walls: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []

    for cluster in clusters:
        wall = fit_wall(cluster)
        if wall:
            walls.append(wall)
            continue
        obj = object_from_cluster(cluster)
        if obj:
            objects.append(obj)

    walls = merge_wall_fragments(walls)
    objects, tracks, next_track_id = update_tracks(objects, tracks, next_track_id, now)
    orchard = detect_orchard_trees(points, walls=walls, clusters=clusters)
    orchard = attach_orchard_temporal_state(orchard, objects)
    analysis = {
        "ok": True,
        "algorithm": ALGORITHM_NAME,
        "coordinate_frame": LIDAR_COORDINATE_FRAME,
        "generated_at": iso_now(),
        "walls": [public_wall(wall) for wall in walls],
        "objects": objects,
        "orchard": orchard,
        "tracking": track_summary(objects),
        "cluster_count": len(clusters),
        "source_point_count": len(points),
    }
    return analysis, tracks, next_track_id


def empty_analysis() -> dict[str, Any]:
    return {
        "ok": False,
        "algorithm": ALGORITHM_NAME,
        "coordinate_frame": LIDAR_COORDINATE_FRAME,
        "walls": [],
        "objects": [],
        "orchard": {
            "trees": [],
            "rejected_candidates": [],
            "excluded_rear_candidates": 0,
            "wall_suppressed_candidates": 0,
            "linear_context_suppressed_candidates": 0,
            "method": "lidar_tree_geometry_wall_context_ood_v4",
        },
        "tracking": {"drawable_tracks": 0, "object_tracks": 0, "method": "sensor_relative_predictive_tracks_v4"},
        "cluster_count": 0,
        "source_point_count": 0,
    }


def cluster_points(points: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(
        (point for point in points if _finite(point.get("x_m")) and _finite(point.get("y_m"))),
        key=lambda point: float(point.get("angle_deg") or 0.0),
    )
    if not ordered:
        return []

    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = [ordered[0]]
    previous = ordered[0]
    for point in ordered[1:]:
        if point_gap(previous, point) > point_gap_threshold(previous, point):
            if len(current) >= 3:
                clusters.append(current)
            current = []
        current.append(point)
        previous = point
    if len(current) >= 3:
        clusters.append(current)

    if len(clusters) > 1 and point_gap(clusters[-1][-1], clusters[0][0]) <= point_gap_threshold(clusters[-1][-1], clusters[0][0]) * 1.2:
        clusters[0] = clusters[-1] + clusters[0]
        clusters.pop()
    return clusters


def point_gap(a: dict[str, Any], b: dict[str, Any]) -> float:
    return math.hypot(float(a["x_m"]) - float(b["x_m"]), float(a["y_m"]) - float(b["y_m"]))


def point_gap_threshold(a: dict[str, Any], b: dict[str, Any]) -> float:
    far = max(float(a.get("distance_m") or 0.0), float(b.get("distance_m") or 0.0))
    return max(0.16, min(0.62, 0.05 + far * 0.045))


def fit_wall(cluster: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(cluster) < 4:
        return None
    features = line_features(cluster)
    if not features:
        return None
    mean_range = features["mean_range"]
    max_width = min(0.24, max(0.11, 0.055 + mean_range * 0.012))
    aspect = features["aspect"]
    length = features["length"]
    width = features["width"]
    points = len(cluster)

    full_wall = points >= 6 and length >= 0.5 and width <= max_width and aspect >= 3.8
    fragment_wall = points >= 4 and length >= 0.32 and width <= max_width * 0.9 and aspect >= 3.2
    if not full_wall and not fragment_wall:
        return None

    confidence = min(0.98, 0.42 + min(0.32, length / 4.0) + min(0.18, aspect / 42.0) + min(0.08, points / 180.0))
    return {
        "label": "wall",
        "start": _round_point(features["start"]),
        "end": _round_point(features["end"]),
        "length_m": round(length, 3),
        "width_m": round(width, 3),
        "points": points,
        "confidence": round(confidence, 3),
        "_points": cluster,
        "_features": features,
    }


def line_features(cluster: list[dict[str, Any]]) -> dict[str, Any] | None:
    xs = [float(point["x_m"]) for point in cluster]
    ys = [float(point["y_m"]) for point in cluster]
    if not xs:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs) / len(xs)
    syy = sum((y - my) ** 2 for y in ys) / len(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
    theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    dx = math.cos(theta)
    dy = math.sin(theta)
    projections = [(x - mx) * dx + (y - my) * dy for x, y in zip(xs, ys)]
    residuals = sorted(abs(-(x - mx) * dy + (y - my) * dx) for x, y in zip(xs, ys))
    start_projection = min(projections)
    end_projection = max(projections)
    length = end_projection - start_projection
    width = residuals[int(0.9 * (len(residuals) - 1))] if residuals else 0.0
    mean_range = sum(math.hypot(x, y) for x, y in zip(xs, ys)) / len(xs)
    return {
        "mx": mx,
        "my": my,
        "dx": dx,
        "dy": dy,
        "theta": theta,
        "start_projection": start_projection,
        "end_projection": end_projection,
        "length": length,
        "width": width,
        "aspect": length / max(width, 0.01),
        "mean_range": mean_range,
        "start": {"x_m": mx + start_projection * dx, "y_m": my + start_projection * dy},
        "end": {"x_m": mx + end_projection * dx, "y_m": my + end_projection * dy},
    }


def merge_wall_fragments(walls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = list(walls)
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            if changed:
                break
            for j in range(i + 1, len(merged)):
                if not mergeable_walls(merged[i], merged[j]):
                    continue
                points = (merged[i].get("_points") or []) + (merged[j].get("_points") or [])
                wall = fit_wall(points)
                if not wall:
                    continue
                wall["confidence"] = round(max(float(merged[i].get("confidence", 0.0)), float(merged[j].get("confidence", 0.0)), float(wall.get("confidence", 0.0))), 3)
                merged[i] = wall
                merged.pop(j)
                changed = True
                break
    return merged


def mergeable_walls(a: dict[str, Any], b: dict[str, Any]) -> bool:
    fa = a.get("_features") or {}
    fb = b.get("_features") or {}
    if not fa or not fb:
        return False
    angle_delta = abs(float(fa["theta"]) - float(fb["theta"])) % math.pi
    angle_delta = min(angle_delta, math.pi - angle_delta)
    if angle_delta > math.radians(14):
        return False
    dx = float(fa["dx"])
    dy = float(fa["dy"])
    ax = float(fa["mx"])
    ay = float(fa["my"])
    bx = float(fb["mx"])
    by = float(fb["my"])
    lateral = abs(-(bx - ax) * dy + (by - ay) * dx)
    if lateral > 0.34:
        return False
    b_start = (bx - ax) * dx + (by - ay) * dy + float(fb["start_projection"])
    b_end = (bx - ax) * dx + (by - ay) * dy + float(fb["end_projection"])
    a_start = float(fa["start_projection"])
    a_end = float(fa["end_projection"])
    gap = max(a_start, b_start) - min(a_end, b_end)
    return gap <= 1.35


def public_wall(wall: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in wall.items() if not key.startswith("_")}


def object_from_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any] | None:
    xs = [float(point["x_m"]) for point in cluster]
    ys = [float(point["y_m"]) for point in cluster]
    if len(xs) < 3:
        return None
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    radius = max(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys))
    distance = math.hypot(cx, cy)
    features = line_features(cluster)
    if features and features["length"] > 0.28 and features["aspect"] >= 3.0 and features["width"] <= 0.2:
        return None
    if radius > 0.75:
        return None
    density = len(cluster) / max(math.pi * max(radius, 0.05) ** 2, 0.01)
    label = "trunk" if radius <= 0.38 and len(cluster) >= 3 and density >= 8.0 else "object"
    return {
        "label": label,
        "center": {"x_m": round(cx, 3), "y_m": round(cy, 3)},
        "radius_m": round(max(0.08, min(radius, 0.75)), 3),
        "distance_m": round(distance, 3),
        "points": len(cluster),
        "density": round(density, 2),
    }


def detect_orchard_trees(
    points: list[dict[str, Any]],
    walls: list[dict[str, Any]] | None = None,
    clusters: list[list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    clusters = clusters if clusters is not None else cluster_points(points)
    if walls is None:
        fitted_walls = [wall for cluster in clusters if (wall := fit_wall(cluster))]
        walls = merge_wall_fragments(fitted_walls)
    trees: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    excluded_rear = 0
    wall_suppressed = 0
    linear_suppressed = 0
    raw_candidates = 0
    for cluster in clusters:
        candidate = orchard_candidate_from_cluster(cluster)
        if not candidate:
            continue
        raw_candidates += 1
        center = candidate["center"]
        if is_rear_person_zone(center):
            excluded_rear += 1
            rejected.append(rejected_orchard_candidate(candidate, "rear_operator_exclusion_zone"))
            continue
        wall_gate = candidate_wall_gate(candidate, walls)
        if wall_gate:
            wall_suppressed += 1
            row = rejected_orchard_candidate(candidate, str(wall_gate.get("reason") or "near_wall_segment"))
            row["wall_relation"] = wall_gate
            rejected.append(row)
            continue
        linear_gate = candidate_linear_context_gate(candidate, points)
        if linear_gate:
            linear_suppressed += 1
            row = rejected_orchard_candidate(candidate, str(linear_gate.get("reason") or "embedded_in_linear_structure"))
            row["linear_context"] = linear_gate
            rejected.append(row)
            continue
        relation = nearest_wall_relation(candidate, walls)
        candidate["wall_distance_m"] = relation.get("distance_m")
        candidate["nearest_wall_length_m"] = relation.get("wall_length_m")
        candidate["admission"] = "geometry_candidate"
        candidate["rejection_reasons"] = []
        trees.append(candidate)
    trees = merge_orchard_candidates(trees)
    rejection_counts: dict[str, int] = {}
    for candidate in rejected:
        for reason in candidate.get("rejection_reasons") or []:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
    return {
        "method": "lidar_tree_geometry_wall_context_ood_v4",
        "trees": trees,
        "rejected_candidates": rejected[:32],
        "raw_candidate_count": raw_candidates,
        "excluded_rear_candidates": excluded_rear,
        "wall_suppressed_candidates": wall_suppressed,
        "linear_context_suppressed_candidates": linear_suppressed,
        "rejection_counts": rejection_counts,
    }


def orchard_candidate_from_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(cluster) < 3:
        return None
    xs = [float(point["x_m"]) for point in cluster]
    ys = [float(point["y_m"]) for point in cluster]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    distance = math.hypot(cx, cy)
    if distance < 0.85 or distance > 13.5:
        return None
    radius = max(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys))
    if radius < 0.035 or radius > 0.58:
        return None
    features = line_features(cluster)
    if fit_wall(cluster):
        return None
    aspect = float(features.get("aspect", 1.0)) if features else 1.0
    width = float(features.get("width", radius)) if features else radius
    length = float(features.get("length", radius * 2.0)) if features else radius * 2.0
    if aspect > 4.6 and length > 0.42 and width < 0.18:
        return None
    line_width_limit = max(0.09, min(0.24, 0.05 + distance * 0.013))
    line_like_fragment = length >= 0.28 and aspect >= 2.75 and width <= line_width_limit
    if line_like_fragment and (radius >= 0.11 or len(cluster) >= 5):
        return None
    if len(cluster) > 70 and distance < 1.35:
        return None
    density = len(cluster) / max(math.pi * max(radius, 0.05) ** 2, 0.01)
    compact_score = 1.0 - min(1.0, abs(radius - 0.18) / 0.36)
    density_score = min(1.0, density / 55.0)
    aspect_score = max(0.0, 1.0 - max(0.0, aspect - 1.7) / 3.0)
    point_score = max(0.0, 1.0 - abs(len(cluster) - 12) / 44.0)
    range_score = max(0.0, 1.0 - max(0.0, distance - 8.5) / 6.0)
    trunk_like = radius <= 0.34 and aspect <= 3.8 and len(cluster) <= 48
    clutter_penalty = 0.12 if radius > 0.36 or len(cluster) > 50 else 0.0
    confidence = (
        0.16
        + 0.34 * compact_score
        + 0.18 * density_score
        + 0.18 * aspect_score
        + 0.08 * point_score
        + 0.06 * range_score
        - clutter_penalty
    )
    if trunk_like:
        confidence += 0.08
    if confidence < 0.56:
        return None
    return {
        "label": "orchard_tree",
        "kind": "trunk_like" if trunk_like else "foliage_cluster",
        "center": {"x_m": round(cx, 3), "y_m": round(cy, 3)},
        "radius_m": round(max(0.1, min(radius, 0.75)), 3),
        "confidence": round(min(0.98, confidence), 3),
        "tree_probability": round(min(0.98, confidence), 3),
        "ood_score": round(max(0.02, 1.0 - min(0.98, confidence)), 3),
        "structure_score": round(max(0.0, min(0.99, 0.38 * compact_score + 0.24 * density_score + 0.24 * aspect_score + 0.14 * point_score)), 3),
        "points": len(cluster),
        "density": round(density, 2),
        "distance_m": round(distance, 3),
        "shape": {
            "aspect": round(aspect, 3),
            "length_m": round(length, 3),
            "width_m": round(width, 3),
        },
    }


def candidate_wall_gate(candidate: dict[str, Any], walls: list[dict[str, Any]]) -> dict[str, Any] | None:
    center = candidate.get("center") or {}
    try:
        cx = float(center["x_m"])
        cy = float(center["y_m"])
    except (KeyError, TypeError, ValueError):
        return None
    radius = float(candidate.get("radius_m") or 0.12)
    distance = float(candidate.get("distance_m") or math.hypot(cx, cy))
    gate_m = max(0.28, min(0.62, radius + 0.22 + distance * 0.014))
    for wall in walls:
        start = wall.get("start") or {}
        end = wall.get("end") or {}
        try:
            sx = float(start["x_m"])
            sy = float(start["y_m"])
            ex = float(end["x_m"])
            ey = float(end["y_m"])
        except (KeyError, TypeError, ValueError):
            continue
        wall_length = math.hypot(ex - sx, ey - sy)
        if wall_length < 0.45:
            continue
        projection, lateral = point_segment_projection(cx, cy, sx, sy, ex, ey)
        outside_m = 0.0
        if projection < 0.0:
            outside_m = -projection * wall_length
        elif projection > 1.0:
            outside_m = (projection - 1.0) * wall_length
        if lateral <= gate_m and outside_m <= 0.42:
            return {
                "reason": "near_wall_segment",
                "distance_m": round(lateral, 3),
                "gate_m": round(gate_m, 3),
                "wall_length_m": round(wall_length, 3),
            }
    return None


def nearest_wall_relation(candidate: dict[str, Any], walls: list[dict[str, Any]]) -> dict[str, Any]:
    center = candidate.get("center") or {}
    try:
        cx = float(center["x_m"])
        cy = float(center["y_m"])
    except (KeyError, TypeError, ValueError):
        return {"distance_m": None, "wall_length_m": None}
    nearest_distance = float("inf")
    nearest_length = None
    for wall in walls:
        start = wall.get("start") or {}
        end = wall.get("end") or {}
        try:
            sx, sy = float(start["x_m"]), float(start["y_m"])
            ex, ey = float(end["x_m"]), float(end["y_m"])
        except (KeyError, TypeError, ValueError):
            continue
        _, distance = point_segment_projection(cx, cy, sx, sy, ex, ey)
        if distance < nearest_distance:
            nearest_distance = distance
            nearest_length = math.hypot(ex - sx, ey - sy)
    return {
        "distance_m": round(nearest_distance, 3) if math.isfinite(nearest_distance) else None,
        "wall_length_m": round(float(nearest_length), 3) if nearest_length is not None else None,
    }


def candidate_linear_context_gate(candidate: dict[str, Any], points: list[dict[str, Any]]) -> dict[str, Any] | None:
    center = candidate.get("center") or {}
    try:
        cx = float(center["x_m"])
        cy = float(center["y_m"])
    except (KeyError, TypeError, ValueError):
        return None
    distance = float(candidate.get("distance_m") or math.hypot(cx, cy))
    neighborhood_radius = max(0.62, min(1.0, 0.58 + distance * 0.025))
    neighbors = [
        point
        for point in points
        if _finite(point.get("x_m"))
        and _finite(point.get("y_m"))
        and math.hypot(float(point["x_m"]) - cx, float(point["y_m"]) - cy) <= neighborhood_radius
    ]
    if len(neighbors) < 8:
        return None
    features = line_features(neighbors)
    if not features:
        return None
    length = float(features["length"])
    width = float(features["width"])
    aspect = float(features["aspect"])
    local_x = cx - float(features["mx"])
    local_y = cy - float(features["my"])
    lateral = abs(-local_x * float(features["dy"]) + local_y * float(features["dx"]))
    width_limit = max(0.12, min(0.23, 0.085 + distance * 0.011))
    if length >= 0.72 and aspect >= 4.1 and width <= width_limit and lateral <= width_limit + 0.08:
        return {
            "reason": "embedded_in_linear_structure",
            "length_m": round(length, 3),
            "width_m": round(width, 3),
            "aspect": round(aspect, 3),
            "lateral_distance_m": round(lateral, 3),
            "neighbor_points": len(neighbors),
        }
    return None


def rejected_orchard_candidate(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    row = dict(candidate)
    row["admission"] = "rejected"
    row["rejection_reasons"] = sorted(set((row.get("rejection_reasons") or []) + [reason]))
    row["ood_score"] = round(max(float(row.get("ood_score") or 0.0), 0.72), 3)
    return row


def attach_orchard_temporal_state(orchard: dict[str, Any], objects: list[dict[str, Any]]) -> dict[str, Any]:
    confirmed = []
    pending = []
    for candidate in orchard.get("trees") or []:
        row = dict(candidate)
        center = row.get("center") or {}
        cx = float(center.get("x_m") or 0.0)
        cy = float(center.get("y_m") or 0.0)
        nearest = None
        nearest_distance = float("inf")
        for obj in objects:
            obj_center = obj.get("center") or {}
            distance = math.hypot(cx - float(obj_center.get("x_m") or 0.0), cy - float(obj_center.get("y_m") or 0.0))
            if distance < nearest_distance:
                nearest = obj
                nearest_distance = distance
        gate = max(0.28, min(0.62, float(row.get("radius_m") or 0.12) + 0.24))
        if nearest is not None and nearest_distance <= gate:
            row["track_id"] = nearest.get("id")
            row["temporal_hits"] = int(nearest.get("track_hits") or 0)
            row["temporal_persistence_s"] = float(nearest.get("track_age_s") or 0.0)
            row["temporal_confidence"] = float(nearest.get("track_confidence") or 0.0)
            row["track_match_distance_m"] = round(nearest_distance, 3)
            is_confirmed = (
                int(nearest.get("track_hits") or 0) >= 2
                and bool(nearest.get("track_drawable"))
                and float(nearest.get("track_confidence") or 0.0) >= 0.46
                and float(row.get("structure_score") or 0.0) >= 0.42
            )
        else:
            row["temporal_hits"] = 0
            row["temporal_persistence_s"] = 0.0
            row["temporal_confidence"] = 0.0
            row["track_match_distance_m"] = None
            is_confirmed = False
        row["admission"] = "temporal_confirmed" if is_confirmed else "temporal_pending"
        row["map_admission"] = "accepted" if is_confirmed else "pending"
        (confirmed if is_confirmed else pending).append(row)
    output = dict(orchard)
    output["trees"] = confirmed + pending
    output["confirmed_trees"] = confirmed
    output["pending_trees"] = pending
    output["temporal_method"] = "predictive_object_track_two_hit_gate_v1"
    output["confirmed_count"] = len(confirmed)
    output["pending_count"] = len(pending)
    return output


def point_segment_projection(px: float, py: float, sx: float, sy: float, ex: float, ey: float) -> tuple[float, float]:
    vx = ex - sx
    vy = ey - sy
    length_sq = vx * vx + vy * vy
    if length_sq <= 1e-9:
        return 0.0, math.hypot(px - sx, py - sy)
    projection = ((px - sx) * vx + (py - sy) * vy) / length_sq
    clamped = max(0.0, min(1.0, projection))
    closest_x = sx + clamped * vx
    closest_y = sy + clamped * vy
    return projection, math.hypot(px - closest_x, py - closest_y)


def is_rear_person_zone(center: dict[str, Any]) -> bool:
    x = float(center.get("x_m") or 0.0)
    y = float(center.get("y_m") or 0.0)
    return y < -0.35 and abs(x) < 1.6 and math.hypot(x, y) < 4.6


def merge_orchard_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: float(item.get("confidence") or 0.0), reverse=True):
        cx = float(candidate["center"]["x_m"])
        cy = float(candidate["center"]["y_m"])
        found = None
        for item in merged:
            ix = float(item["center"]["x_m"])
            iy = float(item["center"]["y_m"])
            gate = max(0.32, min(0.78, 0.16 + 0.035 * max(float(candidate.get("distance_m") or 0.0), float(item.get("distance_m") or 0.0))))
            if candidate.get("kind") != item.get("kind") and gate < 0.5:
                continue
            if math.hypot(cx - ix, cy - iy) <= gate:
                found = item
                break
        if not found:
            candidate["hits"] = 1
            merged.append(candidate)
            continue
        hits = int(found.get("hits") or 1) + 1
        weight = 1.0 / hits
        found["center"] = {
            "x_m": round(float(found["center"]["x_m"]) * (1.0 - weight) + cx * weight, 3),
            "y_m": round(float(found["center"]["y_m"]) * (1.0 - weight) + cy * weight, 3),
        }
        found["radius_m"] = round(max(float(found.get("radius_m") or 0.1), float(candidate.get("radius_m") or 0.1)), 3)
        found["confidence"] = round(max(float(found.get("confidence") or 0.0), float(candidate.get("confidence") or 0.0)), 3)
        found["points"] = int(found.get("points") or 0) + int(candidate.get("points") or 0)
        found["hits"] = hits
    return merged


def track_summary(objects: list[dict[str, Any]]) -> dict[str, Any]:
    drawable = sum(1 for item in objects if item.get("track_drawable"))
    tracked = sum(1 for item in objects if int(item.get("track_hits") or 0) >= 2)
    return {
        "method": "sensor_relative_predictive_tracks_v4",
        "object_tracks": tracked,
        "drawable_tracks": drawable,
    }


def update_tracks(
    objects: list[dict[str, Any]],
    tracks: dict[str, dict[str, Any]],
    next_track_id: int,
    now: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
    matched: set[str] = set()
    for obj in sorted(objects, key=lambda item: float(item.get("radius_m") or 0.0)):
        cx = float(obj["center"]["x_m"])
        cy = float(obj["center"]["y_m"])
        radius = float(obj.get("radius_m") or 0.18)
        best_id = None
        best_score = float("inf")
        for track_id, track in tracks.items():
            if track_id in matched:
                continue
            dt = max(0.001, now - float(track.get("last_at", now)))
            if dt > 3.0:
                continue
            last = track.get("last", {"x_m": cx, "y_m": cy})
            velocity = track.get("velocity", {"x_mps": 0.0, "y_mps": 0.0})
            predicted_x = float(last["x_m"]) + float(velocity.get("x_mps", 0.0)) * min(dt, 0.85)
            predicted_y = float(last["y_m"]) + float(velocity.get("y_mps", 0.0)) * min(dt, 0.85)
            dist = math.hypot(cx - predicted_x, cy - predicted_y)
            speed = math.hypot(float(velocity.get("x_mps", 0.0)), float(velocity.get("y_mps", 0.0)))
            previous_radius = float(track.get("radius_m") or radius)
            radius_delta = abs(radius - previous_radius) / max(radius, previous_radius, 0.08)
            if radius_delta > 1.35:
                continue
            range_gate = 0.015 * math.hypot(cx, cy)
            gate = min(1.25, max(0.38, 0.39 + dt * 1.0 + min(speed, 1.8) * 0.14 + range_gate))
            if dist > gate:
                continue
            instant_speed = math.hypot(cx - float(last["x_m"]), cy - float(last["y_m"])) / dt
            if instant_speed > 3.2:
                continue
            label_penalty = 0.16 if track.get("label") != obj.get("label") else 0.0
            score = dist / max(gate, 0.01) + radius_delta * 0.28 + label_penalty
            if score < best_score:
                best_id = track_id
                best_score = score
        if best_id is None:
            best_id = f"obj-{next_track_id}"
            next_track_id += 1
            tracks[best_id] = {
                "history": [],
                "first_at": now,
                "last_at": now,
                "last": {"x_m": cx, "y_m": cy},
                "velocity": {"x_mps": 0.0, "y_mps": 0.0},
                "radius_m": radius,
                "label": obj.get("label"),
                "hits": 0,
                "stable_hits": 0,
            }
        track = tracks[best_id]
        dt = max(0.001, now - float(track.get("last_at", now)))
        last = track.get("last", {"x_m": cx, "y_m": cy})
        instant_vx = (cx - float(last["x_m"])) / dt
        instant_vy = (cy - float(last["y_m"])) / dt
        previous_velocity = track.get("velocity", {"x_mps": 0.0, "y_mps": 0.0})
        vx = 0.38 * instant_vx + 0.62 * float(previous_velocity.get("x_mps", 0.0))
        vy = 0.38 * instant_vy + 0.62 * float(previous_velocity.get("y_mps", 0.0))
        speed_mps = math.hypot(vx, vy)
        history = (track.get("history") or [])[-8:] + [{"x_m": round(cx, 3), "y_m": round(cy, 3)}]
        quality = track_history_quality(history, speed_mps)
        track["history"] = history
        track["last"] = {"x_m": cx, "y_m": cy}
        track["last_at"] = now
        track["velocity"] = {"x_mps": vx, "y_mps": vy}
        track["radius_m"] = radius
        track["label"] = obj.get("label")
        track["hits"] = int(track.get("hits") or 0) + 1
        track["stable_hits"] = int(track.get("stable_hits") or 0) + (1 if quality["drawable"] else 0)
        matched.add(best_id)
        obj["id"] = best_id
        obj["velocity"] = {"x_mps": round(vx, 3), "y_mps": round(vy, 3), "speed_mps": round(speed_mps, 3)}
        obj["track"] = track["history"]
        obj["track_confidence"] = quality["confidence"]
        obj["track_drawable"] = quality["drawable"] and int(track.get("hits") or 0) >= 2
        obj["track_reference_frame"] = "sensor_relative"
        obj["track_age_s"] = round(now - float(track.get("first_at", now)), 2)
        obj["track_hits"] = track["hits"]
    for track_id in list(tracks):
        if now - float(tracks[track_id].get("last_at", 0.0)) > 4.0:
            tracks.pop(track_id, None)
    return objects, tracks, next_track_id


def track_history_quality(history: list[dict[str, float]], speed_mps: float) -> dict[str, Any]:
    if len(history) < 2:
        return {"drawable": False, "confidence": 0.0}
    segments = [
        math.hypot(float(history[index]["x_m"]) - float(history[index - 1]["x_m"]), float(history[index]["y_m"]) - float(history[index - 1]["y_m"]))
        for index in range(1, len(history))
    ]
    max_segment = max(segments, default=0.0)
    path_m = sum(segments)
    span_m = math.hypot(float(history[-1]["x_m"]) - float(history[0]["x_m"]), float(history[-1]["y_m"]) - float(history[0]["y_m"]))
    smoothness = 1.0 - min(1.0, max_segment / 1.25)
    speed_score = 1.0 - min(1.0, max(0.0, speed_mps - 1.8) / 1.2)
    compactness = 1.0 - min(1.0, max(0.0, path_m - span_m - 1.0) / 3.0)
    confidence = max(0.0, min(0.99, 0.18 + 0.36 * smoothness + 0.26 * speed_score + 0.2 * compactness))
    drawable = confidence >= 0.46 and max_segment <= 1.25 and speed_mps <= 2.6
    return {"drawable": drawable, "confidence": round(confidence, 3)}


def _round_point(point: dict[str, Any]) -> dict[str, float]:
    return {"x_m": round(float(point["x_m"]), 3), "y_m": round(float(point["y_m"]), 3)}


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
