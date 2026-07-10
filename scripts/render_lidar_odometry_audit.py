#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import math
from pathlib import Path
from typing import Any

import cv2  # type: ignore
import numpy as np  # type: ignore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render LiDAR trajectory and synchronized camera audit frames.")
    parser.add_argument("--map", required=True)
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--times", default="0,2.8,3.9,4.9,36.6,41.6,45.0,47.9,49.1,51.4")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    orchard_map = json.loads(Path(args.map).read_text(encoding="utf-8"))
    if isinstance(orchard_map.get("map"), dict):
        orchard_map = orchard_map["map"]
    timeline = json.loads(Path(args.timeline).read_text(encoding="utf-8"))
    (output / "orchard_map.json").write_text(
        json.dumps(orchard_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    render_path(orchard_map, output / "trajectory.png")
    times = [float(value) for value in args.times.split(",") if value.strip()]
    render_camera_sheet(timeline, orchard_map, times, output / "camera_motion_contact_sheet.jpg")
    print(json.dumps({"trajectory": str(output / "trajectory.png"), "camera": str(output / "camera_motion_contact_sheet.jpg")}, indent=2))
    return 0


def render_path(orchard_map: dict[str, Any], path: Path) -> None:
    width, height = 1200, 900
    margin = 90
    canvas = np.full((height, width, 3), (30, 25, 16), np.uint8)
    poses = orchard_map.get("path") or []
    tree_rows = []
    for layer, color in (("lidar", (255, 185, 50)), ("camera", (85, 220, 130)), ("fusion", (70, 125, 255))):
        for tree in orchard_map.get("maps", {}).get(layer, {}).get("trees", []) or []:
            tree_rows.append((float(tree.get("x_m") or 0.0), float(tree.get("y_m") or 0.0), color))
    xs = [float(item.get("x_m") or 0.0) for item in poses] + [row[0] for row in tree_rows] + [0.0]
    ys = [float(item.get("y_m") or 0.0) for item in poses] + [row[1] for row in tree_rows] + [0.0]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(2.0, max_x - min_x)
    span_y = max(2.0, max_y - min_y)
    scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)

    def point(x: float, y: float) -> tuple[int, int]:
        return int(margin + (x - min_x) * scale), int(height - margin - (y - min_y) * scale)

    for x, y, color in tree_rows:
        cv2.circle(canvas, point(x, y), 7, color, 2, cv2.LINE_AA)
    path_points = np.asarray([point(float(item.get("x_m") or 0.0), float(item.get("y_m") or 0.0)) for item in poses], np.int32)
    if len(path_points) >= 2:
        cv2.polylines(canvas, [path_points], False, (235, 235, 240), 3, cv2.LINE_AA)
    key_indexes = trajectory_label_indexes(poses)
    label_boxes: list[tuple[int, int, int, int]] = []
    for index, pose in enumerate(poses):
        if index not in key_indexes and not pose.get("turn_event"):
            continue
        t_s = float(pose.get("t_s") or 0.0)
        center = point(float(pose.get("x_m") or 0.0), float(pose.get("y_m") or 0.0))
        heading = math.radians(float(pose.get("heading_deg") or 0.0))
        tip = (int(center[0] + math.sin(heading) * 24), int(center[1] - math.cos(heading) * 24))
        color = (45, 210, 255) if pose.get("turn_event") else (160, 170, 190)
        cv2.arrowedLine(canvas, center, tip, color, 2, cv2.LINE_AA, tipLength=0.3)
        if index in key_indexes:
            label = f"{t_s:.1f}s / {float(pose.get('heading_unwrapped_deg') or 0):.0f} deg"
            draw_collision_free_label(canvas, center, label, color, label_boxes)
    if poses:
        cv2.circle(canvas, point(float(poses[0].get("x_m") or 0.0), float(poses[0].get("y_m") or 0.0)), 10, (80, 235, 110), -1, cv2.LINE_AA)
        cv2.circle(canvas, point(float(poses[-1].get("x_m") or 0.0), float(poses[-1].get("y_m") or 0.0)), 10, (55, 65, 245), -1, cv2.LINE_AA)
    title = f"{orchard_map.get('algorithm')} | heading {orchard_map.get('odometry', {}).get('heading_change_deg')} deg"
    cv2.putText(canvas, title, (30, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (240, 245, 250), 2, cv2.LINE_AA)
    cv2.imwrite(str(path), canvas)


def trajectory_label_indexes(poses: list[dict[str, Any]]) -> set[int]:
    if not poses:
        return set()
    indexes = {0, len(poses) - 1}
    duration = float(poses[-1].get("t_s") or 0.0)
    for target in np.arange(10.0, duration, 10.0):
        indexes.add(min(range(len(poses)), key=lambda index: abs(float(poses[index].get("t_s") or 0.0) - target)))
    last_heading = float(poses[0].get("heading_unwrapped_deg") or 0.0)
    last_time = float(poses[0].get("t_s") or 0.0)
    for index, pose in enumerate(poses[1:-1], start=1):
        heading = float(pose.get("heading_unwrapped_deg") or 0.0)
        stamp = float(pose.get("t_s") or 0.0)
        if pose.get("turn_event") and abs(heading - last_heading) >= 42.0 and stamp - last_time >= 1.4:
            indexes.add(index)
            last_heading = heading
            last_time = stamp
    return indexes


def draw_collision_free_label(
    canvas: Any,
    anchor: tuple[int, int],
    label: str,
    color: tuple[int, int, int],
    boxes: list[tuple[int, int, int, int]],
) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.43
    thickness = 1
    (text_width, text_height), _ = cv2.getTextSize(label, font, scale, thickness)
    candidates = ((10, -10), (10, 24), (-text_width - 10, -10), (-text_width - 10, 24), (10, 48), (-text_width - 10, 48))
    selected = None
    for offset_x, offset_y in candidates:
        x = max(5, min(canvas.shape[1] - text_width - 5, anchor[0] + offset_x))
        baseline_y = max(text_height + 5, min(canvas.shape[0] - 6, anchor[1] + offset_y))
        box = (x - 3, baseline_y - text_height - 3, x + text_width + 3, baseline_y + 4)
        if not any(boxes_overlap(box, other) for other in boxes):
            selected = (x, baseline_y, box)
            break
    if selected is None:
        x = max(5, min(canvas.shape[1] - text_width - 5, anchor[0] + 10))
        baseline_y = max(text_height + 5, min(canvas.shape[0] - 6, anchor[1] + 48))
        selected = (x, baseline_y, (x - 3, baseline_y - text_height - 3, x + text_width + 3, baseline_y + 4))
    x, baseline_y, box = selected
    boxes.append(box)
    cv2.line(canvas, anchor, (x, baseline_y - text_height // 2), (90, 100, 115), 1, cv2.LINE_AA)
    cv2.rectangle(canvas, (box[0], box[1]), (box[2], box[3]), (36, 39, 48), -1)
    cv2.putText(canvas, label, (x, baseline_y), font, scale, color, thickness, cv2.LINE_AA)


def boxes_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return not (a[2] + 4 < b[0] or b[2] + 4 < a[0] or a[3] + 4 < b[1] or b[3] + 4 < a[1])


def render_camera_sheet(timeline: dict[str, Any], orchard_map: dict[str, Any], times: list[float], path: Path) -> None:
    frames = timeline.get("camera", {}).get("frames") or []
    poses = orchard_map.get("path") or []
    tiles = []
    for target in times:
        frame = min(frames, key=lambda item: abs(float(item.get("t_s") or 0.0) - target)) if frames else None
        if not frame:
            continue
        try:
            image = cv2.imdecode(np.frombuffer(base64.b64decode(frame.get("data") or ""), np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            image = None
        if image is None:
            continue
        pose = min(poses, key=lambda item: abs(float(item.get("t_s") or 0.0) - target)) if poses else {}
        label = f"t={float(frame.get('t_s') or 0):.2f}s heading={float(pose.get('heading_unwrapped_deg') or 0):.1f} deg"
        tiles.append((image, label))
    columns = 2
    cell_width, cell_height = 640, 360
    rows = math.ceil(len(tiles) / columns)
    sheet = np.full((rows * (cell_height + 36), columns * cell_width, 3), 12, np.uint8)
    for index, (image, label) in enumerate(tiles):
        scale = min(cell_width / image.shape[1], cell_height / image.shape[0])
        resized = cv2.resize(image, (int(image.shape[1] * scale), int(image.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        x0 = (index % columns) * cell_width + (cell_width - resized.shape[1]) // 2
        y0 = (index // columns) * (cell_height + 36) + (cell_height - resized.shape[0]) // 2
        sheet[y0 : y0 + resized.shape[0], x0 : x0 + resized.shape[1]] = resized
        cv2.putText(sheet, label, ((index % columns) * cell_width + 8, (index // columns) * (cell_height + 36) + cell_height + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (80, 225, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])


if __name__ == "__main__":
    raise SystemExit(main())
