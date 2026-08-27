from __future__ import annotations

import math
import random
import unittest

from dashboard_server.orchard_mapping import (
    NAVIGATION_VALIDATION_CONTRACT,
    associate_lidar_camera_semantics,
    camera_global_tree_hits,
    camera_input_provenance_summary,
    camera_observation_index,
    camera_tree_local_candidates,
    estimate_local_bilateral_corridor,
    estimate_row_centerline,
    merge_global_trees,
    minimum_cost_maximum_cardinality_assignment,
    minimum_cost_maximum_cardinality_monotonic_pairs,
    orchard_map_readiness,
    pair_lidar_camera_observations,
    sensor_timestamp_s,
    smooth_centerline_points,
)


def stamp(value: float) -> dict:
    seconds = int(math.floor(value))
    return {"sec": seconds, "nanosec": int(round((value - seconds) * 1e9))}


def native_camera_policy(width: int = 640, height: int = 360) -> dict:
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


def local_tree(x_m: float, y_m: float, confidence: float = 0.86) -> dict:
    return {
        "center": {"x_m": x_m, "y_m": y_m},
        "confidence": confidence,
        "structure_score": 0.78,
        "kind": "trunk_like",
        "radius_m": 0.18,
    }


def global_hit(x_m: float, y_m: float, t_s: float) -> dict:
    return {
        "x_m": x_m,
        "y_m": y_m,
        "first_seen_s": t_s,
        "last_seen_s": t_s,
        "confidence": 0.86,
        "lidar_confidence": 0.86,
        "camera_confidence": 0.0,
        "camera_support_hits": 0,
        "structure_score": 0.78,
        "radius_m": 0.18,
        "kind": "trunk_like",
        "metric_anchor": "lidar",
    }


class OrchardMappingV10Test(unittest.TestCase):
    def test_common_header_stamp_replaces_misaligned_relative_timeline(self):
        policy = native_camera_policy()
        lidar = [
            {"t_s": 0.0, "stamp": stamp(100.0)},
            {"t_s": 1.0, "stamp": stamp(101.0)},
        ]
        camera_frames = [
            {
                "t_s": 0.52,
                "stamp": stamp(100.03),
                "overlay": {
                    "ok": True,
                    "algorithm": "local_orchard_vision_ai_worker_v4",
                    "revision": "orchard_trunk_yolo11_trt_clip_ood_v1",
                    "method": "unit",
                    "image": {"width": 640, "height": 360},
                    "detections": [],
                    "inference_input_policy": dict(policy),
                },
            },
            {
                "t_s": 1.52,
                "stamp": stamp(101.04),
                "overlay": {
                    "ok": True,
                    "algorithm": "local_orchard_vision_ai_worker_v4",
                    "revision": "orchard_trunk_yolo11_trt_clip_ood_v1",
                    "method": "unit",
                    "image": {"width": 640, "height": 360},
                    "detections": [],
                    "inference_input_policy": dict(policy),
                },
            },
        ]

        pairs = pair_lidar_camera_observations(lidar, camera_observation_index(camera_frames))

        self.assertEqual([pairs[index]["camera_t_s"] for index in range(2)], [0.52, 1.52])
        self.assertTrue(all(pairs[index]["matched"] for index in range(2)))
        self.assertTrue(all(pairs[index]["sync_timebase"] == "ros_header_stamp" for index in range(2)))
        self.assertLess(max(pairs[index]["sync_delta_s"] for index in range(2)), 0.05)

    def test_legacy_preview_is_counted_as_rejected_camera_provenance(self):
        legacy = {
            "width": 640,
            "height": 360,
            "source_width": 1920,
            "source_height": 1080,
            "overlay": {
                "ok": True,
                "algorithm": "local_orchard_vision_ai_worker_v4",
                "revision": "orchard_trunk_yolo11_trt_clip_ood_v1",
                "image": {"width": 640, "height": 360},
                "detections": [],
            },
        }
        native_policy = native_camera_policy(1920, 1080)
        current = {
            **legacy,
            "inference_input_policy": dict(native_policy),
            "overlay": {**legacy["overlay"], "inference_input_policy": dict(native_policy)},
        }

        summary = camera_input_provenance_summary([legacy, current])
        indexed = camera_observation_index([legacy, current])

        self.assertTrue(summary["enforced"])
        self.assertEqual(summary["accepted_frame_count"], 1)
        self.assertEqual(summary["rejected_frame_count"], 1)
        self.assertFalse(summary["legacy_preview_as_native_allowed"])
        self.assertFalse(indexed[0]["ok"])
        self.assertEqual(indexed[0]["method"], "source_native_provenance_required")
        self.assertTrue(indexed[1]["ok"])
        self.assertEqual(indexed[1]["method"], "local_orchard_vision_ai_worker_v4")

    def test_global_sync_is_one_to_one_and_reports_conflict(self):
        lidar = [
            {"t_s": index * 0.05, "stamp": stamp(100.0 + index * 0.05)}
            for index in range(3)
        ]
        cameras = [
            {"t_s": 0.0, "sensor_stamp_s": 100.0, "ok": True, "detections": []},
            {"t_s": 0.1, "sensor_stamp_s": 100.1, "ok": True, "detections": []},
        ]

        pairs = pair_lidar_camera_observations(lidar, cameras)

        matched = [item for item in pairs.values() if item["matched"]]
        self.assertEqual(len(matched), 2)
        self.assertEqual(len({item["camera_sensor_stamp_s"] for item in matched}), 2)
        self.assertEqual(
            sum(item["state"] == "camera_reserved_by_one_to_one_sync" for item in pairs.values()),
            1,
        )

    def test_assignment_avoids_greedy_cardinality_trap(self):
        matches = minimum_cost_maximum_cardinality_assignment(
            2,
            2,
            [(0, 0, 0.01), (0, 1, 0.2), (1, 0, 0.1)],
        )

        self.assertEqual(matches, [(0, 1), (1, 0)])

    def test_camera_contour_is_semantic_support_for_only_one_anchor(self):
        camera = {
            "matched": True,
            "ok": True,
            "method": "local_worker_tensorrt",
            "sync_delta_s": 0.02,
            "image": {"width": 640, "height": 360},
            "fov_x_rad": math.radians(70.0),
            "detections": [
                {
                    "bbox": [245.0, 80.0, 150.0, 250.0],
                    "tree_probability": 0.9,
                    "map_admission": "accepted",
                }
            ],
        }
        trees = [local_tree(-0.15, 4.0), local_tree(0.15, 4.0)]

        supports = associate_lidar_camera_semantics(trees, camera)

        self.assertEqual(sum(bool(item.get("matched")) for item in supports), 1)
        self.assertEqual(
            sum(item.get("state") == "camera_detection_reserved_by_one_to_one_assignment" for item in supports),
            1,
        )

    def test_stable_pending_camera_can_only_support_existing_lidar_anchor(self):
        detection = {
            "bbox": [280.0, 80.0, 80.0, 250.0],
            "tree_probability": 0.27,
            "confidence": 0.27,
            "ood_score": 0.73,
            "stable": True,
            "stable_hits": 4,
            "track_id": "cam-tree-7",
            "map_admission": "pending",
        }
        camera = {
            "matched": True,
            "ok": True,
            "method": "local_worker_tensorrt",
            "sync_delta_s": 0.02,
            "image": {"width": 640, "height": 360},
            "fov_x_rad": math.radians(70.0),
            "detections": [detection],
        }

        support = associate_lidar_camera_semantics([local_tree(0.0, 4.0)], camera)[0]
        projected = camera_tree_local_candidates({**camera, "intrinsics_calibrated": False})

        self.assertTrue(support["matched"])
        self.assertEqual(support["semantic_admission"], "stable_pending_semantic_only")
        self.assertLess(support["quality"], 0.5)
        self.assertEqual(projected, [])

    def test_pending_camera_requires_temporal_identity_and_rejects_structural_negatives(self):
        base_detection = {
            "bbox": [280.0, 80.0, 80.0, 250.0],
            "tree_probability": 0.27,
            "confidence": 0.27,
            "ood_score": 0.73,
            "stable": True,
            "stable_hits": 4,
            "map_admission": "pending",
        }
        camera = {
            "matched": True,
            "ok": True,
            "sync_delta_s": 0.01,
            "image": {"width": 640, "height": 360},
            "fov_x_rad": math.radians(70.0),
        }
        for detection in (
            base_detection,
            {**base_detection, "track_id": "cam-fence", "label": "fence"},
            {**base_detection, "track_id": "cam-post", "class_name": "utility_post"},
            {
                **base_detection,
                "track_id": "cam-wall",
                "label": "orchard_tree_trunk",
                "clip_ood": {"top_negative": "a wall or paved road"},
            },
        ):
            with self.subTest(detection=detection):
                support = associate_lidar_camera_semantics(
                    [local_tree(0.0, 4.0)],
                    {**camera, "detections": [detection]},
                )[0]
                self.assertFalse(support["matched"])

    def test_raw_rejected_camera_proposals_are_not_used_without_temporal_identity(self):
        camera = {
            "matched": True,
            "ok": True,
            "sync_delta_s": 0.01,
            "image": {"width": 640, "height": 360},
            "fov_x_rad": math.radians(70.0),
            "detections": [],
            "rejected_detections": [
                {
                    "bbox": [280.0, 80.0, 80.0, 250.0],
                    "tree_probability": 0.99,
                    "ood_score": 0.01,
                }
            ],
        }

        support = associate_lidar_camera_semantics([local_tree(0.0, 4.0)], camera)[0]

        self.assertFalse(support["matched"])
        self.assertEqual(support["state"], "no_camera_detection_at_lidar_bearing")

    def test_static_tracks_are_order_invariant_and_one_hit_per_frame(self):
        hits = []
        for index in range(6):
            hits.extend(
                [
                    global_hit(0.02 * (-1) ** index, 4.0, float(index)),
                    global_hit(1.05 + 0.02 * (-1) ** (index + 1), 4.0, float(index)),
                ]
            )
        shuffled = list(hits)
        random.Random(42).shuffle(shuffled)

        ordered_tracks = merge_global_trees(hits, frame_count=100)
        shuffled_tracks = merge_global_trees(shuffled, frame_count=100)

        signature = lambda rows: sorted(
            (round(float(item["x_m"]), 3), int(item["hits"])) for item in rows
        )
        self.assertEqual(signature(ordered_tracks), signature(shuffled_tracks))
        self.assertEqual(signature(ordered_tracks), [(0.0, 6), (1.05, 6)])
        self.assertTrue(all(item["distinct_observation_times"] == 6 for item in ordered_tracks))

    def test_drifting_chain_does_not_become_one_high_confidence_landmark(self):
        hits = [global_hit(index * 0.38, 4.0, float(index)) for index in range(12)]

        tracks = merge_global_trees(hits, frame_count=100)

        self.assertFalse(any(int(item["hits"]) == 12 for item in tracks))
        self.assertTrue(all(float(item["spatial_residual_p90_m"]) <= 0.58 for item in tracks))

    def test_local_parallel_rows_recover_corridor_despite_clutter(self):
        trees = []
        for forward in (1.0, 3.0, 5.0, 7.0, 9.0):
            trees.append(local_tree(2.0 + 0.03 * forward, forward))
            trees.append(local_tree(-2.0 + 0.02 * forward, forward))
        trees.extend([local_tree(5.8, 2.0), local_tree(0.8, 8.0), local_tree(-5.7, 6.0)])

        corridor = estimate_local_bilateral_corridor(
            trees,
            {"x_m": 10.0, "y_m": -3.0, "theta_rad": 0.0},
            4.0,
        )

        self.assertIsNotNone(corridor)
        self.assertAlmostEqual(float(corridor["row_gap_m"]), 4.0, delta=0.15)
        self.assertAlmostEqual(float(corridor["x_m"]), 10.0, delta=0.1)
        self.assertGreaterEqual(int(corridor["left_support_count"]), 5)
        self.assertGreaterEqual(int(corridor["right_support_count"]), 5)

    def test_nonparallel_rows_fail_closed(self):
        trees = []
        for forward in (1.0, 3.0, 5.0, 7.0, 9.0):
            trees.append(local_tree(1.4 + 0.24 * forward, forward))
            trees.append(local_tree(-1.4 - 0.24 * forward, forward))

        corridor = estimate_local_bilateral_corridor(
            trees,
            {"x_m": 0.0, "y_m": 0.0, "theta_rad": 0.0},
            0.0,
        )

        self.assertIsNone(corridor)

    def test_same_row_spacing_and_wall_labels_cannot_form_corridor(self):
        narrow = []
        walls = []
        for forward in (1.0, 3.0, 5.0, 7.0, 9.0):
            narrow.extend([local_tree(0.85, forward), local_tree(-0.85, forward)])
            walls.extend(
                [
                    {**local_tree(2.0, forward), "kind": "wall"},
                    {**local_tree(-2.0, forward), "kind": "fence"},
                ]
            )

        pose = {"x_m": 0.0, "y_m": 0.0, "theta_rad": 0.0}
        self.assertIsNone(estimate_local_bilateral_corridor(narrow, pose, 0.0))
        self.assertIsNone(estimate_local_bilateral_corridor(walls, pose, 0.0))

    def test_time_local_corridor_requires_station_diversity(self):
        path = [
            {"t_s": float(index), "x_m": 0.0, "y_m": float(index), "pose_confidence": 0.9}
            for index in range(7)
        ]
        diverse = [
            {
                "t_s": float(index),
                "x_m": 0.1,
                "y_m": float(index),
                "row_gap_m": 4.0 + 0.02 * (-1) ** index,
                "confidence": 0.85,
            }
            for index in range(6)
        ]
        stationary = [{**item, "y_m": 0.0} for item in diverse]

        ready = estimate_row_centerline([], path, "fusion", diverse)
        rejected = estimate_row_centerline([], path, "fusion", stationary)

        self.assertTrue(ready["ok"], ready)
        self.assertEqual(ready["evidence_mode"], "time_local_bilateral_corridor_consensus")
        self.assertGreaterEqual(ready["length_m"], 4.0)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["points"], [])
        self.assertTrue(
            all(
                segment["method"]
                == "trajectory_segmented_time_local_bilateral_corridor_v10"
                for segment in rejected["corridor_segments"]
            )
        )

    def test_navigation_readiness_requires_external_validation(self):
        centerline = {"ok": True, "points": [{"x_m": 0.0}, {"x_m": 1.0}]}

        unvalidated = orchard_map_readiness([{"points": [1]}, {"points": [1]}], [], [], centerline)
        validated = orchard_map_readiness(
            [{"points": [1]}, {"points": [1]}],
            [],
            [],
            centerline,
            navigation_validation={
                "contract": NAVIGATION_VALIDATION_CONTRACT,
                "manifest_fingerprint": "sha256:" + "a" * 64,
                "reviewer": "independent-test-reviewer",
                "source": "https://example.invalid/validation-manifest.json",
                "ground_truth_validated": True,
                "lidar_base_extrinsics_validated": True,
                "camera_intrinsics_validated": True,
                "camera_lidar_extrinsics_validated": True,
                "trajectory_pose_validated": True,
                "full_rate_replay_validated": True,
            },
            sync_diagnostics={"clock_integrity_ok": True},
        )

        self.assertFalse(unvalidated["navigation_ready"])
        self.assertTrue(unvalidated["observed"]["centerline_evidence_ready"])
        self.assertTrue(validated["navigation_ready"])
        self.assertTrue(validated["observed"]["navigation_validation_provenance"]["valid"])

    def test_boolean_validation_flags_without_external_manifest_never_enable_navigation(self):
        self_attested = orchard_map_readiness(
            [{"points": [1]}, {"points": [1]}],
            [],
            [],
            {"ok": True, "points": [{"x_m": 0.0}, {"x_m": 1.0}]},
            navigation_validation={
                "ground_truth_validated": True,
                "lidar_base_extrinsics_validated": True,
                "camera_intrinsics_validated": True,
                "camera_lidar_extrinsics_validated": True,
                "trajectory_pose_validated": True,
                "full_rate_replay_validated": True,
            },
            sync_diagnostics={"clock_integrity_ok": True},
        )

        self.assertFalse(self_attested["navigation_ready"])
        self.assertFalse(self_attested["observed"]["navigation_validation_provenance"]["valid"])
        self.assertIn("navigation_validation_contract_missing", self_attested["navigation_readiness_reasons"])
        self.assertIn("navigation_validation_manifest_fingerprint_missing", self_attested["navigation_readiness_reasons"])

    def test_missing_camera_calibration_keeps_navigation_fail_closed(self):
        validation = {
            "ground_truth_validated": True,
            "lidar_base_extrinsics_validated": True,
            "camera_intrinsics_validated": True,
            "camera_lidar_extrinsics_validated": False,
            "trajectory_pose_validated": True,
            "full_rate_replay_validated": True,
        }
        readiness = orchard_map_readiness(
            [{"points": [1]}, {"points": [1]}],
            [{}, {}],
            [],
            {"ok": True, "points": [{"x_m": 0.0}, {"x_m": 1.0}]},
            navigation_validation=validation,
            sync_diagnostics={"clock_integrity_ok": True},
        )

        self.assertFalse(readiness["navigation_ready"])
        self.assertIn(
            "camera_lidar_extrinsics_validated_missing",
            readiness["navigation_readiness_reasons"],
        )

    def test_header_regression_preserves_original_order_and_fails_clock_integrity(self):
        lidar_stamps = [100.0, 101.0, 100.4, 101.4]
        camera_stamps = [100.01, 101.01, 100.41, 101.41]
        lidar = [
            {"t_s": float(index), "stamp": stamp(value)}
            for index, value in enumerate(lidar_stamps)
        ]
        cameras = [
            {
                "t_s": float(index) + 0.5,
                "sensor_stamp_s": value,
                "ok": True,
                "detections": [],
            }
            for index, value in enumerate(camera_stamps)
        ]

        pairs = pair_lidar_camera_observations(lidar, cameras, max_delta_s=0.05)

        self.assertEqual(
            [pairs[index]["camera_sensor_stamp_s"] for index in range(4)],
            camera_stamps,
        )
        self.assertTrue(all(pairs[index]["matched"] for index in range(4)))
        self.assertFalse(pairs[0]["clock_integrity_ok"])
        self.assertEqual(pairs[0]["lidar_header_non_monotonic_count"], 1)
        self.assertEqual(pairs[0]["camera_header_non_monotonic_count"], 1)

    def test_camera_world_layer_uses_exact_one_to_one_paired_pose(self):
        camera = {
            "observation_index": 0,
            "t_s": 0.5,
            "sensor_stamp_s": 100.0,
            "ok": True,
            "image": {"width": 640, "height": 360},
            "fov_x_rad": math.radians(70.0),
            "fx_px": 457.0,
            "cx_px": 320.0,
            "detections": [
                {
                    "bbox": [300.0, 100.0, 40.0, 220.0],
                    "tree_probability": 0.9,
                    "map_admission": "accepted",
                }
            ],
        }
        path = [
            {"x_m": 1.0, "y_m": 2.0, "heading_deg": 0.0},
            {"x_m": 100.0, "y_m": 200.0, "heading_deg": 0.0},
        ]

        hits = camera_global_tree_hits(
            [camera],
            path,
            paired_support_by_lidar_index={
                0: {
                    "matched": True,
                    "observation_index": 0,
                    "sync_delta_s": 0.01,
                }
            },
        )

        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(float(hits[0]["x_m"]), 1.0, delta=0.05)
        self.assertGreater(float(hits[0]["y_m"]), 2.0)

    def test_sparse_monotonic_sync_matches_dense_reference_objective(self):
        rng = random.Random(123)

        def objective(left: list[float], right: list[float], gate: float) -> tuple[int, float]:
            rows = [[(0, 0.0) for _ in range(len(right) + 1)] for _ in range(len(left) + 1)]
            for left_index in range(len(left) - 1, -1, -1):
                for right_index in range(len(right) - 1, -1, -1):
                    choices = [rows[left_index + 1][right_index], rows[left_index][right_index + 1]]
                    delta = abs(left[left_index] - right[right_index])
                    if delta <= gate:
                        count, cost = rows[left_index + 1][right_index + 1]
                        choices.append((count + 1, cost + delta))
                    rows[left_index][right_index] = min(choices, key=lambda item: (-item[0], item[1]))
            return rows[0][0]

        for _ in range(50):
            left = [round(rng.uniform(0.0, 2.0), 2) for _ in range(6)]
            right = [round(rng.uniform(0.0, 2.0), 2) for _ in range(7)]
            gate = 0.24
            pairs = minimum_cost_maximum_cardinality_monotonic_pairs(left, right, gate)
            sparse_objective = (
                len(pairs),
                sum(abs(left[left_index] - right[right_index]) for left_index, right_index in pairs),
            )
            expected = objective(left, right, gate)
            self.assertEqual(sparse_objective[0], expected[0])
            self.assertAlmostEqual(sparse_objective[1], expected[1], places=10)

    def test_sparse_monotonic_sync_handles_full_rate_sized_stream(self):
        left = [index * 0.1 for index in range(2000)]
        right = [index / 15.0 + 0.008 for index in range(3000)]

        pairs = minimum_cost_maximum_cardinality_monotonic_pairs(left, right, 0.05)

        self.assertEqual(len(pairs), len(left))
        self.assertTrue(all(left_a < left_b and right_a < right_b for (left_a, right_a), (left_b, right_b) in zip(pairs, pairs[1:])))

    def test_centerline_smoothing_preserves_supported_endpoints(self):
        points = [
            {"x_m": 0.0, "y_m": 0.0},
            {"x_m": 0.3, "y_m": 2.0},
            {"x_m": 0.0, "y_m": 4.0},
        ]

        smoothed = smooth_centerline_points(points)

        self.assertEqual(smoothed[0], points[0])
        self.assertEqual(smoothed[-1], points[-1])

    def test_invalid_sensor_stamp_is_not_silently_normalized(self):
        self.assertIsNone(sensor_timestamp_s({"stamp": {"sec": 1, "nanosec": 1_000_000_000}}))
        self.assertIsNone(sensor_timestamp_s({"stamp": {"sec": "bad", "nanosec": 0}}))


if __name__ == "__main__":
    unittest.main()
