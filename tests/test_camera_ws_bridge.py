import json
import math
import struct
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from dashboard_server.camera_analysis import (
    LOCAL_VISION_ALGORITHM_NAME,
    LOCAL_VISION_ANALYSIS_REVISION,
    VISION_INFERENCE_INPUT_POLICY_REVISION,
)
from scripts.camera_ws_bridge import (
    CAMERA_INFO_IMAGE_PROVENANCE_REVISION,
    CameraState,
    VISION_BEARINGS_SCHEMA,
    VISION_TREE_CLASS,
    camera_info_payload,
    camera_info_image_provenance,
    client_settings,
    encode_binary_camera_frame,
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
    def test_binary_camera_packet_removes_base64_and_preserves_metadata(self):
        metadata = {"type": "frame", "frame_count": 42, "width": 640, "height": 480}
        jpeg = b"\xff\xd8binary-jpeg\xff\xd9"

        packet = encode_binary_camera_frame(metadata, jpeg)

        self.assertEqual(packet[:4], b"UBCF")
        self.assertEqual(packet[4], 1)
        metadata_size = struct.unpack("!I", packet[5:9])[0]
        decoded = json.loads(packet[9 : 9 + metadata_size].decode("utf-8"))
        self.assertEqual(decoded, metadata)
        self.assertEqual(packet[9 + metadata_size :], jpeg)
        self.assertNotIn(b"base64", packet)

    def test_camera_client_settings_negotiate_binary_decode_ack(self):
        settings = client_settings(
            "/ws/camera?resolution=640x480&fps=60&quality=70&transport=binary-v1&flow=decode-ack"
        )
        self.assertEqual(settings["transport"], "binary-v1")
        self.assertEqual(settings["flow"], "decode-ack")
        self.assertEqual(settings["quality"], 70)

    def test_camera_client_settings_negotiate_two_credit_latest_ack(self):
        settings = client_settings(
            "/ws/camera?resolution=1280x720&fps=30&transport=binary-v1&flow=latest-ack"
        )
        self.assertEqual(settings["transport"], "binary-v1")
        self.assertEqual(settings["flow"], "latest-ack")

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
        self.assertFalse(payload["calibration"]["camera_info_provenance_valid"])
        self.assertEqual(payload["intrinsics_source"], "nominal_fov_70deg_unverified")
        self.assertEqual(len(payload["detections"]), 1)
        detection = payload["detections"][0]
        self.assertEqual(detection["class_name"], VISION_TREE_CLASS)
        self.assertLess(detection["bearing_rad"], 0.0, "pixel-right must be negative in REP-103")
        self.assertFalse(detection["calibrated_metric"])
        self.assertNotIn("range_m", detection)
        self.assertEqual(detection["contour"], [[405.0, 50.0], [435.0, 270.0]])

    def test_explicit_validation_is_required_before_bearing_valid(self):
        info = {
            "width": 1920,
            "height": 1080,
            "k": [1371.022086, 0, 960, 0, 1371.022086, 540, 0, 0, 1],
            "frame_id": "camera_link_optical",
            "stamp_ns": 1_000_000_002,
        }
        provenance = camera_info_image_provenance(
            info,
            image_frame_id="camera_link_optical",
            image_width=1920,
            image_height=1080,
            image_stamp_ns=1_000_000_002,
        )
        unverified = vision_tree_bearings_payload(
            worker_overlay([accepted_detection(x=100)]),
            stamp_sec=1,
            stamp_nanosec=2,
            camera_info=info,
            camera_info_provenance=provenance,
            calibration_valid=False,
        )
        verified = vision_tree_bearings_payload(
            worker_overlay([accepted_detection(x=100)]),
            stamp_sec=1,
            stamp_nanosec=2,
            camera_info=info,
            camera_info_provenance=provenance,
            calibration_valid=True,
        )

        self.assertFalse(unverified["calibration"]["bearing_valid"])
        self.assertTrue(verified["calibration"]["bearing_valid"])
        self.assertTrue(verified["calibration"]["camera_info_provenance_valid"])
        self.assertEqual(verified["calibration"]["camera_info_provenance_reasons"], [])
        self.assertEqual(verified["intrinsics_source"], "camera_info_operator_validated")
        self.assertGreater(verified["detections"][0]["bearing_rad"], 0.0, "pixel-left must be positive in REP-103")

    def test_nanosecond_provenance_preserves_large_integer_stamps_exactly(self):
        stamp_ns = 1_784_900_123_456_789_123
        info = {
            "width": 1920,
            "height": 1080,
            "k": [1371.0, 0.0, 960.0, 0.0, 1371.0, 540.0, 0.0, 0.0, 1.0],
            "frame_id": "camera_link_optical",
            "stamp_ns": stamp_ns,
        }

        result = camera_info_image_provenance(
            info,
            image_frame_id="camera_link_optical",
            image_width=1920,
            image_height=1080,
            image_stamp_ns=stamp_ns,
        )

        self.assertTrue(result["valid"])
        self.assertEqual(result["image_stamp_ns"], stamp_ns)
        self.assertEqual(result["camera_info_stamp_ns"], stamp_ns)

    def test_camera_info_provenance_fails_closed_on_header_dimension_stamp_or_clock_mismatch(self):
        info = {
            "width": 1920,
            "height": 1080,
            "k": [1371.0, 0.0, 960.0, 0.0, 1371.0, 540.0, 0.0, 0.0, 1.0],
            "frame_id": "camera_link_optical",
            "stamp_ns": 10_000_000_000,
        }
        common = {
            "image_frame_id": "camera_link_optical",
            "image_width": 1920,
            "image_height": 1080,
            "image_stamp_ns": 10_020_000_000,
            "max_delta_s": 0.12,
        }

        valid = camera_info_image_provenance(info, **common)
        self.assertTrue(valid["valid"])
        self.assertEqual(valid["revision"], CAMERA_INFO_IMAGE_PROVENANCE_REVISION)
        self.assertEqual(valid["gate"], "strict_fail_closed")
        cases = [
            ({**info, "frame_id": "different_camera"}, {}, "frame_id_mismatch"),
            ({**info, "width": 640}, {}, "native_dimensions_mismatch"),
            ({**info, "stamp_ns": 9_000_000_000}, {}, "camera_info_stale"),
            ({**info, "stamp_ns": 11_000_000_000}, {}, "camera_info_future"),
            (info, {"image_clock_integrity_ok": False}, "image_clock_regression"),
            (info, {"camera_info_clock_integrity_ok": False}, "camera_info_clock_regression"),
            (info, {"image_clock_epoch": 1, "camera_info_clock_epoch": 0}, "clock_epoch_mismatch"),
        ]
        for candidate, updates, reason in cases:
            with self.subTest(reason=reason):
                result = camera_info_image_provenance(candidate, **common, **updates)
                self.assertFalse(result["valid"])
                self.assertIn(reason, result["reasons"])

    def test_operator_validation_cannot_override_stale_mismatch_or_regression(self):
        info = {
            "width": 1920,
            "height": 1080,
            "k": [1371.0, 0.0, 960.0, 0.0, 1371.0, 540.0, 0.0, 0.0, 1.0],
            "frame_id": "camera_link_optical",
            "stamp_ns": 10_000_000_000,
        }
        common = {
            "image_frame_id": "camera_link_optical",
            "image_width": 1920,
            "image_height": 1080,
            "image_stamp_ns": 10_000_000_000,
        }
        cases = [
            ({**info, "stamp_ns": 9_000_000_000}, {}, "camera_info_stale"),
            ({**info, "frame_id": "another_camera"}, {}, "frame_id_mismatch"),
            (info, {"image_clock_integrity_ok": False, "image_clock_regression_count": 1}, "image_clock_regression"),
        ]

        for candidate, updates, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                provenance = camera_info_image_provenance(candidate, **common, **updates)
                payload = vision_tree_bearings_payload(
                    worker_overlay([accepted_detection(x=100)]),
                    stamp_sec=10,
                    stamp_nanosec=0,
                    frame_id="camera_link",
                    camera_info=candidate,
                    camera_info_provenance=provenance,
                    calibration_valid=True,
                )

                calibration = payload["calibration"]
                self.assertFalse(calibration["bearing_valid"])
                self.assertFalse(calibration["camera_info_provenance_valid"])
                self.assertEqual(calibration["camera_info_provenance_reason"], expected_reason)
                self.assertIn(expected_reason, calibration["camera_info_provenance_reasons"])
                self.assertEqual(calibration["camera_info_provenance"], provenance)
                self.assertEqual(payload["intrinsics_source"], "nominal_fov_70deg_unverified")

    def test_missing_image_frame_id_is_preserved_and_fails_closed(self):
        state = CameraState()
        frame = np.zeros((108, 192, 3), dtype=np.uint8)
        state.update_frame("", 10, 0, frame, "bgr8")
        state.update_camera_info(
            {
                "width": 192,
                "height": 108,
                "k": [137.0, 0.0, 96.0, 0.0, 137.0, 54.0, 0.0, 0.0, 1.0],
                "frame_id": "camera_link_optical",
                "stamp_ns": 10_000_000_000,
            }
        )

        sample = state.inference_input_after(0)

        self.assertEqual(state.snapshot(include_frame=False)["frame_id"], "")
        self.assertFalse(sample["camera_info_provenance"]["valid"])
        self.assertIn("frame_id_mismatch", sample["camera_info_provenance"]["reasons"])

    def test_malformed_camera_info_metadata_fails_closed_without_exception(self):
        result = camera_info_image_provenance(
            {
                "width": "not-a-number",
                "height": 1080.5,
                "k": [1371.0, 0.0, 960.0, 0.0, 1371.0, 540.0, 0.0, 0.0, 1.0],
                "frame_id": "camera_link_optical",
                "stamp_ns": float("nan"),
            },
            image_frame_id="camera_link_optical",
            image_width=1920,
            image_height=1080,
            image_stamp_ns=10_000_000_000,
        )

        self.assertFalse(result["valid"])
        self.assertIn("camera_info_metadata_invalid", result["reasons"])
        json.dumps(result, allow_nan=False)

    def test_state_clock_regression_invalidates_future_camera_info_provenance(self):
        state = CameraState()
        frame = np.zeros((108, 192, 3), dtype=np.uint8)
        state.update_frame("camera_link_optical", 10, 0, frame, "bgr8")
        state.update_camera_info(
            {
                "width": 192,
                "height": 108,
                "k": [137.0, 0.0, 96.0, 0.0, 137.0, 54.0, 0.0, 0.0, 1.0],
                "frame_id": "camera_link_optical",
                "stamp_ns": 10_000_000_000,
            }
        )
        self.assertTrue(state.inference_input_after(0)["camera_info_provenance"]["valid"])

        state.update_frame("camera_link_optical", 9, 0, frame, "bgr8")
        state.update_camera_info(
            {
                "width": 192,
                "height": 108,
                "k": [137.0, 0.0, 96.0, 0.0, 137.0, 54.0, 0.0, 0.0, 1.0],
                "frame_id": "camera_link_optical",
                "stamp_ns": 9_000_000_000,
            }
        )
        sample = state.inference_input_after(1)

        self.assertFalse(sample["camera_info_provenance"]["valid"])
        self.assertIn("image_clock_regression", sample["camera_info_provenance"]["reasons"])
        self.assertIn("camera_info_clock_regression", sample["camera_info_provenance"]["reasons"])
        self.assertEqual(state.snapshot(include_frame=False)["image_clock_regression_count"], 1)

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
            compressed_topic="/camera/image_jpeg",
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
        self.assertEqual(publisher["coalescing"], "single_slot_newest_frame_no_backlog")
        self.assertEqual(publisher["input_policy"]["revision"], VISION_INFERENCE_INPUT_POLICY_REVISION)

    def test_compressed_preview_is_reported_as_live_without_copying_raw_frame(self):
        state = CameraState()
        state.update_camera_info({"width": 1920, "height": 1080, "stamp_ns": 1_000_000_000})
        jpeg = b"\xff\xd8compressed-preview\xff\xd9"
        state.update_preview(
            "camera_link_optical",
            1,
            0,
            jpeg,
            "bgr8; jpeg compressed bgr8; width=1280; height=720",
        )

        preview = state.preview_snapshot()
        self.assertIs(preview["jpeg"], state.preview_jpeg)
        self.assertEqual(preview["source_width"], 1280)
        self.assertEqual(preview["source_height"], 720)
        status = status_payload(
            state,
            image_topic="/camera/image_raw",
            compressed_topic="/camera/image_jpeg",
            camera_info_topic="/camera/camera_info",
            vision_topic="/orchard/vision/tree_bearings",
            vision_hz=5.0,
            calibration_valid=False,
        )
        self.assertTrue(status["frame_topic_alive"])
        self.assertTrue(status["compressed_frame_topic_alive"])
        self.assertFalse(status["raw_frame_topic_alive"])
        self.assertEqual(status["encoding"], "jpeg")

    def test_vision_loop_propagates_stale_camera_info_as_fail_closed_provenance(self):
        state = CameraState()
        state.update_frame(
            "camera_link_optical",
            10,
            0,
            np.zeros((108, 192, 3), dtype=np.uint8),
            "bgr8",
        )
        state.update_camera_info(
            {
                "width": 192,
                "height": 108,
                "k": [137.0, 0.0, 96.0, 0.0, 137.0, 54.0, 0.0, 0.0, 1.0],
                "frame_id": "camera_link_optical",
                "stamp_ns": 9_000_000_000,
            }
        )
        stop = threading.Event()
        overlay = worker_overlay([], image={"width": 192, "height": 108})
        with patch("scripts.camera_ws_bridge.analyze_camera_image", return_value=overlay):
            thread = threading.Thread(
                target=vision_inference_thread,
                args=(state, stop),
                kwargs={
                    "target_hz": 20.0,
                    "inference_width": 0,
                    "output_frame": "camera_link",
                    "calibration_valid": True,
                    "nominal_fov_deg": 70.0,
                },
            )
            thread.start()
            deadline = time.time() + 1.0
            while state.snapshot(include_frame=False)["vision_inference_count"] < 1 and time.time() < deadline:
                time.sleep(0.01)
            stop.set()
            thread.join(timeout=1.0)

        _, payload = state.pending_vision(0)
        self.assertIsNotNone(payload)
        calibration = payload["calibration"]
        self.assertFalse(calibration["bearing_valid"])
        self.assertFalse(calibration["camera_info_provenance_valid"])
        self.assertEqual(calibration["camera_info_provenance_reason"], "camera_info_stale")
        self.assertEqual(
            calibration["camera_info_provenance"]["revision"],
            CAMERA_INFO_IMAGE_PROVENANCE_REVISION,
        )

    def test_native_inference_coalesces_to_newest_full_resolution_frame(self):
        state = CameraState()
        for value in (10, 20, 30):
            state.update_frame(
                "camera_link_optical",
                value,
                0,
                np.full((108, 192, 3), value, dtype=np.uint8),
                "bgr8",
            )
        stop = threading.Event()
        overlay = worker_overlay([], image={"width": 192, "height": 108})
        observed = {}

        def analyze_native(frame, *, jpeg_quality):
            observed["shape"] = frame.shape
            observed["pixel"] = int(frame[0, 0, 0])
            observed["jpeg_quality"] = jpeg_quality
            return overlay

        with patch("scripts.camera_ws_bridge.analyze_camera_image", side_effect=analyze_native):
            thread = threading.Thread(
                target=vision_inference_thread,
                args=(state, stop),
                kwargs={
                    "target_hz": 5.0,
                    "inference_width": 0,
                    "inference_jpeg_quality": 86,
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

        self.assertEqual(observed["shape"], (108, 192, 3))
        self.assertEqual(observed["pixel"], 30)
        self.assertEqual(observed["jpeg_quality"], 86)
        snap = state.snapshot(include_frame=False)
        self.assertEqual(snap["vision_dropped_frame_count"], 2)
        policy = snap["vision_input_policy"]
        self.assertEqual(policy["mode"], "source_native")
        self.assertEqual(policy["inference_width"], 192)
        self.assertTrue(policy["preview_decoupled"])


if __name__ == "__main__":
    unittest.main()
