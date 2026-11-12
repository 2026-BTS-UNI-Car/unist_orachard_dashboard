"""Analysis of one run, on small made-up records (no browser, no stack)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import analyze  # noqa: E402
import summarize  # noqa: E402

T0 = 1_800_000_000_000  # an arbitrary wall clock in ms


def request(path, start, transfer, decoded=None, status=200, took=20, from_cache=False, wire_status=None, failed=None, finished=True, kind="Script"):
    return dict(path=path, method="GET", type=kind, start=T0 + start, status=status, wire_status=wire_status, from_cache=from_cache,
                transfer=transfer, decoded=transfer if decoded is None else decoded, end=T0 + start + took if finished else None, failed=failed,
                queued_ms=0.5, send_start_ms=1.0)


def control_row(op, at, seq=None, took=120.0, **extra):
    row = dict(op=op, t_call=T0 + at, p_call=float(at), seq=seq, left=30, right=30, reason=None, p_headers=at + took - 1, p_done=at + took,
               status=200, ok=True, error=None, answer_error=None)
    row.update(extra)
    return row


class StatisticsTest(unittest.TestCase):
    def test_percentiles(self):
        values = list(range(1, 101))
        self.assertEqual((analyze.pct(values, 50), analyze.pct(values, 95), analyze.pct(values, 100)), (51, 95, 100))
        self.assertIsNone(analyze.pct([], 50))
        self.assertEqual(analyze.dist([None, 4.0, 2.0, 3.0]), dict(n=3, p50=3.0, p90=4.0, p95=4.0, max=4.0))
        self.assertEqual(analyze.dist([]), dict(n=0))


class LoadSummaryTest(unittest.TestCase):
    def load(self, requests, **extra):
        load = dict(nav_at=T0, ready_at=T0 + 2100, done_at=T0 + 3000, timed_out=False, t=T0 + 6000,
                    milestones=dict(drive_ready=T0 + 900, first_telemetry=T0 + 1500, dom_content_loaded=T0 + 2000, load_event=T0 + 2100),
                    requests=requests)
        load.update(extra)
        return load

    def test_cold_load_counts_page_files_and_the_api_calls_made_while_loading(self):
        summary = analyze.summarize_load(self.load([
            request("/rover-control", 0, 9500, decoded=40000, kind="Document"),
            request("/static/app/00-config-state.js", 100, 5000), request("/static/vendor/hls/hls.min.js", 120, 189000, decoded=620000),
            request("/api/telemetry", 950, 4300, kind="Fetch"), request("/maps/fonts/korean.woff2", 2500, 630000, kind="Font"),
            request("/static/late.mjs", 4000, 2000),              # after "done": a page file that was asked for still belongs to the load
            request("/api/telemetry", 2500, 4300, kind="Fetch"),  # after "usable and loaded": steady polling, not part of the load
            request("/static/vendor/maplibre-gl/maplibre-gl-worker.mjs", 2600, 0, finished=False, status=None) | dict(handed_off=True),
        ]))
        self.assertEqual(summary["total"]["requests"], 7)   # document + 4 static + 1 map asset + 1 API
        self.assertEqual(summary["completed_elsewhere"], ["/static/vendor/maplibre-gl/maplibre-gl-worker.mjs"])
        self.assertEqual(summary["unfinished"], [])
        self.assertEqual(summary["total"]["transfer_bytes"], 9500 + 5000 + 189000 + 4300 + 630000 + 2000)
        self.assertEqual(summary["by_kind"]["api"]["requests"], 1)
        self.assertEqual(summary["by_kind"]["map_asset"]["transfer_bytes"], 630000)
        self.assertEqual((summary["usable_ms"], summary["first_telemetry_ms"], summary["load_event_ms"], summary["settled_ms"]), (900, 1500, 2100, 3000))
        self.assertEqual(summary["until_usable"]["requests"], 3)  # document and the two scripts that started before 900 ms
        self.assertEqual(summary["files_over_100kB"], ["/maps/fonts/korean.woff2", "/static/vendor/hls/hls.min.js"])
        self.assertEqual(summary["largest"][0]["path"], "/maps/fonts/korean.woff2")
        self.assertEqual([r["path"] for r in summary["requests"]][:2], ["/rover-control", "/static/app/00-config-state.js"])
        self.assertEqual(summary["requests"][1]["pre_send_ms"], 1.5)
        self.assertEqual((summary["requests_on_network"], summary["not_modified"], summary["from_cache_without_request"]), (7, 0, 0))

    def test_warm_load_separates_revalidated_and_cached_files(self):
        summary = analyze.summarize_load(self.load([
            request("/rover-control", 0, 280, decoded=40000, wire_status=304, kind="Document"),
            request("/static/app/00-config-state.js", 100, 290, decoded=5000, wire_status=304, from_cache=True),
            request("/static/vendor/maplibre-gl/maplibre-gl.mjs", 200, 0, decoded=590000, from_cache=True),
            request("/static/styles.css", 300, 9000, decoded=40000),
        ]))
        self.assertEqual(summary["not_modified"], 2)                 # the document and one script were asked about and not sent again
        self.assertEqual(summary["from_cache_without_request"], 1)
        self.assertEqual(summary["requests_on_network"], 3)
        self.assertEqual(summary["page_file_body_bytes"], 9000)      # only the file that was sent again
        self.assertEqual(summary["total"]["transfer_bytes"], 280 + 290 + 9000)

    def test_failed_and_unfinished_requests_are_listed(self):
        summary = analyze.summarize_load(self.load([
            request("/rover-control", 0, 9500, kind="Document"), request("/static/a.js", 100, 0, failed="net::ERR_FAILED", status=None),
            request("/maps/korea.pmtiles", 200, 0, finished=False, status=None),
        ], done_at=None, timed_out=True))
        self.assertTrue(summary["timed_out"])
        self.assertIsNone(summary["settled_ms"])
        self.assertEqual(summary["failed"], [dict(path="/static/a.js", error="net::ERR_FAILED")])
        self.assertEqual(summary["unfinished"], ["/maps/korea.pmtiles"])

    def test_load_that_never_settled_lists_the_open_files(self):
        still_open = [dict(path="/static/favicon.ico", type="Other", method="GET", start_ms=40, received_bytes=0, status=None)]
        summary = analyze.summarize_load(self.load([request("/rover-control", 0, 9500, kind="Document")], settled=False, still_open=still_open))
        self.assertIsNone(summary["settled_ms"])
        self.assertEqual(summary["still_open"], still_open)
        self.assertFalse(summary["timed_out"])

    def test_missing_record(self):
        self.assertEqual(analyze.summarize_load(None)["error"], "no load record")
        self.assertTrue(analyze.summarize_load(dict(timed_out=True))["timed_out"])


class ControlSummaryTest(unittest.TestCase):
    def test_requests_are_joined_with_browser_timing_and_the_endpoint_record(self):
        rows = [control_row("arm", 1000, took=200.0), control_row("command", 1300, seq=1, took=150.0), control_row("command", 1500, seq=2, took=160.0),
                control_row("command", 1700, seq=3, took=900.0), control_row("stop", 3000, took=180.0, left=None, right=None),
                control_row("command", 2700, seq=4, p_done=2760.0, error="Failed to fetch", status=None, ok=None)]
        # [op, start, connect start, connect time, request start, response start, response end, bytes]
        timings = [["arm", 1000.4, 0.5, 0.3, 1.2, 190.0, 199.0, 700], ["command", 1300.2, 0.4, 0.3, 1.0, 140.0, 149.0, 700],
                   ["command", 1500.3, 30.0, 0.3, 31.0, 150.0, 159.0, 700], ["command", 1700.1, 0.4, 0.3, 1.1, 880.0, 899.0, 700],
                   ["stop", 3000.5, 0.4, 0.3, 2.0, 170.0, 179.0, 330]]
        events = [dict(kind="request", op="arm", t=(T0 + 1040) / 1000, ok=True), dict(kind="request", op="drive", seq=1, t=(T0 + 1345) / 1000, ok=True),
                  dict(kind="request", op="drive", seq=2, t=(T0 + 1560) / 1000, ok=True), dict(kind="request", op="drive", seq=3, t=(T0 + 2500) / 1000, ok=False),
                  dict(kind="state", reason="input_expired", t=(T0 + 2310) / 1000), dict(kind="request", op="stop", t=(T0 + 3050) / 1000),
                  dict(kind="state", reason="operator_stop", t=(T0 + 3060) / 1000)]
        marks = [[T0 + 2998, "click:stop"], [T0 + 1290, "keydown:KeyW"]]
        cycles = [dict(cycle=0, t_arm_click=T0 + 1000, armed_at=T0 + 1210, t_keydown=T0 + 1290, t_keyup=T0 + 2800, lease_at_hold_end=False)]
        sample = dict(control=rows, control_timings=timings, marks=marks)
        result = analyze.summarize_control([sample], events, marks, cycles, T0, T0 + 10_000)
        command = result["per_op"]["command"]
        self.assertEqual(command["n"], 4)
        self.assertEqual(command["round_trip_ms"]["n"], 3)                # the failed request has no round trip
        self.assertEqual(command["round_trip_ms"]["max"], 900.0)
        self.assertEqual(command["pre_send_ms"]["max"], 31.0)             # the request that waited 30 ms for a connection
        self.assertEqual(command["uplink_ms"]["p50"], 60.0)
        self.assertEqual((command["no_answer"], command["not_seen_by_endpoint"]), (1, 1))
        self.assertEqual(command["failures"], {"network: Failed to fetch": 1})
        self.assertEqual(result["per_op"]["arm"]["uplink_ms"]["p50"], 40.0)
        self.assertEqual(result["endpoint_state_reasons"], {"input_expired": 1, "operator_stop": 1})
        period = result["command_period_at_endpoint_ms"]
        self.assertEqual((period["n"], period["max"], period["nominal"], period["over_input_ttl"]), (2, 940.0, 140, 1))  # the refused third command counts
        stop = result["stop"]
        self.assertEqual(stop["clicks"], 1)
        self.assertEqual(stop["click_to_request_ms"]["p50"], 2)
        self.assertEqual(stop["click_to_endpoint_ms"]["p50"], 52.0)
        self.assertEqual(stop["click_to_answer_ms"]["p50"], 182.0)
        self.assertEqual((stop["not_answered"], stop["not_seen_by_endpoint"]), (0, 0))
        self.assertEqual(result["cycles"], dict(n=1, activation_failed=0, activation_ms=dict(n=1, p50=210, p90=210, p95=210, max=210), control_lost_while_holding=1))

    def test_command_gaps_longer_than_the_input_limit_are_counted(self):
        events = [dict(kind="request", op="drive", seq=i, ok=True, t=(T0 + at) / 1000) for i, at in enumerate((0, 140, 1000, 1140))]
        cycles = [dict(t_arm_click=T0 - 500, armed_at=T0 - 100, t_keydown=T0 - 10, t_keyup=T0 + 2000, lease_at_hold_end=True)]
        result = analyze.summarize_control([], events, [], cycles, T0 - 1000, T0 + 5000, input_ttl_ms=750.0)
        period = result["command_period_at_endpoint_ms"]
        self.assertEqual((period["n"], period["over_input_ttl"], period["input_ttl_ms"]), (3, 1, 750.0))


class LinkAndProcessTest(unittest.TestCase):
    def link(self):
        seconds = [[T0 // 1000 + second, 62_500] for second in range(10)]
        return dict(rate_kbps=500.0, up_rate_kbps=500.0, rtt_ms=150, queue_bytes=16384, stall_every_s=20.0, stall_ms=1000, setup_rtts=1, segment_bytes=1460,
                    connections=40, stalls_wall=[[T0 / 1000 + 4.0, T0 / 1000 + 5.0], [T0 / 1000 + 24.0, T0 / 1000 + 25.0]],
                    down=dict(per_second=seconds, max_queue_delay_s=0.3), up=dict(per_second=[[T0 // 1000, 5000]], max_queue_delay_s=0.01))

    def test_link_window_reports_rate_and_share(self):
        window = analyze.link_window(self.link(), T0, T0 + 10_000)
        self.assertEqual(window["down"]["kbit_per_s"], 500.0)
        self.assertEqual(window["down"]["share_of_rate_pct"], 100.0)
        self.assertEqual(window["up"]["kB_per_s"], 0.5)
        self.assertEqual(len(window["stalls_in_window"]), 1)
        self.assertIsNone(analyze.link_window(None, T0, T0 + 1000))

    def test_recovery_after_a_stall(self):
        stall_end = T0 + 5000
        arrivals = [[T0 + 3900, T0 + 3700, 100], [stall_end + 180, stall_end - 900, 100], [stall_end + 400, stall_end + 300, 100]]
        client = dict(samples=[dict(arrivals={"/ws/lidar": arrivals})])
        result = analyze.stall_recovery(client, [[T0 / 1000 + 4.0, T0 / 1000 + 5.0]], T0, T0 + 10_000)
        lidar = result["/ws/lidar"]
        self.assertEqual(lidar["first_message_after_ms"]["p50"], 180)
        self.assertEqual(lidar["age_of_first_message_ms"]["p50"], 1080)   # it was published before the stall ended
        self.assertEqual(lidar["worst_age_within_3s_ms"]["max"], 1080)

    def test_recovery_when_the_first_message_comes_late(self):
        stall_end = T0 + 5000
        arrivals = [[stall_end + 4200, stall_end - 2000, 100]]  # 4.2 s after the stall: later than the 3 s window
        client = dict(samples=[dict(arrivals={"/ws/camera": arrivals})])
        camera = analyze.stall_recovery(client, [[T0 / 1000 + 3.0, T0 / 1000 + 5.0]], T0, T0 + 10_000)["/ws/camera"]
        self.assertEqual(camera["first_message_after_ms"]["p50"], 4200)
        self.assertEqual(camera["worst_age_within_3s_ms"]["max"], 6200)

    def test_cpu_summary(self):
        rows = [dict(t=T0 / 1000 + i, host_cpu_pct=10.0 + i, ncpu=8, procs=dict(dashboard=dict(cpu_pct=50.0, rss_mb=100.0 + i, threads=9)),
                     chrome=dict(renderer=dict(cpu_pct=30.0, rss_mb=200.0, n=1))) for i in range(5)]
        cpu = analyze.summarize_cpu(rows, T0, T0 + 10_000)
        self.assertEqual(cpu["procs"]["dashboard"], dict(cpu_pct_mean=50.0, cpu_pct_p95=50.0, rss_mb=[100.0, 104.0]))
        self.assertEqual(cpu["host_cpu_pct"]["mean"], 12.0)
        self.assertIsNone(analyze.summarize_cpu(rows, T0 + 60_000, T0 + 70_000))


class WholeRunTest(unittest.TestCase):
    def test_a_run_without_browser_output_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "run.json").write_bytes(json.dumps(dict(scenario="drive", profile="bad", source=dict(short="abc1234"))).encode())
            summary = analyze.analyze(temp)
            self.assertIn("RAW.json is missing", summary["error"])
            Path(temp, "SUMMARY.json").write_bytes(json.dumps(summary).encode())
            row = summarize.row_of(temp)
            self.assertEqual((row["profile"], row["source"], row["camera_fps"]), ("bad", "abc1234", None))
            self.assertIn("Runs with a failure", summarize.render_text([row]))

    def test_tables_show_dashes_for_missing_values(self):
        self.assertEqual(summarize.show(None), "-")
        self.assertEqual(summarize.show([1920]), "1920")
        self.assertEqual(summarize.show(12.5), "12.5")
        self.assertEqual(summarize.show(1234.56), "1235")


if __name__ == "__main__":
    unittest.main()
