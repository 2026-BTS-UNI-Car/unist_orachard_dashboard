"""The field link log report (link_log_report.py) on small made-up logs."""
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO = TOOL_DIR.parents[1]
sys.path.insert(0, str(TOOL_DIR))

import link_log_report  # noqa: E402


def ping(t, rtt, ok=True, grade="normal", recording=None):
    return dict(t=t, kind="ping", ok=ok, rtt_ms=rtt if ok else None, grade=grade, recording_id=recording, profile="auto")


class LinkLogReportTest(unittest.TestCase):
    def test_the_boundaries_are_the_pages_own(self):
        source = (REPO / "static" / "app" / "04-link-grade.js").read_text(encoding="utf-8")
        match = re.search(r"thresholdsMs:\s*\{\s*good:\s*(\d+),\s*normal:\s*(\d+),\s*slow:\s*(\d+)\s*\}", source)
        self.assertIsNotNone(match)
        self.assertEqual(link_log_report.THRESHOLDS_MS, dict(good=int(match[1]), normal=int(match[2]), slow=int(match[3])))
        self.assertIn(f"windowMs: {link_log_report.WINDOW_MS},", source)

    def test_window_minima_follow_the_smallest_answer_of_4_s(self):
        rows = [ping(1000 + 500 * i, 200 if i % 4 else 60) for i in range(40)]   # every fourth answer is quick
        minima = link_log_report.window_minima(rows)
        self.assertTrue(minima)
        self.assertEqual(set(minima), {60})

    def test_offline_stretches_and_grade_share(self):
        rows = [ping(0, 50), ping(500, None, ok=False, grade="normal"), ping(1000, None, ok=False, grade="offline"),
                ping(1500, None, ok=False, grade="offline"), ping(4000, 80), ping(4500, 70)]
        self.assertEqual(link_log_report.offline_stretches(rows), [3.5])
        self.assertEqual(link_log_report.offline_stretches(rows[:2] + rows[4:]), [])   # one missed ping is not offline
        share = link_log_report.grade_share(rows)
        self.assertAlmostEqual(sum(share.values()), 100.0, delta=0.2)
        self.assertGreater(share["offline"], share["normal"])

    def test_report_and_command_line(self):
        rows = [ping(1000 * i, 30 + (i % 5) * 10, recording="r1" if i < 30 else None) for i in range(60)]
        result = link_log_report.report(rows)
        self.assertEqual(result["samples"], 60)
        self.assertEqual(result["ping_answered_pct"], 100.0)
        self.assertEqual(result["rtt_ms"][50], 50)
        self.assertEqual(result["window_min_below_pct"]["normal"], 100.0)
        self.assertEqual(result["window_min_below_pct"]["good"], 0.0)
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "20261113.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n", encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(link_log_report.main([folder, "--by-recording", "--json"]), 0)
            self.assertEqual(sorted(json.loads(out.getvalue())), ["none", "r1"])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(link_log_report.main([folder]), 0)
            empty = Path(folder) / "empty"
            empty.mkdir()
            self.assertEqual(link_log_report.main([str(empty)]), 2)


if __name__ == "__main__":
    unittest.main()
