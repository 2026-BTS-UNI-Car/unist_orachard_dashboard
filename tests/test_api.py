from __future__ import annotations

import json
import os
from pathlib import Path
import base64
import hashlib
import secrets
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request

from dashboard_server.app import create_server


class DashboardApiTest(unittest.TestCase):
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

    def get_json(self, path: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=3) as res:
            self.assertEqual(res.status, 200)
            return json.loads(res.read().decode("utf-8"))

    def get_text(self, path: str) -> str:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=3) as res:
            self.assertEqual(res.status, 200)
            return res.read().decode("utf-8")

    def post_json(self, path: str) -> dict:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="POST")
        with urllib.request.urlopen(req, timeout=5) as res:
            self.assertEqual(res.status, 200)
            return json.loads(res.read().decode("utf-8"))

    def read_ws_text(self, sock: socket.socket, initial: bytes = b"") -> str:
        data = bytearray(initial)
        while len(data) < 2:
            data.extend(sock.recv(2 - len(data)))
        header = bytes(data[:2])
        del data[:2]
        self.assertEqual(len(header), 2)
        opcode = header[0] & 0x0F
        self.assertEqual(opcode, 1)
        length = header[1] & 0x7F
        if length == 126:
            while len(data) < 2:
                data.extend(sock.recv(2 - len(data)))
            length = int.from_bytes(bytes(data[:2]), "big")
            del data[:2]
        elif length == 127:
            while len(data) < 8:
                data.extend(sock.recv(8 - len(data)))
            length = int.from_bytes(bytes(data[:8]), "big")
            del data[:8]
        while len(data) < length:
            data.extend(sock.recv(length - len(data)))
        return bytes(data[:length]).decode("utf-8")

    def open_dashboard_ws(self) -> tuple[socket.socket, bytes]:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        sock.sendall(
            (
                "GET /ws/lidar HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode("ascii")
        )
        raw = b""
        while b"\r\n\r\n" not in raw:
            raw += sock.recv(1024)
        response_raw, leftover = raw.split(b"\r\n\r\n", 1)
        self.assertIn("101 Switching Protocols", response_raw.decode("iso-8859-1"))
        return sock, leftover

    def ws_frame(self, payload: dict) -> bytes:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(body) < 126:
            return bytes([0x81, len(body)]) + body
        return bytes([0x81, 126]) + len(body).to_bytes(2, "big") + body

    def test_health(self):
        payload = self.get_json("/api/health")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service"], "unist_orachard_dashboard")

    def test_telemetry_shape(self):
        payload = self.get_json("/api/telemetry")
        for key in ["system", "network", "docker", "fan", "connection", "rover", "sensors", "fusion", "experiment"]:
            self.assertIn(key, payload)
        self.assertIn("cpu", payload["system"])
        self.assertEqual(payload["system"]["memory"]["display_total"], 8 * 1024 * 1024 * 1024)
        for core in payload["system"]["cpu"].get("cores", []):
            self.assertRegex(core["label"], r"^CPU [0-9]+$")
        self.assertIn("interfaces", payload["network"])
        self.assertEqual(payload["sensors"]["lidar"]["frame_id"], "laser")
        self.assertEqual(payload["sensors"]["camera"]["frame_id"], "camera_link_optical")
        self.assertEqual(payload["sensors"]["imu"]["frame_id"], "imu_link")

    def test_detail_api_shape(self):
        self.assertIn("memory", self.get_json("/api/system"))
        network = self.get_json("/api/network")
        self.assertIn("interfaces", network)
        self.assertIn("primary_ipv4", network)
        self.assertIn("external_ip", network)
        self.assertIn("containers", self.get_json("/api/docker"))
        fan = self.get_json("/api/fan/status")
        self.assertIn("available", fan)
        self.assertIn("mode", fan)
        self.assertIn("calibration", fan)
        self.assertIn("target_rpm_available", fan)
        self.assertIn("calibration", self.get_json("/api/fan/calibration/status"))
        connection = self.get_json("/api/connection")
        self.assertIn("lidar", connection)
        self.assertIn("test_mode", connection["lidar"])

    def test_lidar_scan_endpoint_shape(self):
        payload = self.post_json("/api/lidar/scan")
        self.assertIn("ok", payload)
        self.assertIn("state", payload)
        if payload["ok"]:
            self.assertIn("points", payload)
            self.assertIn("metrics", payload)
        else:
            self.assertIn(payload["state"], {"not_found", "in_use", "busy", "no_scan_data"})

    def test_plan_contains_research_topic(self):
        payload = self.get_json("/api/plan")
        self.assertIn("Vision-LiDAR", payload["research_topic"])
        self.assertEqual(payload["team"], "유니주행차")

    def test_site_map_contains_connection_after_overview(self):
        payload = self.get_json("/api/site-map")
        paths = [item["path"] for item in payload["routes"]]
        self.assertEqual(paths[:3], ["/", "/connection", "/system"])
        self.assertIn("/fan", paths)
        self.assertIn("/docs", paths)

    def test_docs_api_and_route(self):
        index = self.get_json("/api/docs")
        self.assertGreaterEqual(len(index["documents"]), 3)
        slugs = [item["slug"] for item in index["documents"]]
        self.assertIn("lidar-connection", slugs)
        doc = self.get_json("/api/docs/lidar-connection")
        self.assertTrue(doc["ok"])
        self.assertIn("RPLIDAR", doc["markdown"])
        html = self.get_text("/docs/lidar-connection")
        self.assertIn("id=\"docMarkdown\"", html)

    def test_fan_route_and_static_contract(self):
        html = self.get_text("/fan")
        self.assertIn("Fan Control", html)
        self.assertIn("id=\"lidarDownloadButton\"", html)
        self.assertIn("현재 지도 다운로드", html)
        self.assertNotIn("lidarScanButton", html)
        self.assertNotIn("360 스캔 테스트", html)
        self.assertNotIn("lidarScanCanvas3d", html)
        self.assertNotIn("data-lidar-map-mode=\"3d\"", html)
        self.assertFalse((Path(__file__).resolve().parents[1] / "static" / "lidar3d.js").exists())
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/static/lidar3d.js", timeout=3)
        self.assertEqual(ctx.exception.code, 404)
        app_js = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text()
        self.assertIn("${scheme}://${location.host}/ws/lidar", app_js)
        self.assertNotIn("${scheme}://${location.hostname}:8765/ws/lidar", app_js)
        self.assertIn("[UBTS:", app_js)
        compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
        self.assertIn("ros2-lidar-bridge", compose)
        self.assertIn("Dockerfile.ros2", compose)
        self.assertIn("/sys/class/hwmon:/sys/class/hwmon:rw", compose)

    def test_lidar_websocket_proxy_graceful_without_bridge(self):
        old_upstream = os.environ.get("LIDAR_WS_UPSTREAM")
        os.environ["LIDAR_WS_UPSTREAM"] = "ws://127.0.0.1:1/ws/lidar"
        try:
            sock, leftover = self.open_dashboard_ws()
            with sock:
                payload = json.loads(self.read_ws_text(sock, leftover))
                self.assertEqual(payload["type"], "status")
                self.assertFalse(payload["scan_topic_alive"])
                self.assertIn("bridge unavailable", payload["error"])
        finally:
            if old_upstream is None:
                os.environ.pop("LIDAR_WS_UPSTREAM", None)
            else:
                os.environ["LIDAR_WS_UPSTREAM"] = old_upstream

    def test_lidar_websocket_proxy_relays_upstream_frame(self):
        upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        upstream.bind(("127.0.0.1", 0))
        upstream.listen(1)
        upstream_port = upstream.getsockname()[1]

        def serve_once():
            conn, _ = upstream.accept()
            with conn:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += conn.recv(1024)
                key_line = next(
                    line for line in request.decode("iso-8859-1").split("\r\n")
                    if line.lower().startswith("sec-websocket-key:")
                )
                upstream_key = key_line.split(":", 1)[1].strip()
                accept = base64.b64encode(
                    hashlib.sha1(
                        (upstream_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
                    ).digest()
                ).decode("ascii")
                conn.sendall(
                    (
                        "HTTP/1.1 101 Switching Protocols\r\n"
                        "Upgrade: websocket\r\n"
                        "Connection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                    ).encode("ascii")
                    + self.ws_frame({"type": "socket", "ok": True, "message": "fake bridge connected"})
                )
                time.sleep(0.2)
            upstream.close()

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        old_upstream = os.environ.get("LIDAR_WS_UPSTREAM")
        os.environ["LIDAR_WS_UPSTREAM"] = f"ws://127.0.0.1:{upstream_port}/ws/lidar"
        try:
            sock, leftover = self.open_dashboard_ws()
            with sock:
                payload = json.loads(self.read_ws_text(sock, leftover))
                self.assertEqual(payload["type"], "socket")
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["message"], "fake bridge connected")
        finally:
            if old_upstream is None:
                os.environ.pop("LIDAR_WS_UPSTREAM", None)
            else:
                os.environ["LIDAR_WS_UPSTREAM"] = old_upstream
            thread.join(timeout=2)

    def test_docs_catalog_contains_runtime_and_fan_docs(self):
        index = self.get_json("/api/docs")
        slugs = {item["slug"] for item in index["documents"]}
        self.assertIn("jetson-runtime-architecture", slugs)
        self.assertIn("fan-control", slugs)


if __name__ == "__main__":
    unittest.main()
