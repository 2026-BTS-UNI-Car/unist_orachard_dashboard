"""모의 서버 시험. 임시 폴더의 가짜 static으로 127.0.0.1 빈 포트에 띄웠다가 끝낸다."""
import base64
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mock_data  # noqa: E402
import mock_server  # noqa: E402


class FrameTest(unittest.TestCase):
    def test_accept_key_matches_rfc_example(self):
        self.assertEqual(mock_server.websocket_accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_small_medium_and_large_frames_round_trip(self):
        for size in (5, 125, 126, 70000):
            payload = bytes(index % 251 for index in range(size))
            frame = mock_server.encode_ws_frame(payload)
            self.assertEqual(mock_server.decode_ws_frame(frame), (1, payload, len(frame)))

    def test_masked_client_frame(self):
        mask = bytes([1, 2, 3, 4])
        payload = b"hello"
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        frame = bytes([0x81, 0x80 | len(payload)]) + mask + masked
        self.assertEqual(mock_server.decode_ws_frame(frame + b"extra"), (1, payload, len(frame)))

    def test_incomplete_frame(self):
        frame = mock_server.encode_ws_frame(b"x" * 300)
        self.assertIsNone(mock_server.decode_ws_frame(frame[:1]))
        self.assertIsNone(mock_server.decode_ws_frame(frame[:3]))
        self.assertIsNone(mock_server.decode_ws_frame(frame[:-1]))


class RouteKindTest(unittest.TestCase):
    def test_kinds(self):
        routes = mock_server.APP_ROUTES
        self.assertEqual(mock_server.route_kind("/", routes), "app")
        self.assertEqual(mock_server.route_kind("/rover-control", routes), "app")
        self.assertEqual(mock_server.route_kind("/docs/some-document", routes), "app")
        self.assertEqual(mock_server.route_kind("/static/app.js", routes), "static")
        self.assertEqual(mock_server.route_kind("/api/telemetry", routes), "api")
        self.assertEqual(mock_server.route_kind("/ws/lidar", routes), "ws")
        self.assertEqual(mock_server.route_kind("/maps/korea.pmtiles", routes), "other")
        self.assertEqual(mock_server.route_kind("/drive", routes), "other")
        self.assertEqual(mock_server.route_kind("/drive", routes + ("/drive",)), "app")


class MockDataTest(unittest.TestCase):
    def test_long_strings_have_no_spaces_and_at_least_80_characters(self):
        for value in (mock_data.LONG_MOTOR_DEVICE, mock_data.LONG_LIDAR_DEVICE, mock_data.LONG_CAMERA_DEVICE):
            self.assertGreaterEqual(len(value), 80)
            self.assertNotIn(" ", value)

    def test_both_datasets_answer_the_same_paths(self):
        self.assertEqual(sorted(mock_data.http_payloads("empty")), sorted(mock_data.http_payloads("stress")))

    def test_stress_has_many_rows(self):
        payloads = mock_data.http_payloads("stress", now=0)
        self.assertGreaterEqual(len(payloads["/api/telemetry"]["docker"]["containers"]), 30)
        self.assertGreaterEqual(len(payloads["/api/recordings"]["recordings"]), 20)
        self.assertEqual(payloads["/api/telemetry"]["services"]["rover_bridge"]["serial_port"], mock_data.LONG_MOTOR_DEVICE)

    def test_empty_is_empty(self):
        payloads = mock_data.http_payloads("empty", now=0)
        self.assertEqual(payloads["/api/recordings"]["recordings"], [])
        self.assertFalse(payloads["/api/telemetry"]["docker"]["available"])
        self.assertEqual(payloads["/api/telemetry"]["services"], {})

    def test_payloads_are_json_serializable(self):
        for dataset in mock_data.DATASETS:
            json.dumps(mock_data.http_payloads(dataset))

    def test_only_the_fake_position_is_used(self):
        message = mock_data.websocket_message("gps", 1, now=0)
        self.assertEqual((message["latitude"], message["longitude"]), (0.0001, 0.0002))
        self.assertIsNone(mock_data.websocket_message("camera", 1))

    def test_unknown_dataset(self):
        with self.assertRaises(ValueError):
            mock_data.http_payloads("real")


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.static = root / "static"
        cls.static.mkdir()
        (cls.static / "index.html").write_bytes(b"<!doctype html><title>mock index</title>")
        (cls.static / "app.js").write_bytes(b"console.log('mock');")
        (cls.static / "vendor").mkdir()
        (cls.static / "vendor" / "lib.mjs").write_bytes(b"export const value = 1;")
        (root / "outside.txt").write_bytes(b"outside static")
        cls.server = mock_server.MockServer(cls.static, "stress", app_routes=("/drive",))
        cls.thread = cls.server.start_in_thread()
        cls.base = cls.server.base_url

    @classmethod
    def tearDownClass(cls):
        cls.server.stop(cls.thread)
        cls.temp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()

    def status(self, path):
        try:
            return self.get(path)[0]
        except urllib.error.HTTPError as error:
            return error.code

    def test_binds_to_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertGreater(self.server.server_address[1], 0)

    def test_all_app_routes_return_index_html(self):
        index = (self.static / "index.html").read_bytes()
        for route in mock_server.APP_ROUTES + ("/docs/some-document", "/drive"):
            status, content_type, body = self.get(route)
            self.assertEqual((status, body), (200, index), route)
            self.assertTrue(content_type.startswith("text/html"), route)
        self.assertEqual(len(mock_server.APP_ROUTES), 10)

    def test_static_files_and_types(self):
        status, content_type, body = self.get("/static/app.js")
        self.assertEqual((status, body), (200, b"console.log('mock');"))
        self.assertTrue(content_type.startswith("application/javascript"))
        self.assertTrue(self.get("/static/vendor/lib.mjs")[1].startswith("application/javascript"))

    def test_paths_outside_static_are_refused(self):
        for path in ("/static/../outside.txt", "/static/%2e%2e/outside.txt", "/static/..%2foutside.txt", "/static/missing.js", "/outside.txt", "/maps/korea.pmtiles"):
            self.assertEqual(self.status(path), 404, path)

    def test_api_answers_json(self):
        status, content_type, body = self.get("/api/telemetry")
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertEqual(json.loads(body)["services"]["rover_bridge"]["serial_port"], mock_data.LONG_MOTOR_DEVICE)
        self.assertEqual(json.loads(self.get("/api/live/map?transport=tiles")[2])["state"], "idle")
        self.assertTrue(json.loads(self.get("/api/docs/mock-document-01")[2])["ok"])
        self.assertTrue(json.loads(self.get("/api/docker/container/mock0001")[2])["ok"])
        self.assertEqual(len(json.loads(self.get("/api/docker/container/mock0001/logs?tail=180")[2])["lines"]), 180)

    def test_unknown_api_is_404_and_recorded(self):
        self.assertEqual(self.status("/api/not-mocked"), 404)
        self.assertIn("GET /api/not-mocked", self.server.stats()["unmocked"])

    def test_post_is_received_but_not_applied(self):
        request = urllib.request.Request(self.base + "/api/rover/arm", data=b'{"arm": true}', method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            value = json.loads(response.read())
        self.assertFalse(value["ok"])
        self.assertEqual(value["error"], "mock_read_only")
        self.assertIn("POST /api/rover/arm", self.server.stats()["writes"])

    def handshake(self, channel):
        connection = socket.create_connection(("127.0.0.1", self.server.server_address[1]), timeout=3)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        connection.sendall(
            f"GET /ws/{channel} HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode("ascii")
        )
        return connection, key

    def read_until_frame(self, connection, seconds=1.5):
        connection.settimeout(seconds)
        data = b""
        try:
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                data += chunk
                head, separator, rest = data.partition(b"\r\n\r\n")
                if separator and mock_server.decode_ws_frame(rest):
                    return head, mock_server.decode_ws_frame(rest)
        except socket.timeout:
            pass
        return data.partition(b"\r\n\r\n")[0], None

    def test_websocket_feed_and_silent_channels(self):
        connection, key = self.handshake("gps")
        try:
            head, frame = self.read_until_frame(connection)
            self.assertIn(b"101 Switching Protocols", head)
            self.assertIn(mock_server.websocket_accept_key(key).encode("ascii"), head)
            self.assertIsNotNone(frame)
            self.assertEqual(json.loads(frame[1])["type"], "gps")
        finally:
            connection.close()
        for channel in ("camera", "lidar"):
            connection, key = self.handshake(channel)
            try:
                head, frame = self.read_until_frame(connection, 0.6)
                self.assertIn(b"101 Switching Protocols", head, channel)
                self.assertIsNone(frame, channel)
            finally:
                connection.close()

    def test_websocket_close_is_answered(self):
        connection, _ = self.handshake("camera")
        try:
            self.read_until_frame(connection, 0.3)
            connection.sendall(bytes([0x88, 0x80, 1, 2, 3, 4]))
            connection.settimeout(2)
            reply = connection.recv(64)
            self.assertEqual(reply[0] & 0x0F, 0x8)
        finally:
            connection.close()


class ServerOptionsTest(unittest.TestCase):
    def test_missing_index_is_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                mock_server.MockServer(folder, "empty")

    def test_refuse_mode_and_empty_dataset(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "index.html").write_bytes(b"<!doctype html>")
            server = mock_server.MockServer(folder, "empty", websocket="refuse")
            thread = server.start_in_thread()
            try:
                self.assertEqual(server.ws_feeds, ())
                connection = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=3)
                connection.sendall(b"GET /ws/imu HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: AAAAAAAAAAAAAAAAAAAAAA==\r\nSec-WebSocket-Version: 13\r\n\r\n")
                connection.settimeout(2)
                self.assertIn(b" 404 ", connection.recv(256).split(b"\r\n")[0])
                connection.close()
                with urllib.request.urlopen(server.base_url + "/api/recordings", timeout=5) as response:
                    self.assertEqual(json.loads(response.read()), {"recordings": []})
            finally:
                server.stop(thread)
            self.assertFalse(thread.is_alive())

    def test_unknown_options(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "index.html").write_bytes(b"<!doctype html>")
            for arguments in ({"dataset": "real"}, {"websocket": "maybe"}, {"ws_feeds": ("radar",)}):
                with self.assertRaises(ValueError):
                    mock_server.MockServer(folder, **{"dataset": "stress", **arguments})

    def test_front_steer_page_mark(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "index.html").write_bytes(b'<!doctype html><html lang="ko" data-runtime-mode="live"></html>')
            with self.assertRaises(ValueError):
                mock_server.MockServer(folder, "empty", vehicle="tank")
            for vehicle, mark in (("differential", b'<html lang="ko"'), ("front_steer", b'<html data-vehicle-kind="front_steer" lang="ko"')):
                server = mock_server.MockServer(folder, "empty", vehicle=vehicle)
                thread = server.start_in_thread()
                try:
                    with urllib.request.urlopen(server.base_url + "/rover-control", timeout=5) as response:
                        self.assertIn(mark, response.read())
                finally:
                    server.stop(thread)


if __name__ == "__main__":
    unittest.main()
