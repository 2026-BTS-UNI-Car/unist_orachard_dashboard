from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any


ALGORITHM_NAME = "polar_gap_cluster_line_fit_nearest_neighbor_tracks_v2"


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
    walls = []
    objects = []
    for cluster in clusters:
        wall = fit_wall(cluster)
        if wall:
            walls.append(wall)
            continue
        obj = object_from_cluster(cluster)
        if obj:
            objects.append(obj)
    objects, tracks, next_track_id = update_tracks(objects, tracks, next_track_id, now)
    analysis = {
        "ok": True,
        "algorithm": ALGORITHM_NAME,
        "generated_at": iso_now(),
        "walls": walls,
        "objects": objects,
        "cluster_count": len(clusters),
        "source_point_count": len(points),
    }
    return analysis, tracks, next_track_id


def empty_analysis() -> dict[str, Any]:
    return {
        "ok": False,
        "algorithm": ALGORITHM_NAME,
        "walls": [],
        "objects": [],
        "cluster_count": 0,
        "source_point_count": 0,
    }


def cluster_points(points: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    ordered = sorted(
        (point for point in points if _finite(point.get("x_m")) and _finite(point.get("y_m"))),
        key=lambda point: float(point.get("angle_deg") or 0.0),
    )
    for point in ordered:
        if previous:
            gap = math.hypot(float(point["x_m"]) - float(previous["x_m"]), float(point["y_m"]) - float(previous["y_m"]))
            threshold = max(0.18, 0.06 * max(float(point.get("distance_m") or 0.0), float(previous.get("distance_m") or 0.0)))
            if gap > threshold:
                if len(current) >= 4:
                    clusters.append(current)
                current = []
        current.append(point)
        previous = point
    if len(current) >= 4:
        clusters.append(current)
    return clusters


def fit_wall(cluster: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(cluster) < 8:
        return None
    xs = [float(point["x_m"]) for point in cluster]
    ys = [float(point["y_m"]) for point in cluster]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs) / len(xs)
    syy = sum((y - my) ** 2 for y in ys) / len(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
    theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    dx = math.cos(theta)
    dy = math.sin(theta)
    projections = [(x - mx) * dx + (y - my) * dy for x, y in zip(xs, ys)]
    residuals = [abs(-(x - mx) * dy + (y - my) * dx) for x, y in zip(xs, ys)]
    length = max(projections) - min(projections)
    width = sorted(residuals)[int(0.9 * (len(residuals) - 1))]
    if length < 0.65 or width > 0.16 or length / max(width, 0.01) < 6.0:
        return None
    a = min(projections)
    b = max(projections)
    return {
        "label": "wall",
        "start": {"x_m": round(mx + a * dx, 3), "y_m": round(my + a * dy, 3)},
        "end": {"x_m": round(mx + b * dx, 3), "y_m": round(my + b * dy, 3)},
        "length_m": round(length, 3),
        "width_m": round(width, 3),
        "points": len(cluster),
    }


def object_from_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any] | None:
    xs = [float(point["x_m"]) for point in cluster]
    ys = [float(point["y_m"]) for point in cluster]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    radius = max(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys))
    if radius > 0.7:
        return None
    return {
        "label": "object",
        "center": {"x_m": round(cx, 3), "y_m": round(cy, 3)},
        "radius_m": round(max(0.08, radius), 3),
        "points": len(cluster),
    }


def update_tracks(
    objects: list[dict[str, Any]],
    tracks: dict[str, dict[str, Any]],
    next_track_id: int,
    now: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
    matched: set[str] = set()
    for obj in objects:
        cx = float(obj["center"]["x_m"])
        cy = float(obj["center"]["y_m"])
        best_id = None
        best_dist = 0.55
        for track_id, track in tracks.items():
            if track_id in matched:
                continue
            tx = float(track["last"]["x_m"])
            ty = float(track["last"]["y_m"])
            dist = math.hypot(cx - tx, cy - ty)
            if dist < best_dist:
                best_id = track_id
                best_dist = dist
        if best_id is None:
            best_id = f"obj-{next_track_id}"
            next_track_id += 1
            tracks[best_id] = {"history": [], "last_at": now, "last": {"x_m": cx, "y_m": cy}}
        track = tracks[best_id]
        dt = max(0.001, now - float(track.get("last_at", now)))
        last = track.get("last", {"x_m": cx, "y_m": cy})
        velocity = {"x_mps": round((cx - float(last["x_m"])) / dt, 3), "y_mps": round((cy - float(last["y_m"])) / dt, 3)}
        track["history"] = (track.get("history") or [])[-7:] + [{"x_m": round(cx, 3), "y_m": round(cy, 3)}]
        track["last"] = {"x_m": cx, "y_m": cy}
        track["last_at"] = now
        matched.add(best_id)
        obj["id"] = best_id
        obj["velocity"] = velocity
        obj["track"] = track["history"]
    for track_id in list(tracks):
        if now - float(tracks[track_id].get("last_at", 0.0)) > 2.0:
            tracks.pop(track_id, None)
    return objects, tracks, next_track_id


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
