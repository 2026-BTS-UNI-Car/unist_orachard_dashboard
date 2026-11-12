"""Smaller status answers for the drive screen (NET-06).

- /api/telemetry?bundle=drive answers only sensors, services and the rover, and the
  full /api/telemetry is unchanged.
- A JSON GET answer carries an ETag; naming it in If-None-Match gives 304 without
  a body while the answer has not changed.
- The session cookie is sent when the session is created, not on every answer.
"""
import gzip
import http.client
import json
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard_server.app import create_server  # noqa: E402

DRIVE_KEYS = {"meta", "rover", "rover_source", "sensors", "services", "connection", "snapshot_age_s"}


class StatusRequestTest(unittest.TestCase):
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

    def get(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request("GET", path, headers=headers or {})
            response = conn.getresponse()
            body = response.read()
            if response.getheader("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return response, body
        finally:
            conn.close()

    def test_drive_bundle_has_only_what_the_drive_screen_uses(self):
        response, body = self.get("/api/telemetry?bundle=drive")
        self.assertEqual(response.status, 200)
        drive = json.loads(body)
        self.assertEqual(set(drive), DRIVE_KEYS)
        self.assertEqual(drive["meta"]["bundle"], "drive")
        self.assertEqual(set(drive["connection"]), {"rover"}, "the rover state is read again for every request")
        for key in ("imu", "lidar", "camera"):
            self.assertIn(key, drive["sensors"])
        for key in ("rover_bridge", "imu_bridge", "data_recorder"):
            self.assertIn(key, drive["services"])
        _, full_body = self.get("/api/telemetry")
        full = json.loads(full_body)
        for key in ("system", "network", "docker", "fan", "connection", "sensors", "services", "rover", "alerts"):
            self.assertIn(key, full, "the full answer is unchanged")
        self.assertIn("lidar_websocket", full["connection"])
        self.assertLess(len(gzip.compress(body, 1)), 1500, "the drive answer is under 1.5 kB compressed")

    def test_unchanged_json_answers_304(self):
        first, body = self.get("/api/health")
        etag = first.getheader("ETag")
        self.assertTrue(etag and etag.startswith('"j'))
        # /api/health has an uptime that changes; /api/docs does not.
        response, body = self.get("/api/docs")
        etag = response.getheader("ETag")
        again, again_body = self.get("/api/docs", {"If-None-Match": etag})
        self.assertEqual(again.status, 304)
        self.assertEqual(again_body, b"")
        self.assertEqual(again.getheader("ETag"), etag)
        other, other_body = self.get("/api/docs", {"If-None-Match": '"jnot-this-one"'})
        self.assertEqual(other.status, 200)
        self.assertEqual(json.loads(other_body), json.loads(body))

    def test_the_session_cookie_is_sent_once(self):
        first, _ = self.get("/api/health")
        cookie = first.getheader("Set-Cookie")
        self.assertTrue(cookie and "HttpOnly" in cookie)
        token = cookie.split(";", 1)[0]
        second, _ = self.get("/api/health", {"Cookie": token})
        self.assertIsNone(second.getheader("Set-Cookie"), "a known session gets no new cookie")
        third, _ = self.get("/api/health", {"Cookie": "ubts_session=unknown"})
        self.assertIsNotNone(third.getheader("Set-Cookie"), "an unknown session gets a new one")


if __name__ == "__main__":
    unittest.main()
