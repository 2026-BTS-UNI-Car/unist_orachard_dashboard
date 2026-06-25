#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path


def build_snapshot(t: float) -> dict:
    lidar = 0.74 + 0.16 * math.sin(t / 9.0)
    vision = 0.68 + 0.20 * math.sin(t / 12.0 + 0.8)
    total = max(lidar + vision, 0.01)
    lane_y = [-3.0, 0.0, 3.0][int(t / 45.0) % 3]
    x = -4.0 + ((t * 0.35) % 30.0)
    return {
        "rover": {
            "mode": "MOCK_FIELD_TEST",
            "state": "AUTONOMY_READY",
            "pose": {"x": round(x, 2), "y": lane_y, "yaw_deg": round(4.0 * math.sin(t / 6.0), 1)},
            "speed_mps": round(0.32 + 0.04 * math.sin(t / 5.0), 2),
            "battery_percent": round(max(20.0, 87.0 - t / 240.0), 1),
            "motor_state": "enabled",
            "estop": False,
        },
        "fusion": {
            "lidar_reliability": round(max(0.0, min(1.0, lidar)), 2),
            "vision_reliability": round(max(0.0, min(1.0, vision)), 2),
            "lidar_weight": round(lidar / total, 2),
            "vision_weight": round(vision / total, 2),
            "centerline_error_m": round(0.05 + abs(0.11 * math.sin(t / 8.0)), 3),
            "path_tracking_error_m": round(0.07 + abs(0.09 * math.cos(t / 10.0)), 3),
            "row_detection_count": random.choice([3, 4, 4, 4, 5]),
        },
        "experiment": {
            "session_id": "local-mock-loop",
            "runtime_s": round(t, 1),
            "distance_m": round(t * 0.31, 1),
            "rows_completed": int(t / 45.0) % 4,
            "map_coverage_percent": round(min(100.0, 18.0 + t / 6.0), 1),
            "weather_tag": "mock",
            "gps_dependency": "none",
        },
        "alerts": [
            {"level": "info", "message": "update_sample_telemetry.py가 생성한 mock 데이터입니다."}
        ],
    }


def write_snapshot(path: Path, start: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = build_snapshot(time.time() - start)
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write dashboard telemetry fixture data.")
    parser.add_argument("--output", default="runtime/telemetry_snapshot.json")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()

    path = Path(args.output)
    start = time.time()
    while True:
        write_snapshot(path, start)
        if not args.loop:
            break
        time.sleep(max(0.1, args.interval))


if __name__ == "__main__":
    main()
