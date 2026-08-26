#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_server.orchard_mapping import (
    CENTERLINE_MAX_CONSECUTIVE_PAIRED_BIN_GAP,
    CENTERLINE_MIN_CONTIGUOUS_PAIRED_BINS,
    CENTERLINE_MIN_PAIRED_BIN_DENSITY,
    MAP_ALGORITHM_NAME,
    MAP_CACHE_REVISION,
    MIN_DATA_LIDAR_FRAMES,
    MIN_DATA_NONEMPTY_LIDAR_FRAMES,
    NAVIGATION_REQUIRED_VALIDATION_FLAGS,
    build_orchard_map,
    camera_metric_track_is_valid,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the current metric-anchor LiDAR/camera fusion on archived timeline JSON without modifying the archives."
    )
    parser.add_argument("--timeline", action="append", required=True, help="Timeline JSON; API wrappers with a top-level timeline key are accepted.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON report.")
    parser.add_argument("--output", help="Optional path for the aggregate JSON report.")
    return parser.parse_args()


def load_timeline(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    timeline = payload.get("timeline") if isinstance(payload, dict) else None
    if isinstance(timeline, dict):
        return timeline
    if not isinstance(payload, dict):
        raise ValueError("timeline payload must be a JSON object")
    return payload


def nearest_anchor_shift(tree: dict[str, Any], lidar_trees: list[dict[str, Any]]) -> float | None:
    if not lidar_trees:
        return None
    x = float(tree.get("x_m") or 0.0)
    y = float(tree.get("y_m") or 0.0)
    return min(math.hypot(x - float(item.get("x_m") or 0.0), y - float(item.get("y_m") or 0.0)) for item in lidar_trees)


def centerline_segment_gate_consistent(segment: dict[str, Any]) -> bool:
    if not segment.get("ok"):
        return bool(segment.get("fail_closed")) and not (segment.get("points") or [])
    interval = segment.get("paired_interval_s_m")
    if not isinstance(interval, list) or len(interval) != 2:
        return False
    station_min = float(interval[0])
    station_max = float(interval[1])
    return (
        bool(segment.get("straddles_vehicle"))
        and int(segment.get("left_occupied_bins") or 0) >= 3
        and int(segment.get("right_occupied_bins") or 0) >= 3
        and float(segment.get("left_longitudinal_span_m") or 0.0) >= 4.0
        and float(segment.get("right_longitudinal_span_m") or 0.0) >= 4.0
        and float(segment.get("bilateral_overlap_span_m") or 0.0) >= 4.0
        and float(segment.get("bilateral_coverage_ratio") or 0.0) >= 0.5
        and int(segment.get("paired_gap_count") or 0) >= 2
        and float(segment.get("paired_station_coverage_ratio") or 0.0) >= 0.35
        and int(segment.get("contiguous_paired_bin_count") or 0) >= CENTERLINE_MIN_CONTIGUOUS_PAIRED_BINS
        and int(segment.get("max_consecutive_paired_bin_gap") or 0)
        <= CENTERLINE_MAX_CONSECUTIVE_PAIRED_BIN_GAP
        and float(segment.get("paired_bin_density_ratio") or 0.0) >= CENTERLINE_MIN_PAIRED_BIN_DENSITY
        and all(station_min <= float(point.get("s_m") or 0.0) <= station_max for point in segment.get("points") or [])
    )


def corridor_segment_gate_consistent(segment: dict[str, Any]) -> bool:
    if segment.get("method") != "trajectory_segmented_time_local_bilateral_corridor_v10":
        return False
    if segment.get("evidence_mode") != "time_local_bilateral_corridor_consensus":
        return False
    if not segment.get("ok"):
        return bool(segment.get("fail_closed")) and not (segment.get("points") or [])
    points = segment.get("points") or []
    stations = [float(point.get("s_m") or 0.0) for point in points]
    row_gap = float(segment.get("row_gap_m") or 0.0)
    gap_mad = segment.get("corridor_row_gap_mad_m")
    lateral_p90 = segment.get("corridor_lateral_residual_p90_m")
    return (
        segment.get("evidence_mode") == "time_local_bilateral_corridor_consensus"
        and int(segment.get("bilateral_frame_count") or 0) >= 6
        and int(segment.get("corridor_occupied_bins") or 0) >= 3
        and float(segment.get("corridor_station_span_m") or 0.0) >= 4.0
        and float(segment.get("corridor_time_span_s") or 0.0) >= 2.0
        and 3.0 <= row_gap <= 7.0
        and gap_mad is not None
        and float(gap_mad) <= max(0.65, row_gap * 0.16)
        and lateral_p90 is not None
        and float(lateral_p90) <= 0.75
        and len(points) >= 3
        and max(stations, default=0.0) - min(stations, default=0.0) >= 4.0
    )


def evaluation_readiness(timeline: dict[str, Any], orchard_map: dict[str, Any]) -> dict[str, Any]:
    lidar_frames = (timeline.get("lidar") or {}).get("frames") or []
    camera_frames = (timeline.get("camera") or {}).get("frames") or []
    nonempty_lidar_frames = sum(1 for frame in lidar_frames if frame.get("points"))
    data_reasons: list[str] = []
    if len(lidar_frames) < MIN_DATA_LIDAR_FRAMES:
        data_reasons.append("insufficient_lidar_frames")
    if nonempty_lidar_frames < MIN_DATA_NONEMPTY_LIDAR_FRAMES:
        data_reasons.append("insufficient_nonempty_lidar_frames")
    data_sufficiency_ok = not data_reasons
    centerline = orchard_map.get("centerline") or {}
    navigation_reasons = list(data_reasons)
    if not centerline.get("ok") or len(centerline.get("points") or []) < 2:
        navigation_reasons.append("centerline_not_ready")
    sync = (orchard_map.get("fusion") or {}).get("sync") or {}
    if sync.get("clock_integrity_ok") is not True:
        navigation_reasons.append("sensor_header_clock_integrity_not_validated")
    validation = (
        timeline.get("navigation_validation")
        if isinstance(timeline.get("navigation_validation"), dict)
        else {}
    )
    navigation_reasons.extend(
        f"{key}_missing"
        for key in NAVIGATION_REQUIRED_VALIDATION_FLAGS
        if validation.get(key) is not True
    )
    navigation_ready = data_sufficiency_ok and not navigation_reasons
    return {
        "data_sufficiency_ok": data_sufficiency_ok,
        "navigation_ready": navigation_ready,
        "observed": {
            "lidar_frames": len(lidar_frames),
            "nonempty_lidar_frames": nonempty_lidar_frames,
            "camera_frames": len(camera_frames),
            "centerline_points": len(centerline.get("points") or []),
            "centerline_evidence_ready": bool(centerline.get("ok")),
            "sensor_header_clock_integrity_ok": sync.get("clock_integrity_ok") is True,
            "lidar_header_non_monotonic_count": int(sync.get("lidar_header_non_monotonic_count") or 0),
            "camera_header_non_monotonic_count": int(sync.get("camera_header_non_monotonic_count") or 0),
            "navigation_validation": {
                key: validation.get(key) is True
                for key in NAVIGATION_REQUIRED_VALIDATION_FLAGS
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


def evaluate_timeline(path: Path) -> dict[str, Any]:
    timeline = load_timeline(path)
    started = time.perf_counter()
    orchard_map = build_orchard_map(timeline)
    elapsed_s = time.perf_counter() - started
    maps = orchard_map.get("maps") or {}
    lidar_trees = (maps.get("lidar") or {}).get("trees") or []
    camera_trees = (maps.get("camera") or {}).get("trees") or []
    fusion_trees = (maps.get("fusion") or {}).get("trees") or []
    anchored = [tree for tree in fusion_trees if tree.get("metric_anchor") == "lidar"]
    anchor_shifts = [shift for tree in anchored if (shift := nearest_anchor_shift(tree, lidar_trees)) is not None]
    uncalibrated_promotions = [
        tree
        for tree in fusion_trees
        if tree.get("metric_anchor") != "lidar"
        and (
            tree.get("fusion_source") != "camera_only_calibrated"
            or not camera_metric_track_is_valid(tree)
        )
    ]
    centerline = orchard_map.get("centerline") or {}
    centerline_segments = centerline.get("segments") or []
    centerline_bilateral_gate_consistent = all(centerline_segment_gate_consistent(segment) for segment in centerline_segments)
    corridor_segments = centerline.get("corridor_segments") or []
    corridor_gate_consistent = all(corridor_segment_gate_consistent(segment) for segment in corridor_segments)
    fusion = orchard_map.get("fusion") or {}
    sync = fusion.get("sync") or {}
    readiness = evaluation_readiness(timeline, orchard_map)
    invariants = {
        "algorithm_is_current": orchard_map.get("algorithm") == MAP_ALGORITHM_NAME,
        "cache_revision_is_current": orchard_map.get("cache_revision") == MAP_CACHE_REVISION,
        "all_lidar_anchors_have_zero_position_shift": max(anchor_shifts, default=0.0) <= 0.001,
        "all_lidar_landmarks_are_preserved_as_metric_anchors": len(anchored) == len(lidar_trees),
        "no_uncalibrated_camera_only_promotion": not uncalibrated_promotions,
        "sync_accounting_complete": (
            int(sync.get("matched_frames") or 0)
            + int(sync.get("outside_gate_frames") or 0)
            + int(sync.get("camera_unavailable_frames") or 0)
            == int(sync.get("lidar_frames") or 0)
        ),
        "header_clock_regression_fails_navigation_readiness": (
            bool(sync.get("clock_integrity_ok"))
            or (
                not readiness["navigation_ready"]
                and "sensor_header_clock_integrity_not_validated"
                in readiness["navigation_readiness_reasons"]
            )
        ),
        "centerline_fail_closed": bool(centerline.get("ok")) or not (centerline.get("points") or []),
        "centerline_bilateral_gate_consistent": centerline_bilateral_gate_consistent,
        "time_local_corridor_gate_consistent": corridor_gate_consistent,
        "map_readiness_accounting_consistent": (
            bool(orchard_map.get("data_sufficiency_ok")) == readiness["data_sufficiency_ok"]
            and bool(orchard_map.get("navigation_ready")) == readiness["navigation_ready"]
        ),
    }
    implementation_invariants_ok = bool(orchard_map.get("ok")) and all(invariants.values())
    data_sufficiency_ok = bool(readiness["data_sufficiency_ok"])
    navigation_ready = bool(readiness["navigation_ready"])
    return {
        "recording": str(timeline.get("id") or path.stem.replace(".timeline", "")),
        "path": str(path),
        "algorithm": orchard_map.get("algorithm"),
        "cache_revision": orchard_map.get("cache_revision"),
        "ground_truth_available": False,
        "evaluation_scope": "self_consistency_and_fail_closed_invariants_only",
        "accuracy_claim_allowed": False,
        "duration_s": timeline.get("duration_s"),
        "runtime_s": round(elapsed_s, 3),
        "frames": {
            "lidar": len((timeline.get("lidar") or {}).get("frames") or []),
            "camera": len((timeline.get("camera") or {}).get("frames") or []),
        },
        "layers": {"lidar": len(lidar_trees), "camera": len(camera_trees), "fusion": len(fusion_trees)},
        "fusion": {
            "metric_anchor_count": len(anchored),
            "semantic_supported_count": sum(1 for tree in anchored if int(tree.get("camera_support_hits") or 0) > 0),
            "camera_only_promoted_count": int(fusion.get("camera_only_promoted_count") or 0),
            "max_lidar_anchor_shift_m": round(max(anchor_shifts, default=0.0), 6),
            "uncalibrated_promotion_count": len(uncalibrated_promotions),
        },
        "sync": sync,
        "centerline": {
            "ok": bool(centerline.get("ok")),
            "state": centerline.get("state"),
            "method": centerline.get("method"),
            "confidence": centerline.get("confidence"),
            "point_count": centerline.get("point_count"),
            "length_m": centerline.get("length_m"),
            "row_gap_m": centerline.get("row_gap_m"),
            "segment_count": centerline.get("segment_count"),
            "selected_segment_id": centerline.get("selected_segment_id"),
            "evidence_mode": centerline.get("evidence_mode"),
            "segments": [
                {
                    "segment_id": segment.get("segment_id"),
                    "ok": bool(segment.get("ok")),
                    "state": segment.get("state"),
                    "confidence": segment.get("confidence"),
                    "selection_score": segment.get("selection_score"),
                    "path_length_m": segment.get("path_length_m"),
                    "length_m": segment.get("length_m"),
                    "row_gap_m": segment.get("row_gap_m"),
                    "row_gap_mad_m": segment.get("row_gap_mad_m"),
                    "projected_tree_count": segment.get("projected_tree_count"),
                    "straddles_vehicle": bool(segment.get("straddles_vehicle")),
                    "left_occupied_bins": segment.get("left_occupied_bins"),
                    "right_occupied_bins": segment.get("right_occupied_bins"),
                    "left_longitudinal_span_m": segment.get("left_longitudinal_span_m"),
                    "right_longitudinal_span_m": segment.get("right_longitudinal_span_m"),
                    "bilateral_overlap_span_m": segment.get("bilateral_overlap_span_m"),
                    "bilateral_coverage_ratio": segment.get("bilateral_coverage_ratio"),
                    "paired_gap_count": segment.get("paired_gap_count"),
                    "paired_candidate_count": segment.get("paired_candidate_count"),
                    "paired_run_count": segment.get("paired_run_count"),
                    "contiguous_paired_bin_count": segment.get("contiguous_paired_bin_count"),
                    "max_consecutive_paired_bin_gap": segment.get("max_consecutive_paired_bin_gap"),
                    "candidate_max_consecutive_paired_bin_gap": segment.get(
                        "candidate_max_consecutive_paired_bin_gap"
                    ),
                    "paired_bin_density_ratio": segment.get("paired_bin_density_ratio"),
                    "paired_station_span_m": segment.get("paired_station_span_m"),
                    "paired_station_coverage_ratio": segment.get("paired_station_coverage_ratio"),
                    "paired_interval_s_m": segment.get("paired_interval_s_m"),
                    "fail_closed": bool(segment.get("fail_closed")),
                }
                for segment in centerline.get("segments") or []
            ],
            "corridor_segments": [
                {
                    key: segment.get(key)
                    for key in (
                        "segment_id",
                        "ok",
                        "state",
                        "confidence",
                        "selection_score",
                        "path_length_m",
                        "length_m",
                        "row_gap_m",
                        "row_gap_mad_m",
                        "evidence_mode",
                        "bilateral_frame_count",
                        "corridor_occupied_bins",
                        "corridor_station_span_m",
                        "corridor_time_span_s",
                        "corridor_row_gap_mad_m",
                        "corridor_lateral_residual_p90_m",
                        "fail_closed",
                    )
                }
                for segment in centerline.get("corridor_segments") or []
            ],
        },
        "odometry": orchard_map.get("odometry") or {},
        "lidar_ood": orchard_map.get("lidar_ood") or {},
        "invariants": invariants,
        "implementation_invariants_ok": implementation_invariants_ok,
        "data_sufficiency_ok": data_sufficiency_ok,
        "navigation_ready": navigation_ready,
        "readiness": readiness,
        "ok": implementation_invariants_ok and data_sufficiency_ok,
    }


def main() -> int:
    args = parse_args()
    results = []
    for value in args.timeline:
        path = Path(value)
        try:
            result = evaluate_timeline(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result = {"path": str(path), "ok": False, "error": str(exc)}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    implementation_invariants_ok = bool(results) and all(
        item.get("implementation_invariants_ok") for item in results
    )
    data_sufficiency_ok = bool(results) and all(item.get("data_sufficiency_ok") for item in results)
    navigation_ready = bool(results) and all(item.get("navigation_ready") for item in results)
    report = {
        "ok": implementation_invariants_ok and data_sufficiency_ok,
        "algorithm": MAP_ALGORITHM_NAME,
        "cache_revision": MAP_CACHE_REVISION,
        "ground_truth_available": False,
        "evaluation_scope": "self_consistency_and_fail_closed_invariants_only",
        "accuracy_claim_allowed": False,
        "evaluation_policy": "read_only_timeline_evaluation_with_metric_anchor_and_fail_closed_invariants; not precision, recall, or centerline error",
        "implementation_invariants_ok": implementation_invariants_ok,
        "data_sufficiency_ok": data_sufficiency_ok,
        "navigation_ready": navigation_ready,
        "navigation_readiness_policy": "reported_separately_and_non_blocking_for_fail_closed_research_evaluation",
        "recordings": results,
    }
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.pretty:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
