"""Field tuning of the executor through the dashboard (dashboard_server/steer_tuning.py, UI-12,
docs/STEERING_SERVER_KO.md 7). The executor is the reference (field_tuning.py of the simulation repository): the screen
draws the bundle the executor sends, the dashboard checks against the executor's own ranges, sends one of its forms
(basic keys, a preset, a save), passes its refusals through and keeps a change log. The real status sample is
tests/fixtures/executor_status_front_steer.json; the fake executor of tests/test_executor_proxy.py answers like
field_tuning. Temporary folders only."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

from dashboard_server import steer_tuning as tuning
from dashboard_server.executor_proxy import ExecutorError, page_status

try:
    import test_executor_proxy as fake_module          # unittest discover -s tests
except ImportError:                                    # run from the repository root
    from tests import test_executor_proxy as fake_module

SAMPLE = fake_module.SAMPLE
BASIC = ["speed_scale", "target_speed_m_s", "lookahead_base_m", "lookahead_per_speed_s", "lookahead_max_m", "k_d", "k_psi",
         "steer_rate_fraction"]


def status(run_state="idle", **tune):
    """What GET /api/executor/status gives: the real sample through the relay filter, tuning fields replaced."""
    raw = copy.deepcopy(SAMPLE)
    raw["run_state"] = run_state
    raw["tuning"].update(tune)
    return {"ok": True, "configured": True, "reachable": True, "received_at": 1.0, "status": page_status(raw)}


class ViewTest(unittest.TestCase):
    def test_the_bundle_comes_from_the_executor(self):
        view = tuning.view(status())
        by = {p["name"]: p for p in view["params"]}
        self.assertEqual((view["schema"], view["available"], view["applicable"], view["why_not"]),
                         ("ubts.executor_tuning.v1", True, True, None))
        self.assertEqual([p["name"] for p in view["params"] if p["group"] == "basic"], BASIC)
        speed = by["target_speed_m_s"]
        self.assertEqual((speed["label"], speed["unit"], speed["min"], speed["max"], speed["value"], speed["editable"]),
                         ("목표 속도", "m/s", 0.2, 0.5, 0.4, True))
        self.assertEqual((by["k_d"]["unit"], by["k_d"]["min"], by["k_d"]["max"]), ("", 0.0, 3.0))
        advanced = [p["name"] for p in view["params"] if p["group"] == "advanced"]
        locked = [p["name"] for p in view["params"] if p["group"] == "locked"]
        self.assertEqual(advanced, list(SAMPLE["tuning"]["values"]["advanced"]))
        self.assertEqual(locked, list(SAMPLE["tuning"]["values"]["locked"]))
        self.assertFalse(any(p["editable"] or p["editable_when_stopped"] for p in view["params"] if p["group"] != "basic"))
        self.assertEqual(by["speed_table"]["value"], SAMPLE["tuning"]["values"]["advanced"]["speed_table"])
        self.assertEqual((by["speed_table"]["unit"], by["lateral_accel_max_m_s2"]["unit"], by["speed_kp_pct_s_per_m"]["unit"],
                          by["duty_rise_pct_s"]["unit"], by["guard_heading_deg"]["unit"], by["overspeed_s"]["unit"]),
                         ("[duty %, m/s]", "m/s²", "%/(m/s)", "%/s", "°", "s"))
        self.assertTrue(all(p["label"] != p["name"] for p in view["params"]), "every key of the sample has a Korean label")
        self.assertEqual(view["tuning"], {"preset": "default", "revision": 0, "modified": False,
                                          "presets": ["default", "slow_030", "soft_ground"], "writable": False, "unsaved": False})

    def test_a_new_key_of_the_executor_shows_by_its_name(self):
        basic = SAMPLE["tuning"]["basic"] + [{"name": "new_gain", "value": 2, "low": 0, "high": 50, "unit": "x"}]
        by = {p["name"]: p for p in tuning.view(status(basic=basic))["params"]}
        self.assertEqual((by["new_gain"]["label"], by["new_gain"]["unit"], by["new_gain"]["step"], by["new_gain"]["editable"]),
                         ("new_gain", "x", 1, True))

    def test_the_executor_decides_when_it_can_change(self):
        for why in ("tuning_locked_while_running", "tuning_locked_while_armed", "tuning_locked_vehicle_moving"):
            view = tuning.view(status("running", editable=False, not_editable_because=why))
            self.assertEqual((view["applicable"], view["why_not"]), (False, why))
            self.assertFalse(any(p["editable"] for p in view["params"]))
            self.assertTrue(all(p["editable_when_stopped"] for p in view["params"] if p["group"] == "basic"))
        no_tuning = {"ok": True, "configured": True, "status": {"run_state": "idle"}}
        view = tuning.view(no_tuning)
        self.assertEqual((view["available"], view["applicable"], view["why_not"], view["params"]), (False, False, "no_field_tuning", []))
        offline = tuning.view({"ok": False, "configured": False, "error": "executor_not_configured"})
        self.assertEqual((offline["reachable"], offline["configured"], offline["why_not"], offline["params"]),
                         (False, False, "executor_not_configured", []))

    def test_validate_follows_the_executor_forms_and_ranges(self):
        view = tuning.view(status(presets=["default", "slow_030"], writable=True))
        self.assertEqual(tuning.validate({"target_speed_m_s": 0.3, "k_d": 1.2}, view), {"target_speed_m_s": 0.3, "k_d": 1.2})
        self.assertEqual(tuning.validate({"preset": "slow_030"}, view), {"preset": "slow_030"})
        self.assertEqual(tuning.validate({"save": True}, view), {"save": True})
        self.assertEqual(tuning.validate({"save_preset": "wet_0.3", "note": "젖은 땅"}, view), {"save_preset": "wet_0.3", "note": "젖은 땅"})
        cases = [({}, 400, "no_change"), ({"nope": 1}, 400, "unknown_parameter"),
                 ({"operator_timeout_s": 1.0}, 400, "locked_parameter"), ({"min_speed_m_s": 0.2}, 400, "locked_parameter"),
                 ({"speed_table": [[25, 0.2]]}, 400, "locked_parameter"),
                 ({"target_speed_m_s": 0.6}, 400, "out_of_range"), ({"target_speed_m_s": "0.3"}, 400, "out_of_range"),
                 ({"target_speed_m_s": True}, 400, "out_of_range"), ({"k_psi": float("inf")}, 400, "out_of_range"),
                 ({"preset": "nope"}, 400, "unknown_preset"), ({"preset": "slow_030", "k_d": 1}, 400, "preset_alone"),
                 ({"save": False}, 400, "save_form"), ({"save": True, "k_d": 1}, 400, "save_form"),
                 ({"save_preset": "default"}, 400, "bad_preset_name"), ({"save_preset": "has space"}, 400, "bad_preset_name"),
                 ({"save_preset": "ok", "note": "x" * 201}, 400, "bad_note")]
        for body, code, error in cases:
            with self.subTest(body=body), self.assertRaises(ExecutorError) as caught:
                tuning.validate(body, view)
            self.assertEqual((caught.exception.status, caught.exception.error), (code, error))
        with self.assertRaises(ExecutorError) as caught:
            tuning.validate({"save": True}, tuning.view(status()))
        self.assertEqual((caught.exception.status, caught.exception.error), (409, "tuning_not_writable"))
        with self.assertRaises(ExecutorError) as caught:
            tuning.validate({"k_d": 1.0}, tuning.view(status("armed", editable=False, not_editable_because="tuning_locked_while_armed")))
        self.assertEqual((caught.exception.status, caught.exception.error), (409, "tuning_locked_while_armed"))

    def test_page_reply_drops_the_file_path(self):
        reply = tuning.page_reply({"ok": True, "action": "set", "changes": [{"key": "k_d", "old": 1.0, "new": 1.2, "x": 1}],
                                   "tuning": {"preset": "default*", "revision": 3, "file": "/home/x/f.json", "digest": "d"},
                                   "config_digest": "c"})
        self.assertEqual(reply, {"ok": True, "action": "set", "changes": [{"key": "k_d", "old": 1.0, "new": 1.2}],
                                 "tuning": {"preset": "default*", "revision": 3}})
        refused = tuning.page_reply({"ok": False, "error": "invalid_params", "problems": ["k_d: x"] * 30})
        self.assertEqual((refused["ok"], refused["error"], len(refused["problems"])), (False, "invalid_params", 20))

    def test_log_keeps_the_last_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            log = tuning.TuningLog(Path(folder) / "log" / "tuning.jsonl")
            self.assertEqual(log.recent(), [])
            for index in range(tuning.LOG_KEEP + 5):
                log.append({"by": "abcd", "changes": {"k_d": [index, index + 1]}, "ok": True})
            raw = (Path(folder) / "log" / "tuning.jsonl").read_bytes()
            self.assertNotIn(b"\r", raw)
            self.assertEqual(len(raw.decode("utf-8").splitlines()), tuning.LOG_KEEP)
            recent = log.recent()
            self.assertEqual(len(recent), tuning.LOG_SHOW)
            self.assertEqual(recent[0]["changes"]["k_d"], [tuning.LOG_KEEP + 4, tuning.LOG_KEEP + 5], "newest first")


class ApplyTest(fake_module.FakeExecutorCase):
    def test_apply_logs_old_and_new_values_and_revisions(self):
        log = tuning.TuningLog(Path(self.tmp.name) / "tuning.jsonl")
        code, reply = tuning.apply(self.client, {"target_speed_m_s": 0.35}, log, "sess1234")
        self.assertEqual((code, reply["ok"], reply["action"], reply["tuning"]), (200, True, "set", {"preset": "default*", "revision": 2}))
        self.assertEqual(self.fake.tuning["values"]["basic"]["target_speed_m_s"], 0.35)
        entry = log.recent()[0]
        self.assertEqual((entry["by"], entry["action"], entry["ok"], entry["changes"], entry["revision_before"], entry["revision_after"]),
                         ("sess1234", "set", True, {"target_speed_m_s": [0.4, 0.35]}, 1, 2))
        with self.assertRaises(ExecutorError):
            tuning.apply(self.client, {"target_speed_m_s": 9}, log, "sess1234")
        self.assertEqual((log.recent()[0]["ok"], log.recent()[0]["error"]), (False, "out_of_range"))
        self.assertEqual(sum(1 for m, path, _ in self.fake.calls if path == "/api/params"), 1, "refused locally: not sent")

    def test_presets_saves_and_executor_refusals(self):
        log = tuning.TuningLog(Path(self.tmp.name) / "tuning.jsonl")
        code, reply = tuning.apply(self.client, {"preset": "slow_030"}, log, "sess1234")
        self.assertEqual((code, reply["action"], log.recent()[0]["target"]), (200, "load_preset", "slow_030"))
        # the package default file: the dashboard refuses a save first (the executor would answer 400)
        with self.assertRaises(ExecutorError) as caught:
            tuning.apply(self.client, {"save": True}, log, "sess1234")
        self.assertEqual(caught.exception.error, "tuning_not_writable")
        self.fake.tuning["writable"] = True
        code, reply = tuning.apply(self.client, {"save_preset": "wet", "note": ""}, log, "sess1234")
        self.assertEqual((code, reply["action"], log.recent()[0]["action"]), (200, "save_preset", "save_preset"))
        # a rule the dashboard does not know (the executor's own checks): its 400 and problems reach the page
        self.fake.tuning["basic"][5]["high"] = 9.0
        self.fake.reject_over = 4.0
        code, reply = tuning.apply(self.client, {"k_d": 5.0}, log, "sess1234")
        self.assertEqual((code, reply["ok"], reply["error"]), (400, False, "invalid_params"))
        self.assertTrue(reply["problems"] and "k_d" in reply["problems"][0])
        entry = log.recent()[0]
        self.assertEqual((entry["ok"], entry["error"], entry["changes"]), (False, "invalid_params", {"k_d": [1.0, 5.0]}))
        self.assertNotIn("/home/x", json.dumps(log.recent()))
        self.fake.state["run_state"] = "running"
        with self.assertRaises(ExecutorError) as caught:
            tuning.apply(self.client, {"k_d": 1.1}, log, "sess1234")
        self.assertEqual((caught.exception.status, caught.exception.error), (409, "tuning_locked_while_running"))


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
        self.assertEqual([p["name"] for p in view["params"] if p["group"] == "basic"], BASIC)
        status, reply = self.call(page, base + "/api/executor/params", {"lookahead_base_m": 2.5})
        self.assertEqual((status, reply["ok"]), (200, True))
        status, reply = self.call(page, base + "/api/executor/params", {"lookahead_base_m": 99})
        self.assertEqual((status, reply["error"], reply["field"]), (400, "out_of_range", "lookahead_base_m"))
        status, reply = self.call(page, base + "/api/executor/params", {"preset": "soft_ground"})
        self.assertEqual((status, reply["action"]), (200, "load_preset"))
        status, log = self.call(page, base + "/api/executor/tuning/log")
        self.assertEqual([entry["ok"] for entry in log["entries"]], [True, False, True])
        self.assertEqual(log["entries"][2]["changes"], {"lookahead_base_m": [1.5, 2.5]})
        self.assertNotIn("/home/x", json.dumps(log))
        stranger = urllib.request.build_opener()
        self.assertEqual(self.call(stranger, base + "/api/executor/params", {"k_d": 1.0})[0], 403)


if __name__ == "__main__":
    unittest.main()
