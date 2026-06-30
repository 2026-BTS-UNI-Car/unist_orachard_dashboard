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

    def open_dashboard_ws(self, path: str = "/ws/lidar") -> tuple[socket.socket, bytes]:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\n"
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
        self.assertIn("swap", payload["system"])
        self.assertIn("power", payload["system"])
        self.assertNotIn("gpu_percent", payload["system"])
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
        docker_detail = self.get_json("/api/docker/container/test")
        self.assertFalse(docker_detail["ok"])
        docker_logs = self.get_json("/api/docker/container/test/logs?tail=25")
        self.assertFalse(docker_logs["ok"])
        self.assertIn("lines", docker_logs)
        fan = self.get_json("/api/fan/status")
        self.assertIn("available", fan)
        self.assertIn("mode", fan)
        self.assertIn("pwm", fan)
        self.assertIn("desired", fan)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/fan/calibration/status", timeout=3)
        self.assertEqual(ctx.exception.code, 404)
        connection = self.get_json("/api/connection")
        self.assertIn("lidar", connection)
        self.assertIn("test_mode", connection["lidar"])
        self.assertEqual(connection["lidar"]["test_mode"], "ros2_subscribe_only")

        lidar_test = self.post_json("/api/lidar/test")
        self.assertIn("status", lidar_test)
        self.assertNotIn("protocol", lidar_test)
        self.assertNotIn("baud", lidar_test)

    def test_lidar_scan_endpoint_shape(self):
        payload = self.post_json("/api/lidar/scan")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["state"], "disabled")
        self.assertEqual(payload["points"], [])

    def test_lidar_bridge_status_file_marks_scan_live(self):
        status_file = Path(__file__).resolve().parents[1] / "runtime" / "lidar_bridge_status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        old = status_file.read_text(encoding="utf-8") if status_file.exists() else None
        try:
            status_file.write_text(
                json.dumps(
                    {
                        "updated_at": time.time(),
                        "scan_topic_alive": True,
                        "last_scan_age_sec": 0.1,
                        "point_count": 900,
                        "source": "ros2",
                        "frame_id": "laser",
                    }
                ),
                encoding="utf-8",
            )
            payload = self.get_json("/api/connection")
            self.assertTrue(payload["lidar_bridge"]["scan_topic_alive"])
            self.assertTrue(payload["services"]["ros2_bridge"]["ok"])
            self.assertTrue(payload["sensors"]["lidar"]["ok"])
            self.assertEqual(payload["sensors"]["lidar"]["state"], "scan_live")
        finally:
            if old is None:
                status_file.unlink(missing_ok=True)
            else:
                status_file.write_text(old, encoding="utf-8")

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
        self.assertIn("Manual PWM", html)
        self.assertIn("id=\"fanAutoToggle\"", html)
        self.assertIn("id=\"lidarRangeValue\"", html)
        self.assertIn("id=\"lidarRangeSlider\"", html)
        self.assertIn("id=\"lidarPointSlider\"", html)
        self.assertIn("data-lidar-points=\"reset\"", html)
        self.assertIn("id=\"systemSwapValue\"", html)
        self.assertIn("id=\"systemNetworkValue\"", html)
        self.assertIn("id=\"systemPowerValue\"", html)
        self.assertIn("id=\"hostPower\"", html)
        self.assertIn("id=\"containerDetail\"", html)
        self.assertIn("id=\"containerLogs\"", html)
        self.assertIn("id=\"containerRefreshButton\"", html)
        self.assertIn("data-lidar-range=\"reset\"", html)
        self.assertIn(">Auto</button>", html)
        self.assertNotIn("chartGpu", html)
        self.assertNotIn("systemGpuValue", html)
        self.assertNotIn("Fan RPM", html)
        self.assertNotIn("PWM-RPM", html)
        self.assertNotIn("fanCalibrateButton", html)
        self.assertNotIn("lidarScanButton", html)
        self.assertNotIn("lidarAccumulateToggle", html)
        self.assertNotIn("점 누적", html)
        self.assertNotIn(">Baud<", html)
        self.assertNotIn("360 스캔 테스트", html)
        self.assertNotIn("serial live fallback", html)
        self.assertNotIn("LiDAR serial port는 ROS2 driver만 소유합니다", html)
        self.assertNotIn("LiDAR Device</span>", html)
        self.assertNotIn("Last Scan</span>", html)
        self.assertNotIn("ROS Guard", html)
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
        self.assertIn("LIDAR_RANGE_AUTO_KEY", app_js)
        self.assertIn("LIDAR_POINTS_KEY", app_js)
        self.assertIn("max_points", app_js)
        self.assertIn("downloadLidarPng", app_js)
        self.assertIn("/api/docker/container/", app_js)
        self.assertNotIn("downloadCanvasPng", app_js)
        self.assertNotIn("lidarAccumulate", app_js)
        self.assertNotIn("gpuReadout", app_js)
        self.assertIn("displayAngle", app_js)
        self.assertNotIn("Front 0°", app_js)
        compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
        telemetry_py = (Path(__file__).resolve().parents[1] / "dashboard_server" / "telemetry.py").read_text()
        lidar_driver_dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile.lidar_driver").read_text()
        self.assertIn("ros2-lidar-driver", compose)
        self.assertIn("ros2-lidar-bridge", compose)
        self.assertIn("Dockerfile.lidar_driver", compose)
        self.assertIn("Dockerfile.ros2", compose)
        self.assertIn("LIDAR_BRIDGE_SOURCE:-ros2", compose)
        self.assertIn("LIDAR_SCAN_MAX_POINTS:-3600", compose)
        self.assertIn("./runtime:/app/runtime", compose)
        self.assertNotIn("LIDAR_BRIDGE_SOURCE:-auto", compose)
        self.assertIn("stats?stream=false&one-shot=true", telemetry_py)
        self.assertIn("def docker_container_logs", telemetry_py)
        self.assertIn("ina3221", telemetry_py)
        self.assertNotIn("def _gpu_percent", telemetry_py)
        self.assertIn("sllidar_s2_launch.py", lidar_driver_dockerfile)
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
        captured_request_line = []

        def serve_once():
            conn, _ = upstream.accept()
            with conn:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += conn.recv(1024)
                request_lines = request.decode("iso-8859-1").split("\r\n")
                captured_request_line.append(request_lines[0])
                key_line = next(
                    line for line in request_lines
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
            sock, leftover = self.open_dashboard_ws("/ws/lidar?max_points=1700")
            with sock:
                payload = json.loads(self.read_ws_text(sock, leftover))
                self.assertEqual(payload["type"], "socket")
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["message"], "fake bridge connected")
            self.assertEqual(captured_request_line, ["GET /ws/lidar?max_points=1700 HTTP/1.1"])
        finally:
            if old_upstream is None:
                os.environ.pop("LIDAR_WS_UPSTREAM", None)
            else:
                os.environ["LIDAR_WS_UPSTREAM"] = old_upstream
            thread.join(timeout=2)

    def test_lidar_websocket_proxy_handles_two_clients(self):
        upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        upstream.bind(("127.0.0.1", 0))
        upstream.listen(2)
        upstream_port = upstream.getsockname()[1]
        accepted = []

        def serve_two():
            try:
                for index in range(2):
                    conn, _ = upstream.accept()
                    accepted.append(conn)
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
                        + self.ws_frame({"type": "socket", "ok": True, "client": index})
                    )
                time.sleep(0.2)
            finally:
                for conn in accepted:
                    conn.close()
                upstream.close()

        thread = threading.Thread(target=serve_two, daemon=True)
        thread.start()
        old_upstream = os.environ.get("LIDAR_WS_UPSTREAM")
        os.environ["LIDAR_WS_UPSTREAM"] = f"ws://127.0.0.1:{upstream_port}/ws/lidar"
        sockets = []
        try:
            first, first_leftover = self.open_dashboard_ws()
            second, second_leftover = self.open_dashboard_ws()
            sockets.extend([first, second])
            payloads = [
                json.loads(self.read_ws_text(first, first_leftover)),
                json.loads(self.read_ws_text(second, second_leftover)),
            ]
            self.assertEqual({payload["client"] for payload in payloads}, {0, 1})
            self.assertTrue(all(payload["ok"] for payload in payloads))
        finally:
            for sock in sockets:
                sock.close()
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
