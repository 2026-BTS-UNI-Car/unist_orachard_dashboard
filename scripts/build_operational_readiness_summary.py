#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


ARCHIVES = ("20260708T053333Z_both", "20260708T054036Z_both")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a reproducible Jetson operational-readiness summary.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument(
        "--output",
        default="runtime/analysis/operational_readiness/readiness_summary.json",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def get_json(url: str) -> dict[str, Any]:
    return json.loads(urlopen(url, timeout=10).read().decode("utf-8"))


def benchmark_segment(recording_id: str) -> dict[str, Any]:
    url = f"http://127.0.0.1:8080/api/recordings/{recording_id}/timeline?start_s=0&duration_s=4"
    rows = []
    for _ in range(2):
        request = Request(url, headers={"Accept-Encoding": "gzip"})
        started = time.perf_counter()
        with urlopen(request, timeout=15) as response:
            raw = response.read()
            encoding = response.headers.get("Content-Encoding")
        body = gzip.decompress(raw) if encoding == "gzip" else raw
        payload = json.loads(body)
        rows.append(
            {
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "wire_bytes": len(raw),
                "json_bytes": len(body),
                "content_encoding": encoding or "identity",
                "lidar_frames": len(payload["timeline"]["lidar"]["frames"]),
                "camera_frames": len(payload["timeline"]["camera"]["frames"]),
            }
        )
    return {"cold_or_first": rows[0], "warm": rows[1]}


def training_metrics(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    best = max(rows, key=lambda row: float(row["metrics/mAP50-95(M)"]))
    return {
        "selection": "maximum validation mask mAP50-95",
        "epoch": int(best["epoch"]),
        "box": {
            "precision": round(float(best["metrics/precision(B)"]), 5),
            "recall": round(float(best["metrics/recall(B)"]), 5),
            "map50": round(float(best["metrics/mAP50(B)"]), 5),
            "map50_95": round(float(best["metrics/mAP50-95(B)"]), 5),
        },
        "mask": {
            "precision": round(float(best["metrics/precision(M)"]), 5),
            "recall": round(float(best["metrics/recall(M)"]), 5),
            "map50": round(float(best["metrics/mAP50(M)"]), 5),
            "map50_95": round(float(best["metrics/mAP50-95(M)"]), 5),
        },
    }


def map_summary(root: Path, recording_id: str) -> dict[str, Any]:
    payload = load_json(root / "runtime" / "orchard_analysis" / f"{recording_id}_orchard_map.json")
    orchard_map = payload["map"]
    path = orchard_map.get("path") or []
    return {
        "algorithm": orchard_map.get("algorithm"),
        "path_points": len(path),
        "start_pose": path[0] if path else None,
        "end_pose": path[-1] if path else None,
        "layers": {name: len(layer.get("trees") or []) for name, layer in orchard_map.get("maps", {}).items()},
        "odometry": orchard_map.get("odometry"),
        "lidar_ood": orchard_map.get("lidar_ood"),
        "final_turn_from_45s_deg": (
            round(float(path[-1]["heading_unwrapped_deg"]) - float(min(path, key=lambda row: abs(float(row["t_s"]) - 45.0))["heading_unwrapped_deg"]), 1)
            if recording_id == ARCHIVES[0] and path
            else None
        ),
    }


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)

    camera_eval = load_json(root / "runtime/analysis/operational_readiness/trt_model_eval_v2/evaluation.json")["summary"]
    lidar_eval = load_json(root / "runtime/analysis/operational_readiness/lidar_tree_ood_v1/evaluation.json")["summary"]
    weights = root / "runtime/training_runs/orchard_trunk_teacher_ood_v1/weights"
    disk = shutil.disk_usage(root)
    summary = {
        "generated_at_unix": round(time.time(), 3),
        "generated_on": "Jetson Orin Nano",
        "status": "operational_research_prototype",
        "dashboard_health": get_json("http://127.0.0.1:8080/api/health"),
        "vision_worker_health": get_json("http://127.0.0.1:8785/health"),
        "camera": {
            "training": training_metrics(root / "runtime/training_runs/orchard_trunk_teacher_ood_v1/results.csv"),
            "evaluation": camera_eval,
            "artifacts": {name: {"bytes": (weights / name).stat().st_size} for name in ("best.pt", "best.onnx", "best.engine")},
        },
        "lidar": {"evaluation": lidar_eval},
        "maps": {recording_id: map_summary(root, recording_id) for recording_id in ARCHIVES},
        "archive_streaming": {recording_id: benchmark_segment(recording_id) for recording_id in ARCHIVES},
        "disk": {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free},
        "safety_and_limits": [
            "No physical rover motion was commanded during validation.",
            "Camera and LiDAR counts are not precision/recall against human ground truth; manual orchard GT remains required for a publishable accuracy claim.",
            "LiDAR odometry is scan-derived because wheel odometry, IMU and TF were absent from these two archives.",
            "Fusion is a research layer and is not yet a safety-rated navigation input.",
        ],
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
