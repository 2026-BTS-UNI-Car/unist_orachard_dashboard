"""compare.py, summarize.py와 run_checks.py의 인자 처리 시험. 브라우저는 띄우지 않는다."""
import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import compare  # noqa: E402
import run_checks  # noqa: E402
import summarize  # noqa: E402
import ui_metrics  # noqa: E402


def make_page(route, viewport, **overrides):
    metrics = {
        "horizontal_overflow_px": 0, "outside_viewport_elements": 0, "overflowing_elements": 0, "overflowing_elements_vertical": 0,
        "clipped_texts": 0, "ellipsis_truncations": 0, "overlaps": 0, "overlaps_pinned_vs_flow": 0, "interactive_total": 10,
        "interactive_in_first_viewport": 5, "small_targets": 0, "tiny_targets": 0, "small_inline_links": 0, "canvases": 1,
        "canvas_mismatches": 0, "canvas_resize_checked": 1, "canvas_stale_after_resize": 0, "stop_found": True, "stop_visible": True,
        "stop_top_px": 12, "stop_width_px": 120, "stop_height_px": 48, "stop_in_first_viewport": True, "stop_always_in_viewport": True,
        "page_height_px": 900, "screens": 1.0, "console_errors": 0, "page_errors": 0, "failed_requests": 0, "http_errors": 0,
        "blocked_external_requests": 0, "broken_images": 0, "probe_problems": 0, "style_gradient_elements": 0, "style_blur_elements": 0,
        "style_shadow_kinds": 1, "style_font_size_kinds": 5, "style_font_weight_kinds": 2, "style_text_colors": 4, "style_heavy_text_percent": 0.0,
    }
    metrics.update(overrides)
    return {"key": ui_metrics.page_key("stress", route, viewport), "dataset": "stress", "route": route, "route_slug": ui_metrics.route_slug(route),
            "viewport": {"name": viewport, "width": int(viewport.split("x")[0]), "height": int(viewport.split("x")[1]), "mobile": True}, "metrics": metrics}


def make_run(pages):
    return {"schema": ui_metrics.RUN_SCHEMA, "schema_version": ui_metrics.RUN_SCHEMA_VERSION, "pages": pages, "summary": ui_metrics.summarize(pages)}


class CompareCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        path = self.folder / name
        path.write_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        return path

    def run_compare(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = compare.main([str(argument) for argument in arguments])
        return code, output.getvalue()

    def rules(self, mode="fail"):
        return self.write("rules.json", {
            "schema": "ubts.ui_check.rules", "schema_version": 1,
            "fixed_rules": [
                {"id": "no-horizontal-overflow", "title_ko": "가로 넘침 없음", "metric": "horizontal_overflow_px", "op": "<=", "value": 0, "mode": mode},
                {"id": "no-overlap", "title_ko": "겹침 없음", "metric": "overlaps", "op": "<=", "value": 0, "mode": "fail"},
            ],
            "regression": {"missing_page_mode": "fail", "new_page_mode": "report", "load_error_mode": "fail",
                           "metrics": {"small_targets": {"better": "lower", "tolerance": 0, "mode": "fail", "title_ko": "44px 미만 누름 영역"}}},
        })

    def test_identical_run_passes(self):
        run = self.write("run.json", make_run([make_page("/", "390x844"), make_page("/rover-control", "390x844")]))
        code, text = self.run_compare("--run", run, "--baseline", run, "--rules", self.rules())
        self.assertEqual(code, 0, text)
        self.assertIn("판정: 통과", text)

    def test_fixed_rule_failure_names_page_viewport_and_rule(self):
        run = self.write("run.json", make_run([make_page("/", "390x844"), make_page("/rover-control", "390x844", horizontal_overflow_px=21)]))
        code, text = self.run_compare("--run", run, "--rules", self.rules())
        self.assertEqual(code, 1)
        for part in ("/rover-control", "390x844", "no-horizontal-overflow", "판정: 실패"):
            self.assertIn(part, text)

    def test_report_only_rule_does_not_fail_unless_strict(self):
        run = self.write("run.json", make_run([make_page("/rover-control", "390x844", horizontal_overflow_px=21)]))
        rules = self.rules("report")
        code, text = self.run_compare("--run", run, "--rules", rules)
        self.assertEqual(code, 0, text)
        self.assertIn("참고", text)
        self.assertEqual(self.run_compare("--run", run, "--rules", rules, "--strict")[0], 1)

    def test_worse_than_baseline_fails(self):
        baseline = self.write("baseline.json", make_run([make_page("/fan", "820x1180", small_targets=4)]))
        better = self.write("better.json", make_run([make_page("/fan", "820x1180", small_targets=2)]))
        worse = self.write("worse.json", make_run([make_page("/fan", "820x1180", small_targets=5)]))
        self.assertEqual(self.run_compare("--run", better, "--baseline", baseline, "--rules", self.rules())[0], 0)
        code, text = self.run_compare("--run", worse, "--baseline", baseline, "--rules", self.rules())
        self.assertEqual(code, 1)
        self.assertIn("기준선보다 나빠짐", text)
        self.assertIn("4 → 5", text)

    def test_missing_page_and_load_error_fail(self):
        baseline = self.write("baseline.json", make_run([make_page("/", "390x844"), make_page("/fan", "390x844")]))
        fewer = self.write("fewer.json", make_run([make_page("/", "390x844")]))
        self.assertEqual(self.run_compare("--run", fewer, "--baseline", baseline, "--rules", self.rules())[0], 1)
        broken_page = {**make_page("/fan", "390x844"), "metrics": None, "error": "TimeoutError"}
        broken = self.write("broken.json", make_run([make_page("/", "390x844"), broken_page]))
        code, text = self.run_compare("--run", broken, "--rules", self.rules())
        self.assertEqual(code, 1)
        self.assertIn("쪽을 열거나 재지 못함", text)

    def test_bad_files_exit_with_2(self):
        run = self.write("run.json", make_run([make_page("/", "390x844")]))
        other = self.write("other.json", {"schema": "something.else", "schema_version": 1, "pages": []})
        bad_rules = self.write("bad_rules.json", {"schema": "ubts.ui_check.rules", "schema_version": 1, "fixed_rules": [{"id": "x"}]})
        self.assertEqual(self.run_compare("--run", other, "--rules", self.rules())[0], 2)
        self.assertEqual(self.run_compare("--run", self.folder / "missing.json", "--rules", self.rules())[0], 2)
        self.assertEqual(self.run_compare("--run", run, "--rules", bad_rules)[0], 2)

    def test_result_json_is_written(self):
        run = self.write("run.json", make_run([make_page("/rover-control", "390x844", overlaps=1)]))
        target = self.folder / "verdict.json"
        self.assertEqual(self.run_compare("--run", run, "--rules", self.rules(), "--json", target)[0], 1)
        verdict = json.loads(target.read_text(encoding="utf-8"))
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["failures"][0]["rule_id"], "no-overlap")
        self.assertNotIn(b"\r\n", target.read_bytes())


class SummarizeTest(unittest.TestCase):
    def test_tables_contain_pages(self):
        run = make_run([make_page("/rover-control", "390x844", horizontal_overflow_px=21, stop_top_px=5134, stop_in_first_viewport=False), make_page("/", "1440x900", stop_visible=False)])
        overview = summarize.overview(run)
        self.assertIn("390x844", overview)
        pages = summarize.pages_table(run, datasets=["stress"], viewports=["390x844"])
        self.assertIn("5134px · 첫 화면 밖", pages)
        self.assertNotIn("1440x900", pages)
        self.assertIn("없음", summarize.pages_table(run, viewports=["1440x900"]))
        self.assertEqual(len(summarize.notable_pages(run)), 1)


class RunChecksArgumentTest(unittest.TestCase):
    def test_viewport_parsing(self):
        self.assertEqual(run_checks.parse_viewport("390x844"), {"name": "390x844", "width": 390, "height": 844, "mobile": True})
        self.assertTrue(run_checks.parse_viewport("1180x820")["mobile"])
        self.assertFalse(run_checks.parse_viewport("1440x900")["mobile"])
        self.assertFalse(run_checks.parse_viewport("820x1180:desktop")["mobile"])
        self.assertTrue(run_checks.parse_viewport("1440x900:mobile")["mobile"])
        for bad in ("390", "axb", "10x10", "390x844:tv"):
            with self.assertRaises(argparse.ArgumentTypeError):
                run_checks.parse_viewport(bad)

    def test_route_normalization(self):
        self.assertEqual(run_checks.normalize_route("/rover-control"), "/rover-control")
        self.assertEqual(run_checks.normalize_route("rover-control"), "/rover-control")
        self.assertEqual(run_checks.normalize_route("overview"), "/")
        self.assertEqual(run_checks.normalize_route("/"), "/")
        with self.assertRaises(ValueError):
            run_checks.normalize_route("C:/Program Files/Git/rover-control")

    def test_local_urls_lose_host_and_port(self):
        self.assertEqual(run_checks.local_url("http://127.0.0.1:51234/api/live/map?transport=tiles"), "/api/live/map?transport=tiles")
        self.assertEqual(run_checks.local_url("https://example.invalid/x"), "https://example.invalid/x")

    def test_defaults_cover_nine_routes_and_five_viewports(self):
        self.assertEqual(len(run_checks.DEFAULT_ROUTES), 8)   # the eight menu pages (UI-16)
        self.assertEqual(run_checks.DEFAULT_VIEWPORTS, ["390x844", "844x390", "820x1180", "1180x820", "1440x900"])
        self.assertIn("#roverStopButton", run_checks.DEFAULT_STOP_SELECTORS)
        # 앱 틀의 STOP 표시가 주행 패널의 STOP보다 먼저다.
        self.assertEqual(run_checks.DEFAULT_STOP_SELECTORS[0], "[data-ui-check~='stop']")

    def test_probe_source_is_a_function_expression(self):
        source = run_checks.load_probe_source()
        self.assertTrue(source.startswith("(config) =>"))
        self.assertTrue(source.endswith("}"))

    def test_output_folder_inside_static_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            static = Path(folder) / "static"
            static.mkdir()
            (static / "index.html").write_bytes(b"<!doctype html>")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                run_checks.parse_args(["--static", str(static), "--out", str(static / "out")])
            options = run_checks.parse_args(["--static", str(static), "--out", str(Path(folder) / "out"), "--route", "fan", "--dataset", "empty"])
            self.assertEqual((options.route, options.datasets), (["/fan"], ["empty"]))


if __name__ == "__main__":
    unittest.main()
