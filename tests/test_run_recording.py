"""Recordings of an autonomy run (dashboard_server/run_recording.py, 2026-11-20): the record of a driven stretch written
next to the recorder's files (first stretch, start-again with the stretch before and why it stopped, the end with
driving time and distance from the executor's run metrics), the keeper that stops a recording nobody stops (a run that
no longer drives, a go that never came, an executor that is away; a manual recording is never looked at), the server
start in the middle of a recording, the list fields and the fallback folder. A fake executor, recorder and clocks; no
network, no thread. Run: python -B -m pytest -q tests/test_run_recording.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard_server import run_recording as rr  # noqa: E402


class World:
    """An executor, a recorder and two clocks the test moves by hand."""

    def __init__(self, root: Path):
        self.archive = root / "recordings"
        self.archive.mkdir()
        self.fallback = root / "runtime" / "autonomy_segments"
        self.wall, self.steady = 1_800_000_000.0, 500.0
        self.executor = {"run": 7, "run_state": "armed", "operator_link": {"go_active": False},
                         "metrics": {"run_id": 7, "time_s": 0.0, "distance_m": 0.0},
                         "single_row": {"distance_m": 0.0}, "speed": {"target_mps": 0.4}, "gnss": {"mode": "off"}}
        self.recording = None                 # {"id", "tag"}
        self.count = 0
        self.stops = []
        self.refuse_stop = False
        self.logs = []
        self.keeper = rr.RunRecordings(self.archive, self.fallback, lambda: self.executor, self.recorder_status,
                                       self.recorder_stop, log=lambda name, **kw: self.logs.append((name, kw)),
                                       clock=lambda: self.wall, steady=lambda: self.steady)

    def pass_time(self, seconds):
        self.wall += seconds
        self.steady += seconds

    # the recorder, as scripts/data_recorder_service.py answers
    def start(self, tag="autonomy", folder=True):
        self.count += 1
        rid = f"20261120T0000{self.count:02d}Z_both"
        if folder:
            (self.archive / rid).mkdir()
            (self.archive / rid / "metadata.json").write_bytes(b'{"id": "%s", "tag": "%s", "state": "recording"}\n' % (rid.encode(), tag.encode()))
        self.recording = {"id": rid, "tag": tag}
        result = {"ok": True, "state": "recording", "recording_id": rid, "status": {"ok": True, "active": True, "recording_id": rid, "tag": tag}}
        self.keeper.recording_started({"sensors": ["lidar"], "tag": tag}, result)
        return rid

    def recorder_status(self):
        if self.recording:
            return {"ok": True, "active": True, "state": "recording", "recording_id": self.recording["id"], "tag": self.recording["tag"]}
        return {"ok": True, "active": False, "state": "idle"}

    def recorder_stop(self):
        self.stops.append(self.recording["id"] if self.recording else None)
        if self.refuse_stop:
            return {"ok": False, "state": "recorder_unavailable", "error": "timeout"}
        if not self.recording:
            return {"ok": True, "state": "idle"}
        rid, self.recording = self.recording["id"], None
        return {"ok": True, "state": "archived", "recording_id": rid}

    # the executor
    def drive(self, seconds, metres, state="running", go_active=True):
        self.executor["run_state"] = state
        self.executor["operator_link"]["go_active"] = go_active
        self.executor["metrics"]["time_s"] = round(self.executor["metrics"]["time_s"] + seconds, 2)
        self.executor["metrics"]["distance_m"] = round(self.executor["metrics"]["distance_m"] + metres, 3)
        self.executor["single_row"]["distance_m"] = self.executor["metrics"]["distance_m"]
        self.pass_time(seconds)

    def halt(self, state, reason=None, detail=None):
        self.executor.update(run_state=state, stop_reason=reason, stop_detail=detail)
        self.executor["operator_link"]["go_active"] = False

    def record(self, rid):
        return json.loads((self.archive / rid / rr.FILE_NAME).read_bytes().decode("utf-8"))


class RunRecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w = World(Path(self.tmp.name))

    def test_first_stretch_then_a_start_again_and_the_row_end(self):
        w = self.w
        w.keeper.run_started({"row_len_m": 45.0, "gnss_mode": "off"}, {"ok": True, "run_id": 7, "gnss_mode": "off"})
        first = w.start()
        raw = (w.archive / first / rr.FILE_NAME).read_bytes()
        self.assertTrue(raw.endswith(b"}\n") and b"\r" not in raw, "UTF-8, LF, one newline at the end")
        self.assertEqual(sorted(p.name for p in (w.archive / first).iterdir()), [rr.FILE_NAME, "metadata.json"], "no temporary file is left")
        a = w.record(first)
        self.assertEqual((a["schema"], a["recording_id"], a["run"], a["segment"], a["resumed"], a["previous"]),
                         ("ubts.autonomy_segment.v1", first, 7, 1, False, None))
        self.assertEqual(a["settings"], {"row_len_m": 45.0, "gnss_mode": "off", "target_speed_m_s": 0.4})
        self.assertEqual((a["start"]["run_state"], a["start"]["run_drive_time_s"], a["start"]["run_distance_m"]), ("armed", 0.0, 0.0))
        self.assertEqual((a["go"], a["end"], a["drive_time_s"]), (None, None, None), "open until the recording stops")
        w.pass_time(2.5)
        w.keeper.go_sent({"ok": True, "run_id": 7, "go_serial": 1})
        self.assertEqual(w.record(first)["go"]["serial"], 1)
        w.drive(35.2, 12.3)
        # the screen connection is lost: the run pauses and the page stops the recording
        w.halt("paused", "OPERATOR_LINK_LOST", "operator_link_lost")
        result = w.keeper.stop("page")
        self.assertEqual((result["ok"], result["state"], result["recording_id"]), (True, "archived", first))
        a = w.record(first)
        self.assertEqual(a["end"]["run_state"], "paused")
        self.assertEqual((a["end"]["reason"], a["end"]["detail"], a["end"]["text"], a["end"]["stopped_by"], a["end"]["recorder"]),
                         ("OPERATOR_LINK_LOST", "operator_link_lost", "화면 연결 끊김", "page", "archived"))
        self.assertEqual((a["drive_time_s"], a["distance_m"], a["run_drive_time_s"], a["run_distance_m"], a["time_source"]),
                         (35.2, 12.3, 35.2, 12.3, "executor_metrics"))
        self.assertEqual(w.keeper.status_fields()["autonomy_segment"]["recording_id"], first, "the last stretch stays for the page")
        # the operator waits a while, then starts again: a new recording, the second stretch of run 7
        w.pass_time(40.0)
        second = w.start()
        self.assertNotEqual(second, first)
        b = w.record(second)
        self.assertEqual((b["run"], b["segment"], b["resumed"]), (7, 2, True))
        self.assertEqual(b["previous"], {"recording_id": first, "stop": {"run_state": "paused", "reason": "OPERATOR_LINK_LOST",
                                                                       "detail": "operator_link_lost", "text": "화면 연결 끊김"}})
        self.assertEqual((b["start"]["run_drive_time_s"], b["start"]["run_distance_m"]), (35.2, 12.3))
        w.keeper.go_sent({"ok": True, "go_serial": 2})
        w.drive(70.0, 30.9)
        w.halt("ended", "ROW_END", "row_end")
        w.keeper.stop("page")
        b = w.record(second)
        self.assertEqual((b["end"]["run_state"], b["end"]["text"]), ("ended", "열 끝 도착"))
        self.assertEqual((b["drive_time_s"], b["distance_m"]), (70.0, 30.9), "the stretch: standing still in between does not count")
        self.assertEqual((b["run_drive_time_s"], b["run_distance_m"]), (105.2, 43.2), "the run: both stretches")
        self.assertNotIn("lat", json.dumps(b))
        # the list: both recordings say what they are; a manual one gets nothing
        (w.archive / "20261120T000900Z_both").mkdir()
        listing = w.keeper.decorate({"ok": True, "recordings": [{"id": second, "tag": "autonomy"}, {"id": first, "tag": "autonomy"},
                                                                {"id": "20261120T000900Z_both", "tag": "manual"}, {"id": "../x", "tag": "autonomy"}]})
        rows = listing["recordings"]
        self.assertEqual(rows[0]["segment"], {"run": 7, "segment": 2, "resumed": True, "ended": True, "end_text": "열 끝 도착", "stopped_by": "page",
                                              "drive_time_s": 70.0, "distance_m": 30.9, "time_source": "executor_metrics",
                                              "previous_recording_id": first, "previous_stop_text": "화면 연결 끊김"})
        self.assertEqual((rows[1]["segment"]["segment"], rows[1]["segment"]["resumed"], rows[1]["segment"]["end_text"]), (1, False, "화면 연결 끊김"))
        self.assertNotIn("segment", rows[2])
        self.assertNotIn("segment", rows[3], "an id that is not a plain name is not opened")

    def test_the_keeper_stops_a_recording_nobody_stops(self):
        w = self.w
        rid = w.start()
        self.assertEqual(w.keeper.tick(), "waiting", "the go is on its way (the page waits 2.5 s after the recording started)")
        w.pass_time(3.0)
        w.keeper.go_sent({"ok": True, "go_serial": 1})
        w.executor["operator_link"]["go_active"] = True       # the press is held through the input warm-up: not running yet
        self.assertEqual(w.keeper.tick(), "driving")
        w.drive(20.0, 7.5)
        self.assertEqual(w.keeper.tick(), "driving")
        # the screen is closed: the heartbeat stops, the executor pauses the run, nobody stops the recording
        w.halt("paused", "OPERATOR_LINK_LOST", "operator_link_lost")
        self.assertEqual(w.keeper.tick(), "waiting")
        w.pass_time(4.0)
        self.assertEqual(w.keeper.tick(), "waiting", "less than 5 s: not yet")
        self.assertEqual(w.stops, [])
        w.pass_time(1.5)
        self.assertEqual(w.keeper.tick(), "stopped")
        self.assertEqual(w.stops, [rid], "the server stopped the recorder")
        a = w.record(rid)
        self.assertEqual((a["end"]["stopped_by"], a["end"]["run_state"], a["end"]["text"]), ("server", "paused", "화면 연결 끊김"))
        self.assertEqual((a["drive_time_s"], a["distance_m"]), (20.0, 7.5))
        self.assertEqual(w.keeper.tick(), "idle", "nothing more to keep")
        self.assertEqual(len(w.stops), 1)

    def test_the_page_stopped_first_and_a_manual_recording_is_left_alone(self):
        w = self.w
        rid = w.start()
        w.keeper.go_sent({"ok": True})
        w.drive(8.0, 3.0)
        w.halt("latched", "OPERATOR_OR_BACKSTOP", "operator_stop")
        w.keeper.stop("page")
        w.pass_time(30.0)
        self.assertEqual(w.keeper.tick(), "idle", "the page stopped it: the keeper does nothing")
        self.assertEqual(w.stops, [rid])
        self.assertEqual(w.record(rid)["end"]["text"], "STOP 누름")
        # a recording started by hand: no record, no keeper, however long the run stands
        manual = w.start(tag="manual")
        self.assertFalse((w.archive / manual / rr.FILE_NAME).exists())
        w.halt("idle")
        w.pass_time(600.0)
        self.assertEqual(w.keeper.tick(), "idle")
        self.assertEqual(w.stops, [rid], "the manual recording goes on")
        self.assertEqual(w.keeper.stop("page")["recording_id"], manual, "its own stop button still stops it")

    def test_a_go_that_never_came_and_an_executor_that_is_away(self):
        w = self.w
        rid = w.start()
        w.pass_time(10.0)
        self.assertEqual(w.keeper.tick(), "waiting")
        w.pass_time(6.0)
        self.assertEqual(w.keeper.tick(), "waiting", "the 15 s count from the first look")
        w.pass_time(10.0)
        self.assertEqual(w.keeper.tick(), "stopped")
        a = w.record(rid)
        self.assertEqual((a["end"]["detail"], a["end"]["text"], a["drive_time_s"], a["distance_m"]), ("not_started", "출발하지 않음", 0.0, 0.0))
        # the executor stops answering while a stretch is recorded
        rid = w.start()
        w.keeper.go_sent({"ok": True})
        w.drive(5.0, 2.0)
        self.assertEqual(w.keeper.tick(), "driving")
        good, w.executor = w.executor, None
        self.assertEqual(w.keeper.tick(), "unreachable")
        w.pass_time(14.0)
        self.assertEqual(w.keeper.tick(), "unreachable")
        w.pass_time(2.0)
        self.assertEqual(w.keeper.tick(), "stopped")
        a = w.record(rid)
        self.assertEqual((a["end"]["stopped_by"], a["drive_time_s"], a["distance_m"]), ("server", 5.0, 2.0), "the last numbers that were read")
        w.executor = good
        # a recorder that refuses the stop: tried again later, not at every look
        rid = w.start()
        w.keeper.go_sent({"ok": True})
        w.drive(3.0, 1.0)
        w.halt("ended", "ROW_END", "row_end")
        w.keeper.tick()
        w.pass_time(6.0)
        w.refuse_stop = True
        self.assertEqual(w.keeper.tick(), "failed")
        attempts = len(w.stops)
        w.pass_time(2.0)
        self.assertEqual(w.keeper.tick(), "waiting")
        self.assertEqual(len(w.stops), attempts, "no new attempt for 10 s")
        w.refuse_stop = False
        w.pass_time(9.0)
        self.assertEqual(w.keeper.tick(), "stopped")
        self.assertEqual(w.record(rid)["end"]["text"], "열 끝 도착")

    def test_the_recording_ended_elsewhere_and_the_run_went_on_to_a_new_run(self):
        w = self.w
        rid = w.start()
        w.keeper.go_sent({"ok": True})
        w.drive(12.0, 4.4)
        w.halt("latched", "OPERATOR_OR_BACKSTOP", "operator_stop")
        self.assertEqual(w.keeper.tick(), "waiting")           # the keeper saw the stop of run 7
        # the operator presses prepare at once: a new run, armed, its numbers at zero
        w.executor = {"run": 8, "run_state": "armed", "operator_link": {"go_active": False},
                      "metrics": {"run_id": 8, "time_s": 0.0, "distance_m": 0.0}, "single_row": {"distance_m": 0.0}}
        w.recording = None                                      # and the recorder stopped by itself (it was restarted)
        w.pass_time(6.0)
        self.assertEqual(w.keeper.tick(), "gone")
        self.assertEqual(w.stops, [], "nothing to stop")
        a = w.record(rid)
        self.assertEqual((a["end"]["stopped_by"], a["end"]["run_state"], a["end"]["text"]), ("elsewhere", "latched", "STOP 누름"),
                         "the end is the one of its own run, not the state of the next run")
        self.assertEqual((a["drive_time_s"], a["distance_m"]), (12.0, 4.4))
        # the next run's first stretch is number 1 again and has no stretch before it
        nxt = w.start()
        b = w.record(nxt)
        self.assertEqual((b["run"], b["segment"], b["resumed"], b["previous"]), (8, 1, False, None))

    def test_a_start_again_whose_stretch_before_is_not_known(self):
        w = self.w
        # run 3 of an executor that was started again: an old record of another run 3 ended somewhere else
        old = w.archive / "20261119T101010Z_both"
        old.mkdir()
        (old / rr.FILE_NAME).write_bytes(json.dumps({"schema": rr.SCHEMA, "recording_id": old.name, "run": 3, "segment": 4,
                                                     "end": {"text": "x"}, "run_distance_m": 88.0}).encode("utf-8"))
        w.executor.update(run=3, run_state="paused", stop_reason="PERCEPTION_OR_LOCALISATION", stop_detail="stale_lidar")
        w.executor["metrics"] = {"run_id": 3, "time_s": 10.0, "distance_m": 3.5}
        w.executor["single_row"]["distance_m"] = 3.5
        rid = w.start()
        a = w.record(rid)
        self.assertEqual((a["segment"], a["resumed"]), (None, True), "a start again, its number not known")
        self.assertEqual(a["previous"], {"recording_id": None, "stop": {"run_state": "paused", "reason": "PERCEPTION_OR_LOCALISATION",
                                                                      "detail": "stale_lidar", "text": "LiDAR 입력 끊김"}})
        # the same after a server start: the stretch before is found again in its folder
        w.keeper.stop("page")
        fresh = World.__new__(World)
        fresh.__dict__.update(w.__dict__)
        fresh.keeper = rr.RunRecordings(w.archive, w.fallback, lambda: w.executor, w.recorder_status, w.recorder_stop,
                                        clock=lambda: w.wall, steady=lambda: w.steady)
        w.keeper = fresh.keeper
        nxt = w.start()
        self.assertEqual(w.record(nxt)["previous"]["recording_id"], rid, "read from the folder of the stretch before")
        self.assertEqual(w.record(nxt)["segment"], None, "one more than a number that is not known is not known")

    def test_server_start_while_a_run_recording_runs(self):
        w = self.w
        rid = w.start()
        w.keeper.go_sent({"ok": True})
        w.drive(15.0, 5.0)
        again = rr.RunRecordings(w.archive, w.fallback, lambda: w.executor, w.recorder_status, w.recorder_stop,
                                 clock=lambda: w.wall, steady=lambda: w.steady)
        self.assertEqual(again.tick(), "idle")
        self.assertEqual(again.recover()["recording_id"], rid, "the recorder says an autonomy recording is running")
        self.assertEqual(again.tick(), "driving")
        w.halt("paused", "OPERATOR_LINK_LOST", "operator_link_lost")
        again.tick()
        w.pass_time(6.0)
        self.assertEqual(again.tick(), "stopped")
        a = w.record(rid)
        self.assertEqual((a["segment"], a["drive_time_s"], a["end"]["stopped_by"]), (1, 15.0, "server"), "the record written at the start is kept")
        # a manual recording is not taken over
        w.start(tag="manual")
        other = rr.RunRecordings(w.archive, w.fallback, lambda: w.executor, w.recorder_status, w.recorder_stop)
        self.assertIsNone(other.recover())
        # an autonomy recording without a record (it started before this server knew of records): one is made, marked
        w.recording = None
        orphan = w.start(tag="manual")
        w.recording["tag"] = "autonomy"
        third = rr.RunRecordings(w.archive, w.fallback, lambda: w.executor, w.recorder_status, w.recorder_stop,
                                 clock=lambda: w.wall, steady=lambda: w.steady)
        self.assertTrue(third.recover()["recovered"])
        self.assertTrue(w.record(orphan)["recovered"])

    def test_no_metrics_uses_the_clock_and_a_folder_that_is_missing_uses_the_fallback(self):
        w = self.w
        del w.executor["metrics"]
        rid = w.start(folder=False)                              # the recorder's folder is not where the server looks
        self.assertFalse((w.archive / rid).exists(), "no folder is made in the archive")
        path = w.fallback / f"{rid}.json"
        self.assertTrue(path.is_file())
        w.pass_time(2.5)
        w.keeper.go_sent({"ok": True})
        w.executor["run_state"] = "running"
        w.executor["single_row"]["distance_m"] = 4.0
        w.pass_time(10.0)
        self.assertEqual(w.keeper.tick(), "driving")
        w.halt("ended", "ROW_END", "row_end")
        w.keeper.stop("page")
        a = json.loads(path.read_bytes().decode("utf-8"))
        self.assertEqual((a["time_source"], a["drive_time_s"], a["distance_m"], a["run_drive_time_s"]), ("wall_clock", 10.0, 4.0, 10.0))
        self.assertEqual(w.keeper.decorate({"recordings": [{"id": rid, "tag": "autonomy"}]})["recordings"][0]["segment"]["drive_time_s"], 10.0)

    def test_words_and_the_page_fields(self):
        self.assertEqual(rr.stop_words("ROW_END", "anything"), "열 끝 도착")
        self.assertEqual(rr.stop_words("CONTROL_AUTHORITY", "lease_lost"), "조종 권한 거둠")
        self.assertEqual(rr.stop_words("CONTROL_AUTHORITY", "new_detail"), "조향·구동 응답 부족")
        self.assertEqual(rr.stop_words("NEW_CATEGORY", None), "기타(NEW_CATEGORY)")
        self.assertIsNone(rr.stop_words(None, None))
        self.assertIsNone(rr.sample(None))
        view = rr.sample({"run": 4, "run_state": "running", "metrics": {"run_id": 3, "time_s": 99.0}, "single_row": {"distance_m": 2.0}})
        self.assertEqual((view["time_s"], view["distance_m"]), (None, 2.0), "the metrics of another run are not this run's time")
        w = self.w
        self.assertEqual(w.keeper.status_fields(), {})
        self.assertEqual(w.keeper.with_segment({"ok": True, "status": {"active": False}}), {"ok": True, "status": {"active": False}})
        rid = w.start()
        reply = w.keeper.with_segment({"ok": True, "status": {"active": True}})
        self.assertEqual(reply["autonomy_segment"]["recording_id"], rid)
        self.assertEqual(reply["status"]["autonomy_segment"]["recording_id"], rid, "also where the page keeps the recorder status")
        self.assertEqual(w.keeper.decorate({"ok": False}), {"ok": False})


try:
    from dashboard_server import app as app_module  # needs Linux (fcntl)
except ImportError:  # pragma: no cover - Windows
    app_module = None


@unittest.skipIf(app_module is None, "the dashboard server imports only on Linux")
class RecordingRoutesTest(unittest.TestCase):
    """The routes of dashboard_server/app.py with a fake recorder (provider) and a fake executor client."""

    def setUp(self):
        import http.cookiejar
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        archive = root / "recordings"
        archive.mkdir()
        test = self
        self.executor = {"run": 2, "run_state": "armed", "operator_link": {"go_active": False},
                         "metrics": {"run_id": 2, "time_s": 0.0, "distance_m": 0.0}, "single_row": {"distance_m": 0.0}}
        self.recorder = {"active": None, "count": 0, "regenerated": []}

        class Provider:
            recording_archive_dir = archive

            def start_recording(self, payload):
                test.recorder["count"] += 1
                rid = "20261120T01000%dZ_both" % test.recorder["count"]
                (archive / rid).mkdir()
                test.recorder["active"] = {"id": rid, "tag": payload.get("tag") or "manual"}
                return {"ok": True, "state": "recording", "recording_id": rid, "status": self.recording_status()}

            def stop_recording(self):
                active, test.recorder["active"] = test.recorder["active"], None
                if not active:
                    return {"ok": True, "state": "idle", "status": self.recording_status()}
                return {"ok": True, "state": "archived", "recording_id": active["id"], "status": self.recording_status()}

            def recording_status(self):
                active = test.recorder["active"]
                if active:
                    return {"ok": True, "active": True, "state": "recording", "recording_id": active["id"], "tag": active["tag"], "status_fresh": True}
                return {"ok": True, "active": False, "state": "idle", "status_fresh": True}

            def recordings(self):
                return {"ok": True, "recordings": [{"id": path.name, "tag": "autonomy"} for path in sorted(archive.iterdir(), reverse=True)]}

        class Mapping:
            def regenerate(self, recording_id):
                test.recorder["regenerated"].append(recording_id)

        class Client:
            def call(self, method, path, body=None, timeout=2.0):
                return 200, test.executor

            def status(self, grid=False):
                return {"ok": True, "configured": True, "status": {"run_state": test.executor["run_state"]}}

            def action(self, name, body):
                if name == "start":
                    return 200, {"ok": True, "run_id": 2, "gnss_mode": body.get("gnss_mode")}
                if name == "go":
                    test.executor["run_state"] = "running"
                    return 200, {"ok": True, "run_id": 2, "go_serial": 5}
                return 409, {"ok": False, "error": "refused"}

        handler = app_module.DashboardHandler
        names = ("provider", "archive_mapping", "executor_client", "run_recordings")
        saved = {name: handler.__dict__.get(name, None) for name in names}
        handler.provider, handler.archive_mapping, handler.executor_client = Provider(), Mapping(), Client()
        handler.run_recordings = rr.RunRecordings(archive, root / "runtime" / "autonomy_segments", app_module.executor_status_raw,
                                                  lambda: handler.provider.recording_status(), app_module.stop_recording_now)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(2)
            for name, value in saved.items():
                setattr(handler, name, value)
        self.addCleanup(close)
        self.base = "http://127.0.0.1:%d" % server.server_address[1]
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.archive = archive

    def call(self, path, body=None):
        import urllib.error
        import urllib.request
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "Origin": self.base} if body is not None else {}
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST" if body is not None else "GET")
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"null")

    def test_start_go_stop_through_the_routes(self):
        self.assertEqual(self.call("/api/recordings/status")[1].get("autonomy_segment"), None, "no stretch yet (and the session cookie)")
        self.assertEqual(self.call("/api/executor/start", {"row_len_m": 30, "gnss_mode": "off"})[0], 200)
        code, started = self.call("/api/recordings/start", {"sensors": ["lidar", "camera"], "tag": "autonomy", "kind": "both"})
        rid = started["recording_id"]
        self.assertEqual((code, started["ok"], started["autonomy_segment"]["recording_id"], started["status"]["autonomy_segment"]["segment"]),
                         (200, True, rid, 1))
        record = json.loads((self.archive / rid / rr.FILE_NAME).read_bytes().decode("utf-8"))
        self.assertEqual((record["run"], record["resumed"], record["settings"]["row_len_m"], record["settings"]["gnss_mode"]), (2, False, 30, "off"))
        self.assertEqual(self.call("/api/executor/go", {"session": "abcd1234"})[1]["go_serial"], 5)
        status = self.call("/api/recordings/status")[1]
        self.assertEqual((status["active"], status["autonomy_segment"]["go"]["serial"]), (True, 5), "the go of the relay is in the record")
        self.executor.update(run_state="paused", stop_reason="OPERATOR_LINK_LOST", stop_detail="operator_link_lost")
        self.executor["metrics"].update(time_s=12.5, distance_m=4.0)
        self.executor["single_row"]["distance_m"] = 4.0
        code, stopped = self.call("/api/recordings/stop", {})
        self.assertEqual((code, stopped["ok"], stopped["state"], stopped["recording_id"]), (200, True, "archived", rid))
        self.assertEqual(self.recorder["regenerated"], [rid], "the archive map of the recording is made as before")
        self.assertEqual((stopped["autonomy_segment"]["drive_time_s"], stopped["autonomy_segment"]["end"]["text"]), (12.5, "화면 연결 끊김"))
        listing = self.call("/api/recordings")[1]["recordings"]
        self.assertEqual((listing[0]["id"], listing[0]["segment"]["segment"], listing[0]["segment"]["end_text"]), (rid, 1, "화면 연결 끊김"))
        # a manual recording: the plain routes, no record
        manual = self.call("/api/recordings/start", {"sensors": ["lidar"], "tag": "manual", "kind": "lidar"})[1]["recording_id"]
        self.assertFalse((self.archive / manual / rr.FILE_NAME).exists())
        self.assertEqual(self.call("/api/recordings/stop", {})[1]["recording_id"], manual)
        self.assertEqual(self.call("/api/recordings/stop", {})[1]["state"], "idle", "a stop with nothing running is the recorder's answer")


if __name__ == "__main__":
    unittest.main()
