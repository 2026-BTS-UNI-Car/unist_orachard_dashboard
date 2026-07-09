from __future__ import annotations

import unittest

from dashboard_server.camera_analysis import (
    CAMERA_ALGORITHM_NAME,
    LOCAL_VISION_ALGORITHM_NAME,
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
        self.assertTrue(current_camera_overlay_algorithm(LOCAL_VISION_ALGORITHM_NAME))


if __name__ == "__main__":
    unittest.main()
