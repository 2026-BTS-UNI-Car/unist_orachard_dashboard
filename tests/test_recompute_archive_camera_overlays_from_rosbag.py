from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from scripts.recompute_archive_camera_overlays_from_rosbag import (
    backup_timeline,
    image_message_to_bgr,
    source_policy,
    stamp_ns,
)


class ArchiveRosbagCameraRecomputeTest(unittest.TestCase):
    def test_stamp_and_source_policy_are_source_native(self) -> None:
        self.assertEqual(stamp_ns({"sec": 12, "nanosec": 345}), 12_000_000_345)
        policy = source_policy(1920, 1080, 86)
        self.assertEqual(policy["mode"], "source_native")
        self.assertEqual(policy["requested_width"], 0)
        self.assertEqual(policy["source_width"], 1920)
        self.assertEqual(policy["inference_width"], 1920)
        self.assertEqual(policy["jpeg_quality"], 86)

    def test_ros_bgr8_step_padding_is_not_interpreted_as_pixels(self) -> None:
        message = SimpleNamespace(
            height=2,
            width=2,
            step=8,
            encoding="bgr8",
            data=bytes([1, 2, 3, 4, 5, 6, 99, 98, 7, 8, 9, 10, 11, 12, 97, 96]),
        )
        image = image_message_to_bgr(message, cv2, np)
        self.assertEqual(image.shape, (2, 2, 3))
        self.assertEqual(image.tolist(), [[[1, 2, 3], [4, 5, 6]], [[7, 8, 9], [10, 11, 12]]])

    def test_backup_is_created_once_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            timeline = Path(tmp) / "timeline.json"
            timeline.write_text("original", encoding="utf-8")
            backup = backup_timeline(timeline, ".bak")
            timeline.write_text("changed", encoding="utf-8")
            self.assertEqual(backup_timeline(timeline, ".bak"), backup)
            self.assertEqual(backup.read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
