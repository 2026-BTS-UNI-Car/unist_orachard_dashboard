from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

import numpy as np

try:
    from scripts.data_recorder_service import RecordingState
except ModuleNotFoundError as exc:
    if exc.name not in {"rclpy", "sensor_msgs"}:
        raise
    RecordingState = None


@unittest.skipUnless(RecordingState is not None, "ROS2 Python runtime is required")
class DataRecorderCameraPipelineTests(unittest.TestCase):
    def make_state(self, tmp: str, *, wait_s: float = 0.2) -> RecordingState:
        args = SimpleNamespace(
            archive_dir=str(Path(tmp) / "recordings"),
            status_file=str(Path(tmp) / "status.json"),
            camera_preview_width=160,
            camera_jpeg_quality=68,
            camera_vision_sync_max_delta_s=0.12,
            camera_vision_wait_timeout_s=wait_s,
            camera_archive_flush_timeout_s=0.5,
            timeline_hz=2.0,
            timeline_max_frames=10,
            imu_raw_topic="/imu/data_raw",
            imu_corrected_topic="/imu/data",
            imu_mag_topic="/imu/mag",
            imu_driver_status_topic="/orchard/imu/driver_status",
            imu_calibration_topic="/orchard/imu/calibration",
            imu_timeline_hz=10.0,
            imu_timeline_max_frames=100,
            imu_calibration_window_s=0.4,
            imu_calibration_min_samples=5,
            imu_stationary_max_gyro_rad_s=0.08,
            imu_stationary_gravity_tolerance_m_s2=2.0,
            imu_stationary_max_accel_std_m_s2=0.35,
            cmd_topic="/cmd_vel",
            rover_status_topic="/rover/status",
            odom_topic="/odom",
            tf_topic="/tf",
            tf_static_topic="/tf_static",
            vision_topic="/orchard/vision/tree_bearings",
            scan_topic="/scan",
            camera_topic="/camera/image_raw",
            camera_info_topic="/camera/camera_info",
        )
        state = RecordingState(args)
        with state.lock:
            state.active = {
                "kind": "camera",
                "started_monotonic": time.time() - 1.0,
                "id": "test_camera",
            }
        return state

    def image_message(self, value: int = 37) -> SimpleNamespace:
        image = np.full((180, 320, 3), value, dtype=np.uint8)
        return SimpleNamespace(
            height=180,
            width=320,
            encoding="bgr8",
            data=image.tobytes(),
            header=SimpleNamespace(
                frame_id="camera_link_optical",
                stamp=SimpleNamespace(sec=123, nanosec=456),
            ),
        )

    def shared_vision_message(self) -> SimpleNamespace:
        payload = {
            "schema": "orchard.camera_bearings.v1",
            "ok": True,
            "algorithm": "local_orchard_vision_ai_worker_v4",
            "revision": "orchard_trunk_yolo11_trt_clip_ood_v1",
            "stamp": {"sec": 123, "nanosec": 456},
            "stamp_ns": 123_000_000_456,
            "inference_input_policy": {
                "revision": "source_native_preview_decoupled_jpeg_v1",
                "mode": "source_native",
                "requested_width": 0,
                "source_width": 320,
                "source_height": 180,
                "inference_width": 320,
                "inference_height": 180,
                "jpeg_quality": 86,
                "preview_decoupled": True,
                "no_upscale": True,
            },
            "detections": [
                {
                    "class_name": "orchard_tree_trunk",
                    "label": "orchard_tree_trunk",
                    "bbox": [120, 20, 30, 100],
                    "confidence": 0.9,
                    "tree_probability": 0.9,
                    "ood_score": 0.1,
                    "stable": True,
                    "stable_hits": 3,
                    "track_id": "cam-tree-e0-1",
                }
            ],
        }
        return SimpleNamespace(data=json.dumps(payload))

    def imu_message(self) -> SimpleNamespace:
        return SimpleNamespace(
            header=SimpleNamespace(frame_id="imu_link", stamp=SimpleNamespace(sec=123, nanosec=456)),
            orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
            angular_velocity=SimpleNamespace(x=0.01, y=-0.02, z=0.005),
            linear_acceleration=SimpleNamespace(x=0.0, y=0.0, z=9.80665),
            orientation_covariance=[-1.0] + [0.0] * 8,
            angular_velocity_covariance=[-1.0] + [0.0] * 8,
            linear_acceleration_covariance=[-1.0] + [0.0] * 8,
        )

    def test_ros_callback_only_coalesces_latest_frame_without_wait_or_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self.make_state(tmp, wait_s=1.5)
            started = time.perf_counter()
            state.observe_camera(self.image_message(11))
            with state.lock:
                state.next_camera_sample_at = 0.0
            state.observe_camera(self.image_message(22))
            callback_duration = time.perf_counter() - started

            self.assertLess(callback_duration, 0.1)
            self.assertEqual(state.camera_enqueued_count, 2)
            self.assertEqual(state.camera_coalesced_frame_count, 1)
            self.assertEqual(int(state.camera_pending_sample["data"][0]), 22)
            self.assertEqual(len(state.camera_frames), 0)

    def test_shared_native_inference_is_stamp_matched_and_scaled_to_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self.make_state(tmp)
            state.observe_vision(self.shared_vision_message())
            shutdown = threading.Event()
            worker = threading.Thread(target=state.camera_archive_worker, args=(shutdown,))
            worker.start()
            try:
                state.observe_camera(self.image_message())
                self.assertTrue(state.camera_idle_event.wait(timeout=1.0))
            finally:
                shutdown.set()
                state.camera_pending_event.set()
                worker.join(timeout=1.0)

            self.assertFalse(worker.is_alive())
            self.assertEqual(len(state.camera_frames), 1)
            archived = state.camera_frames[0]
            self.assertEqual((archived["width"], archived["height"]), (160, 90))
            self.assertEqual((archived["source_width"], archived["source_height"]), (320, 180))
            self.assertEqual(archived["inference_input_policy"]["inference_width"], 320)
            self.assertEqual(archived["inference_input_policy"]["mode"], "source_native")
            self.assertEqual(archived["overlay"]["image"], {"width": 160, "height": 90})
            self.assertEqual(archived["overlay"]["detections"][0]["bbox"], [60, 10, 15, 50])
            self.assertTrue(archived["vision_sync"]["matched"])
            self.assertFalse(archived["vision_sync"]["independent_inference"])
            status = state.status()["camera_pipeline"]
            self.assertTrue(status["latest_only"])
            self.assertEqual(status["inference_owner"], "camera_ws_bridge")
            self.assertFalse(status["independent_inference"])
            self.assertEqual(status["coalescing"], "single_slot_archive_worker_and_dds_best_effort_keep_last_1")
            self.assertEqual(status["shared_vision_match_count"], 1)
            self.assertEqual(status["shared_vision_received_count"], 1)
            self.assertIsNotNone(status["shared_vision_received_age_s"])
            self.assertLess(status["shared_vision_received_age_s"], 1.0)

    def test_bounded_or_incomplete_shared_inference_provenance_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self.make_state(tmp)
            message = self.shared_vision_message()
            payload = json.loads(message.data)
            payload["inference_input_policy"].update(
                {
                    "mode": "bounded_width",
                    "requested_width": 160,
                    "inference_width": 160,
                    "inference_height": 90,
                }
            )

            state.observe_vision(SimpleNamespace(data=json.dumps(payload)))

            self.assertEqual(state.shared_vision_payloads, [])
            self.assertEqual(state.shared_vision_received_count, 0)
            self.assertEqual(state.shared_vision_provenance_rejected_count, 1)
            status = state.status()["camera_pipeline"]
            self.assertEqual(status["shared_vision_provenance_rejected_count"], 1)

    def test_imu_is_included_by_default_and_start_reference_is_published(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self.make_state(tmp)
            with state.lock:
                state.active.update(
                    {
                        "kind": "both",
                        "include_imu": True,
                        "started_monotonic": time.time() - 1.0,
                        "started_steady": 10.0,
                        "stopping": False,
                    }
                )
                state.imu_calibrator.begin("test_camera", now_monotonic=10.0)
            corrected = None
            with patch("scripts.data_recorder_service.time.monotonic", side_effect=[10.0, 10.1, 10.2, 10.3, 10.4, 10.5]):
                for _ in range(6):
                    corrected = state.observe_imu(self.imu_message())
            self.assertIsNotNone(corrected)
            self.assertTrue(state.imu_calibrator.ready)
            self.assertGreater(len(state.imu_frames), 0)
            self.assertEqual(corrected["frame_id"], "imu_link_recording_start")
            self.assertAlmostEqual(corrected["angular_velocity"]["x"], 0.0, places=6)
            topics = state.topics_for("both")
            self.assertIn("/imu/data_raw", topics)
            self.assertIn("/imu/data", topics)
            self.assertIn("/imu/mag", topics)

    def test_imu_can_be_explicitly_disabled_but_default_contract_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self.make_state(tmp)
            self.assertNotIn("/imu/data_raw", state.topics_for("both", include_imu=False))
            self.assertIn("/imu/data_raw", state.topics_for("camera"))

    def test_dashboard_manual_imu_zero_works_without_active_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self.make_state(tmp)
            with state.lock:
                state.active = None
            state.observe_imu(self.imu_message())
            result = state.zero_imu()
            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "zeroed")
            self.assertFalse(result["recording_active"])
            corrected = state.observe_imu(self.imu_message())
            self.assertIsNotNone(corrected)
            self.assertAlmostEqual(corrected["euler_deg"]["roll"], 0.0, places=6)
            self.assertEqual(corrected["calibration_source"], "dashboard_manual_zero")

    def test_dashboard_manual_imu_zero_fails_closed_without_fresh_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self.make_state(tmp)
            result = state.zero_imu()
            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "imu_unavailable")


if __name__ == "__main__":
    unittest.main()
