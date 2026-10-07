"""Device-free cadence and driver checks; no claimed hardware FPS result."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from scripts.camera_cadence import CameraCadence
from scripts.camera_preview_worker import LatestCameraWorker


def load_driver():
    # The test needs the production tick/publish methods, not a ROS executor,
    # device, daemon or recorder. ROS message shapes are sufficient here.
    modules = {}
    for name in ("fcntl", "rclpy", "rclpy.node", "rclpy.qos", "sensor_msgs", "sensor_msgs.msg"):
        modules[name] = ModuleType(name)
    modules["rclpy.node"].Node = type("Node", (), {})
    for name in ("DurabilityPolicy", "HistoryPolicy", "QoSProfile", "ReliabilityPolicy"):
        setattr(modules["rclpy.qos"], name, type(name, (), {}))

    class Message:
        def __init__(self):
            self.header = SimpleNamespace(stamp=None, frame_id=None)

    for name in ("CameraInfo", "CompressedImage", "Image"):
        setattr(modules["sensor_msgs.msg"], name, Message)
    source = Path(__file__).resolve().parents[1] / "scripts/camera_ros2_driver.py"
    spec = importlib.util.spec_from_file_location("camera_cadence_driver_under_test", source)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


DRIVER = load_driver()


def publication_node():
    node = DRIVER.UsbCameraDriver.__new__(DRIVER.UsbCameraDriver)
    node.args = SimpleNamespace(rotate_180=True, compressed_preview_width=1280,
                                compressed_jpeg_quality=70, raw_publish_fps=5, fov_deg=70)
    node.frame_id = "camera_link_optical"
    node.recording_pub = SimpleNamespace(get_subscription_count=lambda: 1, publish=Mock())
    node.native_encoder = SimpleNamespace(submit=Mock())
    for name in ("compressed_pub", "preview_info_pub", "image_pub", "info_pub"):
        setattr(node, name, SimpleNamespace(publish=Mock()))
    node.get_clock = lambda: SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: "ros_stamp"))
    node.raw_frame_count = node.compressed_frame_count = node.frame_count = 0
    node.raw_submit_count = 0
    node.last_raw_publish_at = node.last_raw_submitted_at = 0
    node.preview_worker = LatestCameraWorker(node.process_preview_frame)
    return node


class CameraCadenceTests(unittest.TestCase):
    def driver(self, clock, read):
        node = DRIVER.UsbCameraDriver.__new__(DRIVER.UsbCameraDriver)
        node.capture_cadence = CameraCadence(30)
        node.request_fps = 30
        node.cap = SimpleNamespace(read=read)
        node.apply_settings_file = lambda: False
        node.write_status = Mock()
        node.publish_frame = Mock()
        node.close_camera = Mock()
        node.fail_count = 0
        node.last_read_ms = None
        node.last_error = None
        return node

    def test_blocking_capture_period_is_not_waited_again_after_completion(self):
        clock = SimpleNamespace(now=0.0)

        def read():
            clock.now += 1 / 30  # A genuine blocking capture interval.
            return True, object()

        node = self.driver(clock, read)
        # Model preview work, and a 60 Hz timer that cannot run concurrently.
        node.publish_frame.side_effect = lambda frame: setattr(clock, "now", clock.now + .003)
        next_tick = 0.0
        with patch.object(DRIVER.time, "monotonic", side_effect=lambda: clock.now):
            while clock.now < 2:
                clock.now = max(clock.now, next_tick)
                next_tick += 1 / 60
                node.tick()
        self.assertGreaterEqual(node.publish_frame.call_count, 54)
        self.assertLessEqual(node.publish_frame.call_count, 60)
        self.assertAlmostEqual(node.last_read_ms, 33.33, places=2)
        self.assertGreater(node.capture_cadence.status()["observed_capture_hz"], 27)

    def test_late_work_has_one_acquisition_and_no_catch_up_queue(self):
        cadence = CameraCadence(30)
        self.assertTrue(cadence.claim(100))
        self.assertTrue(cadence.claim(101))
        self.assertFalse(cadence.claim(101))
        self.assertFalse(cadence.claim(101.01))
        self.assertTrue(cadence.claim(101 + 1 / 30))
        self.assertEqual(cadence.attempts, 3)
        self.assertEqual(cadence.skipped_slots, 29)

    def test_wall_clock_jump_does_not_change_acquisition_or_success_rate(self):
        clock = SimpleNamespace(now=10.0)
        node = self.driver(clock, lambda: (True, object()))
        with patch.object(DRIVER.time, "monotonic", side_effect=lambda: clock.now), \
                patch.object(DRIVER.time, "time", side_effect=AssertionError("wall_clock_used_for_cadence")):
            node.tick()
            clock.now += .01
            node.tick()
            clock.now += .03
            node.tick()
        self.assertEqual(node.publish_frame.call_count, 2)
        self.assertEqual(node.capture_cadence.status()["observed_capture_hz"], 25)

    def test_failed_reads_use_slots_without_inventing_successful_frames(self):
        clock = SimpleNamespace(now=1.0)
        node = self.driver(clock, lambda: (False, None))
        with patch.object(DRIVER.time, "monotonic", side_effect=lambda: clock.now):
            for _ in range(5):
                node.tick()
                clock.now += 1 / 30
        self.assertEqual(node.fail_count, 5)
        self.assertEqual(node.capture_cadence.status()["successful_reads"], 0)
        self.assertIsNone(node.capture_cadence.status()["observed_capture_hz"])
        node.close_camera.assert_called_once()
        node.publish_frame.assert_not_called()

    def test_invalid_clock_and_rate_are_rejected(self):
        for fps in (0, -.1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                CameraCadence(fps)
            self.assertIsNone(DRIVER.normalize_mode(1920, 1080, fps, "MJPG"))
        with self.assertRaises(ValueError):
            CameraCadence(30).claim(float("nan"))

    def test_window_bounded_and_new_capture_session_starts_immediately(self):
        cadence = CameraCadence(30)
        for i in range(200):
            now = 100 + i / 30
            self.assertTrue(cadence.claim(now))
            cadence.succeeded(now)
        self.assertEqual(len(cadence.completed_at), 120)
        self.assertEqual(cadence.status()["observed_capture_hz"], 30)
        replacement = CameraCadence(60)
        self.assertTrue(replacement.claim(999))
        self.assertFalse(replacement.claim(999 + .01))

    def test_settings_rate_change_restarts_cadence_before_reopening_camera(self):
        clock = SimpleNamespace(now=10.0)
        node = self.driver(clock, lambda: (True, object()))
        with tempfile.TemporaryDirectory() as directory:
            node.settings_file = Path(directory) / "camera_settings.json"
            node.settings_file.write_text(json.dumps({"width": 1920, "height": 1080, "fps": 60}), encoding="utf-8")
            node.request_width, node.request_height, node.request_fourcc = 1920, 1080, "MJPG"
            node.settings_mtime_ns = None
            node.next_settings_check_at = 0
            node.last_read_ms, node.last_publish_work_ms = 40, 10
            self.assertTrue(DRIVER.UsbCameraDriver.apply_settings_file(node, force=True))
            self.assertEqual(node.capture_cadence.status()["requested_fps"], 60)
            self.assertTrue(node.capture_cadence.claim(clock.now))
            self.assertIsNone(node.last_read_ms)
            self.assertIsNone(node.last_publish_work_ms)
            node.close_camera.assert_called_once()

    def test_driver_keeps_native_dimensions_rotation_and_preview_quality(self):
        node = publication_node()
        self.addCleanup(node.preview_worker.close)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[0, 0] = [1, 2, 3]
        original = frame.copy()
        with patch.object(DRIVER.cv2, "imencode", wraps=cv2.imencode) as encode:
            node.publish_frame(frame)
            self.assertTrue(node.preview_worker.wait_idle())
        self.assertIsNone(node.preview_worker.status()['error'])
        submitted, (stamp, generation) = node.native_encoder.submit.call_args.args
        self.assertEqual(submitted.shape, (1080, 1920, 3))
        self.assertEqual(submitted[-1, -1].tolist(), [1, 2, 3])
        self.assertFalse(submitted.flags.writeable)
        np.testing.assert_array_equal(frame, original)
        self.assertEqual(stamp, "ros_stamp")
        self.assertEqual(encode.call_args.args[1].shape, (720, 1280, 3))
        self.assertEqual(encode.call_args.args[2], [cv2.IMWRITE_JPEG_QUALITY, 70])
        preview = node.compressed_pub.publish.call_args.args[0]
        native_raw = node.image_pub.publish.call_args.args[0]
        self.assertEqual((native_raw.width, native_raw.height), (1920, 1080))
        self.assertIn("width=1280; height=720", preview.format)
        self.assertEqual(node.frame_count, 1)


if __name__ == "__main__":
    unittest.main()
