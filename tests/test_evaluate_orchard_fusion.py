from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_orchard_fusion import evaluate_timeline
from scripts.evaluate_orchard_fusion_v10 import evaluate_timeline as evaluate_timeline_v10
from scripts.evaluate_orchard_fusion_v9 import evaluate_timeline as evaluate_timeline_v9


class OrchardFusionEvaluationReadinessTest(unittest.TestCase):
    def evaluate(self, timeline: dict) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timeline.json"
            path.write_text(json.dumps(timeline), encoding="utf-8")
            return evaluate_timeline(path)

    def test_empty_timeline_keeps_invariants_separate_but_overall_fails(self):
        result = self.evaluate(
            {
                "id": "empty",
                "lidar": {"frames": []},
                "camera": {"frames": []},
            }
        )

        self.assertTrue(result["implementation_invariants_ok"], result)
        self.assertFalse(result["data_sufficiency_ok"])
        self.assertFalse(result["navigation_ready"])
        self.assertFalse(result["ok"])
        self.assertIn(
            "insufficient_lidar_frames",
            result["readiness"]["data_sufficiency_reasons"],
        )

    def test_versioned_entrypoints_delegate_to_generic_evaluator(self):
        self.assertIs(evaluate_timeline_v9, evaluate_timeline)
        self.assertIs(evaluate_timeline_v10, evaluate_timeline)

    def test_one_empty_lidar_frame_is_not_data_sufficient(self):
        result = self.evaluate(
            {
                "id": "one-empty",
                "lidar": {"frames": [{"t_s": 0.0, "points": []}]},
                "camera": {"frames": []},
            }
        )

        self.assertTrue(result["implementation_invariants_ok"], result)
        self.assertFalse(result["data_sufficiency_ok"])
        self.assertFalse(result["navigation_ready"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["readiness"]["observed"]["nonempty_lidar_frames"], 0)

    def test_sufficient_input_can_pass_evaluation_without_claiming_navigation_ready(self):
        result = self.evaluate(
            {
                "id": "two-nonempty-no-row",
                "lidar": {
                    "frames": [
                        {"t_s": 0.0, "points": [{"x_m": 0.0, "y_m": 1.0}]},
                        {"t_s": 0.5, "points": [{"x_m": 0.0, "y_m": 1.0}]},
                    ]
                },
                "camera": {"frames": []},
            }
        )

        self.assertTrue(result["implementation_invariants_ok"], result)
        self.assertTrue(result["data_sufficiency_ok"], result)
        self.assertFalse(result["navigation_ready"])
        self.assertTrue(result["ok"], result)
        reasons = result["readiness"]["navigation_readiness_reasons"]
        self.assertIn("centerline_not_ready", reasons)
        self.assertIn("ground_truth_validated_missing", reasons)
        self.assertIn("lidar_base_extrinsics_validated_missing", reasons)
        self.assertIn("camera_intrinsics_validated_missing", reasons)
        self.assertIn("camera_lidar_extrinsics_validated_missing", reasons)
        self.assertIn("trajectory_pose_validated_missing", reasons)
        self.assertIn("full_rate_replay_validated_missing", reasons)
        self.assertIn("sensor_header_clock_integrity_not_validated", reasons)


if __name__ == "__main__":
    unittest.main()
