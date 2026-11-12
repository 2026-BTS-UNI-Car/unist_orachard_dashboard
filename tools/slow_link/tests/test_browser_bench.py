"""One short real run of the bench: stack, link emulator, Chrome, analysis. Off unless asked for.

  PowerShell:  $env:SLOW_LINK_BROWSER_TESTS = "1"; $env:SLOW_LINK_INPUTS = "<inputs.json>"; $env:SLOW_LINK_PYTHON = "<python with numpy, websockets, opencv, psutil>"
               python -B -m unittest tests.test_browser_bench
  bash:        SLOW_LINK_BROWSER_TESTS=1 SLOW_LINK_INPUTS=<inputs.json> SLOW_LINK_PYTHON=<python> python -B -m unittest tests.test_browser_bench

It measures the committed HEAD (git archive), writes into a temporary folder and removes it afterwards.
About 90 seconds.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

ENABLED = os.environ.get("SLOW_LINK_BROWSER_TESTS") == "1"


@unittest.skipUnless(ENABLED, "set SLOW_LINK_BROWSER_TESTS=1 (and SLOW_LINK_INPUTS, SLOW_LINK_PYTHON) to run the browser test")
class BrowserBenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="slow-link-test-")
        root = Path(cls.temp.name)
        python = os.environ.get("SLOW_LINK_PYTHON", sys.executable)
        command = [python, "-B", str(TOOL_DIR / "run_bench.py"), "--scenario", "quick", "--profile", "normal", "--source", "HEAD",
                   "--inputs", os.environ["SLOW_LINK_INPUTS"], "--out-root", str(root / "results"), "--work", str(root / "work"),
                   "--live-s", "30", "--label", "selftest", "--no-camera-original"]
        cls.completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        folders = sorted((root / "results").glob("*_quick_normal_*"))
        cls.folder = folders[-1] if folders else None
        cls.summary = json.loads((cls.folder / "SUMMARY.json").read_text(encoding="utf-8")) if cls.folder else {}
        cls.run_info = json.loads((cls.folder / "run.json").read_text(encoding="utf-8")) if cls.folder else {}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_run_finished_and_cleaned_up(self):
        self.assertEqual(self.completed.returncode, 0, self.completed.stdout[-2000:] + self.completed.stderr[-2000:])
        self.assertIsNone(self.run_info.get("failed"))
        self.assertIsNone(self.summary.get("error"))
        self.assertEqual(self.run_info["cleanup"]["ports_still_open"], [])
        self.assertEqual(self.run_info["cleanup"]["bench_browsers_killed"], [])
        self.assertEqual(len(self.run_info["source"]["commit"]), 40)
        self.assertEqual(self.summary["run"]["source"]["commit"], self.run_info["source"]["commit"])

    def test_page_loaded_and_streams_arrived(self):
        cold = self.summary["load"]["cold"]
        self.assertFalse(cold["timed_out"])
        self.assertIsNotNone(cold["usable_ms"])
        self.assertGreater(cold["total"]["requests"], 5)
        client = self.summary["live"]["clients"][0]
        self.assertGreater(client["render_delay_ms"]["camera"]["n"], 0)
        self.assertGreater(client["render_delay_ms"]["lidar"]["n"], 0)
        self.assertGreater(client["ws"]["/ws/imu"]["msgs_per_s"], 0)
        self.assertGreater(client["ws"]["/ws/gps"]["msgs_per_s"], 0)
        # The link emulator was in the path and limited it to the profile rate (2 Mbit/s, 5 % allowance).
        self.assertLessEqual(self.summary["live"]["link"]["down"]["kbit_per_s"], 2000 * 1.05)

    def test_drive_and_stop_reached_the_recording_endpoint(self):
        control = self.summary["live"]["control"]
        self.assertGreater(control["per_op"]["command"]["n"], 5)
        self.assertEqual(control["per_op"]["command"]["not_seen_by_endpoint"], 0)
        self.assertGreaterEqual(control["stop"]["clicks"], 1)
        self.assertEqual(control["stop"]["not_seen_by_endpoint"], 0)
        self.assertEqual(control["limits"]["core"], {"input_ttl_s": 0.75, "ticket_ttl_s": 1.5, "owner_ttl_s": 30.0})
        events = [json.loads(line) for line in (self.folder / "control_events.jsonl").read_text(encoding="utf-8").splitlines()]
        motor = [row for row in events if row["kind"] == "motor"]
        self.assertTrue(any(row["op"] == "drive" for row in motor))
        self.assertEqual(motor[-1]["op"], "hard_stop")


if __name__ == "__main__":
    unittest.main()
