"""Tuning values of the executor through the dashboard (dashboard_server/steer_tuning.py, UI-12,
docs/STEERING_SERVER_KO.md 7): groups and locks, stopped-only, range checks before the executor, the change log, and
the dashboard routes on Linux. Uses the fake executor of tests/test_executor_proxy.py. Temporary folders only."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from dashboard_server import steer_tuning as tuning
from dashboard_server.executor_proxy import ExecutorClient, ExecutorError

try:
    import test_executor_proxy as fake_module          # unittest discover -s tests
except ImportError:                                    # run from the repository root
    from tests import test_executor_proxy as fake_module


def status(run_state="idle", **tune):
    return {"ok": True, "configured": True, "reachable": True, "received_at": 1.0,
            "status": {"run_state": run_state, "tuning": {"preset": "p", "revision": 3, "editable": True, **tune}}}


class ViewTest(unittest.TestCase):
    def test_groups_locks_and_values(self):
        view = tuning.view(status(values={"target_speed_m_s": 0.35, "operator_timeout_s": 0.5, "lookahead_m": float("nan")}))
        by = {p["name"]: p for p in view["params"]}
        self.assertEqual(view["schema"], "ubts.executor_tuning.v0")
        self.assertTrue(view["applicable"])
        self.assertEqual([n for n, p in by.items() if p["group"] == "basic"],
                         ["speed_table_scale", "target_speed_m_s", "lookahead_m", "lateral_gain", "heading_gain",
                          "steer_rate_limit_deg_s"], "decision 16: the basic group")
        self.assertTrue(all(p["editable"] for p in by.values() if p["group"] == "basic"))
        self.assertFalse(any(p["editable"] or p["editable_when_stopped"] for p in by.values() if p["group"] != "basic"))
        self.assertEqual((by["target_speed_m_s"]["value"], by["lookahead_m"]["value"]), (0.35, None))
        self.assertEqual(by["operator_timeout_s"]["value"], 0.5)
        self.assertTrue(all(p["source"] for p in view["params"]), "every value has a source")
        for p in view["params"]:
            self.assertLess(p["min"], p["max"], p["name"])

    def test_executor_names_and_limits_win_but_safety_stays_locked(self):
        view = tuning.view(status(editable_names=["min_speed_m_s", "operator_timeout_s"],
                                  limits={"target_speed_m_s": [0.25, 0.45], "lookahead_m": [3, 1]}))
        by = {p["name"]: p for p in view["params"]}
        self.assertTrue(by["min_speed_m_s"]["editable"])
        self.assertFalse(by["target_speed_m_s"]["editable"], "not in the executor's list")
        self.assertFalse(by["operator_timeout_s"]["editable"], "safety limits are never editable on the screen")
        self.assertEqual((by["target_speed_m_s"]["min"], by["target_speed_m_s"]["max"]), (0.25, 0.45))
        self.assertEqual((by["lookahead_m"]["min"], by["lookahead_m"]["max"]), (1.0, 4.0), "a reversed range is ignored")

    def test_only_while_stopped(self):
        for state in ("idle", "ready", "ended"):
            self.assertTrue(tuning.view(status(state))["applicable"], state)
        for state in ("armed", "running", "paused", "latched", None):
            view = tuning.view(status(state))
            self.assertFalse(view["applicable"], state)
            self.assertFalse(any(p["editable"] for p in view["params"]))
        self.assertFalse(tuning.view(status(editable=False))["applicable"])
        offline = tuning.view({"ok": False, "configured": False, "error": "executor_not_configured"})
        self.assertEqual((offline["reachable"], offline["configured"], offline["applicable"]), (False, False, False))
        self.assertEqual(len(offline["params"]), len(tuning.CATALOG))

    def test_validate(self):
        view = tuning.view(status())
        self.assertEqual(tuning.validate({"target_speed_m_s": 0.3, "lookahead_m": 2}, view), {"target_speed_m_s": 0.3, "lookahead_m": 2})
        cases = [({}, 400, "no_change"), ({"nope": 1}, 400, "unknown_parameter"),
                 ({"operator_timeout_s": 1.0}, 400, "locked_parameter"), ({"min_speed_m_s": 0.2}, 400, "locked_parameter"),
                 ({"target_speed_m_s": 0.6}, 400, "out_of_range"), ({"target_speed_m_s": "0.3"}, 400, "out_of_range"),
                 ({"target_speed_m_s": True}, 400, "out_of_range"), ({"target_speed_m_s": float("inf")}, 400, "out_of_range")]
        for body, code, error in cases:
            with self.subTest(body=body), self.assertRaises(ExecutorError) as caught:
                tuning.validate(body, view)
            self.assertEqual((caught.exception.status, caught.exception.error), (code, error))
        with self.assertRaises(ExecutorError) as caught:
            tuning.validate({"target_speed_m_s": 0.3}, tuning.view(status("running")))
        self.assertEqual((caught.exception.status, caught.exception.error), (409, "not_stopped"))

    def test_log_keeps_the_last_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            log = tuning.TuningLog(Path(folder) / "log" / "tuning.jsonl")
            self.assertEqual(log.recent(), [])
            for index in range(tuning.LOG_KEEP + 5):
                log.append({"by": "abcd", "changes": {"lookahead_m": [index, index + 1]}, "ok": True})
            lines = (Path(folder) / "log" / "tuning.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), tuning.LOG_KEEP)
            recent = log.recent()
            self.assertEqual(len(recent), tuning.LOG_SHOW)
            self.assertEqual(recent[0]["changes"]["lookahead_m"], [tuning.LOG_KEEP + 4, tuning.LOG_KEEP + 5], "newest first")


class ApplyTest(fake_module.FakeExecutorCase):
    def test_apply_logs_old_and_new_values_and_revisions(self):
        log = tuning.TuningLog(Path(self.tmp.name) / "tuning.jsonl")
        code, reply = tuning.apply(self.client, {"target_speed_m_s": 0.35}, log, "sess1234")
        self.assertEqual((code, reply["ok"]), (200, True))
        self.assertEqual(self.fake.tuning["values"]["target_speed_m_s"], 0.35)
        entry = log.recent()[0]
        self.assertEqual((entry["by"], entry["ok"], entry["changes"], entry["revision_before"], entry["revision_after"]),
                         ("sess1234", True, {"target_speed_m_s": [0.4, 0.35]}, 1, 2))
        with self.assertRaises(ExecutorError):
            tuning.apply(self.client, {"target_speed_m_s": 9}, log, "sess1234")
        self.assertEqual((log.recent()[0]["ok"], log.recent()[0]["error"]), (False, "out_of_range"))
        self.assertEqual(sum(1 for m, path, _ in self.fake.calls if path == "/api/params"), 1, "refused locally: not sent")
        self.fake.state["run_state"] = "running"
        with self.assertRaises(ExecutorError) as caught:
            tuning.apply(self.client, {"target_speed_m_s": 0.3}, log, "sess1234")
        self.assertEqual(caught.exception.error, "not_stopped")


@unittest.skipIf(fake_module.app_module is None, "the dashboard server imports only on Linux")
class RouteTest(fake_module.DashboardRouteTest):
    def test_dashboard_relays_with_session_and_origin(self):   # covered in test_executor_proxy
        pass

    def test_without_an_executor(self):                        # covered in test_executor_proxy
        pass

    def test_tuning_routes(self):
        log_path = Path(self.tmp.name) / "tuning.jsonl"
        base, page = self.serve({"UBTS_EXECUTOR_URL": "http://%s" % self.fake.host_header,
                                 "UBTS_EXECUTOR_TOKEN_FILE": str(self.token_file), "UBTS_EXECUTOR_TUNING_LOG": str(log_path)})
        status, view = self.call(page, base + "/api/executor/tuning")
        self.assertEqual((status, view["applicable"], view["tuning"]["revision"]), (200, True, 1))
        status, reply = self.call(page, base + "/api/executor/params", {"lookahead_m": 2.5})
        self.assertEqual((status, reply["ok"]), (200, True))
        status, reply = self.call(page, base + "/api/executor/params", {"lookahead_m": 99})
        self.assertEqual((status, reply["error"], reply["field"]), (400, "out_of_range", "lookahead_m"))
        status, log = self.call(page, base + "/api/executor/tuning/log")
        self.assertEqual([entry["ok"] for entry in log["entries"]], [False, True])
        self.assertEqual(log["entries"][1]["changes"], {"lookahead_m": [2.0, 2.5]})
        stranger = urllib.request.build_opener()
        self.assertEqual(self.call(stranger, base + "/api/executor/params", {"lookahead_m": 2.0})[0], 403)


if __name__ == "__main__":
    unittest.main()
