from __future__ import annotations

import unittest

from dashboard_server.camera_analysis import (
    CameraOverlayStabilizer,
    CAMERA_ALGORITHM_NAME,
    LOCAL_VISION_ALGORITHM_NAME,
    LOCAL_VISION_ANALYSIS_REVISION,
    analyze_camera_frame_payload,
    current_camera_overlay_algorithm,
)


class CameraAnalysisPolicyTest(unittest.TestCase):
    def test_camera_perception_requires_local_ai_worker(self) -> None:
        overlay = analyze_camera_frame_payload({"data": "not-a-real-image"})

        self.assertFalse(overlay["ok"])
        self.assertEqual(overlay["algorithm"], LOCAL_VISION_ALGORITHM_NAME)
        self.assertEqual(overlay["method"], "vision_ai_url_required")
        self.assertTrue(overlay["ai_required"])
        self.assertIn("ORCHARD_VISION_AI_URL", overlay["error"])

    def test_legacy_opencv_overlay_is_no_longer_current(self) -> None:
        self.assertFalse(current_camera_overlay_algorithm(CAMERA_ALGORITHM_NAME))
        self.assertFalse(current_camera_overlay_algorithm(LOCAL_VISION_ALGORITHM_NAME))
        self.assertTrue(current_camera_overlay_algorithm(LOCAL_VISION_ALGORITHM_NAME, LOCAL_VISION_ANALYSIS_REVISION))

    def test_failed_current_overlay_is_not_reused(self) -> None:
        overlay = analyze_camera_frame_payload(
            {
                "data": "not-a-real-image",
                "overlay": {
                    "ok": False,
                    "algorithm": LOCAL_VISION_ALGORITHM_NAME,
                    "revision": LOCAL_VISION_ANALYSIS_REVISION,
                    "method": "backend_unavailable",
                    "error": "old worker failure",
                },
            }
        )

        self.assertEqual(overlay["method"], "vision_ai_url_required")
        self.assertNotEqual(overlay["error"], "old worker failure")

    def test_stabilizer_requires_temporal_confirmation_for_map_admission(self) -> None:
        stabilizer = CameraOverlayStabilizer()

        first = stabilizer.update(
            {
                "ok": True,
                "image": {"width": 640, "height": 360},
                "detections": [
                    {
                        "bbox": [100, 20, 40, 250],
                        "confidence": 0.22,
                        "tree_probability": 0.58,
                        "ood_score": 0.42,
                        "admission": "accepted",
                        "label": "trunk",
                    }
                ],
            },
            now=1.0,
        )
        second = stabilizer.update(
            {
                "ok": True,
                "image": {"width": 640, "height": 360},
                "detections": [
                    {
                        "bbox": [102, 21, 40, 250],
                        "confidence": 0.24,
                        "tree_probability": 0.61,
                        "ood_score": 0.39,
                        "admission": "accepted",
                        "label": "trunk",
                    }
                ],
            },
            now=1.2,
        )

        self.assertEqual(first["detections"], [])
        self.assertEqual(first["pending_detections"], 1)
        self.assertEqual(len(second["detections"]), 1)
        self.assertEqual(second["detections"][0]["map_admission"], "accepted")

    def test_stabilizer_allows_very_high_probability_for_immediate_display_only(self) -> None:
        overlay = CameraOverlayStabilizer().update(
            {
                "ok": True,
                "image": {"width": 640, "height": 360},
                "detections": [
                    {
                        "bbox": [100, 20, 40, 250],
                        "confidence": 0.84,
                        "tree_probability": 0.84,
                        "ood_score": 0.16,
                        "admission": "accepted",
                        "label": "trunk",
                    }
                ],
            },
            now=1.0,
        )

        self.assertEqual(len(overlay["detections"]), 1)
        self.assertEqual(overlay["detections"][0]["display_admission"], "accepted")
        self.assertEqual(overlay["detections"][0]["map_admission"], "pending")


if __name__ == "__main__":
    unittest.main()
