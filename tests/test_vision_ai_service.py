import unittest
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "vision_ai_service.py"
SPEC = importlib.util.spec_from_file_location("vision_ai_service", MODULE_PATH)
vision_ai_service = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(vision_ai_service)


class Point:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class VisionAiServiceTest(unittest.TestCase):
    def test_roboflow_contour_accepts_point_objects(self) -> None:
        prediction = type("Prediction", (), {"points": [Point(10.2, 20.8), Point(30.1, 40.4), Point(50.0, 25.0)]})()

        contour = vision_ai_service.roboflow_contour(prediction, width=64, height=48)

        self.assertEqual(contour, [[10, 21], [30, 40], [50, 25]])

    def test_dedupe_detections_keeps_higher_confidence_overlap(self) -> None:
        detections = [
            {"bbox": [10, 10, 40, 40], "confidence": 0.4},
            {"bbox": [12, 12, 40, 40], "confidence": 0.8},
            {"bbox": [90, 90, 20, 20], "confidence": 0.3},
        ]

        deduped = vision_ai_service.dedupe_detections(detections, iou_threshold=0.5)

        self.assertEqual([item["confidence"] for item in deduped], [0.8, 0.3])

    def test_runtime_ood_rejects_person_like_candidate(self) -> None:
        detection = {
            "bbox": [100, 30, 40, 240],
            "confidence": 0.48,
            "model_confidence": 0.48,
            "features": {
                "aspect_h_over_w": 6.0,
                "height_ratio": 0.67,
                "area_ratio": 0.04,
                "bottom_ratio": 0.75,
                "edge_contact": False,
                "scene_green_ratio": 0.2,
                "canopy_green_support": 0.1,
            },
            "clip_ood": {
                "tree_probability": 0.03,
                "top_negative": "a person or human body",
                "top_negative_probability": 0.82,
            },
        }

        result = vision_ai_service.apply_runtime_ood_policy(detection)

        self.assertEqual(result["admission"], "rejected")
        self.assertIn("clip_strong_non_tree", result["rejection_reasons"])

    def test_runtime_ood_admits_supported_tree_trunk(self) -> None:
        detection = {
            "bbox": [100, 30, 40, 240],
            "confidence": 0.76,
            "model_confidence": 0.76,
            "features": {
                "aspect_h_over_w": 6.0,
                "height_ratio": 0.67,
                "area_ratio": 0.04,
                "bottom_ratio": 0.75,
                "edge_contact": False,
                "scene_green_ratio": 0.2,
                "canopy_green_support": 0.22,
            },
            "clip_ood": {
                "tree_probability": 0.81,
                "top_negative": "a concrete or wooden building column",
                "top_negative_probability": 0.04,
            },
        }

        result = vision_ai_service.apply_runtime_ood_policy(detection)

        self.assertEqual(result["admission"], "accepted")
        self.assertGreater(result["tree_probability"], 0.7)
        self.assertEqual(result["model_provenance"]["remote_api"], False)

    def test_runtime_ood_rejects_low_support_pavilion_post(self) -> None:
        result = vision_ai_service.apply_runtime_ood_policy(
            {
                "bbox": [144, 79, 24, 162],
                "model_confidence": 0.144,
                "features": {
                    "aspect_h_over_w": 6.75,
                    "height_ratio": 0.45,
                    "area_ratio": 0.016,
                    "bottom_ratio": 0.67,
                    "edge_contact": False,
                    "scene_green_ratio": 0.17,
                    "canopy_green_support": 0.028,
                },
                "clip_ood": {
                    "tree_probability": 0.597,
                    "top_negative": "a metal utility pole",
                    "top_negative_probability": 0.171,
                },
            }
        )

        self.assertEqual(result["admission"], "rejected")
        self.assertIn("structural_post_without_canopy", result["rejection_reasons"])


if __name__ == "__main__":
    unittest.main()
