"""실제 Chrome으로 도는 시험. 흠을 일부러 넣은 화면과 흠이 없는 화면을 재서 측정 코드가 맞는지 본다.

브라우저를 띄우므로 기본으로는 건너뛴다. 돌리려면 환경 변수를 준다.

    UI_CHECK_BROWSER_TESTS=1 python -B -m unittest tests.test_browser_probe
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import run_checks  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ENABLED = os.environ.get("UI_CHECK_BROWSER_TESTS") == "1"


def measure(fixture: str, folder: str) -> dict:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = run_checks.main([
            "--static", str(FIXTURES / fixture), "--out", folder, "--dataset", "empty", "--route", "overview",
            "--viewport", "390x844", "--viewport", "1440x900", "--no-screenshots", "--settle-ms", "600", "--retries", "0",
        ])
    if code != 0:
        raise AssertionError(f"run_checks ended with {code}: {output.getvalue()[-800:]}")
    run = json.loads((Path(folder) / "ui_check.json").read_text(encoding="utf-8"))
    return {page["viewport"]["name"]: page for page in run["pages"]}


@unittest.skipUnless(ENABLED, "브라우저 시험은 UI_CHECK_BROWSER_TESTS=1일 때만 돈다")
class DefectFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.pages = measure("defects", cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_phone_width_metrics(self):
        page = self.pages["390x844"]
        metrics, details = page["metrics"], page["details"]
        self.assertEqual(metrics["horizontal_overflow_px"], 1008 - 390)
        self.assertGreaterEqual(metrics["outside_viewport_elements"], 1)
        self.assertTrue(any(row["sel"] == "div#wide" for row in details["outside_viewport"]))
        self.assertEqual(metrics["overlaps"], 1)
        self.assertEqual({details["overlaps"][0]["a"], details["overlaps"][0]["b"]}, {"button#overlapA", "button#overlapB"})
        self.assertEqual([row["sel"] for row in details["clipped_texts"]], ["div#clipBox"])
        self.assertEqual([row["sel"] for row in details["ellipsis_truncations"]], ["div#ellipsisBox"])
        self.assertEqual([row["sel"] for row in details["overflowing_elements"]], ["div#spillBox"])
        self.assertEqual([row["sel"] for row in details["small_targets"]], ["button#small"])
        self.assertEqual(metrics["small_inline_links"], 1)
        self.assertEqual(metrics["canvases"], 3)
        self.assertEqual([row["sel"] for row in details["canvas_mismatches"]], ["canvas#staleFixed"])
        stale = [row["sel"] for row in details["canvas_resize_probe"]["rows"] if row["stale"]]
        self.assertEqual(stale, ["canvas#drawnOnce"])
        self.assertEqual(metrics["broken_images"], 3)
        self.assertEqual(metrics["page_errors"], 1)
        self.assertGreaterEqual(metrics["console_errors"], 2)
        self.assertEqual(metrics["http_errors"], 1)
        self.assertEqual(metrics["blocked_external_requests"], 1)
        self.assertEqual(metrics["probe_problems"], 0)

    def test_stop_far_below_first_screen(self):
        metrics = self.pages["390x844"]["metrics"]
        self.assertTrue(metrics["stop_visible"])
        self.assertGreater(metrics["stop_top_px"], 2000)
        self.assertFalse(metrics["stop_in_first_viewport"])
        self.assertFalse(metrics["stop_always_in_viewport"])
        self.assertGreater(metrics["screens"], 2)

    def test_desktop_width_has_no_horizontal_overflow(self):
        metrics = self.pages["1440x900"]["metrics"]
        self.assertEqual(metrics["horizontal_overflow_px"], 0)
        self.assertEqual(metrics["overlaps"], 1)
        self.assertEqual(metrics["clipped_texts"], 1)


@unittest.skipUnless(ENABLED, "브라우저 시험은 UI_CHECK_BROWSER_TESTS=1일 때만 돈다")
class CleanFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.pages = measure("clean", cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_no_findings_at_both_widths(self):
        for name, page in self.pages.items():
            metrics = page["metrics"]
            for key in ("horizontal_overflow_px", "outside_viewport_elements", "overflowing_elements", "overflowing_elements_vertical", "clipped_texts",
                        "overlaps", "small_targets", "canvas_mismatches", "canvas_stale_after_resize", "broken_images", "page_errors",
                        "console_errors", "failed_requests", "http_errors", "blocked_external_requests", "probe_problems"):
                self.assertEqual(metrics[key], 0, f"{name} {key}: {page['details'].get(key)}")

    def test_pinned_stop_is_always_reachable(self):
        for name, page in self.pages.items():
            metrics = page["metrics"]
            self.assertTrue(metrics["stop_in_first_viewport"], name)
            self.assertTrue(metrics["stop_always_in_viewport"], name)
            self.assertEqual(page["details"]["stop"]["layer"], "fixed")
            self.assertTrue(page["details"]["stop"]["hit_test"])

    def test_bar_over_scrolling_content_is_not_an_overlap(self):
        metrics = self.pages["390x844"]["metrics"]
        self.assertEqual(metrics["overlaps"], 0)
        self.assertGreaterEqual(metrics["overlaps_pinned_vs_flow"], 1)


if __name__ == "__main__":
    unittest.main()
