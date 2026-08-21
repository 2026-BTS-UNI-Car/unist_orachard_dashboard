import json
import math
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from dashboard_server.camera_analysis import LOCAL_VISION_ALGORITHM_NAME, LOCAL_VISION_ANALYSIS_REVISION
from scripts.camera_ws_bridge import (
    CameraState,
    VISION_BEARINGS_SCHEMA,
    VISION_TREE_CLASS,
    camera_info_payload,
    scale_overlay_geometry,
    status_payload,
    vision_inference_thread,
    vision_tree_bearings_payload,
)


def worker_overlay(detections=None, **updates):
    payload = {
        "ok": True,
        "algorithm": LOCAL_VISION_ALGORITHM_NAME,
        "revision": LOCAL_VISION_ANALYSIS_REVISION,
        "image": {"width": 640, "height": 360},
        "detections": detections or [],
    }
    payload.update(updates)
    return payload


def accepted_detection(x=400, **updates):
    detection = {
        "label": "orchard_tree_trunk",
        "bbox": [x, 40, 40, 240],
        "contour": [[[x + 5, 50]], [[x + 35, 270]]],
        "confidence": 0.82,
        "tree_probability": 0.91,
        "ood_score": 0.08,
        "map_admission": "accepted",
    }
    detection.update(updates)
    return detection


def assert_strict_ros_contract(testcase, payload):
    testcase.assertEqual(payload["schema"], "orchard.camera_bearings.v1")
    testcase.assertIs(type(payload["ok"]), bool)
    testcase.assertEqual(payload["algorithm"], "local_orchard_vision_ai_worker_v4")
    testcase.assertEqual(payload["revision"], "orchard_trunk_yolo11_trt_clip_ood_v1")
    testcase.assertEqual(set(payload["stamp"]), {"sec", "nanosec"})
    testcase.assertEqual(payload["frame_id"], "camera_link")
    testcase.assertIs(type(payload["calibration"]["bearing_valid"]), bool)
    testcase.assertIs(type(payload["calibration"]["metric_range_valid"]), bool)
    testcase.assertLessEqual(len(payload["detections"]), 64)
    for detection in payload["detections"]:
        testcase.assertEqual(detection["class_name"], "orchard_tree_trunk")
        testcase.assertEqual(detection["label"], "orchard_tree_trunk")
        testcase.assertTrue(math.isfinite(detection["bearing_rad"]))
        testcase.assertGreaterEqual(detection["bearing_rad"], -math.pi)
        testcase.assertLessEqual(detection["bearing_rad"], math.pi)
        for key in ("confidence", "tree_probability", "ood_score"):
            testcase.assertTrue(math.isfinite(detection[key]))
            testcase.assertGreaterEqual(detection[key], 0.0)
            testcase.assertLessEqual(detection[key], 1.0)
        testcase.assertIs(type(detection["calibrated_metric"]), bool)
        if detection["calibrated_metric"]:
            testcase.assertGreater(detection["range_m"], 0.0)
            testcase.assertIsInstance(detection["range_source"], str)
    json.dumps(payload, allow_nan=False)


class CameraVisionBearingContractTests(unittest.TestCase):
    def test_ros_camera_info_numpy_arrays_do_not_require_boolean_coercion(self):
        message = SimpleNamespace(
            header=SimpleNamespace(
                frame_id="camera_link_optical",
                stamp=SimpleNamespace(sec=123, nanosec=456),
            ),
            width=1920,
            height=1080,
            k=np.array([1371.0, 0.0, 960.0, 0.0, 1371.0, 540.0, 0.0, 0.0, 1.0]),
            d=np.zeros(5),
            distortion_model="plumb_bob",
        )

        payload = camera_info_payload(message)

        self.assertEqual(payload["k"], [1371.0, 0.0, 960.0, 0.0, 1371.0, 540.0, 0.0, 0.0, 1.0])
        self.assertEqual(payload["d"], [0.0] * 5)
        self.assertEqual(payload["stamp_ns"], 123_000_000_456)

    def test_nominal_camera_info_emits_strict_unverified_bearing_only_contract(self):
        payload = vision_tree_bearings_payload(
            worker_overlay([accepted_detection()]),
            stamp_sec=123,
            stamp_nanosec=456,
            camera_info={"width": 1920, "height": 1080, "k": [1371.022086, 0, 960, 0, 1371.022086, 540, 0, 0, 1]},
        )

        assert_strict_ros_contract(self, payload)
        self.assertEqual(payload["schema"], VISION_BEARINGS_SCHEMA)
        self.assertFalse(payload["calibration"]["bearing_valid"])
        self.assertFalse(payload["calibration"]["metric_range_valid"])
        self.assertEqual(payload["intrinsics_source"], "camera_info_nominal_or_unverified")
        self.assertEqual(len(payload["detections"]), 1)
        detection = payload["detections"][0]
        self.assertEqual(detection["class_name"], VISION_TREE_CLASS)
        self.assertLess(detection["bearing_rad"], 0.0, "pixel-right must be negative in REP-103")
        self.assertFalse(detection["calibrated_metric"])
        self.assertNotIn("range_m", detection)
        self.assertEqual(detection["contour"], [[405.0, 50.0], [435.0, 270.0]])

    def test_explicit_validation_is_required_before_bearing_valid(self):
        info = {"width": 1920, "height": 1080, "k": [1371.022086, 0, 960, 0, 1371.022086, 540, 0, 0, 1]}
        unverified = vision_tree_bearings_payload(
            worker_overlay([accepted_detection(x=100)]),
            stamp_sec=1,
            stamp_nanosec=2,
            camera_info=info,
            calibration_valid=False,
        )
        verified = vision_tree_bearings_payload(
            worker_overlay([accepted_detection(x=100)]),
            stamp_sec=1,
            stamp_nanosec=2,
            camera_info=info,
            calibration_valid=True,
        )

        self.assertFalse(unverified["calibration"]["bearing_valid"])
        self.assertTrue(verified["calibration"]["bearing_valid"])
        self.assertEqual(verified["intrinsics_source"], "camera_info_operator_validated")
        self.assertGreater(verified["detections"][0]["bearing_rad"], 0.0, "pixel-left must be positive in REP-103")

    def test_nominal_70_degree_pinhole_projection_and_edge_gate(self):
        payload = vision_tree_bearings_payload(
            worker_overlay(
                [
                    accepted_detection(x=0),
                    accepted_detection(x=620),
                    accepted_detection(x=700),
                ]
            ),
            stamp_sec=1,
            stamp_nanosec=0,
            nominal_fov_deg=70.0,
        )

        self.assertEqual(len(payload["detections"]), 2)
        left, right = payload["detections"]
        self.assertAlmostEqual(left["bearing_rad"], math.radians(33.52), delta=0.03)
        self.assertAlmostEqual(right["bearing_rad"], -math.radians(35.0), delta=0.03)
        self.assertFalse(payload["calibration"]["bearing_valid"])

    def test_worker_failure_or_revision_mismatch_is_empty_fail_closed(self):
        cases = [
            worker_overlay([accepted_detection()], ok=False, error="worker unavailable"),
            worker_overlay([accepted_detection()], revision="stale-revision"),
            None,
        ]
        for overlay in cases:
            with self.subTest(overlay=overlay):
                payload = vision_tree_bearings_payload(overlay, stamp_sec=1, stamp_nanosec=0)
                assert_strict_ros_contract(self, payload)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["detections"], [])

    def test_only_map_accepted_finite_detections_are_published_and_capped(self):
        detections = [
            accepted_detection(map_admission="pending"),
            accepted_detection(label="person"),
            accepted_detection(confidence=float("nan")),
            accepted_detection(bbox=[1, 2, -3, 4]),
            "malformed",
            *[accepted_detection(x=100 + index % 400) for index in range(70)],
        ]
        payload = vision_tree_bearings_payload(
            worker_overlay(detections),
            stamp_sec=1,
            stamp_nanosec=0,
        )

        assert_strict_ros_contract(self, payload)
        self.assertEqual(len(payload["detections"]), 64)
        mislabeled = vision_tree_bearings_payload(
            worker_overlay([accepted_detection(label="person")]),
            stamp_sec=1,
            stamp_nanosec=0,
        )
        self.assertEqual(mislabeled["detections"], [])

    def test_shared_overlay_geometry_scales_without_reinference(self):
        overlay = worker_overlay([accepted_detection(x=100, line=[100, 10, 120, 300])])
        scaled = scale_overlay_geometry(overlay, 1280, 720)

        self.assertEqual(scaled["image"], {"width": 1280, "height": 720})
        detection = scaled["detections"][0]
        self.assertEqual(detection["bbox"], [200, 80, 80, 480])
        self.assertEqual(detection["line"], [200, 20, 240, 600])
        self.assertEqual(overlay["detections"][0]["bbox"], [100, 40, 40, 240])

    def test_latest_only_state_returns_newest_frame_without_a_queue(self):
        state = CameraState()
        for value in (10, 20, 30):
            state.update_frame("camera_link_optical", value, 0, np.full((12, 20, 3), value, dtype=np.uint8), "bgr8")

        sample = state.inference_input_after(1)
        self.assertEqual(sample["frame_count"], 3)
        self.assertEqual(int(sample["frame_bgr"][0, 0, 0]), 30)
        self.assertIsNone(state.inference_input_after(3))

    def test_vision_loop_runs_with_zero_browser_clients(self):
        state = CameraState()
        state.update_frame("camera_link_optical", 11, 22, np.zeros((90, 160, 3), dtype=np.uint8), "bgr8")
        stop = threading.Event()
        overlay = worker_overlay([accepted_detection(x=80)])
        with patch("scripts.camera_ws_bridge.analyze_camera_image", return_value=overlay) as analyze:
            thread = threading.Thread(
                target=vision_inference_thread,
                args=(state, stop),
                kwargs={
                    "target_hz": 20.0,
                    "inference_width": 160,
                    "output_frame": "camera_link",
                    "calibration_valid": False,
                    "nominal_fov_deg": 70.0,
                },
            )
            thread.start()
            deadline = time.time() + 1.0
            while state.snapshot(include_frame=False)["vision_inference_count"] < 1 and time.time() < deadline:
                time.sleep(0.01)
            stop.set()
            thread.join(timeout=1.0)

        self.assertFalse(thread.is_alive())
        analyze.assert_called_once()
        snap = state.snapshot(include_frame=False)
        self.assertEqual(snap["client_count"], 0)
        self.assertEqual(snap["vision_inference_count"], 1)
        self.assertEqual(snap["vision_generation"], 1)
        status = status_payload(
            state,
            image_topic="/camera/image_raw",
            camera_info_topic="/camera/camera_info",
            vision_topic="/orchard/vision/tree_bearings",
            vision_hz=20.0,
            calibration_valid=False,
        )
        publisher = status["vision_publisher"]
        self.assertFalse(publisher["bearing_calibration_requested"])
        self.assertFalse(publisher["bearing_calibration_valid"])
        self.assertFalse(publisher["metric_range_valid"])
        self.assertEqual(publisher["last_payload"]["schema"], VISION_BEARINGS_SCHEMA)
        self.assertEqual(publisher["last_payload"]["source"], "camera_ws_bridge_shared_worker_v4")


if __name__ == "__main__":
    unittest.main()
