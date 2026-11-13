"""The dashboard's relay of the row executor's operator API (dashboard_server/executor_proxy.py, UI-08,
docs/STEERING_SERVER_KO.md 6). A fake executor checks the operator HTTP rules (exact Host, same Origin, X-Row-Token,
JSON body <= 4 KiB) and keeps a small run state of contract v0 (start, arm, go, heartbeat, stop, ack, end).
The executor status sample (tests/fixtures/executor_status_front_steer.json) is a real ExecutorLoop status of the
simulation repository (front_steer, one_press, SHADOW, field tuning and local grid; the tuning file path cut to its
name). No vehicle, no executor process: temporary folders only."""
from __future__ import annotations

import copy
import http.cookiejar
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from dashboard_server import executor_proxy as proxy
from dashboard_server.executor_proxy import ExecutorClient, ExecutorError, page_status, parse_action_body

SAMPLE = json.loads((Path(__file__).resolve().parent / "fixtures" / "executor_status_front_steer.json").read_text(encoding="utf-8"))
LOCKS = {"running": "tuning_locked_while_running", "paused": "tuning_locked_while_running", "armed": "tuning_locked_while_armed"}


class FakeExecutor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, token):
        super().__init__(("127.0.0.1", 0), FakeHandler)
        self.token = token
        self.host_header = "127.0.0.1:%d" % self.server_address[1]
        self.calls = []
        self.state = {"run_state": "idle", "mode": "MOTOR", "session": None}
        self.tuning = copy.deepcopy(SAMPLE["tuning"])           # the executor's field tuning (field_tuning.State.status)
        self.tuning["revision"] = 1
        self.reject_over = None            # a rule only the executor knows (its cross checks): refuse values above this
        self.extra_status = {}

    def status_body(self):
        state = self.state
        lock = LOCKS.get(state["run_state"])
        tuning = dict(self.tuning, editable=lock is None, not_editable_because=lock, file="/home/x/field_tuning.json")
        return {"run_state": state["run_state"], "mode": state["mode"], "go_ready": state["run_state"] == "armed",
                "operator_link": {"fresh": state["session"] is not None, "age_s": 0.05, "timeout_s": 0.5},
                "gnss": {"state": "fix", "lat": 35.1, "lon": 129.1, "h_acc_m": 4.2},
                "log": {"path": "/home/x/run.jsonl"}, "tuning": tuning, "local_grid_map": SAMPLE["local_grid_map"],
                **self.extra_status}

    def params(self, body):
        """POST /api/params of the executor's field tuning: (HTTP status, reply) (OPERATOR_API_KO.md 6)."""
        lock = LOCKS.get(self.state["run_state"])
        if lock:
            return 409, {"ok": False, "error": lock}
        tuning = self.tuning
        brief = {"preset": tuning["preset"], "revision": tuning["revision"], "digest": "d", "file": "/home/x/field_tuning.json",
                 "file_sha256": "s"}
        if "preset" in body:
            if body["preset"] not in tuning["presets"]:
                return 400, {"ok": False, "error": "invalid_params", "problems": ["preset %r: unknown" % body["preset"]]}
            tuning["preset"], tuning["modified"] = body["preset"], False
            tuning["revision"] += 1
            return 200, {"ok": True, "action": "load_preset", "changes": [], "tuning": dict(brief, preset=body["preset"]),
                         "config_digest": "c"}
        if "save" in body or "save_preset" in body:
            if not tuning["writable"]:
                return 400, {"ok": False, "error": "invalid_params",
                             "problems": ["save: the package default file is read only (configure tuning_file)"]}
            tuning["unsaved"] = False
            return 200, {"ok": True, "action": "save_preset" if "save_preset" in body else "save", "tuning": brief}
        basic = {row["name"]: row for row in tuning["basic"]}
        problems = ["%s: not a tuning key" % key for key in body if key not in basic]
        problems += ["%s: %r outside %s..%s" % (key, value, basic[key]["low"], basic[key]["high"]) for key, value in body.items()
                     if key in basic and not basic[key]["low"] <= value <= basic[key]["high"]]
        if self.reject_over is not None:
            problems += ["%s: %r breaks a controller rule" % (key, value) for key, value in body.items() if value > self.reject_over]
        if problems:
            return 400, {"ok": False, "error": "invalid_params", "problems": problems}
        changes = []
        for key, value in sorted(body.items()):
            if basic[key]["value"] != value:
                changes.append({"key": key, "old": basic[key]["value"], "new": value})
                basic[key]["value"] = tuning["values"]["basic"][key] = value
        tuning["revision"] += 1
        tuning["preset"] = tuning["preset"].rstrip("*") + "*"
        tuning["modified"] = tuning["unsaved"] = True
        return 200, {"ok": True, "action": "set", "changes": changes, "tuning": dict(brief, revision=tuning["revision"], preset=tuning["preset"]),
                     "config_digest": "c"}


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def checked(self):
        if self.headers.get("Host") != self.server.host_header:
            self.reply(421, {"ok": False, "error": "wrong_host"}); return False
        if self.headers.get("X-Row-Token") != self.server.token:
            self.reply(403, {"ok": False, "error": "forbidden"}); return False
        return True

    def do_GET(self):
        if not self.checked():
            return
        self.server.calls.append(("GET", self.path, None))
        self.reply(200, self.server.status_body())

    def do_POST(self):
        if not self.checked():
            return
        if self.headers.get("Origin") != "http://" + self.server.host_header:
            return self.reply(403, {"ok": False, "error": "origin"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4096:
            return self.reply(413, {"ok": False, "error": "body_size"})
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.calls.append(("POST", self.path, body))
        state = self.server.state
        route = self.path
        if route == "/api/start":
            state["run_state"] = "ready" if state["mode"] == "MOTOR" else "armed"
            return self.reply(200, {"ok": True, "run_state": state["run_state"], "status": self.server.status_body()})
        elif route == "/api/arm" and state["run_state"] == "ready":
            state["run_state"] = "armed"
        elif route == "/api/go":
            if state["run_state"] != "armed":
                return self.reply(409, {"ok": False, "error": "arm_first", "blockers": ["arm_first"]})
            state["run_state"], state["session"] = "running", body["session"]
        elif route == "/api/heartbeat":
            if state["session"] not in (None, body["session"]):
                return self.reply(409, {"ok": False, "error": "session_conflict"})
        elif route == "/api/stop":
            state["run_state"] = "latched"
        elif route == "/api/ack":
            state["run_state"] = "ready"
        elif route == "/api/end":
            state["run_state"] = "ended"
        elif route == "/api/params":
            return self.reply(*self.server.params(body))
        else:
            return self.reply(409, {"ok": False, "error": "refused"})
        self.reply(200, {"ok": True, "run_state": state["run_state"]})


class RelayUnitTest(unittest.TestCase):
    def test_status_keeps_only_the_screen_fields(self):
        raw = {"run_state": "running", "go_blockers": ["a", 3, "b"], "stop_detail": "x" * 500, "lidar_age": float("nan"),
               "gnss": {"state": "fix", "lat": 35.0, "lon": 129.0, "h_acc_m": 3.0}, "log": {"path": "/secret"},
               "single_row": {"phase": "FOLLOW", "confidence": {"level": "OK", "x": 1}, "geometry": [[1, 2]]},
               "tuning": {"revision": 4, "file": "/secret/field_tuning.json", "digest": "d", "secret": 1,
                          "basic": [{"name": "k_d", "value": 1.0, "low": 0.0, "high": 3.0, "unit": ""}, {"value": 2}, "x"],
                          "values": {"basic": {"k_d": 1.0}, "advanced": {"speed_table": [[25, 0.24], [80, "x"]]}, "other": {}}},
               "local_grid_map": {"schema": "ubts.row_executor.local_grid.v1", "bits": "AA=="}, "token": "t"}
        out = page_status(raw)
        self.assertEqual(out["run_state"], "running")
        self.assertEqual(out["go_blockers"], ["a", "b"])
        self.assertEqual(len(out["stop_detail"]), 200)
        self.assertIsNone(out["lidar_age"])
        self.assertEqual(out["gnss"], {"state": "fix", "h_acc_m": 3.0}, "no GNSS coordinates")
        self.assertEqual(out["single_row"], {"phase": "FOLLOW", "confidence": {"level": "OK"}})
        self.assertEqual(out["tuning"], {"revision": 4, "basic": [{"name": "k_d", "value": 1.0, "low": 0.0, "high": 3.0, "unit": ""}],
                                         "values": {"basic": {"k_d": 1.0}, "advanced": {"speed_table": [[25, 0.24]]}}})
        self.assertNotIn("local_grid_map", out, "the grid only for the map screen")
        self.assertEqual(page_status(raw, grid=True)["local_grid_map"], {"schema": "ubts.row_executor.local_grid.v1", "bits": "AA=="})
        for name in ("log", "token"):
            self.assertNotIn(name, out)
        self.assertEqual(page_status(None), {})

    def test_real_executor_status_sample(self):
        out = page_status(SAMPLE, grid=True)
        self.assertEqual((out["run_state"], out["start_mode"], out["mode"]), ("idle", "one_press", "SHADOW"))
        tuning = out["tuning"]
        self.assertEqual([row["name"] for row in tuning["basic"]],
                         ["speed_scale", "target_speed_m_s", "lookahead_base_m", "lookahead_per_speed_s", "lookahead_max_m",
                          "k_d", "k_psi", "steer_rate_fraction"], "field_tuning basic group, executor order")
        self.assertEqual(tuning["basic"][1], {"name": "target_speed_m_s", "value": 0.4, "low": 0.2, "high": 0.5, "unit": "m/s"})
        self.assertEqual(sorted(tuning["values"]), ["advanced", "basic", "locked"])
        self.assertEqual(tuning["values"]["advanced"]["speed_table"], SAMPLE["tuning"]["values"]["advanced"]["speed_table"])
        self.assertEqual(tuning["values"]["locked"], SAMPLE["tuning"]["values"]["locked"])
        self.assertEqual((tuning["presets"], tuning["writable"], tuning["editable"]), (["default", "slow_030", "soft_ground"], False, True))
        grid = out["local_grid_map"]
        self.assertEqual((grid["shape"], grid["cell_m"], grid["encoding"], grid["bits"]),
                         ([200, 100], 0.1, "bitmap", SAMPLE["local_grid_map"]["bits"]))
        self.assertEqual(sorted(grid["lines"]), ["centre", "left", "quality", "right", "stamp"])
        text = json.dumps(out)
        for secret in ("field_tuning.default.json", "file_sha256", "digest", "native_navigation"):
            self.assertNotIn(secret, text)
        broken = dict(SAMPLE["local_grid_map"], schema="other")
        self.assertNotIn("local_grid_map", page_status(dict(SAMPLE, local_grid_map=broken), grid=True))
        big = dict(SAMPLE["local_grid_map"], bits="A" * (proxy.MAX_GRID_BITS + 4), cells=[[1, 2]] * 3000, shape=[1, "x"])
        trimmed = page_status(dict(SAMPLE, local_grid_map=big), grid=True)["local_grid_map"]
        self.assertNotIn("bits", trimmed)
        self.assertNotIn("shape", trimmed)
        self.assertEqual(len(trimmed["cells"]), proxy.MAX_GRID_CELLS)

    def test_action_bodies(self):
        self.assertEqual(parse_action_body("start", {"row_len_m": None, "other": 1}), {"row_len_m": None})
        self.assertEqual(parse_action_body("start", {"row_len_m": 33.2}), {"row_len_m": 33.2})
        for bad in (0, -1, 2000, True, "12"):
            with self.subTest(row=bad), self.assertRaises(ExecutorError) as caught:
                parse_action_body("start", {"row_len_m": bad})
            self.assertEqual((caught.exception.status, caught.exception.error), (400, "invalid_row_len"))
        self.assertEqual(parse_action_body("go", {"session": "ab12", "x": 1}), {"session": "ab12"})
        for bad in ("abc", "x" * 65, "has space", None, 12):
            with self.subTest(session=bad), self.assertRaises(ExecutorError):
                parse_action_body("heartbeat", {"session": bad})
        self.assertEqual(parse_action_body("stop", {"anything": 1}), {})
        with self.assertRaises(ExecutorError):
            parse_action_body("stop", [])

    def test_url_and_configuration(self):
        self.assertIsNone(ExecutorClient.from_env({}))
        for url in ("https://10.77.0.1:8097", "http://10.77.0.1", "http://10.77.0.1:8097/page", "http://u:p@h:1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ExecutorClient(url, None)
        client = ExecutorClient.from_env({"UBTS_EXECUTOR_URL": "http://127.0.0.1:9", "UBTS_EXECUTOR_TOKEN_FILE": ""})
        self.assertEqual((client.origin, client.token_file), ("http://127.0.0.1:9", None))
        with self.assertRaises(ExecutorError) as caught:
            client.token()
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(proxy.not_configured_status()["configured"], False)


class FakeExecutorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ubts-exec-")
        self.token_file = Path(self.tmp.name) / "operator_token.txt"
        self.token_file.write_text("tok-" + "a" * 28 + "\n", encoding="utf-8")
        self.fake = FakeExecutor("tok-" + "a" * 28)
        self.thread = threading.Thread(target=self.fake.serve_forever, daemon=True)
        self.thread.start()
        self.client = ExecutorClient("http://%s" % self.fake.host_header, str(self.token_file))

    def tearDown(self):
        self.fake.shutdown(); self.fake.server_close(); self.thread.join(2)
        self.tmp.cleanup()


class RelayHttpTest(FakeExecutorCase):
    def test_one_press_flow_with_the_operator_rules(self):
        reply = self.client.status()
        self.assertEqual((reply["ok"], reply["status"]["run_state"]), (True, "idle"))
        self.assertNotIn("lat", reply["status"]["gnss"])
        self.assertNotIn("log", reply["status"])
        self.assertNotIn("local_grid_map", reply["status"])
        self.assertEqual(self.client.status(grid=True)["status"]["local_grid_map"]["shape"], [200, 100])
        code, started = self.client.action("start", {"row_len_m": None})
        self.assertEqual(code, 200)
        self.assertNotIn("log", started["status"], "a start reply's status passes the same filter")
        self.assertNotIn("file", started["status"]["tuning"])
        self.assertNotIn("local_grid_map", started["status"])
        self.assertEqual(self.client.action("arm", {})[0], 200)
        code, body = self.client.action("go", {"session": "s3ss10n"})
        self.assertEqual((code, body["run_state"]), (200, "running"))
        self.assertEqual(self.client.action("heartbeat", {"session": "s3ss10n"})[0], 200)
        self.assertEqual(self.client.action("heartbeat", {"session": "other1"}), (409, {"ok": False, "error": "session_conflict"}))
        self.assertEqual(self.client.action("stop", {})[1]["run_state"], "latched")
        self.assertEqual(self.client.action("ack", {})[1]["run_state"], "ready")
        self.assertEqual(self.client.action("end", {})[1]["run_state"], "ended")
        posts = [(path, body) for method, path, body in self.fake.calls if method == "POST"]
        self.assertEqual(posts[0], ("/api/start", {"row_len_m": None}))
        self.assertEqual(posts[2], ("/api/go", {"session": "s3ss10n"}))
        with self.assertRaises(ExecutorError) as caught:
            self.client.action("hold", {})
        self.assertEqual(caught.exception.status, 404)

    def test_refusal_token_and_unreachable(self):
        code, body = self.client.action("go", {"session": "abcd"})
        self.assertEqual((code, body["error"], body["blockers"]), (409, "arm_first", ["arm_first"]))
        # the executor restarted with a new token: the file changes and is read again
        self.fake.token = "tok-" + "b" * 28
        with self.assertRaises(ExecutorError) as caught:
            self.client.action("stop", {})
        self.assertEqual((caught.exception.status, caught.exception.error), (502, "executor_token_rejected"))
        time.sleep(0.02)
        self.token_file.write_text("tok-" + "b" * 28 + "\n", encoding="utf-8")
        os.utime(self.token_file, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
        self.assertEqual(self.client.action("stop", {})[0], 200)
        self.token_file.unlink()
        self.assertEqual(self.client.status()["error"], "executor_token_missing")
        away = ExecutorClient("http://127.0.0.1:9", str(self.token_file))
        self.token_file.write_text("x", encoding="utf-8")
        status = away.status()
        self.assertEqual((status["ok"], status["reachable"], status["error"]), (False, False, "executor_unreachable"))


try:
    from dashboard_server import app as app_module  # needs Linux (fcntl)
except ImportError:  # pragma: no cover - Windows
    app_module = None


@unittest.skipIf(app_module is None, "the dashboard server imports only on Linux")
class DashboardRouteTest(FakeExecutorCase):
    def serve(self, env):
        handler = app_module.DashboardHandler
        saved = {name: getattr(handler, name, None)
                 for name in ("provider", "dashboard_settings", "extension_routes", "page_filter", "executor_client", "tuning_log")}
        base = {key: value for key, value in os.environ.items() if not key.startswith(("DASHBOARD_", "UBTS_EXECUTOR_"))}
        base.update(env, DASHBOARD_LIVE_MAP_WORKER="0")
        with mock.patch.dict(os.environ, base, clear=True):
            server = app_module.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown(); server.server_close(); thread.join(2)
            for name, value in saved.items():
                setattr(handler, name, value)
            app_module.STATIC_ASSETS.set_page_filter(None)
        self.addCleanup(close)
        base_url = "http://127.0.0.1:%d" % server.server_address[1]
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        return base_url, opener

    def call(self, opener, url, body=None, origin=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
        try:
            with opener.open(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw)
            except ValueError:
                return error.code, None

    def test_dashboard_relays_with_session_and_origin(self):
        base, page = self.serve({"UBTS_EXECUTOR_URL": "http://%s" % self.fake.host_header,
                                 "UBTS_EXECUTOR_TOKEN_FILE": str(self.token_file)})
        status, reply = self.call(page, base + "/api/executor/status")       # also gives the session cookie
        self.assertEqual((status, reply["configured"], reply["status"]["run_state"]), (200, True, "idle"))
        self.assertNotIn("token", json.dumps(reply))
        self.assertNotIn("local_grid_map", reply["status"])
        status, reply = self.call(page, base + "/api/executor/status?grid=1")
        self.assertEqual((status, reply["status"]["local_grid_map"]["encoding"]), (200, "bitmap"))
        stranger = urllib.request.build_opener()
        self.assertEqual(self.call(stranger, base + "/api/executor/stop", {})[0], 403, "no session")
        self.assertEqual(self.call(page, base + "/api/executor/stop", {}, origin="http://evil.example")[0], 403)
        self.assertEqual(self.call(page, base + "/api/executor/start", {"row_len_m": None}, origin=base)[0], 200)
        self.assertEqual(self.call(page, base + "/api/executor/arm", {})[0], 200)
        self.assertEqual(self.call(page, base + "/api/executor/go", {"session": "bad session"})[0], 400)
        status, reply = self.call(page, base + "/api/executor/go", {"session": "abcd1234"})
        self.assertEqual((status, reply["run_state"]), (200, "running"))
        self.assertEqual(self.call(page, base + "/api/executor/heartbeat", {"session": "zzzz"})[0], 409)
        self.assertEqual(self.call(page, base + "/api/executor/hold", {})[0], 404)
        self.assertEqual(self.call(page, base + "/api/executor/stop", {})[1]["run_state"], "latched")
        config = self.call(page, base + "/api/dashboard/config")[1]
        self.assertIs(config["executor_configured"], True)

    def test_without_an_executor(self):
        base, page = self.serve({})
        status, reply = self.call(page, base + "/api/executor/status")
        self.assertEqual((status, reply["configured"], reply["error"]), (200, False, "executor_not_configured"))
        status, reply = self.call(page, base + "/api/executor/stop", {})
        self.assertEqual((status, reply["error"]), (503, "executor_not_configured"))
        self.assertIs(self.call(page, base + "/api/dashboard/config")[1]["executor_configured"], False)


if __name__ == "__main__":
    unittest.main()
