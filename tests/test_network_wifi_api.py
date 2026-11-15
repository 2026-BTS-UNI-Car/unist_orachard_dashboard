"""Wi-Fi management of the dashboard: WifiManager (checks, caches, limits), the /api/network/wifi routes
(session and Origin, body size, no password in answers or logs) and the helper call (request on stdin,
never on the command line). The route and helper-call parts need the Linux server modules."""
import importlib.util
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from dashboard_server.wifi_manager import CONNECT_CONFIRM, SCAN_MIN_INTERVAL_S, WifiManager, http_status

LINUX_SERVER = importlib.util.find_spec("termios") is not None and importlib.util.find_spec("fcntl") is not None
PASSWORD = "fake-password-77"
SAVED_UUID = "22222222-2222-4222-8222-222222222222"


class FakeHelper:
    def __init__(self):
        self.calls = []
        self.answers = {
            "wifi-saved": {"ok": True, "profiles": [{"uuid": SAVED_UUID, "name": "Field-5G", "ssid": "Field-5G"}]},
            "wifi-scan": {"ok": True, "networks": [{"ssid": "New Net"}], "hidden_count": 0, "scanned_at": 1.0, "age_s": 0.0},
            "wifi-connect": {"ok": True, "state": "accepted", "txn": "0123456789abcdef", "ssid": "New Net"},
            "wifi-confirm": {"ok": True, "state": "accepted"},
            "wifi-revert": {"ok": True, "state": "accepted"},
            "wifi-profile": {"ok": True, "state": "updated"},
        }

    def __call__(self, command, payload=None):
        self.calls.append((command, json.loads(json.dumps(payload))))
        return dict(self.answers[command])


class Clock:
    value = 100.0

    def __call__(self):
        return self.value


class WifiManagerTests(unittest.TestCase):
    def setUp(self):
        self.helper = FakeHelper()
        self.clock = Clock()
        self.enabled = True
        self.wifi = WifiManager(self.helper, lambda: self.enabled, clock=self.clock)

    def connect(self, **extra):
        payload = {"confirm": CONNECT_CONFIRM, "ssid": "New Net", "password": PASSWORD, "autoconnect": True, "priority": 10}
        payload.update(extra)
        return self.wifi.connect(payload)

    def test_disabled_does_nothing(self):
        self.enabled = False
        self.assertEqual(self.wifi.overview()["state"], "disabled")
        self.assertEqual(self.wifi.scan()["state"], "disabled")
        self.assertEqual(self.connect()["state"], "disabled")
        self.assertEqual(self.helper.calls, [])

    def test_overview_caches_the_saved_list_and_never_scans(self):
        self.wifi.overview()
        self.wifi.overview()
        self.assertEqual([call[0] for call in self.helper.calls], ["wifi-saved"])
        self.clock.value += 11
        self.wifi.overview()
        self.wifi.overview(refresh_saved=True)
        self.assertEqual([call[0] for call in self.helper.calls], ["wifi-saved"] * 3)

    def test_scan_is_limited_for_all_viewers(self):
        self.assertTrue(self.wifi.scan()["ok"])
        limited = self.wifi.scan()
        self.assertEqual(limited["state"], "rate_limited")
        self.assertEqual(limited["scan"]["networks"], [{"ssid": "New Net"}])
        self.clock.value += SCAN_MIN_INTERVAL_S + 0.1
        self.assertTrue(self.wifi.scan()["ok"])
        self.assertEqual([call[0] for call in self.helper.calls], ["wifi-scan", "wifi-scan"])
        self.assertEqual(self.wifi.overview()["scan"]["networks"], [{"ssid": "New Net"}])

    def test_refused_scan_does_not_use_up_the_interval(self):
        self.helper.answers["wifi-scan"] = {"ok": False, "state": "scan_not_available"}
        self.assertFalse(self.wifi.scan()["ok"])
        self.assertEqual(self.wifi.scan()["state"], "scan_not_available")
        self.assertEqual(len(self.helper.calls), 2)

    def test_connect_passes_only_known_fields_and_drops_the_password(self):
        result = self.connect(extra_field="x")
        self.assertTrue(result["ok"])
        command, payload = self.helper.calls[-1]
        self.assertEqual(command, "wifi-connect")
        self.assertEqual(payload, {"ssid": "New Net", "password": PASSWORD, "autoconnect": True, "priority": 10})
        self.assertNotIn(PASSWORD, json.dumps(result))
        self.assertNotIn(PASSWORD, repr(self.wifi.__dict__))

    def test_connect_checks(self):
        cases = [
            ({"confirm": "yes"}, "confirmation_required"),
            ({"ssid": "", "uuid": None}, "ssid_invalid"),
            ({"ssid": "x" * 33}, "ssid_invalid"),
            ({"uuid": "../etc"}, "uuid_invalid"),
            ({"password": "short"}, "password_invalid"),
            ({"password": "비밀번호비밀번호"}, "password_invalid"),
            ({"priority": True}, "priority_invalid"),
            ({"priority": 1000}, "priority_invalid"),
            ({"autoconnect": "yes"}, "autoconnect_boolean"),
        ]
        for extra, code in cases:
            with self.subTest(code=code):
                result = self.connect(**extra)
                self.assertFalse(result["ok"])
                self.assertIn(code, (result.get("error"), result.get("state")))
        self.assertEqual(self.helper.calls, [])
        self.assertEqual(http_status({"ok": False, "state": "bad_request"}), 400)

    def test_saved_connect_without_password_and_rate_limit(self):
        self.assertTrue(self.wifi.connect({"confirm": CONNECT_CONFIRM, "uuid": SAVED_UUID.upper(), "password": ""})["ok"])
        self.assertEqual(self.helper.calls[-1][1], {"uuid": SAVED_UUID, "autoconnect": True, "priority": 0})
        limited = self.connect()
        self.assertEqual(limited["state"], "rate_limited")
        self.assertEqual(http_status(limited), 429)

    def test_trial_and_profile_requests(self):
        self.assertEqual(self.wifi.confirm({"txn": "nope"})["error"], "txn_invalid")
        self.assertTrue(self.wifi.confirm({"txn": "0123456789abcdef"})["ok"])
        self.assertTrue(self.wifi.revert({"txn": "0123456789abcdef"})["ok"])
        self.assertTrue(self.wifi.update_profile({"uuid": SAVED_UUID, "autoconnect": False, "priority": -5})["ok"])
        self.assertEqual(self.helper.calls[-1], ("wifi-profile", {"uuid": SAVED_UUID, "autoconnect": False, "priority": -5}))
        self.assertEqual(self.wifi.update_profile({"uuid": SAVED_UUID, "autoconnect": False, "priority": -5})["state"], "rate_limited")
        self.assertEqual(self.wifi.update_profile({"uuid": SAVED_UUID, "autoconnect": 1, "priority": 0})["error"], "autoconnect_boolean")


@unittest.skipUnless(LINUX_SERVER, "the dashboard server modules need Linux (termios, fcntl)")
class WifiRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dashboard_server import app as app_module
        cls.app_module = app_module
        cls.helper = FakeHelper()

        class Provider:
            wifi = WifiManager(cls.helper, lambda: True)
            mode_calls = []

            def set_network_mode(self, payload):
                self.mode_calls.append(payload)
                return {"ok": True, "state": "accepted"}

        class Handler(app_module.DashboardHandler):
            provider = Provider()

        cls.provider = Handler.provider
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, path, payload=None, cookie=None, origin=None, raw=None):
        body = raw if raw is not None else (None if payload is None else json.dumps(payload).encode("utf-8"))
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body, headers=headers,
                                         method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as res:
                return res.status, json.loads(res.read().decode("utf-8")), res.headers
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(text), exc.headers
            except json.JSONDecodeError:
                return exc.code, text, exc.headers

    def cookie(self):
        status, payload, headers = self.request("/api/network/wifi")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        return headers.get("Set-Cookie").split(";", 1)[0]

    def test_mutating_routes_need_a_session_and_the_same_origin(self):
        for path in ("/api/network/wifi/scan", "/api/network/wifi/connect", "/api/network/wifi/confirm",
                     "/api/network/wifi/revert", "/api/network/wifi/profile", "/api/network/mode"):
            with self.subTest(path=path):
                status, _, _ = self.request(path, {"confirm": CONNECT_CONFIRM})
                self.assertEqual(status, 403)
        cookie = self.cookie()
        status, _, _ = self.request("/api/network/wifi/scan", {}, cookie=cookie, origin="http://evil.example")
        self.assertEqual(status, 403)
        status, _, _ = self.request("/api/network/mode", {"action": "ap"}, cookie=cookie, origin="http://evil.example")
        self.assertEqual(status, 403)
        self.assertEqual(self.provider.mode_calls, [])
        status, payload, _ = self.request("/api/network/mode", {"action": "ap"}, cookie=cookie,
                                          origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(status, 200)
        self.assertEqual(self.provider.mode_calls, [{"action": "ap"}])

    def test_connect_answer_and_log_carry_no_password(self):
        cookie = self.cookie()
        events = []
        with mock.patch.object(self.app_module, "dashboard_log", lambda event, **fields: events.append((event, fields))):
            status, payload, _ = self.request("/api/network/wifi/connect", {
                "confirm": CONNECT_CONFIRM, "ssid": "New Net", "password": PASSWORD, "autoconnect": True, "priority": 3,
            }, cookie=cookie, origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["txn"], "0123456789abcdef")
        self.assertNotIn(PASSWORD, json.dumps(payload))
        self.assertNotIn(PASSWORD, repr(events))
        self.assertEqual(self.helper.calls[-1][1]["password"], PASSWORD)

    def test_bad_requests(self):
        cookie = self.cookie()
        status, _, _ = self.request("/api/network/wifi/unknown", {}, cookie=cookie)
        self.assertEqual(status, 404)
        status, _, _ = self.request("/api/network/wifi/connect", raw=b"{" + b" " * 5000 + b"}", cookie=cookie)
        self.assertEqual(status, 413)
        status, payload, _ = self.request("/api/network/wifi/profile", {"uuid": "x", "autoconnect": True, "priority": 0}, cookie=cookie)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "uuid_invalid")


@unittest.skipUnless(LINUX_SERVER, "the dashboard server modules need Linux (termios, fcntl)")
class HelperCallTests(unittest.TestCase):
    def test_request_goes_on_stdin_with_its_own_time_limit(self):
        import tempfile
        from dashboard_server import telemetry
        provider = telemetry.TelemetryProvider.__new__(telemetry.TelemetryProvider)
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "ubts-network-mode"
            helper.write_text("#!/bin/sh\n", encoding="utf-8")
            provider.network_mode_helper = str(helper)
            provider.host_root = Path("/")
            seen = {}

            def fake_run(argv, **kwargs):
                seen.update(argv=argv, **kwargs)
                class Done:
                    returncode = 0
                    stdout = '{"ok":true,"state":"accepted","txn":"0123456789abcdef"}\n'
                    stderr = ""
                return Done()

            with mock.patch.object(telemetry.subprocess, "run", fake_run):
                result = provider._network_mode_command("wifi-connect", {"ssid": "New Net", "password": PASSWORD})
        self.assertTrue(result["ok"])
        self.assertEqual(seen["argv"], [str(helper), "wifi-connect", "--json"])
        self.assertNotIn(PASSWORD, " ".join(seen["argv"]))
        self.assertEqual(json.loads(seen["input"])["password"], PASSWORD)
        self.assertEqual(seen["timeout"], telemetry.TelemetryProvider.NETWORK_HELPER_TIMEOUTS["wifi-connect"])
        self.assertEqual(provider._network_mode_command("rm -rf /")["state"], "bad_command")


if __name__ == "__main__":
    unittest.main()
