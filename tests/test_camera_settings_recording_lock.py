"""A camera frame-size change is refused while the recorder may be recording camera frames.

The camera export keeps one frame size per recording and rejects a size change
inside it; an FPS-only change keeps the size and stays allowed.
"""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard_server.telemetry import TelemetryProvider  # noqa: E402


def provider(tmp, recording, settings=None):
    p = TelemetryProvider.__new__(TelemetryProvider)
    p.camera_settings_file = Path(tmp) / "camera_settings.json"
    p.camera_driver_status_file = Path(tmp) / "camera_driver_status.json"
    p.camera_driver_status_file.write_text(json.dumps({"updated_at": time.time()}), encoding="utf-8")
    if settings is not None:
        p.camera_settings_file.write_text(json.dumps(settings), encoding="utf-8")
    p.recording_status = lambda: dict(recording)
    return p


CURRENT = {"resolution": "1920x1080", "width": 1920, "height": 1080, "fps": 30, "fourcc": "MJPG"}


class CameraSizeLockTests(unittest.TestCase):
    def test_size_change_refused_while_recording_camera(self):
        for kind in ("camera", "both"):
            with tempfile.TemporaryDirectory() as tmp:
                p = provider(tmp, {"ok": True, "active": True, "kind": kind, "state": "recording",
                                   "recording_id": "20261013T000000Z_" + kind}, CURRENT)
                before = p.camera_settings_file.read_bytes()
                result = p.set_camera_settings({"resolution": "1280x720", "fps": 60})
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"], "camera_resolution_locked_while_recording")
                self.assertEqual(result["recording_id"], "20261013T000000Z_" + kind)
                self.assertEqual(p.camera_settings_file.read_bytes(), before)

    def test_finalizing_and_unknown_recorder_refuse_size_change(self):
        cases = [({"ok": True, "active": True, "kind": None, "state": "finalizing"},
                  "camera_resolution_locked_while_recording"),
                 ({"ok": False, "active": None, "state": "not_connected"},
                  "camera_resolution_change_needs_idle_recorder")]
        for recording, error in cases:
            with tempfile.TemporaryDirectory() as tmp:
                p = provider(tmp, recording, CURRENT)
                result = p.set_camera_settings({"resolution": "1280x720", "fps": 60})
                self.assertEqual(result["error"], error)

    def test_same_size_idle_and_lidar_only_are_allowed(self):
        allowed = [
            ({"ok": True, "active": True, "kind": "both", "state": "recording"}, {"resolution": "1920x1080", "fps": 30}),
            ({"ok": True, "active": False, "state": "idle"}, {"resolution": "1280x720", "fps": 60}),
            ({"ok": True, "active": True, "kind": "lidar", "state": "recording"}, {"resolution": "1280x720", "fps": 60}),
        ]
        for recording, payload in allowed:
            with tempfile.TemporaryDirectory() as tmp:
                p = provider(tmp, recording, CURRENT)
                result = p.set_camera_settings(payload)
                self.assertTrue(result["ok"], (recording, result))
                saved = json.loads(p.camera_settings_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["resolution"], payload["resolution"])

    def test_unsupported_mode_is_still_rejected_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = provider(tmp, {"ok": True, "active": True, "kind": "both"}, CURRENT)
            self.assertEqual(p.set_camera_settings({"resolution": "123x45"})["error"], "unsupported_camera_resolution")


if __name__ == "__main__":
    unittest.main()
