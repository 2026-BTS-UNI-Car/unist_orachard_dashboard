from __future__ import annotations

import json
import math
import tempfile
import time
import unittest
from pathlib import Path

from dashboard_server.orchard_mapping import (
    CAMERA_FOV_X_RAD,
    MAP_ALGORITHM_NAME,
    MAP_CACHE_REVISION,
    build_orchard_map,
    camera_observation_index,
    camera_tree_local_candidates,
    current_orchard_map_cache,
    estimate_row_centerline,
    fuse_tree_maps,
    fuse_tree_pair,
    lidar_camera_semantic_support,
    local_to_global_tree,
    merge_global_trees,
    nearest_camera_support,
    select_map_tree_candidates,
    spatial_thin_trees,
)
from scripts.precompute_orchard_maps import current_cache


def native_camera_policy(width: int, height: int) -> dict:
    return {
        "revision": "source_native_preview_decoupled_jpeg_v1",
        "mode": "source_native",
        "requested_width": 0,
        "source_width": width,
        "source_height": height,
        "inference_width": width,
        "inference_height": height,
        "jpeg_quality": 86,
        "preview_decoupled": True,
        "no_upscale": True,
    }


def lidar_tree(x_m: float, y_m: float, confidence: float = 0.78) -> dict:
    return {
        "x_m": x_m,
        "y_m": y_m,
        "radius_m": 0.2,
        "confidence": confidence,
        "lidar_confidence": confidence,
        "camera_confidence": 0.0,
        "camera_quality": 0.0,
        "camera_support_hits": 0,
        "hits": 9,
        "first_seen_s": 0.0,
        "last_seen_s": 4.0,
        "metric_anchor": "lidar",
    }


class OrchardMappingV9Test(unittest.TestCase):
    def test_nearest_camera_sync_has_max_delta_gate_and_diagnostics(self):
        index = [{"t_s": 1.0, "ok": True, "method": "unit", "detections": []}]

        matched = nearest_camera_support(index, 1.1, max_delta_s=0.25)
        rejected = nearest_camera_support(index, 1.31, max_delta_s=0.25)
        missing = nearest_camera_support([], 1.0, max_delta_s=0.25)

        self.assertTrue(matched["matched"])
        self.assertAlmostEqual(matched["sync_delta_s"], 0.1)
        self.assertEqual(rejected["state"], "outside_sync_gate")
        self.assertFalse(rejected["matched"])
        self.assertAlmostEqual(rejected["sync_delta_s"], 0.31)
        self.assertEqual(missing["state"], "camera_unavailable")

    def test_y_forward_lidar_bearing_associates_contour_and_keeps_metric_anchor(self):
        tree = {"center": {"x_m": 1.0, "y_m": 4.0}, "confidence": 0.76, "structure_score": 0.8}
        bearing = math.atan2(1.0, 4.0)
        focal_x = 640.0 / (2.0 * math.tan(CAMERA_FOV_X_RAD / 2.0))
        center_u = 320.0 - focal_x * math.tan(bearing)
        camera = {
            "matched": True,
            "ok": True,
            "method": "local_worker_tensorrt",
            "sync_delta_s": 0.04,
            "image": {"width": 640, "height": 360},
            "fov_x_rad": CAMERA_FOV_X_RAD,
            "detections": [
                {
                    "bbox": [center_u - 18.0, 100.0, 36.0, 210.0],
                    "contour": [[[center_u - 12.0, 100.0]], [[center_u + 12.0, 300.0]]],
                    "tree_probability": 0.91,
                    "map_admission": "accepted",
                }
            ],
        }

        support = lidar_camera_semantic_support(tree, camera)
        pose = {"x_m": 8.0, "y_m": -3.0, "theta_rad": 0.4}
        hit = local_to_global_tree(tree, pose, 2.0, support)
        expected_x = math.cos(0.4) * 1.0 - math.sin(0.4) * 4.0 + 8.0
        expected_y = math.sin(0.4) * 1.0 + math.cos(0.4) * 4.0 - 3.0

        self.assertTrue(support["matched"])
        self.assertEqual(support["association_shape"], "contour")
        self.assertEqual(hit["metric_anchor"], "lidar")
        self.assertAlmostEqual(hit["x_m"], expected_x, places=3)
        self.assertAlmostEqual(hit["y_m"], expected_y, places=3)
        self.assertEqual(hit["camera_support_hits"], 1)

    def test_pinhole_projection_rejects_lidar_bearing_outside_camera_fov(self):
        camera = {
            "matched": True,
            "ok": True,
            "method": "local_worker_tensorrt",
            "sync_delta_s": 0.02,
            "image": {"width": 640, "height": 360},
            "fov_x_rad": CAMERA_FOV_X_RAD,
            "detections": [{"bbox": [590.0, 80.0, 45.0, 220.0], "tree_probability": 0.99, "map_admission": "accepted"}],
        }

        support = lidar_camera_semantic_support({"center": {"x_m": 10.0, "y_m": 1.0}}, camera)

        self.assertFalse(support["matched"])
        self.assertEqual(support["state"], "lidar_bearing_outside_camera_fov")
        self.assertLess(support["predicted_u_px"], 0.0)

    def test_camera_info_intrinsics_are_scaled_and_preserved_for_preview(self):
        policy = native_camera_policy(1920, 1080)
        frame = {
            "t_s": 1.0,
            "source_width": 1920,
            "source_height": 1080,
            "inference_input_policy": dict(policy),
            "camera_info": {
                "width": 1920,
                "height": 1080,
                "k": [1371.022086, 0.0, 960.0, 0.0, 1371.022086, 540.0, 0.0, 0.0, 1.0],
            },
            "overlay": {
                "ok": True,
                "algorithm": "local_orchard_vision_ai_worker_v4",
                "revision": "orchard_trunk_yolo11_trt_clip_ood_v1",
                "method": "unit",
                "image": {"width": 640, "height": 360},
                "detections": [],
                "inference_input_policy": dict(policy),
            },
        }

        indexed = camera_observation_index([frame])[0]

        self.assertFalse(indexed["intrinsics_calibrated"])
        self.assertEqual(indexed["intrinsics_source"], "camera_info_nominal_or_unverified")
        self.assertEqual(indexed["projection_intrinsics_source"], "camera_info_nominal_or_unverified_scaled_to_overlay")
        self.assertAlmostEqual(indexed["fx_px"], 1371.022086 / 3.0, places=5)
        self.assertAlmostEqual(indexed["cx_px"], 320.0, places=5)
        self.assertAlmostEqual(indexed["fov_x_deg"], math.degrees(2.0 * math.atan(960.0 / 1371.022086)), places=3)

        frame["camera_info"]["calibration_valid"] = True
        validated = camera_observation_index([frame])[0]
        self.assertTrue(validated["intrinsics_calibrated"])
        self.assertEqual(validated["intrinsics_source"], "timeline_camera_info_validated")

    def test_missing_or_low_quality_camera_never_penalizes_lidar(self):
        lidar = lidar_tree(2.0, 5.0, confidence=0.73)

        no_camera = fuse_tree_pair(lidar, None, None)
        low_quality = fuse_tree_pair({**lidar, "camera_confidence": 0.9, "camera_quality": 0.0}, None, None)

        self.assertEqual(no_camera["confidence"], 0.73)
        self.assertEqual(low_quality["confidence"], 0.73)
        self.assertEqual((no_camera["x_m"], no_camera["y_m"]), (2.0, 5.0))
        self.assertEqual(no_camera["semantic_weight_camera"], 0.0)

    def test_uncalibrated_camera_only_landmark_is_not_promoted(self):
        lidar = lidar_tree(1.0, 3.0)
        monocular = {
            "x_m": 30.0,
            "y_m": 40.0,
            "confidence": 0.99,
            "hits": 20,
            "metric_calibrated": False,
            "source": "camera",
        }

        fused = fuse_tree_maps([lidar], [monocular])

        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["metric_anchor"], "lidar")
        self.assertNotEqual(fused[0]["fusion_source"], "camera_only_calibrated")

    def test_camera_metric_projection_requires_validated_intrinsics_and_extrinsics(self):
        detection = {
            "bbox": [280.0, 80.0, 80.0, 250.0],
            "tree_probability": 0.95,
            "map_admission": "accepted",
            "metric_projection": {
                "validated": True,
                "intrinsics_validated": True,
                "coordinate_frame": "lidar_bridge_xy_v1",
                "x_m": 1.0,
                "y_m": 4.0,
            },
        }
        frame = {"image": {"width": 640, "height": 360}, "detections": [detection]}

        unverified = camera_tree_local_candidates(frame)[0]
        detection["metric_projection"]["extrinsics_validated"] = True
        verified = camera_tree_local_candidates(frame)[0]

        self.assertFalse(unverified["metric_calibrated"])
        self.assertTrue(verified["metric_calibrated"])
        self.assertEqual(verified["projection"]["method"], "validated_camera_lidar_metric_projection")

    def test_camera_pixel_bearing_uses_rep103_left_positive_sign(self):
        left = camera_tree_local_candidates(
            {
                "image": {"width": 640, "height": 360},
                "detections": [{"bbox": [80.0, 80.0, 80.0, 250.0], "tree_probability": 0.95, "map_admission": "accepted"}],
            }
        )[0]
        right = camera_tree_local_candidates(
            {
                "image": {"width": 640, "height": 360},
                "detections": [{"bbox": [480.0, 80.0, 80.0, 250.0], "tree_probability": 0.95, "map_admission": "accepted"}],
            }
        )[0]

        self.assertGreater(left["bearing_deg"], 0.0)
        self.assertGreater(left["center"]["x_m"], 0.0)
        self.assertLess(right["bearing_deg"], 0.0)
        self.assertLess(right["center"]["x_m"], 0.0)

    def test_camera_merge_separates_metric_calibration_provenance(self):
        hits = []
        for index in range(5):
            hits.append(
                {
                    "x_m": 0.0,
                    "y_m": 4.0,
                    "radius_m": 0.2,
                    "confidence": 0.95,
                    "camera_confidence": 0.95,
                    "source": "camera",
                    "metric_calibrated": True,
                    "first_seen_s": float(index),
                    "last_seen_s": float(index),
                    "hits": 1,
                }
            )
            hits.append(
                {
                    "x_m": 0.56,
                    "y_m": 4.0,
                    "radius_m": 0.2,
                    "confidence": 0.95,
                    "camera_confidence": 0.95,
                    "source": "camera",
                    "metric_calibrated": False,
                    "first_seen_s": float(index) + 0.01,
                    "last_seen_s": float(index) + 0.01,
                    "hits": 1,
                }
            )

        tracks = merge_global_trees(hits, frame_count=10, source="camera")

        self.assertEqual(len(tracks), 2)
        calibrated = next(track for track in tracks if track["metric_calibrated"])
        uncalibrated = next(track for track in tracks if not track["metric_calibrated"])
        self.assertEqual((calibrated["calibrated_hits"], calibrated["uncalibrated_hits"]), (5, 0))
        self.assertEqual((uncalibrated["calibrated_hits"], uncalibrated["uncalibrated_hits"]), (0, 5))
        self.assertAlmostEqual(calibrated["x_m"], 0.0, places=3)
        self.assertAlmostEqual(uncalibrated["x_m"], 0.56, places=3)

    def test_mixed_provenance_camera_track_is_never_promoted(self):
        mixed = {
            "x_m": 0.56,
            "y_m": 4.0,
            "confidence": 0.99,
            "hits": 5,
            "metric_calibrated": True,
            "metric_provenance_consistent": False,
            "calibrated_hits": 1,
            "uncalibrated_hits": 4,
            "source": "camera",
        }

        self.assertEqual(fuse_tree_maps([], [mixed]), [])

    def test_long_recording_policy_has_no_fixed_18_or_80_landmark_cap(self):
        spatial = [lidar_tree(index * 1.1, 2.0) for index in range(120)]
        confirmed_hits = [
            {**lidar_tree(index * 1.1, 0.0), "first_seen_s": 0.0, "last_seen_s": 3.0, "hits": 5}
            for index in range(100)
        ]
        local_candidates = [
            {
                "center": {"x_m": 1.2 + index * 0.01, "y_m": 3.0},
                "confidence": 0.85,
                "structure_score": 0.8,
                "kind": "trunk_like",
                "points": 8,
            }
            for index in range(30)
        ]

        self.assertEqual(len(spatial_thin_trees(spatial, min_spacing_m=0.82)), 120)
        self.assertEqual(len(merge_global_trees(confirmed_hits, frame_count=500)), 100)
        self.assertEqual(len(select_map_tree_candidates(local_candidates)), 30)

    def test_u_turn_centerline_uses_trajectory_segments_not_global_axis(self):
        path = []
        for y in range(0, 13, 2):
            path.append({"x_m": 0.0, "y_m": float(y), "pose_confidence": 0.92, "turn_event": y == 12})
        path.extend(
            [
                {"x_m": 4.0, "y_m": 16.0, "pose_confidence": 0.88, "turn_event": True},
                {"x_m": 8.0, "y_m": 12.0, "pose_confidence": 0.9, "turn_event": False},
            ]
        )
        for y in range(10, -1, -2):
            path.append({"x_m": 8.0, "y_m": float(y), "pose_confidence": 0.92, "turn_event": False})
        trees = []
        for center_x in (0.0, 8.0):
            for y in (1.25, 3.75, 6.25, 8.75, 11.25):
                trees.extend([lidar_tree(center_x - 2.0, y), lidar_tree(center_x + 2.0, y)])

        centerline = estimate_row_centerline(trees, path, "fusion")

        self.assertTrue(centerline["ok"], centerline)
        self.assertGreaterEqual(centerline["segment_count"], 2)
        self.assertEqual(centerline["method"], "trajectory_segmented_hybrid_bilateral_rows_v10")
        xs = [float(point["x_m"]) for point in centerline["points"]]
        self.assertLess(max(xs) - min(xs), 3.0)
        self.assertGreaterEqual(centerline["confidence"], centerline["confidence_threshold"])

    def test_centerline_fails_closed_without_two_supported_rows(self):
        path = [{"x_m": 0.0, "y_m": float(y), "pose_confidence": 0.9} for y in range(0, 13, 2)]
        trees = [lidar_tree(-2.0, y) for y in (1.25, 3.75, 6.25, 8.75, 11.25)]

        centerline = estimate_row_centerline(trees, path, "fusion")

        self.assertFalse(centerline["ok"])
        self.assertEqual(centerline["points"], [])
        self.assertEqual(centerline["state"], "low_confidence_fail_closed")

    def test_centerline_selects_nearest_bounding_rows_when_outer_rows_exist(self):
        path = [{"x_m": 0.0, "y_m": float(y), "pose_confidence": 0.9} for y in range(0, 16, 1)]
        trees = []
        for y in (1.25, 3.75, 6.25, 8.75, 11.25):
            trees.extend([lidar_tree(-2.0, y), lidar_tree(2.0, y), lidar_tree(-6.0, y), lidar_tree(6.0, y)])

        centerline = estimate_row_centerline(trees, path, "fusion")

        self.assertTrue(centerline["ok"], centerline)
        self.assertAlmostEqual(centerline["row_gap_m"], 4.0, delta=0.2)

    def test_centerline_rejects_one_valid_pair_with_long_one_sided_extrapolation(self):
        path = [{"x_m": 0.0, "y_m": float(y), "pose_confidence": 0.95} for y in range(0, 21)]
        trees = []
        for key in range(7):
            station = (key + 0.5) * 2.5
            trees.append(lidar_tree(-2.0, station))
            trees.append(lidar_tree(2.0 if key == 3 else 6.0, station))

        centerline = estimate_row_centerline(trees, path, "fusion")

        self.assertFalse(centerline["ok"])
        self.assertEqual(centerline["points"], [])
        segment = centerline["segments"][0]
        self.assertEqual(segment["state"], "insufficient_paired_bilateral_bins")
        self.assertEqual(segment["paired_gap_count"], 1)
        self.assertGreaterEqual(segment["left_longitudinal_span_m"], 15.0)
        self.assertGreaterEqual(segment["right_longitudinal_span_m"], 15.0)

    def test_centerline_rejects_two_far_pairs_with_no_contiguous_evidence(self):
        path = [{"x_m": 0.0, "y_m": float(y), "pose_confidence": 0.95} for y in range(25)]
        trees = []
        for key in range(9):
            station = (key + 0.5) * 2.5
            trees.append(lidar_tree(-2.0, station))
            trees.append(lidar_tree(2.0 if key in (0, 8) else 6.0, station))

        centerline = estimate_row_centerline(trees, path, "fusion")

        self.assertFalse(centerline["ok"], centerline)
        self.assertEqual(centerline["points"], [])
        segment = centerline["segments"][0]
        self.assertEqual(segment["state"], "insufficient_contiguous_paired_evidence")
        self.assertEqual(segment["paired_candidate_count"], 2)
        self.assertEqual(segment["contiguous_paired_bin_count"], 1)
        self.assertEqual(segment["candidate_max_consecutive_paired_bin_gap"], 8)
        self.assertEqual(segment["paired_gap_count"], 1)

    def test_centerline_rejects_short_dense_pair_run_over_long_overlap(self):
        path = [{"x_m": 0.0, "y_m": float(y), "pose_confidence": 0.95} for y in range(0, 23)]
        trees = []
        for key in range(9):
            station = (key + 0.5) * 2.5
            trees.append(lidar_tree(-2.0, station))
            trees.append(lidar_tree(2.0 if key in (0, 1) else 6.0, station))

        centerline = estimate_row_centerline(trees, path, "fusion")

        self.assertFalse(centerline["ok"], centerline)
        self.assertEqual(centerline["points"], [])
        segment = centerline["segments"][0]
        self.assertEqual(segment["state"], "insufficient_paired_station_coverage")
        self.assertAlmostEqual(segment["paired_bin_density_ratio"], 1.0)
        self.assertLess(segment["paired_station_coverage_ratio"], 0.35)
        self.assertLess(segment["paired_station_span_m"], 4.0)

    def test_centerline_rejects_bilateral_rows_without_station_overlap(self):
        path = [{"x_m": 0.0, "y_m": float(y), "pose_confidence": 0.95} for y in range(0, 24)]
        trees = [lidar_tree(-2.0, (key + 0.5) * 2.5) for key in range(4)]
        trees.extend(lidar_tree(2.0, (key + 0.5) * 2.5) for key in range(4, 8))

        centerline = estimate_row_centerline(trees, path, "fusion")

        self.assertFalse(centerline["ok"])
        self.assertEqual(centerline["points"], [])
        self.assertEqual(centerline["segments"][0]["state"], "insufficient_bilateral_station_overlap")

    def test_centerline_projects_only_small_holes_inside_paired_interval(self):
        path = [{"x_m": 0.0, "y_m": float(y), "pose_confidence": 0.95} for y in range(0, 18)]
        trees = []
        for key in (0, 2, 4):
            station = (key + 0.5) * 2.5
            trees.extend([lidar_tree(-2.0, station), lidar_tree(2.0, station)])
        trees.append(lidar_tree(-2.0, 3.75))
        trees.append(lidar_tree(-2.0, 13.75))

        centerline = estimate_row_centerline(trees, path, "fusion")

        self.assertTrue(centerline["ok"], centerline)
        self.assertTrue(centerline["straddles_vehicle"])
        self.assertTrue(any(point["source"] == "left_small_hole_projection" for point in centerline["points"]))
        self.assertLessEqual(max(float(point["s_m"]) for point in centerline["points"]), 11.25)
        self.assertGreaterEqual(min(float(point["s_m"]) for point in centerline["points"]), 1.25)

    def test_stale_v8_cache_is_invalidated_by_api_and_precompute_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timeline_path = root / "timeline.json"
            cache_path = root / "orchard_map.json"
            timeline_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
            time.sleep(0.01)
            cache_path.write_text(
                json.dumps({"ok": True, "map": {"algorithm": "orchard_three_layer_weighted_centerline_v8"}}),
                encoding="utf-8",
            )
            stale_payload = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertFalse(current_orchard_map_cache(stale_payload))
            self.assertFalse(current_cache(cache_path, timeline_path))

            cache_path.write_text(
                json.dumps({"ok": True, "map": {"ok": True, "algorithm": MAP_ALGORITHM_NAME}}),
                encoding="utf-8",
            )
            same_algorithm_stale_revision = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertFalse(current_orchard_map_cache(same_algorithm_stale_revision))
            self.assertFalse(current_cache(cache_path, timeline_path))

            cache_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "map": {
                            "ok": True,
                            "algorithm": MAP_ALGORITHM_NAME,
                            "cache_revision": "stale_same_v9_revision",
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(current_orchard_map_cache(json.loads(cache_path.read_text(encoding="utf-8"))))

            cache_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "map": {
                            "ok": True,
                            "algorithm": MAP_ALGORITHM_NAME,
                            "cache_revision": MAP_CACHE_REVISION,
                        },
                    }
                ),
                encoding="utf-8",
            )
            same_revision_missing_input_contract = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertFalse(current_orchard_map_cache(same_revision_missing_input_contract))

            current_provenance = {
                "enforced": True,
                "required_revision": "source_native_preview_decoupled_jpeg_v1",
                "required_mode": "source_native",
                "required_requested_width": 0,
                "required_jpeg_quality": 86,
                "frame_count": 0,
                "accepted_frame_count": 0,
                "rejected_frame_count": 0,
                "legacy_preview_as_native_allowed": False,
            }
            cache_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "map": {
                            "ok": True,
                            "algorithm": MAP_ALGORITHM_NAME,
                            "cache_revision": MAP_CACHE_REVISION,
                            "camera": {"input_provenance": current_provenance},
                        },
                    }
                ),
                encoding="utf-8",
            )
            current = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertTrue(current_orchard_map_cache(current))
            self.assertTrue(current_cache(cache_path, timeline_path))
            current["map"]["camera"]["input_provenance"]["frame_count"] = 1
            self.assertFalse(current_orchard_map_cache(current))

    def test_build_map_publishes_v9_sync_and_metric_anchor_diagnostics(self):
        payload = build_orchard_map(
            {
                "ok": True,
                "duration_s": 0.0,
                "lidar": {"frames": [{"t_s": 0.0, "points": []}]},
                "camera": {"frames": []},
            }
        )

        self.assertEqual(payload["algorithm"], MAP_ALGORITHM_NAME)
        self.assertEqual(payload["cache_revision"], MAP_CACHE_REVISION)
        self.assertEqual(payload["fusion"]["sync"]["camera_unavailable_frames"], 1)
        self.assertEqual(payload["fusion"]["camera_only_metric_policy"], "fail_closed_unless_metric_projection_is_explicitly_calibrated_and_validated")
        self.assertEqual(
            payload["camera"]["projection_model"]["model"],
            "pinhole_u_equals_cx_minus_fx_tan_bearing",
        )
        self.assertFalse(payload["camera"]["projection_model"]["nominal_intrinsics_calibrated"])
        self.assertTrue(payload["camera"]["input_provenance"]["enforced"])
        self.assertEqual(payload["camera"]["input_provenance"]["frame_count"], 0)


if __name__ == "__main__":
    unittest.main()
