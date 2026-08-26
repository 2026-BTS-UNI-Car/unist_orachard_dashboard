from __future__ import annotations

import unittest

import numpy as np

from dashboard_server.camera_analysis import (
    CameraOverlayStabilizer,
    CAMERA_ALGORITHM_NAME,
    LOCAL_VISION_ALGORITHM_NAME,
    LOCAL_VISION_ANALYSIS_REVISION,
    VISION_INFERENCE_INPUT_POLICY_REVISION,
    analyze_camera_frame_payload,
    current_camera_overlay_algorithm,
    current_source_native_camera_overlay,
    current_source_native_inference_policy,
    prepare_camera_inference_frame,
)


class CameraAnalysisPolicyTest(unittest.TestCase):
    @staticmethod
    def native_policy(width: int = 1920, height: int = 1080) -> dict:
        return {
            "revision": VISION_INFERENCE_INPUT_POLICY_REVISION,
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

    def test_native_inference_policy_keeps_source_pixels_without_copy_or_upscale(self) -> None:
        source = np.zeros((1080, 1920, 3), dtype=np.uint8)

        native, policy = prepare_camera_inference_frame(source, 0)
        oversized_cap, oversized_policy = prepare_camera_inference_frame(source, 3840)

        self.assertIs(native, source)
        self.assertIs(oversized_cap, source)
        self.assertEqual(native.shape, (1080, 1920, 3))
        self.assertEqual(policy["revision"], VISION_INFERENCE_INPUT_POLICY_REVISION)
        self.assertEqual(policy["mode"], "source_native")
        self.assertEqual(policy["inference_width"], 1920)
        self.assertTrue(policy["preview_decoupled"])
        self.assertTrue(oversized_policy["no_upscale"])

    def test_bounded_inference_preserves_aspect_ratio_independently_of_preview(self) -> None:
        source = np.zeros((1080, 1920, 3), dtype=np.uint8)

        inference, policy = prepare_camera_inference_frame(source, 960)

        self.assertEqual(inference.shape, (540, 960, 3))
        self.assertEqual(policy["mode"], "bounded_width")
        self.assertEqual(policy["source_width"], 1920)
        self.assertEqual(policy["source_height"], 1080)
        self.assertEqual(policy["inference_width"], 960)
        self.assertEqual(policy["inference_height"], 540)

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

    def test_current_native_overlay_requires_complete_matching_pixel_provenance(self) -> None:
        policy = self.native_policy()
        overlay = {
            "ok": True,
            "algorithm": LOCAL_VISION_ALGORITHM_NAME,
            "revision": LOCAL_VISION_ANALYSIS_REVISION,
            "image": {"width": 640, "height": 360},
            "detections": [],
            "inference_input_policy": dict(policy),
        }
        frame = {
            "width": 640,
            "height": 360,
            "source_width": 1920,
            "source_height": 1080,
            "inference_input_policy": dict(policy),
            "overlay": overlay,
        }

        self.assertTrue(current_source_native_inference_policy(policy))
        self.assertTrue(current_source_native_camera_overlay(overlay, frame=frame))

        bounded = {**policy, "mode": "bounded_width", "requested_width": 640, "inference_width": 640, "inference_height": 360}
        self.assertFalse(current_source_native_inference_policy(bounded))
        self.assertFalse(current_source_native_camera_overlay({**overlay, "inference_input_policy": bounded}, frame=frame))
        self.assertFalse(current_source_native_inference_policy({**policy, "requested_width": "0"}))

    def test_legacy_640_preview_cannot_be_reinferred_as_native_v10(self) -> None:
        overlay = analyze_camera_frame_payload(
            {
                "width": 640,
                "height": 360,
                "source_width": 1920,
                "source_height": 1080,
                "data": "legacy-preview-jpeg",
                "overlay": {
                    "ok": True,
                    "algorithm": LOCAL_VISION_ALGORITHM_NAME,
                    "revision": LOCAL_VISION_ANALYSIS_REVISION,
                    "image": {"width": 640, "height": 360},
                    "detections": [],
                },
            },
            require_source_native_provenance=True,
        )

        self.assertFalse(overlay["ok"])
        self.assertEqual(overlay["method"], "source_native_provenance_required")

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

    def test_stabilizer_resets_identity_on_sensor_clock_regression(self) -> None:
        stabilizer = CameraOverlayStabilizer()
        proposal = {
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
        }

        first = stabilizer.update(proposal, now=100.0)
        regressed = stabilizer.update(proposal, now=10.0)

        self.assertEqual(first["detections"][0]["stable_hits"], 1)
        self.assertEqual(regressed["detections"][0]["stable_hits"], 1)
        self.assertEqual(regressed["detections"][0]["map_admission"], "pending")
        self.assertTrue(regressed["stabilizer_clock_reset"])
        self.assertFalse(regressed["stabilizer_clock_integrity_ok"])
        self.assertNotEqual(first["detections"][0]["track_id"], regressed["detections"][0]["track_id"])

    def test_stabilizer_duplicate_stamp_cannot_add_confirmation_hit(self) -> None:
        stabilizer = CameraOverlayStabilizer()
        proposal = {
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
        }

        stabilizer.update(proposal, now=50.0)
        duplicate = stabilizer.update(proposal, now=50.0)

        self.assertEqual(duplicate["detections"][0]["stable_hits"], 1)
        self.assertEqual(duplicate["detections"][0]["map_admission"], "pending")
        self.assertTrue(duplicate["stabilizer_clock_reset"])


if __name__ == "__main__":
    unittest.main()
