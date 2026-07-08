from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any


ALGORITHM_NAME = "polar_gap_cluster_line_fit_fragment_merge_predictive_tracks_v3"
LIDAR_COORDINATE_FRAME = "lidar_bridge_xy_v1"


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
    orchard = detect_orchard_trees(points)
    analysis = {
        "ok": True,
        "algorithm": ALGORITHM_NAME,
        "coordinate_frame": LIDAR_COORDINATE_FRAME,
        "generated_at": iso_now(),
        "walls": [public_wall(wall) for wall in walls],
        "objects": objects,
        "orchard": orchard,
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
        "orchard": {"trees": [], "excluded_rear_candidates": 0, "method": "lidar_geometry_v1"},
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
        "points": len(cluster),
        "density": round(density, 2),
    }


def detect_orchard_trees(points: list[dict[str, Any]]) -> dict[str, Any]:
    trees: list[dict[str, Any]] = []
    excluded_rear = 0
    for cluster in cluster_points(points):
        candidate = orchard_candidate_from_cluster(cluster)
        if not candidate:
            continue
        center = candidate["center"]
        if is_rear_person_zone(center):
            excluded_rear += 1
            continue
        trees.append(candidate)
    trees = merge_orchard_candidates(trees)
    return {
        "method": "lidar_geometry_v1",
        "trees": trees,
        "excluded_rear_candidates": excluded_rear,
    }


def orchard_candidate_from_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(cluster) < 3:
        return None
    xs = [float(point["x_m"]) for point in cluster]
    ys = [float(point["y_m"]) for point in cluster]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    distance = math.hypot(cx, cy)
    if distance < 0.45 or distance > 14.0:
        return None
    radius = max(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys))
    if radius < 0.035 or radius > 0.65:
        return None
    features = line_features(cluster)
    aspect = float(features.get("aspect", 1.0)) if features else 1.0
    width = float(features.get("width", radius)) if features else radius
    length = float(features.get("length", radius * 2.0)) if features else radius * 2.0
    if aspect > 4.2 and length > 0.45 and width < 0.18:
        return None
    density = len(cluster) / max(math.pi * max(radius, 0.05) ** 2, 0.01)
    compact_score = 1.0 - min(1.0, abs(radius - 0.18) / 0.55)
    density_score = min(1.0, density / 28.0)
    aspect_score = max(0.0, 1.0 - max(0.0, aspect - 1.8) / 3.2)
    range_score = max(0.0, 1.0 - max(0.0, distance - 9.0) / 7.0)
    confidence = 0.2 + 0.32 * compact_score + 0.24 * density_score + 0.16 * aspect_score + 0.08 * range_score
    if confidence < 0.42:
        return None
    return {
        "label": "orchard_tree",
        "center": {"x_m": round(cx, 3), "y_m": round(cy, 3)},
        "radius_m": round(max(0.1, min(radius, 0.75)), 3),
        "confidence": round(min(0.98, confidence), 3),
        "points": len(cluster),
        "density": round(density, 2),
        "distance_m": round(distance, 3),
    }


def is_rear_person_zone(center: dict[str, Any]) -> bool:
    x = float(center.get("x_m") or 0.0)
    y = float(center.get("y_m") or 0.0)
    return y < -0.35 and abs(x) < 4.5


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
        best_id = None
        best_dist = float("inf")
        for track_id, track in tracks.items():
            if track_id in matched:
                continue
            dt = max(0.001, now - float(track.get("last_at", now)))
            last = track.get("last", {"x_m": cx, "y_m": cy})
            velocity = track.get("velocity", {"x_mps": 0.0, "y_mps": 0.0})
            predicted_x = float(last["x_m"]) + float(velocity.get("x_mps", 0.0)) * min(dt, 0.85)
            predicted_y = float(last["y_m"]) + float(velocity.get("y_mps", 0.0)) * min(dt, 0.85)
            dist = math.hypot(cx - predicted_x, cy - predicted_y)
            speed = math.hypot(float(velocity.get("x_mps", 0.0)), float(velocity.get("y_mps", 0.0)))
            gate = min(2.2, max(0.7, 0.45 + dt * 1.7 + speed * 0.22))
            if dist < gate and dist < best_dist:
                best_id = track_id
                best_dist = dist
        if best_id is None:
            best_id = f"obj-{next_track_id}"
            next_track_id += 1
            tracks[best_id] = {
                "history": [],
                "first_at": now,
                "last_at": now,
                "last": {"x_m": cx, "y_m": cy},
                "velocity": {"x_mps": 0.0, "y_mps": 0.0},
                "hits": 0,
            }
        track = tracks[best_id]
        dt = max(0.001, now - float(track.get("last_at", now)))
        last = track.get("last", {"x_m": cx, "y_m": cy})
        instant_vx = (cx - float(last["x_m"])) / dt
        instant_vy = (cy - float(last["y_m"])) / dt
        previous_velocity = track.get("velocity", {"x_mps": 0.0, "y_mps": 0.0})
        vx = 0.52 * instant_vx + 0.48 * float(previous_velocity.get("x_mps", 0.0))
        vy = 0.52 * instant_vy + 0.48 * float(previous_velocity.get("y_mps", 0.0))
        track["history"] = (track.get("history") or [])[-13:] + [{"x_m": round(cx, 3), "y_m": round(cy, 3)}]
        track["last"] = {"x_m": cx, "y_m": cy}
        track["last_at"] = now
        track["velocity"] = {"x_mps": vx, "y_mps": vy}
        track["hits"] = int(track.get("hits") or 0) + 1
        matched.add(best_id)
        obj["id"] = best_id
        obj["velocity"] = {"x_mps": round(vx, 3), "y_mps": round(vy, 3), "speed_mps": round(math.hypot(vx, vy), 3)}
        obj["track"] = track["history"]
        obj["track_age_s"] = round(now - float(track.get("first_at", now)), 2)
        obj["track_hits"] = track["hits"]
    for track_id in list(tracks):
        if now - float(tracks[track_id].get("last_at", 0.0)) > 4.0:
            tracks.pop(track_id, None)
    return objects, tracks, next_track_id


def _round_point(point: dict[str, Any]) -> dict[str, float]:
    return {"x_m": round(float(point["x_m"]), 3), "y_m": round(float(point["y_m"]), 3)}


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
