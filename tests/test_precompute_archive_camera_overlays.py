from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.precompute_archive_camera_overlays import process_timeline


class PrecomputeArchiveCameraOverlayProvenanceTest(unittest.TestCase):
    def test_legacy_preview_is_failed_closed_without_worker_reinference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recording = Path(directory) / "legacy"
            recording.mkdir()
            timeline_path = recording / "timeline.json"
            timeline_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "camera": {
                            "frames": [
                                {
                                    "t_s": 1.0,
                                    "width": 640,
                                    "height": 360,
                                    "source_width": 1920,
                                    "source_height": 1080,
                                    "data": "legacy-640-preview",
                                    "overlay": {
                                        "ok": True,
                                        "algorithm": "local_orchard_vision_ai_worker_v4",
                                        "revision": "orchard_trunk_yolo11_trt_clip_ood_v1",
                                        "image": {"width": 640, "height": 360},
                                        "detections": [],
                                    },
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(force=False, dry_run=False, checkpoint_every=0)

            summary, remaining = process_timeline(recording, args, None)

            self.assertIsNone(remaining)
            self.assertEqual(summary["current_ok"], 0)
            self.assertEqual(summary["updated"], 0)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["provenance_rejected"], 1)
            written = json.loads(timeline_path.read_text(encoding="utf-8"))
            overlay = written["camera"]["frames"][0]["overlay"]
            self.assertFalse(overlay["ok"])
            self.assertEqual(overlay["method"], "source_native_provenance_required")
            required = written["precompute"]["camera_overlay"]["required_input_policy"]
            self.assertEqual(required["mode"], "source_native")
            self.assertEqual(required["requested_width"], 0)
            self.assertEqual(required["jpeg_quality"], 86)


if __name__ == "__main__":
    unittest.main()
