"""Accepted values and the compare command (no browser, no stack; made-up summaries and the accepted file in the repository)."""
import contextlib
import copy
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import compare  # noqa: E402
import run_bench  # noqa: E402

PROFILES = json.loads((TOOL_DIR / "profiles.json").read_text(encoding="utf-8"))["profiles"]
STREAMS = ("/ws/camera", "/ws/lidar", "/ws/imu", "/ws/gps")


def spread(p50, p95=None, top=None):
    p95 = p50 if p95 is None else p95
    return dict(n=20, p50=p50, p90=p95, p95=p95, max=p95 if top is None else top)


def after_stall(fresh_ms=600.0, backlog=1, camera_fresh_ms=9000.0, camera_not_fresh=0):
    def item(fresh, count, not_fresh=0):
        return dict(stalls=5, first_message_after_ms=spread(200.0), age_of_first_message_ms=spread(1200.0), worst_age_within_3s_ms=spread(1200.0),
                    backlog_messages=spread(count, count, count), fresh_after_ms=spread(fresh, fresh, fresh),
                    not_fresh_within_horizon=not_fresh, fresh_horizon_ms=15000.0)
    return {"/ws/lidar": item(fresh_ms, 1), "/ws/imu": item(fresh_ms - 100, backlog), "/ws/gps": item(fresh_ms - 50, 1),
            "/ws/camera": item(camera_fresh_ms, 2, camera_not_fresh)}


def make_summary(scenario="drive", profile="normal", slow=1.0, commit="a" * 40, label=None):
    """A summary with the parts the key numbers are read from. slow > 1 makes every delay longer and every rate lower."""
    stalled = bool(PROFILES.get(profile, {}).get("stall_ms"))
    client = dict(
        render_delay_ms=dict(
            camera=dict(spread(1250.0 * slow, 1300.0 * slow), rate_hz=round(2.0 / slow, 2), gap_ms=dict(p50=500, p95=520, max=600), frame_widths=[1920], mean_frame_B=112000),
            lidar=dict(spread(370.0 * slow, 420.0 * slow), rate_hz=round(2.1 / slow, 2), gap_ms=dict(p50=470, p95=500, max=640)),
            imu=dict(spread(310.0 * slow, 330.0 * slow), rate_hz=8.2, gap_ms=dict(p50=120, p95=130, max=300)),
            gps_text=dict(spread(460.0 * slow, 900.0 * slow), gap_ms=dict(p50=330, p95=490, max=700))),
        ws={path: dict(msgs_per_s=2.0, kB_per_s=5.0, mean_msg_B=650, opens_in_window=0, closes_in_window=0, close_codes=[]) for path in STREAMS},
        http_groups=dict(status_polling=dict(per_s=1.9, kB_per_s=2.86), live_map=dict(per_s=1.0, kB_per_s=1.42),
                         control=dict(per_s=2.0, kB_per_s=1.4), other=dict(per_s=0.0, kB_per_s=0.0)))
    control = dict(
        per_op=dict(command=dict(n=170, round_trip_ms=spread(382.0 * slow, 397.0 * slow, 404.0 * slow), pre_send_ms=spread(2.3, 3.0, 14.0), uplink_ms=spread(78.0)),
                    stop=dict(n=14, round_trip_ms=spread(370.0 * slow), pre_send_ms=spread(2.0, 2.8, 2.9), uplink_ms=spread(78.0)),
                    arm=dict(n=14, round_trip_ms=spread(400.0), pre_send_ms=spread(2.0, 3.0, 3.2), uplink_ms=spread(78.0))),
        command_period_at_endpoint_ms=dict(spread(420.0 * slow, 433.0 * slow), nominal=140, over_input_ttl=0, input_ttl_ms=750.0),
        endpoint_state_reasons=dict(operator_armed=14, operator_stop=14),
        stop=dict(clicks=14, click_to_endpoint_ms=spread(80.0 * slow, 82.0 * slow), click_to_answer_ms=spread(370.0 * slow, 377.0 * slow),
                  pre_send_ms=spread(2.0, 2.8, 2.9), not_answered=0, not_seen_by_endpoint=0),
        cycles=dict(n=14, activation_failed=0, activation_ms=spread(416), control_lost_while_holding=0))

    def kinds(static, map_asset):
        return dict(document=dict(requests=1, on_network=1), static=dict(requests=47, on_network=static), map_asset=dict(requests=4, on_network=map_asset),
                    api=dict(requests=9, on_network=9))
    load = dict(
        cold=dict(usable_ms=round(1740 * slow), load_event_ms=round(4540 * slow), settled_ms=14254, still_open=[], timed_out=False,
                  total=dict(requests=61, transfer_bytes=1501267), by_kind=kinds(47, 4), requests_on_network=61, not_modified=0, page_file_body_bytes=1487830,
                  files_over_100kB=["/maps/fonts/korean.woff2", "/static/assets/favicon.png", "/static/vendor/hls/hls.min.js",
                                    "/static/vendor/maplibre-gl/maplibre-gl-shared.mjs", "/static/vendor/maplibre-gl/maplibre-gl.mjs"]),
        warm=dict(usable_ms=round(590 * slow), load_event_ms=1067, settled_ms=4112, still_open=[], timed_out=False, total=dict(requests=61, transfer_bytes=28870),
                  by_kind=kinds(47, 1), requests_on_network=58, not_modified=48, page_file_body_bytes=629, files_over_100kB=[]))
    live = dict(duration_s=120.0, clients=[client], series=[], control=control,
                link=dict(down=dict(kbit_per_s=1998.0, share_of_rate_pct=99.9), up=dict(kbit_per_s=31.0), stalls_in_window=[[100.0, 101.0]] if stalled else []))
    if stalled:
        live["after_stall"] = after_stall()
    settings = dict(run_bench.SCENARIOS.get(scenario, {}))
    run = dict(scenario=scenario, profile=profile, profile_settings=dict(PROFILES[profile]) if profile in PROFILES else None, scenario_settings=settings,
               clients=1, label=label, started_utc="2026-11-12T04:26:07+00:00", source=dict(commit=commit, short=commit[:7], rev=commit[:7]),
               tool=dict(commit="b" * 40, tool_folder_modified=False))
    return dict(schema="ubts.slow_link.summary", version=1, run=run, error=None, lost_tabs=[], host_pauses=[], load=load, live=live)


def write_run(root, name, summary):
    folder = Path(root) / name
    folder.mkdir(parents=True)
    (folder / "SUMMARY.json").write_bytes(json.dumps(summary).encode("utf-8"))
    return folder


def run_main(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = compare.main([str(arg) for arg in args])
    return code, out.getvalue(), err.getvalue()


class KeyNumbersTest(unittest.TestCase):
    def test_key_numbers_of_a_drive_run(self):
        numbers = compare.metrics_of(make_summary())
        self.assertEqual((numbers["camera_fps"], numbers["camera_delay_p95"], numbers["lidar_hz"], numbers["command_rt_p95"]), (2.0, 1300.0, 2.1, 397.0))
        self.assertEqual((numbers["command_presend_p95"], numbers["control_presend_max"]), (3.0, 14.0))   # the longest wait of any control request
        self.assertEqual((numbers["input_expired"], numbers["control_lost_cycles"], numbers["stream_closes"]), (0, 0, 0))
        self.assertEqual((numbers["cold_kB"], numbers["cold_page_files"], numbers["cold_over_100kB"]), (1501.3, 52, 5))
        self.assertEqual((numbers["warm_page_files_on_network"], numbers["warm_page_file_body_B"]), (49, 629))  # the nine data requests are not page files
        self.assertEqual((numbers["cold_usable_s"], numbers["warm_usable_s"], numbers["link_share_pct"]), (1.74, 0.59, 99.9))
        self.assertIsNone(numbers["stall_backlog_max"])          # a profile without stalls
        self.assertNotIn("map_growth_ratio", numbers)             # a number of another scenario
        self.assertEqual(set(numbers), {key for key, spec in compare.METRICS.items() if "drive" in spec["scenarios"]})

    def test_a_first_open_that_did_not_finish_has_no_weight(self):
        summary = make_summary(profile="very_bad")
        summary["load"]["cold"].update(settled_ms=None, still_open=[dict(path="/maps/fonts/korean.woff2")])
        numbers = compare.metrics_of(summary)
        self.assertEqual((numbers["cold_kB"], numbers["cold_page_files"], numbers["cold_over_100kB"]), (None, None, None))
        self.assertEqual(numbers["cold_usable_s"], 1.74)          # the time until usable is still a measurement

    def test_numbers_after_a_stall(self):
        summary = make_summary(profile="bad")
        numbers = compare.metrics_of(summary)
        self.assertEqual((numbers["stall_sensor_fresh_max_ms"], numbers["stall_camera_fresh_max_ms"], numbers["stall_backlog_max"]), (600.0, 9000.0, 2))
        summary["live"]["after_stall"] = after_stall(backlog=11, camera_not_fresh=2)
        numbers = compare.metrics_of(summary)
        self.assertEqual((numbers["stall_backlog_max"], numbers["stall_camera_fresh_max_ms"]), (11, 15000.0))   # never fresh: written as the watched time
        for item in summary["live"]["after_stall"].values():       # a summary written before these numbers existed
            for key in ("backlog_messages", "fresh_after_ms", "not_fresh_within_horizon", "fresh_horizon_ms"):
                del item[key]
        numbers = compare.metrics_of(summary)
        self.assertEqual((numbers["stall_sensor_fresh_max_ms"], numbers["stall_backlog_max"]), (None, None))

    def test_map_growth_compares_the_first_and_the_last_full_minute(self):
        summary = make_summary(scenario="drive_map", profile="bad")
        live = summary["live"]
        live.update(duration_s=600.3, live_map_start=dict(ok=True))
        live["series"] = [dict(t_s=t, map_response_wire_B=20000 + 300 * t) for t in range(10, 601, 10)] + [dict(t_s=602, map_response_wire_B=None)]
        live["clients"][0]["render_delay_ms"]["map:liveMapCanvas"] = dict(distinct_versions_per_s=0.25, published_to_drawn_ms=spread(3035.3))
        numbers = compare.metrics_of(summary)
        first, last = 20000 + 300 * 30, 20000 + 300 * 565       # means of t = 10..50 and of t = 540..590
        self.assertEqual(numbers["map_growth_ratio"], round(last / first, 2))
        self.assertEqual(numbers["map_last_minute_kB"], round(last / 1000, 1))
        self.assertEqual((numbers["map_versions_per_s"], numbers["map_drawn_delay_p50"]), (0.25, 3035.3))
        self.assertNotIn("cold_kB", numbers)
        del live["live_map_start"]                                # the map was not drawing: its growth means nothing
        self.assertIsNone(compare.metrics_of(summary)["map_growth_ratio"])

    def test_hidden_tab_and_session_expiry(self):
        summary = make_summary(scenario="hidden")
        client = summary["live"]["clients"][0]
        hidden = copy.deepcopy(client)
        hidden["ws"]["/ws/gps"]["kB_per_s"] = 0.0
        hidden["http_groups"]["status_polling"]["kB_per_s"] = 0.151
        summary["live"].update(visible_phase=dict(clients=[client]), hidden_phase=dict(clients=[hidden]))
        client["ws"]["/ws/gps"]["closes_in_window"] = 1          # closed at the moment the tab was hidden: counted over the whole live part
        numbers = compare.metrics_of(summary)
        self.assertEqual((numbers["hidden_stream_kB_s"], numbers["hidden_status_kB_s"], numbers["stream_closes"]), (15.0, 0.151, 1))
        summary = make_summary(scenario="session_expiry")
        for path in STREAMS:
            summary["live"]["clients"][0]["ws"][path]["closes_in_window"] = 3
        numbers = compare.metrics_of(summary)
        self.assertEqual((numbers["stream_closes"], numbers["session_longest_gap_ms"]), (12, 700))

    def test_a_run_that_must_not_be_used(self):
        self.assertEqual(compare.unusable(make_summary()), [])
        summary = make_summary()
        summary["run"]["failed"] = "TimeoutExpired: Command '['node', 'X:\\\\somewhere\\\\runner.mjs']' timed out"
        summary["host_pauses"] = [[100.0, 6.5]]
        reasons = compare.unusable(summary)
        self.assertEqual(len(reasons), 2)
        self.assertNotIn("somewhere", " ".join(reasons))          # only the kind of error, never a local path
        self.assertTrue(compare.unusable(dict(schema="ubts.slow_link.summary", run={}, error="RAW.json is missing")))


class VerdictTest(unittest.TestCase):
    def test_allowed_change_is_the_larger_of_the_two_limits(self):
        tolerance = dict(rel=0.10, abs=15, stall_rel=0.25, stall_abs=50)
        self.assertEqual(compare.allowance(tolerance, 400.0, False), 40.0)
        self.assertEqual(compare.allowance(tolerance, 100.0, False), 15)
        self.assertEqual(compare.allowance(tolerance, 400.0, True), 100.0)
        self.assertEqual(compare.allowance(tolerance, 100.0, True), 50)

    def test_lower_is_better(self):
        self.assertEqual(compare.judge("lower", 400.0, 440.0, 40.0), "same")     # exactly at the limit
        self.assertEqual(compare.judge("lower", 400.0, 440.1, 40.0), "worse")
        self.assertEqual(compare.judge("lower", 400.0, 359.9, 40.0), "better")
        self.assertEqual(compare.judge("lower", 0, 0, 0), "same")

    def test_higher_is_better(self):
        self.assertEqual(compare.judge("higher", 2.0, 1.79, 0.2), "worse")
        self.assertEqual(compare.judge("higher", 2.0, 1.8, 0.2), "same")
        self.assertEqual(compare.judge("higher", 2.0, 2.3, 0.2), "better")

    def test_a_profile_with_stalls_uses_its_own_tolerance(self):
        cell = dict(metrics=dict(command_rt_p95=700.0), conditions=dict(profile_settings=dict(stall_ms=1000)))
        tolerances = compare.default_tolerances()
        row = compare.compare_cell(cell, dict(command_rt_p95=800.0), tolerances)[0]
        self.assertEqual((row["verdict"], row["allowed"]), ("same", 175.0))        # 25 % on a profile with stalls
        cell["conditions"]["profile_settings"]["stall_ms"] = 0
        row = compare.compare_cell(cell, dict(command_rt_p95=800.0), tolerances)[0]
        self.assertEqual((row["verdict"], row["allowed"]), ("worse", 70.0))        # 10 % without

    def test_rows_for_missing_new_and_report_only_numbers(self):
        cell = dict(metrics=dict(command_rt_p95=400.0, lidar_hz=2.0, control_presend_max=14.0), conditions={})
        rows = {row["metric"]: row for row in compare.compare_cell(cell, dict(lidar_hz=2.0, camera_fps=2.0, control_presend_max=1790.0), compare.default_tolerances())}
        self.assertEqual({key: row["verdict"] for key, row in rows.items()},
                         dict(command_rt_p95="missing", lidar_hz="same", camera_fps="new", control_presend_max="worse"))
        self.assertEqual(rows["control_presend_max"]["mode"], "report")
        self.assertEqual([row["metric"] for row in compare.failing(list(rows.values()))], ["command_rt_p95"])
        self.assertEqual(compare.failing(list(rows.values()), allow_missing=True), [])
        self.assertEqual({row["metric"] for row in compare.failing(list(rows.values()), strict=True)}, {"command_rt_p95", "control_presend_max"})


class CompareCommandTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.accepted = self.root / "accepted" / "baseline.json"
        self.runs = {profile: write_run(self.root / "runs", f"20261112T000000Z_drive_{profile}_aaaaaaa", make_summary(profile=profile)) for profile in PROFILES}
        code, out, err = run_main("--accept", *self.runs.values(), "--accepted", self.accepted)
        self.assertEqual((code, err), (0, ""))

    def tearDown(self):
        self.temp.cleanup()

    def test_the_accepted_file(self):
        text = self.accepted.read_bytes().decode("utf-8")
        accepted = json.loads(text)
        self.assertEqual(list(accepted["cells"]), ["drive/good", "drive/normal", "drive/bad", "drive/very_bad"])
        cell = accepted["cells"]["drive/bad"]
        self.assertEqual((cell["run"], cell["short"], cell["commit"]), ("20261112T000000Z_drive_bad_aaaaaaa", "aaaaaaa", "a" * 40))
        self.assertEqual(cell["metrics"]["stall_backlog_max"], 2)
        self.assertNotIn("stall_backlog_max", accepted["cells"]["drive/normal"]["metrics"])
        self.assertEqual(set(accepted["tolerances"]), set(compare.METRICS))
        self.assertTrue(accepted["provisional"])
        self.assertNotIn("\r", text)
        self.assertNotIn(str(self.root).replace("\\", "/").split("/")[-1], text)    # no local path of the run
        self.assertNotIn("label", cell["conditions"]["profile_settings"])

    def test_the_same_run_passes(self):
        code, out, err = run_main("--run", self.runs["normal"], "--accepted", self.accepted)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("command_rt_p95", out)
        self.assertIn("나빠짐 0", out)
        self.assertIn("통과", out)

    def test_all_runs_of_a_folder_are_compared(self):
        report = self.root / "report.json"
        code, out, err = run_main("--run", self.root / "runs", "--accepted", self.accepted, "--json", report)
        self.assertEqual((code, err), (0, ""))
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual([run["cell"] for run in result["runs"]], ["drive/bad", "drive/good", "drive/normal", "drive/very_bad"])
        self.assertTrue(all(run["status"] == "ok" for run in result["runs"]))

    def test_a_worse_run_fails_and_names_the_metric(self):
        summary = make_summary()
        summary["live"]["control"]["per_op"]["command"]["round_trip_ms"]["p95"] = 600.0
        report = self.root / "report.json"
        code, out, err = run_main("--run", write_run(self.root / "later", "worse", summary), "--accepted", self.accepted, "--json", report)
        self.assertEqual(code, 1)
        self.assertRegex(out, r"실패: command_rt_p95\s")
        run = json.loads(report.read_text(encoding="utf-8"))["runs"][0]
        self.assertEqual((run["status"], run["failed"], run["counts"]["worse"]), ("worse", ["command_rt_p95"], 1))
        row = next(row for row in run["rows"] if row["metric"] == "command_rt_p95")
        self.assertEqual((row["accepted"], row["measured"], row["change"], row["verdict"]), (397.0, 600.0, 203.0, "worse"))

    def test_a_run_on_a_worse_link_fails_against_the_values_of_a_better_one(self):
        slow = write_run(self.root / "later", "slow", make_summary(profile="bad", slow=1.8))
        code, out, err = run_main("--run", slow, "--accepted", self.accepted, "--cell", "drive/normal")
        self.assertEqual(code, 1)
        failed = out.split("실패: ")[1]
        for metric in ("camera_fps", "camera_delay_p50", "lidar_hz", "command_rt_p50", "command_rt_p95", "stop_endpoint_p50", "cold_usable_s"):
            self.assertIn(metric, failed)

    def test_a_better_run_is_not_a_failure(self):
        fast = write_run(self.root / "later", "fast", make_summary(slow=0.5))
        report = self.root / "report.json"
        code, out, err = run_main("--run", fast, "--accepted", self.accepted, "--json", report)
        self.assertEqual((code, err), (0, ""))
        run = json.loads(report.read_text(encoding="utf-8"))["runs"][0]
        self.assertGreater(run["counts"]["better"], 10)
        self.assertEqual(run["counts"]["worse"], 0)
        self.assertIn("좋아짐", out)

    def test_a_missing_metric_is_reported_not_raised(self):
        summary = make_summary()
        del summary["live"]["control"]                             # the drive exercise did not run or the page changed
        summary["live"]["clients"][0]["render_delay_ms"].pop("lidar")
        folder = write_run(self.root / "later", "partial", summary)
        code, out, err = run_main("--run", folder, "--accepted", self.accepted)
        self.assertEqual(code, 1)
        self.assertIn("없음", out)
        self.assertIn("command_rt_p95", out.split("실패: ")[1])
        self.assertIn("lidar_hz", out.split("실패: ")[1])
        code, out, err = run_main("--run", folder, "--accepted", self.accepted, "--allow-missing")
        self.assertEqual(code, 0)

    def test_a_rare_long_wait_is_shown_but_fails_only_when_strict(self):
        summary = make_summary()
        summary["live"]["control"]["per_op"]["stop"]["pre_send_ms"]["max"] = 603.4
        folder = write_run(self.root / "later", "wait", summary)
        code, out, err = run_main("--run", folder, "--accepted", self.accepted)
        self.assertEqual(code, 0)
        self.assertIn("알리기만 함", out)
        code, out, err = run_main("--run", folder, "--accepted", self.accepted, "--strict")
        self.assertEqual(code, 1)
        self.assertIn("control_presend_max", out.split("실패: ")[1])

    def test_a_run_without_accepted_values_or_with_other_conditions(self):
        quick = write_run(self.root / "later", "quick", make_summary(scenario="quick", profile="bad"))
        code, out, err = run_main("--run", quick, "--accepted", self.accepted)
        self.assertEqual(code, 2)
        self.assertIn("받아들인 값이 없는 칸", out)
        summary = make_summary(profile="bad")
        summary["run"]["scenario_settings"]["live_s"] = 60
        shorter = write_run(self.root / "later", "shorter", summary)
        code, out, err = run_main("--run", shorter, "--accepted", self.accepted)
        self.assertEqual(code, 2)
        self.assertIn("live_s 120 → 60", out)
        code, out, err = run_main("--run", shorter, "--accepted", self.accepted, "--ignore-conditions")
        self.assertEqual(code, 0)

    def test_a_failed_run_and_a_wrong_path_are_input_errors(self):
        summary = make_summary()
        summary["run"]["failed"] = "RuntimeError: browser runner failed"
        failed = write_run(self.root / "later", "failed", summary)
        code, out, err = run_main("--run", failed, "--accepted", self.accepted)
        self.assertEqual(code, 2)
        self.assertIn("쓸 수 없는 실행", out)
        self.assertEqual(run_main("--run", self.root / "nowhere", "--accepted", self.accepted)[0], 2)
        self.assertEqual(run_main("--run", self.runs["normal"], "--accepted", self.root / "nothing.json")[0], 2)
        (self.root / "other.json").write_bytes(b'{"schema": "something else"}')
        self.assertEqual(run_main("--run", self.root / "other.json", "--accepted", self.accepted)[0], 2)
        code, out, err = run_main("--accept", failed, "--accepted", self.accepted)
        self.assertEqual(code, 2)
        self.assertIn("받아들일 수 없는 실행", err)

    def test_a_summary_file_alone_can_be_compared(self):
        code, out, err = run_main("--run", self.runs["good"] / "SUMMARY.json", "--accepted", self.accepted)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("drive/good", out)

    def test_accepting_again_replaces_only_the_given_cells_and_keeps_edited_tolerances(self):
        accepted = json.loads(self.accepted.read_text(encoding="utf-8"))
        accepted["tolerances"]["command_rt_p95"]["rel"] = 0.33
        self.accepted.write_bytes(json.dumps(accepted, ensure_ascii=False).encode("utf-8"))
        newer = write_run(self.root / "later", "20261201T000000Z_drive_normal_ccccccc", make_summary(slow=0.5, commit="c" * 40))
        self.assertEqual(run_main("--accept", newer, "--accepted", self.accepted)[0], 0)
        accepted = json.loads(self.accepted.read_text(encoding="utf-8"))
        self.assertEqual(list(accepted["cells"]), ["drive/good", "drive/normal", "drive/bad", "drive/very_bad"])
        self.assertEqual((accepted["cells"]["drive/normal"]["short"], accepted["cells"]["drive/bad"]["short"]), ("ccccccc", "aaaaaaa"))
        self.assertEqual(accepted["cells"]["drive/normal"]["metrics"]["command_rt_p95"], 198.5)
        self.assertEqual(accepted["commits"], ["aaaaaaa", "ccccccc"])
        self.assertEqual(accepted["tolerances"]["command_rt_p95"]["rel"], 0.33)
        first = self.accepted.read_bytes()
        self.assertEqual(run_main("--accept", newer, "--accepted", self.accepted)[0], 0)
        self.assertEqual(self.accepted.read_bytes(), first)       # the same runs give the same file
        self.assertEqual(run_main("--accept", newer, "--accepted", self.accepted, "--replace-all", "--reset-tolerances")[0], 0)
        accepted = json.loads(self.accepted.read_text(encoding="utf-8"))
        self.assertEqual(list(accepted["cells"]), ["drive/normal"])
        self.assertEqual(accepted["tolerances"]["command_rt_p95"]["rel"], compare.METRICS["command_rt_p95"]["rel"])

    def test_two_runs_for_one_cell_are_refused(self):
        again = write_run(self.root / "later", "again", make_summary())
        code, out, err = run_main("--accept", self.runs["normal"], again, "--accepted", self.root / "new.json")
        self.assertEqual(code, 2)
        self.assertIn("drive/normal", err)
        self.assertFalse((self.root / "new.json").exists())


class AcceptedValuesInTheRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.text = compare.ACCEPTED_FILE.read_bytes().decode("utf-8")
        self.accepted = compare.load_accepted(compare.ACCEPTED_FILE)

    def test_cells_and_numbers(self):
        cells = self.accepted["cells"]
        for name in ("drive/good", "drive/normal", "drive/bad", "drive/very_bad"):
            self.assertIn(name, cells)
        self.assertTrue(self.accepted["provisional"])
        for name, cell in cells.items():
            scenario = name.split("/")[0]
            self.assertTrue(cell["run"].endswith(cell["short"]) or cell["short"] in cell["run"], name)
            self.assertRegex(cell["commit"], r"^[0-9a-f]{40}$")
            self.assertRegex(cell["measured_utc"], r"^20\d\d-\d\d-\d\dT")
            self.assertTrue(cell["metrics"], name)
            for key, value in cell["metrics"].items():
                self.assertIn(key, compare.METRICS, name)
                self.assertIn(scenario, compare.METRICS[key]["scenarios"], f"{name} {key}")
                self.assertIsInstance(value, (int, float), f"{name} {key}")
                self.assertNotIsInstance(value, bool, f"{name} {key}")
        for key, tolerance in self.accepted["tolerances"].items():
            self.assertIn(key, compare.METRICS)
            for part in ("rel", "abs", "stall_rel", "stall_abs"):
                self.assertGreaterEqual(tolerance[part], 0, key)

    def test_the_file_holds_no_local_path_and_no_name(self):
        self.assertNotIn("\r", self.text)
        self.assertIsNone(re.search(r"[A-Za-z]:[\\/]", self.text))
        self.assertIsNone(re.search(r"(?i)users[\\/]|/home/", self.text))

    def test_the_accepted_values_pass_against_themselves(self):
        for name, cell in self.accepted["cells"].items():
            rows = compare.compare_cell(cell, dict(cell["metrics"]), self.accepted["tolerances"])
            self.assertEqual(len(rows), len(cell["metrics"]), name)
            self.assertEqual({row["verdict"] for row in rows}, {"same"}, name)

    def test_the_bad_link_fails_against_the_values_of_the_normal_link(self):
        cells, tolerances = self.accepted["cells"], self.accepted["tolerances"]
        rows = compare.compare_cell(cells["drive/normal"], dict(cells["drive/bad"]["metrics"]), tolerances)
        failed = {row["metric"] for row in compare.failing(rows)}
        for metric in ("camera_fps", "camera_delay_p95", "lidar_hz", "command_rt_p50", "command_rt_p95", "command_period_p95", "stop_endpoint_p50",
                       "stop_answer_p50", "cold_usable_s", "input_expired"):
            self.assertIn(metric, failed)
        verdicts = {row["metric"]: row["verdict"] for row in rows}
        self.assertEqual(verdicts["cold_page_files"], "same")     # the same files on every link
        self.assertEqual(verdicts["stall_backlog_max"], "new")    # the normal link has no stalls
        back = {row["metric"]: row["verdict"] for row in compare.compare_cell(cells["drive/bad"], dict(cells["drive/normal"]["metrics"]), tolerances)}
        self.assertEqual((back["command_rt_p95"], back["camera_fps"], back["stall_backlog_max"]), ("better", "better", "missing"))


if __name__ == "__main__":
    unittest.main()
