#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

import cv2  # type: ignore
import numpy as np  # type: ignore

from dashboard_server.lidar_analysis import analyze_points


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate LiDAR wall/person/tree OOD on archived timelines.")
    parser.add_argument("--timeline", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preview-count", type=int, default=48)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    frame_rows = []
    previews = []
    for timeline_path in [Path(value) for value in args.timeline]:
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        source = str(timeline.get("id") or timeline_path.parent.name)
        frames = timeline.get("lidar", {}).get("frames") or []
        preview_indexes = set(np.linspace(0, max(0, len(frames) - 1), min(args.preview_count, len(frames)), dtype=int).tolist())
        tracks: dict[str, dict[str, Any]] = {}
        next_track_id = 1
        for index, frame in enumerate(frames):
            started = time.perf_counter()
            analysis, tracks, next_track_id = analyze_points(frame.get("points") or [], tracks, next_track_id, now=float(frame.get("t_s") or 0.0))
            orchard = analysis.get("orchard") or {}
            row = {
                "source": source,
                "index": index,
                "t_s": round(float(frame.get("t_s") or 0.0), 3),
                "points": len(frame.get("points") or []),
                "walls": len(analysis.get("walls") or []),
                "objects": len(analysis.get("objects") or []),
                "raw_candidates": int(orchard.get("raw_candidate_count") or 0),
                "accepted_candidates": len(orchard.get("trees") or []),
                "rejected_candidates": len(orchard.get("rejected_candidates") or []),
                "rejection_counts": orchard.get("rejection_counts") or {},
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "accepted": orchard.get("trees") or [],
                "rejected": orchard.get("rejected_candidates") or [],
            }
            frame_rows.append(row)
            if index in preview_indexes:
                previews.append((render_scan(frame.get("points") or [], analysis), f"{source[:18]} {row['t_s']:.1f}s +{row['accepted_candidates']}/-{row['rejected_candidates']} W{row['walls']}"))
    summary = summarize(frame_rows)
    artifact = {"summary": summary, "frames": frame_rows}
    (output / "evaluation.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    write_contact_sheet(previews, output / "lidar_tree_ood_contact_sheet.jpg")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, dict[str, Any]] = {}
    rejection_counts: dict[str, int] = {}
    for row in rows:
        bucket = by_source.setdefault(row["source"], {"frames": 0, "points": 0, "walls": 0, "raw_candidates": 0, "accepted_candidates": 0, "rejected_candidates": 0})
        for key in ("frames",):
            bucket[key] += 1
        for key in ("points", "walls", "raw_candidates", "accepted_candidates", "rejected_candidates"):
            bucket[key] += int(row[key])
        for reason, count in row.get("rejection_counts", {}).items():
            rejection_counts[reason] = rejection_counts.get(reason, 0) + int(count)
    latency = [float(row["elapsed_ms"]) for row in rows]
    return {
        "frames": len(rows),
        "raw_candidates": sum(int(row["raw_candidates"]) for row in rows),
        "accepted_candidates": sum(int(row["accepted_candidates"]) for row in rows),
        "rejected_candidates": sum(int(row["rejected_candidates"]) for row in rows),
        "rejection_counts": rejection_counts,
        "mean_ms": round(statistics.fmean(latency), 2) if latency else 0.0,
        "p95_ms": round(float(np.percentile(latency, 95)), 2) if latency else 0.0,
        "by_source": by_source,
    }


def render_scan(points: list[dict[str, Any]], analysis: dict[str, Any]) -> Any:
    size = 500
    center = size // 2
    max_range = 12.0
    scale = (size * 0.44) / max_range
    canvas = np.full((size, size, 3), (14, 21, 41), np.uint8)
    for radius in (2, 4, 6, 8, 10, 12):
        cv2.circle(canvas, (center, center), int(radius * scale), (42, 54, 81), 1, cv2.LINE_AA)
    for point in points:
        try:
            x, y = float(point["x_m"]), float(point["y_m"])
        except (KeyError, TypeError, ValueError):
            continue
        px, py = project(x, y, center, scale)
        if 0 <= px < size and 0 <= py < size:
            cv2.circle(canvas, (px, py), 1, (235, 200, 45), -1, cv2.LINE_AA)
    for wall in analysis.get("walls") or []:
        start, end = wall.get("start") or {}, wall.get("end") or {}
        cv2.line(canvas, project(float(start.get("x_m") or 0.0), float(start.get("y_m") or 0.0), center, scale), project(float(end.get("x_m") or 0.0), float(end.get("y_m") or 0.0), center, scale), (55, 205, 250), 3, cv2.LINE_AA)
    orchard = analysis.get("orchard") or {}
    for candidate in orchard.get("rejected_candidates") or []:
        candidate_marker(canvas, candidate, center, scale, (40, 60, 235), rejected=True)
    for candidate in orchard.get("trees") or []:
        candidate_marker(canvas, candidate, center, scale, (70, 235, 100), rejected=False)
    cv2.circle(canvas, (center, center), 5, (250, 250, 250), -1, cv2.LINE_AA)
    return canvas


def candidate_marker(canvas: Any, candidate: dict[str, Any], center: int, scale: float, color: tuple[int, int, int], rejected: bool) -> None:
    point = candidate.get("center") or {}
    px, py = project(float(point.get("x_m") or 0.0), float(point.get("y_m") or 0.0), center, scale)
    radius = max(4, int(float(candidate.get("radius_m") or 0.12) * scale))
    if rejected:
        cv2.line(canvas, (px - radius, py - radius), (px + radius, py + radius), color, 2, cv2.LINE_AA)
        cv2.line(canvas, (px - radius, py + radius), (px + radius, py - radius), color, 2, cv2.LINE_AA)
    else:
        cv2.circle(canvas, (px, py), radius, color, 2, cv2.LINE_AA)


def project(x: float, y: float, center: int, scale: float) -> tuple[int, int]:
    return int(round(center + x * scale)), int(round(center - y * scale))


def write_contact_sheet(items: list[tuple[Any, str]], path: Path) -> None:
    if not items:
        return
    columns = 6
    cell = 250
    label_height = 28
    rows = math.ceil(len(items) / columns)
    sheet = np.full((rows * (cell + label_height), columns * cell, 3), 10, np.uint8)
    for index, (image, label) in enumerate(items):
        resized = cv2.resize(image, (cell, cell), interpolation=cv2.INTER_AREA)
        x = (index % columns) * cell
        y = (index // columns) * (cell + label_height)
        sheet[y : y + cell, x : x + cell] = resized
        cv2.putText(sheet, label, (x + 3, y + cell + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (100, 225, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])


if __name__ == "__main__":
    raise SystemExit(main())
