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


if __name__ == "__main__":
    unittest.main()
