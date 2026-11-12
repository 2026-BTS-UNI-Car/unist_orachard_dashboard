"""NET-11 / L7: a session in use slides, and its sensor streams are not cut when the
first lifetime ends (the bench saw all four streams close together at expiry)."""
import sys
import threading
import time
import unittest
import http.client
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard_server import app as dashboard_app  # noqa: E402
from dashboard_server.app import create_server  # noqa: E402
from tests.test_ws_relay_backpressure import Clock, Ready, Socket  # noqa: E402
from dashboard_server.ws_relay import relay_sensor_stream  # noqa: E402


class RelayDeadlineTest(unittest.TestCase):
    def test_a_deadline_function_is_read_again(self):
        clock = Clock()
        deadline = {"value": 1.0}

        def hook(ready, _readers, _writers):
            # While the relay waits for the old deadline the page uses its session:
            # the deadline moves to 2.0 s. At 1.0 s the relay reads it again and goes on.
            if ready.calls == 1:
                deadline["value"] = 2.0
        client, upstream = Socket(writable=False), Socket()
        result = relay_sensor_stream(client, upstream, clock=clock, wall_clock=clock,
                                     select_ready=Ready(clock, hook), session_deadline=lambda: deadline["value"])
        self.assertEqual(result.reason, "session_expired")
        self.assertAlmostEqual(clock.value, 2.0)

    def test_a_number_still_works(self):
        clock = Clock()
        result = relay_sensor_stream(Socket(writable=False), Socket(), clock=clock, wall_clock=clock,
                                     select_ready=Ready(clock), session_deadline=0.5)
        self.assertEqual(result.reason, "session_expired")
        self.assertAlmostEqual(clock.value, 0.5)


class SessionSlideTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def get(self, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", "/api/health", headers=headers or {})
            response = conn.getresponse()
            response.read()
            return response
        finally:
            conn.close()

    def test_a_used_session_is_extended_past_half_its_life(self):
        first = self.get()
        token = first.getheader("Set-Cookie").split(";", 1)[0].split("=", 1)[1]
        cookie = {"Cookie": f"{dashboard_app.SESSION_COOKIE_NAME}={token}"}
        with dashboard_app._SESSION_LOCK:
            created = dashboard_app._SESSIONS[token]["expires_at"]
        self.assertIsNone(self.get(cookie).getheader("Set-Cookie"), "early in its life: nothing to extend")
        with dashboard_app._SESSION_LOCK:
            dashboard_app._SESSIONS[token]["expires_at"] = time.time() + dashboard_app.SESSION_TTL_S / 2 - 5
        again = self.get(cookie)
        self.assertIsNotNone(again.getheader("Set-Cookie"), "the cookie is sent again with the new lifetime")
        with dashboard_app._SESSION_LOCK:
            extended = dashboard_app._SESSIONS[token]["expires_at"]
        self.assertGreater(extended, created - 1)
        self.assertGreater(extended, time.time() + dashboard_app.SESSION_TTL_S - 5)


if __name__ == "__main__":
    unittest.main()
