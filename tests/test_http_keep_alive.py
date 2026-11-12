"""Persistent HTTP connections of the dashboard server (NET-05).

JSON and static answers keep the TCP connection open, so a control command on a
slow link does not pay a new connection (one more round trip) every time. A
request body the server did not read closes the connection, errors close it,
and an idle connection is closed after KEEP_ALIVE_IDLE_S.
"""
import http.client
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard_server.app import DashboardHandler, create_server  # noqa: E402


class KeepAliveTest(unittest.TestCase):
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

    def connection(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        return conn

    def request(self, conn, method, path, body=None, headers=None):
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        return response, data

    def test_json_answers_reuse_one_connection(self):
        conn = self.connection()
        first, body = self.request(conn, "GET", "/api/health")
        self.assertEqual(first.status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(first.getheader("Connection"), "keep-alive")
        local = conn.sock.getsockname()
        second, _ = self.request(conn, "GET", "/api/health")
        self.assertEqual(second.status, 200)
        self.assertIsNotNone(conn.sock, "the connection stayed open")
        self.assertEqual(conn.sock.getsockname(), local, "the second answer came on the same connection")

    def test_static_files_reuse_the_connection(self):
        conn = self.connection()
        page, _ = self.request(conn, "GET", "/rover-control")
        self.assertEqual((page.status, page.getheader("Connection")), (200, "keep-alive"))
        local = conn.sock.getsockname()
        script, body = self.request(conn, "GET", "/static/app/02-shell.js")
        self.assertEqual(script.status, 200)
        self.assertIn(b"function currentRoute", body)
        self.assertEqual(conn.sock.getsockname(), local)

    def test_control_commands_reuse_the_connection(self):
        conn = self.connection()
        response, _ = self.request(conn, "GET", "/api/health")
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        local = conn.sock.getsockname()
        headers = {"Cookie": cookie, "Content-Type": "application/json"}
        with patch("dashboard_server.rover_api.request", return_value={"ok": True}) as motor:
            for route, op in (("arm", "arm"), ("command", "drive"), ("command", "drive"), ("stop", "stop")):
                answer, body = self.request(conn, "POST", f"/api/rover/{route}", json.dumps({"left": 10, "right": 10}), headers)
                self.assertEqual(answer.status, 200, route)
                self.assertTrue(json.loads(body)["ok"])
                self.assertEqual(answer.getheader("Connection"), "keep-alive")
                self.assertEqual(motor.call_args.args[0]["op"], op)
                self.assertEqual(conn.sock.getsockname(), local, f"{route} on the same connection")
        self.assertEqual(motor.call_count, 4)

    def test_an_unread_request_body_closes_the_connection(self):
        conn = self.connection()
        # /api/imu/zero answers 410 without reading the body: the bytes left over must not be
        # taken for the next request line, so the server closes.
        answer, _ = self.request(conn, "POST", "/api/imu/zero", json.dumps({"x": 1}), {"Content-Type": "application/json"})
        self.assertEqual(answer.status, 410)
        self.assertEqual(answer.getheader("Connection"), "close")
        # http.client opens a new connection by itself; the next answer is fine.
        again, body = self.request(conn, "GET", "/api/health")
        self.assertEqual(again.status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_errors_close_the_connection(self):
        conn = self.connection()
        answer, _ = self.request(conn, "POST", "/api/rover/stop", "{}", {"Content-Type": "application/json"})
        self.assertEqual(answer.status, 403, "no session")
        self.assertEqual(answer.getheader("Connection"), "close")

    def test_an_idle_connection_is_closed(self):
        with patch.object(DashboardHandler, "KEEP_ALIVE_IDLE_S", 0.3):
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            self.addCleanup(sock.close)
            sock.sendall(b"GET /api/health HTTP/1.1\r\nHost: x\r\n\r\n")
            received = b""
            while b"\r\n\r\n" not in received:
                received += sock.recv(4096)
            head, _, rest = received.partition(b"\r\n\r\n")
            length = int([line.split(b":", 1)[1] for line in head.split(b"\r\n") if line.lower().startswith(b"content-length")][0])
            while len(rest) < length:
                rest += sock.recv(4096)
            started = time.monotonic()
            self.assertEqual(sock.recv(4096), b"", "the server closes an idle connection")
            self.assertLess(time.monotonic() - started, 3.0)

    def test_safety_times_are_unchanged(self):
        # The keep-alive wait is a connection setting only; the control timeouts stay where they were.
        from dashboard_server import rover_api
        import inspect
        self.assertIn("client.settimeout(0.6)", inspect.getsource(rover_api.request))
        self.assertGreaterEqual(DashboardHandler.KEEP_ALIVE_IDLE_S, 5.0)


if __name__ == "__main__":
    unittest.main()
