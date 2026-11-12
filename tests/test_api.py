from __future__ import annotations

import json
import gzip
import os
from pathlib import Path
import base64
import hashlib
import re
import secrets
import shutil
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

    def test_large_json_supports_compact_gzip_transport(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/docs",
            headers={"Accept-Encoding": "gzip"},
        )
        with urllib.request.urlopen(request, timeout=3) as res:
            self.assertEqual(res.headers.get("Content-Encoding"), "gzip")
            decoded = gzip.decompress(res.read())
            self.assertIn("documents", json.loads(decoded))
            self.assertNotIn(b"\n  ", decoded)

    def test_docs_assets_are_served_but_path_traversal_is_blocked(self):
        # The repository ships no document images at the moment, so the route is checked against a temporary
        # docs folder: an image below docs/assets is served, a path that climbs out of it is not.
        import tempfile
        from unittest.mock import patch
        import dashboard_server.app as app_module
        with tempfile.TemporaryDirectory() as root:
            docs = Path(root)
            (docs / "assets" / "figures").mkdir(parents=True)
            image = bytes((0xFF, 0xD8, 0xFF, 0xE0)) + bytes(200)
            (docs / "assets" / "figures" / "sample.jpg").write_bytes(image)
            (docs / "architecture.md").write_bytes(b"outside the asset folder")
            with patch.object(app_module, "DOCS_ROOT", docs):
                path = "/docs-assets/figures/sample.jpg"
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=3) as res:
                    self.assertEqual(res.status, 200)
                    self.assertEqual(res.headers.get("Content-Type"), "image/jpeg")
                    self.assertEqual(res.read(), image)
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(f"http://127.0.0.1:{self.port}/docs-assets/../architecture.md", timeout=3)
                self.assertIn(context.exception.code, {403, 404})

    def post_json(self, path: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as res:
            self.assertEqual(res.status, 200)
            return json.loads(res.read().decode("utf-8"))

    def session_cookie(self) -> str:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=3) as res:
            cookie = res.headers.get("Set-Cookie", "")
        self.assertIn("ubts_session=", cookie)
        return cookie.split(";", 1)[0]

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

    def open_dashboard_ws(self, path: str = "/ws/lidar", include_session: bool = True) -> tuple[socket.socket, bytes]:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        cookie_header = f"Cookie: {self.session_cookie()}\r\n" if include_session else ""
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"{cookie_header}"
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

    def test_occupancy_proxy_preserves_binary_metadata_and_post_body(self):
        observed = {}

        class UpstreamHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                observed["get_path"] = self.path
                payload = b"\x89PNG\r\n\x1a\nmap"
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("X-OGM-Meta", '{"frame_index":3}')
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                observed["post_path"] = self.path
                observed["post_body"] = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = b'{"ok":true,"session_id":"fresh"}'
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        previous = os.environ.get("OCCUPANCY_GRID_URL")
        os.environ["OCCUPANCY_GRID_URL"] = f"http://127.0.0.1:{upstream.server_address[1]}"
        try:
            url = f"http://127.0.0.1:{self.port}/occupancy-api/api/v1/sessions/s1/frame.png?t_s=1.5"
            with urllib.request.urlopen(url, timeout=3) as res:
                self.assertEqual(res.status, 200)
                self.assertEqual(res.headers.get("Content-Type"), "image/png")
                self.assertEqual(res.headers.get("X-OGM-Meta"), '{"frame_index":3}')
                self.assertTrue(res.read().startswith(b"\x89PNG"))
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/occupancy-api/api/v1/sessions",
                data=b'{"archive_id":"sample"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as res:
                self.assertEqual(res.status, 201)
                self.assertEqual(json.loads(res.read())["session_id"], "fresh")
            self.assertEqual(observed["get_path"], "/api/v1/sessions/s1/frame.png?t_s=1.5")
            self.assertEqual(observed["post_body"], b'{"archive_id":"sample"}')
        finally:
            if previous is None:
                os.environ.pop("OCCUPANCY_GRID_URL", None)
            else:
                os.environ["OCCUPANCY_GRID_URL"] = previous
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=2)

    def test_telemetry_shape(self):
        payload = self.get_json("/api/telemetry")
        for key in ["system", "network", "docker", "fan", "connection", "rover", "sensors", "experiment"]:
            self.assertIn(key, payload)
        self.assertIn("cpu", payload["system"])
        self.assertIn("swap", payload["system"])
        self.assertIn("power", payload["system"])
        self.assertNotIn("gpu_percent", payload["system"])
        self.assertEqual(payload["system"]["memory"]["display_total"], 8 * 1024 * 1024 * 1024)
        self.assertIn("disks", payload["system"])
        for disk in payload["system"].get("disks", []):
            self.assertIn("mount", disk)
            self.assertIn("source", disk)
            self.assertIn("fstype", disk)
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
        self.assertIn("camera", connection)
        self.assertIn("camera_bridge", connection)
        self.assertIn("imu_bridge", connection)
        self.assertIn("rover_bridge", connection)
        self.assertIn("rover", connection)
        self.assertIn("data_recorder", connection["services"])
        self.assertIn("test_mode", connection["lidar"])
        self.assertEqual(connection["lidar"]["test_mode"], "ros2_subscribe_only")
        self.assertEqual(connection["camera"]["test_mode"], "ros2_subscribe_only")
        self.assertEqual(connection["sensors"]["imu"]["dashboard_access"], "subscribe_only")
        self.assertEqual(connection["sensors"]["imu"]["raw_topic"], "/imu/data_raw")

        lidar_test = self.post_json("/api/lidar/test")
        self.assertIn("status", lidar_test)
        self.assertNotIn("protocol", lidar_test)
        self.assertNotIn("baud", lidar_test)

    def test_lidar_scan_endpoint_shape(self):
        payload = self.post_json("/api/lidar/scan")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["state"], "disabled")
        self.assertEqual(payload["points"], [])

    def test_rover_stop_requires_operator_session(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post_json("/api/rover/stop", {})
        self.assertEqual(error.exception.code, 403)

    def test_rover_control_without_password_and_owner_binding(self):
        from unittest.mock import patch
        cookie = self.session_cookie()
        def post(path, payload):
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                data=json.dumps(payload).encode(), headers={"Cookie": cookie, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.load(response)
        with patch("dashboard_server.rover_api.request", return_value={"ok": True}) as motor:
            self.assertTrue(post("/api/rover/arm", {"owner": "client-cannot-choose"})["ok"])
            owner = motor.call_args.args[0]["owner"]
            self.assertNotEqual(owner, "client-cannot-choose")
            self.assertEqual(motor.call_args.args[0]["op"], "arm")
            for route, op in (("command", "drive"), ("release", "release"), ("stop", "stop")):
                self.assertTrue(post(f"/api/rover/{route}", {})["ok"])
                self.assertEqual(motor.call_args.args[0]["owner"], owner)
                self.assertEqual(motor.call_args.args[0]["op"], op)

    def test_rover_control_rejects_foreign_origin(self):
        from unittest.mock import patch
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/rover/arm",
            data=b"{}", headers={"Cookie": self.session_cookie(), "Origin": "https://other.invalid",
                                 "Content-Type": "application/json"})
        with patch("dashboard_server.rover_api.request") as motor:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(error.exception.code, 403)
            motor.assert_not_called()

    def test_recordings_api_reads_archive_and_downloads_tarball(self):
        root = Path(__file__).resolve().parents[1] / "runtime" / "recordings" / "unit_lidar_archive"
        playback_cache = Path(__file__).resolve().parents[1] / "runtime" / "playback_cache" / root.name
        root.mkdir(parents=True, exist_ok=True)
        try:
            (root / "metadata.json").write_text(
                json.dumps(
                    {
                        "id": "unit_lidar_archive",
                        "kind": "lidar",
                        "state": "archived",
                        "duration_s": 1.2,
                        "include_imu": True,
                        "topics": ["/scan", "/imu/data_raw", "/imu/data"],
                        "timeline": {"lidar_frames": 1, "camera_frames": 0, "imu_frames": 1, "imu_calibration_valid": True},
                    }
                ),
                encoding="utf-8",
            )
            (root / "preview_scan.json").write_text(
                json.dumps({"ok": True, "points": [{"x_m": 1.0, "y_m": 0.0, "distance_m": 1.0}], "metrics": {"rendered_points": 1}}),
                encoding="utf-8",
            )
            (root / "analysis.json").write_text(
                json.dumps({"ok": True, "walls": [{"start": {"x_m": 0, "y_m": 0}, "end": {"x_m": 1, "y_m": 0}}], "objects": []}),
                encoding="utf-8",
            )
            (root / "timeline.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "duration_s": 1.2,
                        "algorithm": "polar_gap_cluster_line_fit_fragment_merge_predictive_tracks_v5",
                        "coordinate_frame": "lidar_bridge_xy_v1",
                        "lidar": {"frames": [{"t_s": 0.0, "points": [{"x_m": 1.0, "y_m": 0.0, "distance_m": 1.0}], "overlay": {}}]},
                        "camera": {"frames": []},
                        "imu": {
                            "frames": [
                                {
                                    "t_s": 0.0,
                                    "stamp": {"sec": 1, "nanosec": 0},
                                    "corrected": {
                                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                                        "euler_deg": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
                                    },
                                    "calibration_state": "ready",
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            listing = self.get_json("/api/recordings")
            self.assertTrue(listing["ok"])
            self.assertIn("unit_lidar_archive", {item["id"] for item in listing["recordings"]})
            detail = self.get_json("/api/recordings/unit_lidar_archive")
            self.assertTrue(detail["ok"])
            self.assertEqual(detail["recording"]["metadata"]["kind"], "lidar")
            self.assertEqual(detail["recording"]["timeline_summary"]["lidar_frames"], 1)
            self.assertEqual(detail["recording"]["timeline_summary"]["imu_frames"], 1)
            self.assertTrue(detail["recording"]["timeline_summary"]["imu_calibration_valid"])
            timeline = self.get_json("/api/recordings/unit_lidar_archive/timeline")
            self.assertTrue(timeline["ok"])
            self.assertEqual(timeline["timeline"]["algorithm"], "polar_gap_cluster_line_fit_fragment_merge_predictive_tracks_v5")
            segment = self.get_json("/api/recordings/unit_lidar_archive/timeline?start_s=0&duration_s=0.5")
            self.assertTrue(segment["ok"])
            self.assertTrue(segment["timeline"]["segment"]["partial"])
            self.assertEqual(len(segment["timeline"]["lidar"]["frames"]), 1)
            self.assertIsInstance(segment["timeline"]["lidar"]["frames"][0]["points"][0], dict)
            compact = self.get_json(
                "/api/recordings/unit_lidar_archive/timeline"
                "?start_s=0&duration_s=4&profile=dashboard-compact-v1"
            )
            compact_timeline = compact["timeline"]
            self.assertEqual(compact_timeline["playback"]["profile"], "dashboard-compact-v1")
            self.assertEqual(
                compact_timeline["playback"]["lidar_point_fields"],
                ["x_m", "y_m", "distance_m", "quality", "angle_deg"],
            )
            self.assertEqual(compact_timeline["lidar"]["frames"][0]["points"][0], [1.0, 0.0, 1.0, None, None])
            self.assertEqual(
                set(compact_timeline["imu"]["frames"][0]["corrected"]),
                {"euler_deg"},
            )
            self.get_json(
                "/api/recordings/unit_lidar_archive/timeline"
                "?start_s=0&duration_s=0.5&profile=dashboard-compact-v1"
            )
            source_after_compact = self.get_json("/api/recordings/unit_lidar_archive/timeline")
            self.assertIn(
                "orientation",
                source_after_compact["timeline"]["imu"]["frames"][0]["corrected"],
            )
            with self.assertRaises(urllib.error.HTTPError) as removed:
                self.get_json("/api/recordings/unit_lidar_archive/orchard-map")
            self.assertEqual(removed.exception.code, 404)
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/recordings/unit_lidar_archive/download", timeout=5) as res:
                self.assertEqual(res.status, 200)
                self.assertEqual(res.headers.get("Content-Type"), "application/gzip")
                self.assertIn("unit_lidar_archive.tar.gz", res.headers.get("Content-Disposition", ""))
                self.assertGreater(len(res.read()), 40)
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/recordings/unit_lidar_archive/download",
                method="HEAD",
            )
            with urllib.request.urlopen(req, timeout=5) as res:
                self.assertEqual(res.status, 200)
                self.assertEqual(res.headers.get("Content-Type"), "application/gzip")
        finally:
            for child in root.glob("*"):
                child.unlink(missing_ok=True)
            root.rmdir()
            cache = Path(__file__).resolve().parents[1] / "runtime" / "recording_downloads" / "unit_lidar_archive.tar.gz"
            cache.unlink(missing_ok=True)
            shutil.rmtree(playback_cache, ignore_errors=True)
            orchard_cache = Path(__file__).resolve().parents[1] / "runtime" / "orchard_analysis" / "unit_lidar_archive_orchard_map.json"
            orchard_cache.unlink(missing_ok=True)

    def test_video_archive_detail_and_http_range_streaming(self):
        root = Path(__file__).resolve().parents[1] / "runtime" / "recordings" / "unit_camera_video"
        root.mkdir(parents=True, exist_ok=True)
        video_bytes = bytes(range(256)) * 8
        try:
            (root / "video.mp4").write_bytes(video_bytes)
            (root / "metadata.json").write_text(
                json.dumps(
                    {
                        "id": "unit_camera_video",
                        "kind": "camera",
                        "state": "archived",
                        "duration_s": 2.0,
                        "timeline": {"lidar_frames": 0, "camera_frames": 2, "sample_hz": 1.0},
                        "source_video": {"file": "video.mp4", "mime": "video/mp4", "fps": 60.0},
                    }
                ),
                encoding="utf-8",
            )
            (root / "analysis.json").write_text(json.dumps({"ok": True, "walls": [], "objects": []}), encoding="utf-8")
            (root / "preview_scan.json").write_text(json.dumps({"ok": False, "points": []}), encoding="utf-8")
            (root / "timeline.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "duration_s": 2.0,
                        "video": {"file": "video.mp4", "mime": "video/mp4"},
                        "sync": {"native_video": True},
                        "lidar": {"frames": []},
                        "camera": {"frames": [{"t_s": 0.0, "data": "AA=="}, {"t_s": 1.0, "data": "AA=="}]},
                    }
                ),
                encoding="utf-8",
            )

            detail = self.get_json("/api/recordings/unit_camera_video")
            recording = detail["recording"]
            self.assertEqual(recording["kind"], "camera")
            self.assertEqual(recording["video_url"], f"/api/recordings/unit_camera_video/video?v={len(video_bytes)}")
            self.assertEqual(recording["source_video"]["bytes"], len(video_bytes))
            self.assertNotIn("path", recording["source_video"])

            segment = self.get_json("/api/recordings/unit_camera_video/timeline?start_s=0&duration_s=2")
            self.assertEqual(len(segment["timeline"]["camera"]["frames"]), 2)
            self.assertNotIn("data", segment["timeline"]["camera"]["frames"][0])

            range_request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/recordings/unit_camera_video/video",
                headers={"Range": "bytes=100-199"},
            )
            with urllib.request.urlopen(range_request, timeout=5) as res:
                self.assertEqual(res.status, 206)
                self.assertEqual(res.headers.get("Accept-Ranges"), "bytes")
                self.assertEqual(res.headers.get("Content-Range"), f"bytes 100-199/{len(video_bytes)}")
                self.assertEqual(res.read(), video_bytes[100:200])

            head_request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/recordings/unit_camera_video/video",
                method="HEAD",
            )
            with urllib.request.urlopen(head_request, timeout=5) as res:
                self.assertEqual(res.status, 200)
                self.assertEqual(res.headers.get("Content-Type"), "video/mp4")
                self.assertEqual(int(res.headers.get("Content-Length", 0)), len(video_bytes))
        finally:
            for child in root.glob("*"):
                child.unlink(missing_ok=True)
            root.rmdir()

    def test_archive_playback_sidecar_serves_binary_jpeg_without_base64_segment(self):
        root = Path(__file__).resolve().parents[1] / "runtime" / "recordings" / "unit_playback_sidecar"
        cache_root = Path(__file__).resolve().parents[1] / "runtime" / "playback_cache" / root.name
        root.mkdir(parents=True, exist_ok=True)
        jpeg = b"\xff\xd8unit-playback-jpeg\xff\xd9"
        encoded = base64.b64encode(jpeg).decode("ascii")
        try:
            (root / "metadata.json").write_text(
                json.dumps(
                    {
                        "id": root.name,
                        "kind": "both",
                        "duration_s": 8.0,
                        "timeline": {"lidar_frames": 2, "camera_frames": 2, "imu_frames": 0, "sample_hz": 0.25},
                    }
                ),
                encoding="utf-8",
            )
            (root / "analysis.json").write_text("{}", encoding="utf-8")
            (root / "preview_scan.json").write_text("{}", encoding="utf-8")
            (root / "timeline.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "duration_s": 8.0,
                        "lidar": {"frames": [{"t_s": 0.0, "points": []}, {"t_s": 4.0, "points": []}]},
                        "camera": {
                            "frames": [
                                {"t_s": 0.0, "mime": "image/jpeg", "data": encoded, "width": 16, "height": 9},
                                {"t_s": 4.0, "mime": "image/jpeg", "data": encoded, "width": 16, "height": 9},
                            ]
                        },
                        "imu": {"frames": []},
                    }
                ),
                encoding="utf-8",
            )

            segment = self.get_json(f"/api/recordings/{root.name}/timeline?start_s=0&duration_s=4")
            playback = segment["timeline"]["playback"]
            frame = segment["timeline"]["camera"]["frames"][0]
            self.assertEqual(playback["revision"], "raw_sensor_segment_v2")
            self.assertNotIn("data", frame)
            self.assertIn("/camera-frame/frame-", frame["frame_url"])
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{frame['frame_url']}", timeout=3) as res:
                self.assertEqual(res.headers.get("Content-Type"), "image/jpeg")
                self.assertIn("immutable", res.headers.get("Cache-Control", ""))
                self.assertEqual(res.read(), jpeg)
        finally:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(cache_root, ignore_errors=True)

    def test_lidar_bridge_status_file_marks_scan_live(self):
        status_file = Path(__file__).resolve().parents[1] / "runtime" / "test_lidar_bridge_status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        provider = self.server.RequestHandlerClass.provider
        old_provider_file = provider.lidar_bridge_status_file
        provider.lidar_bridge_status_file = status_file
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
            provider.lidar_bridge_status_file = old_provider_file
            if old is None:
                status_file.unlink(missing_ok=True)
            else:
                status_file.write_text(old, encoding="utf-8")

    def test_camera_driver_status_file_exposes_actual_modes(self):
        status_file = Path(__file__).resolve().parents[1] / "runtime" / "test_camera_driver_status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        provider = self.server.RequestHandlerClass.provider
        old_provider_file = provider.camera_driver_status_file
        provider.camera_driver_status_file = status_file
        old = status_file.read_text(encoding="utf-8") if status_file.exists() else None
        try:
            status_file.write_text(
                json.dumps(
                    {
                        "updated_at": time.time(),
                        "state": "usb_detected_video_node_missing",
                        "present": False,
                        "usb_present": True,
                        "video_nodes": [],
                        "usb_devices": [
                            {
                                "vendor_id": "0c45",
                                "product_id": "6362",
                                "manufacturer": "Sonix Technology Co., Ltd.",
                                "product": "USB 2.0 Camera",
                            }
                        ],
                        "last_error": "USB camera video node not found",
                        "supported_view_modes": [
                            {"resolution": "1920x1080", "width": 1920, "height": 1080, "fps": [30]},
                            {"resolution": "1280x720", "width": 1280, "height": 720, "fps": [60]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            payload = self.get_json("/api/connection")
            camera = payload["camera"]
            self.assertTrue(camera["usb_present"])
            self.assertEqual(camera["driver_state"], "usb_detected_video_node_missing")
            self.assertEqual(camera["supported_view_modes_source"], "v4l2")
            self.assertEqual(camera["supported_view_modes"][0]["fps"], [30])
            self.assertEqual(camera["supported_view_modes"][1]["fps"], [60])
            self.assertIn(
                payload["sensors"]["camera"]["state"],
                {"video_node_missing", "waiting_for_ros2", "waiting_for_frame", "frame_live"},
            )
        finally:
            provider.camera_driver_status_file = old_provider_file
            if old is None:
                status_file.unlink(missing_ok=True)
            else:
                status_file.write_text(old, encoding="utf-8")

    def test_camera_settings_endpoint_writes_capture_settings(self):
        root = Path(__file__).resolve().parents[1]
        settings_file = root / "runtime" / "camera_settings.json"
        driver_status_file = root / "runtime" / "camera_driver_status.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        old_settings = settings_file.read_text(encoding="utf-8") if settings_file.exists() else None
        old_driver = driver_status_file.read_text(encoding="utf-8") if driver_status_file.exists() else None
        try:
            driver_status_file.write_text(
                json.dumps(
                    {
                        "updated_at": time.time(),
                        "supported_view_modes": [
                            {"resolution": "1920x1080", "width": 1920, "height": 1080, "fps": [30]},
                            {"resolution": "1280x720", "width": 1280, "height": 720, "fps": [60]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            payload = self.post_json("/api/camera/settings", {"resolution": "1280x720", "fps": 60})
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["settings"]["resolution"], "1280x720")
            self.assertEqual(payload["settings"]["fps"], 60)
            saved = json.loads(settings_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["width"], 1280)
            self.assertEqual(saved["height"], 720)
            self.assertEqual(saved["fps"], 60)
            rejected = self.post_json("/api/camera/settings", {"resolution": "1280x720", "fps": 30})
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["error"], "unsupported_camera_fps")
        finally:
            if old_settings is None:
                settings_file.unlink(missing_ok=True)
            else:
                settings_file.write_text(old_settings, encoding="utf-8")
            if old_driver is None:
                driver_status_file.unlink(missing_ok=True)
            else:
                driver_status_file.write_text(old_driver, encoding="utf-8")

    def test_plan_contains_research_topic(self):
        payload = self.get_json("/api/plan")
        self.assertIn("2D LiDAR", payload["research_topic"])
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
        self.assertIn("simulation-run", slugs)
        self.assertIn("lidar-connection", slugs)
        self.assertIn("lidar-drive-test-recording", slugs)
        self.assertIn("usb-camera-connection", slugs)
        self.assertIn("imu-ros2-recording", slugs)
        self.assertIn("jetson-offline-access-20260827", slugs)
        self.assertIn("esp-rover-control", slugs)
        self.assertIn("rover-control-dashboard", slugs)
        self.assertIn("rover-control-todo", slugs)
        sim_doc = self.get_json("/api/docs/simulation-run")
        self.assertTrue(sim_doc["ok"])
        self.assertIn("orchard_full.launch.py", sim_doc["markdown"])
        self.assertIn("mode:=sim", sim_doc["markdown"])
        doc = self.get_json("/api/docs/lidar-connection")
        self.assertTrue(doc["ok"])
        self.assertIn("RPLIDAR", doc["markdown"])
        camera_doc = self.get_json("/api/docs/usb-camera-connection")
        self.assertTrue(camera_doc["ok"])
        self.assertIn("/camera/image_raw", camera_doc["markdown"])
        imu_doc = self.get_json("/api/docs/imu-ros2-recording")
        self.assertTrue(imu_doc["ok"])
        self.assertIn("/imu/data_raw", imu_doc["markdown"])
        self.assertIn("Dashboard", imu_doc["markdown"])
        offline_doc = self.get_json("/api/docs/jetson-offline-access-20260827")
        self.assertTrue(offline_doc["ok"])
        self.assertIn("wlP1p1s0", offline_doc["markdown"])
        self.assertIn("jetson-ssh.sarihub.com", offline_doc["markdown"])
        drive_doc = self.get_json("/api/docs/lidar-drive-test-recording")
        self.assertTrue(drive_doc["ok"])
        self.assertIn("rosbag2", drive_doc["markdown"])
        rover_doc = self.get_json("/api/docs/esp-rover-control")
        self.assertTrue(rover_doc["ok"])
        self.assertIn("ESP8266", rover_doc["markdown"])
        control_doc = self.get_json("/api/docs/rover-control-dashboard")
        self.assertTrue(control_doc["ok"])
        self.assertIn("control.sock", control_doc["markdown"])
        todo_doc = self.get_json("/api/docs/rover-control-todo")
        self.assertTrue(todo_doc["ok"])
        self.assertIn("Rover Control TODO", todo_doc["markdown"])
        html = self.get_text("/docs/lidar-connection")
        self.assertIn("id=\"docMarkdown\"", html)

    def test_raw_sensor_pages_and_removed_features(self):
        from html.parser import HTMLParser
        class Elements(HTMLParser):
            def __init__(self):
                super().__init__(); self.ids = set()
            def handle_starttag(self, tag, attrs):
                self.ids.add(dict(attrs).get("id"))
        html = self.get_text("/rover-control")
        elements = Elements(); elements.feed(html)
        for name in ("roverCameraImage", "roverLidarCanvas", "roverArmButton", "roverSpeedSlider", "roverArchivePlayButton", "roverArchiveScrubber", "roverArchiveCameraImage", "roverArchiveLidarCanvas"):
            self.assertIn(name, elements.ids)
        self.assertNotIn("Fusion Trees", html)
        self.assertNotIn("roverCameraOverlayCanvas", elements.ids)
        self.assertIn("SIMULATION", html)
        self.assertNotIn("fusion", self.get_json("/api/telemetry"))


    def test_lidar_websocket_requires_browser_session(self):
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        with sock:
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
            self.assertIn("403", raw.decode("iso-8859-1"))

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

    def test_camera_websocket_proxy_graceful_without_bridge(self):
        old_upstream = os.environ.get("CAMERA_WS_UPSTREAM")
        os.environ["CAMERA_WS_UPSTREAM"] = "ws://127.0.0.1:1/ws/camera"
        try:
            sock, leftover = self.open_dashboard_ws("/ws/camera?resolution=640x480&fps=5")
            with sock:
                payload = json.loads(self.read_ws_text(sock, leftover))
                self.assertEqual(payload["type"], "status")
                self.assertFalse(payload["frame_topic_alive"])
                self.assertIn("bridge unavailable", payload["error"])
        finally:
            if old_upstream is None:
                os.environ.pop("CAMERA_WS_UPSTREAM", None)
            else:
                os.environ["CAMERA_WS_UPSTREAM"] = old_upstream

    def test_imu_websocket_requires_browser_session(self):
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        with sock:
            sock.sendall(
                (
                    "GET /ws/imu HTTP/1.1\r\n"
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
            self.assertIn("403", raw.decode("iso-8859-1"))

    def test_imu_websocket_proxy_graceful_without_bridge(self):
        old_upstream = os.environ.get("IMU_WS_UPSTREAM")
        os.environ["IMU_WS_UPSTREAM"] = "ws://127.0.0.1:1/ws/imu"
        try:
            sock, leftover = self.open_dashboard_ws("/ws/imu")
            with sock:
                payload = json.loads(self.read_ws_text(sock, leftover))
                self.assertEqual(payload["type"], "imu")
                self.assertFalse(payload["imu_topic_alive"])
                self.assertIn("bridge unavailable", payload["error"])
        finally:
            if old_upstream is None:
                os.environ.pop("IMU_WS_UPSTREAM", None)
            else:
                os.environ["IMU_WS_UPSTREAM"] = old_upstream

    def test_imu_websocket_proxy_relays_ros2_bridge_frame(self):
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
                key_line = next(line for line in request_lines if line.lower().startswith("sec-websocket-key:"))
                upstream_key = key_line.split(":", 1)[1].strip()
                accept = base64.b64encode(
                    hashlib.sha1(
                        (upstream_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
                    ).digest()
                ).decode("ascii")
                frame = {
                    "type": "imu",
                    "schema": "orchard.imu_websocket.v1",
                    "imu_topic_alive": True,
                    "raw": {"frame_id": "imu_link", "orientation": {"x": 0, "y": 0, "z": 0, "w": 1}},
                }
                conn.sendall(
                    (
                        "HTTP/1.1 101 Switching Protocols\r\n"
                        "Upgrade: websocket\r\n"
                        "Connection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                    ).encode("ascii")
                    + self.ws_frame(frame)
                )
                time.sleep(0.2)
            upstream.close()

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        old_upstream = os.environ.get("IMU_WS_UPSTREAM")
        os.environ["IMU_WS_UPSTREAM"] = f"ws://127.0.0.1:{upstream_port}/ws/imu"
        try:
            sock, leftover = self.open_dashboard_ws("/ws/imu")
            with sock:
                payload = json.loads(self.read_ws_text(sock, leftover))
                self.assertEqual(payload["schema"], "orchard.imu_websocket.v1")
                self.assertTrue(payload["imu_topic_alive"])
                self.assertEqual(payload["raw"]["frame_id"], "imu_link")
            self.assertEqual(captured_request_line, ["GET /ws/imu HTTP/1.1"])
        finally:
            if old_upstream is None:
                os.environ.pop("IMU_WS_UPSTREAM", None)
            else:
                os.environ["IMU_WS_UPSTREAM"] = old_upstream
            thread.join(timeout=2)

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
        self.assertIn("simulation-run", slugs)
        self.assertIn("lidar-drive-test-recording", slugs)
        self.assertIn("rover-control-dashboard", slugs)
        self.assertIn("rover-control-todo", slugs)
        self.assertIn("imu-ros2-recording", slugs)
        self.assertIn("jetson-offline-access-20260827", slugs)


if __name__ == "__main__":
    unittest.main()
