"""The link probe and the optional field link log (NET-10)."""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard_server.app import create_server  # noqa: E402


class LinkGradeApiTest(unittest.TestCase):
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

    def request(self, conn, method, path, body=None, headers=None):
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response, response.read()

    def test_ping_is_204_without_a_body_on_a_kept_connection(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        first, body = self.request(conn, "GET", "/api/ping")
        self.assertEqual((first.status, body), (204, b""))
        self.assertIn("app;dur=", first.getheader("Server-Timing"))
        self.assertEqual(first.getheader("Cache-Control"), "no-store")
        self.assertEqual(first.getheader("Connection"), "keep-alive")
        local = conn.sock.getsockname()
        second, _ = self.request(conn, "GET", "/api/ping")
        self.assertEqual(second.status, 204)
        self.assertEqual(conn.sock.getsockname(), local)

    def test_link_log_appends_samples(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        response, _ = self.request(conn, "GET", "/api/health")
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"DASHBOARD_LINK_LOG_DIR": folder}):
            samples = [{"t": 1, "kind": "ping", "ok": True, "rtt_ms": 41, "grade": "normal"},
                       {"t": 2, "kind": "control", "ok": False, "rtt_ms": None, "grade": "offline", "extra": "dropped"},
                       {"t": 3, "kind": "<script>", "ok": True, "rtt_ms": -5, "grade": "x"}]
            body = json.dumps({"samples": samples, "recording_id": "20261113T010203Z_both", "profile": "auto"})
            answer, data = self.request(conn, "POST", "/api/link-log", body, {"Cookie": cookie, "Content-Type": "application/json"})
            self.assertEqual(answer.status, 200)
            self.assertEqual(json.loads(data), {"ok": True, "written": 3, "dropped": 0})
            files = list(Path(folder).glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0], {"t": 1, "kind": "ping", "ok": True, "rtt_ms": 41, "grade": "normal",
                                       "recording_id": "20261113T010203Z_both", "profile": "auto"})
            self.assertNotIn("extra", rows[1])
            self.assertEqual((rows[2]["kind"], rows[2]["rtt_ms"], rows[2]["grade"]), (None, None, None), "only known words and numbers")
            refused, _ = self.request(conn, "POST", "/api/link-log", body, {"Content-Type": "application/json"})
            self.assertEqual(refused.status, 403, "a session is required")


if __name__ == "__main__":
    unittest.main()
