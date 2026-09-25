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
        path = "/docs-assets/orchard_perception_models/rf_yolo11_trunk_seg.jpg"
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=3) as res:
            self.assertEqual(res.status, 200)
            self.assertEqual(res.headers.get("Content-Type"), "image/jpeg")
            self.assertGreater(len(res.read()), 100)
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
        for key in ["system", "network", "docker", "fan", "connection", "rover", "sensors", "fusion", "experiment"]:
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
            orchard_cache = root.parent.parent / "orchard_analysis" / "unit_lidar_archive_orchard_map.json"
            orchard_cache.parent.mkdir(parents=True, exist_ok=True)
            orchard_cache.write_text(
                json.dumps({"ok": True, "id": "unit_lidar_archive", "map": {"algorithm": "orchard_three_layer_weighted_centerline_v8"}}),
                encoding="utf-8",
            )
            orchard_map = self.get_json("/api/recordings/unit_lidar_archive/orchard-map")
            self.assertTrue(orchard_map["ok"])
            self.assertEqual(orchard_map["map"]["algorithm"], "orchard_metric_anchor_semantic_fusion_local_frenet_v10")
            self.assertEqual(set(orchard_map["map"]["maps"].keys()), {"lidar", "camera", "fusion"})
            self.assertIn("centerline", orchard_map["map"]["maps"]["fusion"])
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
            self.assertEqual(playback["revision"], "binary_jpeg_segment_sidecar_v1")
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
        self.assertIn("simulation-run", slugs)
        self.assertIn("lidar-connection", slugs)
        self.assertIn("lidar-drive-test-recording", slugs)
        self.assertIn("usb-camera-connection", slugs)
        self.assertIn("imu-ros2-recording", slugs)
        self.assertIn("jetson-offline-access-20260827", slugs)
        self.assertIn("esp-rover-control", slugs)
        self.assertIn("rover-control-dashboard", slugs)
        self.assertIn("lidar-archive-analysis", slugs)
        self.assertIn("lidar-object-overlay", slugs)
        self.assertIn("orchard-tree-algorithm-report", slugs)
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
        analysis_doc = self.get_json("/api/docs/lidar-archive-analysis")
        self.assertTrue(analysis_doc["ok"])
        self.assertIn("polar_gap_cluster", analysis_doc["markdown"])
        overlay_doc = self.get_json("/api/docs/lidar-object-overlay")
        self.assertTrue(overlay_doc["ok"])
        self.assertIn("Object 글자는", overlay_doc["markdown"])
        todo_doc = self.get_json("/api/docs/rover-control-todo")
        self.assertTrue(todo_doc["ok"])
        self.assertIn("Rover Control TODO", todo_doc["markdown"])
        report_doc = self.get_json("/api/docs/orchard-tree-algorithm-report")
        self.assertTrue(report_doc["ok"])
        self.assertIn("과수 인식 알고리즘 기술 부록", report_doc["markdown"])
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
        self.assertIn("min=\"50\" max=\"3200\" step=\"50\" value=\"900\"", html)
        self.assertIn("data-lidar-points=\"reset\"", html)
        self.assertIn("id=\"systemSwapValue\"", html)
        self.assertIn("id=\"systemNetworkValue\"", html)
        self.assertIn("id=\"systemPowerValue\"", html)
        self.assertIn("id=\"hostPower\"", html)
        self.assertIn("id=\"containerDetail\"", html)
        self.assertIn("id=\"containerLogs\"", html)
        self.assertIn("id=\"containerActions\"", html)
        self.assertIn("id=\"containerRefreshButton\"", html)
        self.assertIn("id=\"cameraLiveButton\"", html)
        self.assertIn("id=\"cameraLiveImage\"", html)
        self.assertIn("id=\"cameraLiveOverlayCanvas\"", html)
        self.assertIn("id=\"cameraResolutionSelect\"", html)
        self.assertIn("id=\"cameraFpsSelect\"", html)
        self.assertIn("data-route=\"/rover-control\"", html)
        self.assertIn("id=\"roverSpeedSlider\"", html)
        self.assertIn("id=\"roverDrivePanelToggle\"", html)
        self.assertIn("data-rover-drive=\"forward\"", html)
        self.assertIn("id=\"roverCameraToggle\"", html)
        self.assertIn("id=\"roverLidarToggle\"", html)
        self.assertIn("id=\"roverRecordLidarButton\"", html)
        self.assertIn("id=\"roverRecordCameraButton\"", html)
        self.assertIn("id=\"roverRecordBothButton\"", html)
        self.assertIn("id=\"roverRecordStartButton\"", html)
        self.assertIn("id=\"roverRecordingTimer\"", html)
        self.assertNotIn("id=\"roverControlPassword\"", html)
        self.assertIn("aria-pressed=\"false\">조종 활성화 OFF", html)
        self.assertIn("id=\"roverArmButton\"", html)
        self.assertIn("data-lidar-overlay-mode=\"both\"", html)
        self.assertIn("data-archive-overlay-mode=\"overlay\"", html)
        self.assertIn("id=\"roverArchiveOverlayToggle\"", html)
        self.assertIn("id=\"roverArchiveRangeSlider\"", html)
        self.assertIn("id=\"roverArchiveDownloadButton\"", html)
        self.assertIn("id=\"roverArchivePlayButton\"", html)
        self.assertIn("id=\"roverArchiveScrubber\"", html)
        self.assertIn("id=\"roverArchiveCameraImage\"", html)
        self.assertIn("id=\"roverArchiveCameraOverlayCanvas\"", html)
        self.assertIn("id=\"roverArchiveList\"", html)
        self.assertIn("id=\"roverArchiveLidarCanvas\"", html)
        self.assertNotIn("id=\"roverProxyFrame\"", html)
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
        self.assertIn("LIDAR_SPEC_MAX_POINTS = 3200", app_js)
        self.assertIn("LIDAR_RECONNECT_DEBOUNCE_MS", app_js)
        self.assertIn("scheduleLidarReconnectForSettings", app_js)
        self.assertIn("closeLidarLive", app_js)
        self.assertIn("setLidarOverlayMode", app_js)
        self.assertIn("setArchiveOverlayMode", app_js)
        self.assertIn("setArchiveOverlayVisible", app_js)
        self.assertIn("setArchiveRange", app_js)
        self.assertIn("prepareArchiveTimelineCache", app_js)
        self.assertIn("archiveCameraFrameSrc", app_js)
        self.assertIn("cameraFrame?.frame_url || cameraFrame?.data", app_js)
        self.assertIn("cameraBootstrapNeeded", app_js)
        self.assertIn("setRoverDrivePanelCollapsed", app_js)
        self.assertIn("toggleArchivePlayback", app_js)
        self.assertIn("Live 화면 밖에서는 LiDAR WebSocket을 닫습니다.", app_js)
        self.assertIn("max_points", app_js)
        self.assertIn("downloadLidarPng", app_js)
        self.assertIn("/api/docker/container/", app_js)
        self.assertIn("data-container-action=\"restart\"", app_js)
        self.assertIn("${scheme}://${location.host}/ws/camera", app_js)
        self.assertIn("CAMERA_RESOLUTION_KEY", app_js)
        self.assertIn("DEFAULT_CAMERA_MODES", app_js)
        self.assertIn("applyCameraModeCatalog", app_js)
        self.assertIn("/api/camera/settings", app_js)
        self.assertIn("/api/imu/zero", app_js)
        self.assertIn("roverImuZeroButton", html)
        self.assertIn("/api/network/mode", app_js)
        self.assertIn("networkEnableApButton", html)
        self.assertLess(html.index("networkEnableApButton"), html.index("networkModeReadout"))
        self.assertIn("networkScanWifiButton", html)
        self.assertIn("Observed FPS", app_js)
        self.assertIn("syncSelectOptions", app_js)
        self.assertIn("initCameraLive();", app_js)
        self.assertIn("Live 화면 밖에서는 Camera WebSocket을 닫습니다.", app_js)
        self.assertIn("renderRoverControl", app_js)
        self.assertIn("initRoverControl();", app_js)
        self.assertIn("/api/rover/${op}", app_js)
        self.assertIn("/api/recordings/start", app_js)
        self.assertIn("/timeline", app_js)
        self.assertIn("/orchard-map", app_js)
        self.assertIn("PERCEPTION_OVERLAY_KEY", app_js)
        self.assertIn("CAMERA_OVERLAY_MODE_KEY", app_js)
        self.assertIn("drawCameraOverlayOnCanvas", app_js)
        self.assertIn("archiveFusedTreesForTime", app_js)
        self.assertIn("drawOrchardMap", app_js)
        self.assertIn("orchardMapDisplayFrame", app_js)
        self.assertIn("projectHeading", app_js)
        self.assertIn("roverArchiveCameraVideo", html)
        self.assertIn("archiveNativeVideo", app_js)
        self.assertIn("safeDocAssetHref", app_js)
        self.assertIn("/docs-assets/", app_js)
        self.assertIn("ARCHIVE_SEGMENT_SECONDS = 4", app_js)
        self.assertIn('ARCHIVE_PLAYBACK_PROFILE = "dashboard-compact-v1"', app_js)
        self.assertIn("scheduleArchiveSegmentsForTime", app_js)
        self.assertIn("scheduleArchiveSeek", app_js)
        self.assertIn("preloadArchiveCameraFrame", app_js)
        self.assertIn("No verified AI result for this camera frame", app_js)
        self.assertNotIn('debugLog("archive", "render segment load failed"', app_js)
        self.assertIn("ARCHIVE_IMU_SYNC_MAX_DELTA_S = 0.2", app_js)
        self.assertIn("recordingSensorLabel", app_js)
        self.assertIn("roverArchiveImuPanel", html)
        self.assertIn("roverArchiveImuCanvas", html)
        self.assertIn("roverArchiveImuTime", html)
        self.assertIn("roverKeyFromEvent", app_js)
        self.assertIn("drawLidarMapOnCanvas", app_js)
        self.assertNotIn("fillText(item.label", app_js)
        self.assertIn("chartShiftDurationMs", app_js)
        self.assertIn("CHART_BUFFER_POINTS = MAX_POINTS + 1", app_js)
        self.assertNotIn("downloadCanvasPng", app_js)
        self.assertNotIn("lidarAccumulate", app_js)
        self.assertNotIn("gpuReadout", app_js)
        self.assertIn("displayAngle", app_js)
        self.assertNotIn("Front 0°", app_js)
        compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
        telemetry_py = (Path(__file__).resolve().parents[1] / "dashboard_server" / "telemetry.py").read_text()
        lidar_bridge_py = (Path(__file__).resolve().parents[1] / "scripts" / "lidar_ws_bridge.py").read_text()
        lidar_driver_dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile.lidar_driver").read_text()
        lidar_driver_script = (Path(__file__).resolve().parents[1] / "scripts" / "run_lidar_driver.sh").read_text()
        self.assertIn("ros2-lidar-driver", compose)
        self.assertIn("ros2-lidar-bridge", compose)
        self.assertIn("ros2-camera-driver", compose)
        self.assertIn("ros2-camera-bridge", compose)
        self.assertIn("ros2-esp-rover-bridge", compose)
        self.assertIn("ros2-data-recorder", compose)
        self.assertIn("CAMERA_WS_UPSTREAM", compose)
        self.assertIn("DASHBOARD_CONTAINER_NAME", compose)
        self.assertIn("CAMERA_DRIVER_STATUS_FILE", compose)
        self.assertIn("CAMERA_SETTINGS_FILE", compose)
        self.assertIn("CAMERA_MAX_TICK_FPS", compose)
        self.assertIn("CAMERA_AUTO_USB_RECOVER", compose)
        self.assertIn("/sys:/host-sys:rw", compose)
        self.assertIn("ROVER_BRIDGE_STATUS_FILE", compose)
        self.assertIn("ROVER_CONTROL_SOCKET", compose)
        self.assertIn("RECORDER_API_URL", compose)
        self.assertIn("RECORDING_ARCHIVE_DIR", compose)
        self.assertIn("DATA_RECORDER_STATUS_FILE", compose)
        self.assertIn("RECORDER_TIMELINE_HZ", compose)
        self.assertIn("RECORDER_CAMERA_PREVIEW_WIDTH", compose)
        self.assertIn("ORCHARD_VISION_INFERENCE_WIDTH:-0", compose)
        self.assertIn("ORCHARD_VISION_INFERENCE_JPEG_QUALITY:-86", compose)
        self.assertIn("RECORDER_SHARED_VISION_SYNC_MAX_DELTA_S:-0.16", compose)
        self.assertIn("RECORDER_SHARED_VISION_WAIT_TIMEOUT_S:-0.35", compose)
        self.assertIn("--vision-inference-width", compose)
        self.assertIn("--camera-vision-sync-max-delta-s", compose)
        self.assertNotIn("ROVER_NETWORK_ENABLED", compose)
        self.assertNotIn("--no-allow-network", compose)
        self.assertNotIn("--command-file", compose)
        self.assertNotIn("ROVER_BASE_URL", compose)
        self.assertIn("Dockerfile.lidar_driver", compose)
        self.assertIn("Dockerfile.ros2", compose)
        self.assertIn("LIDAR_BRIDGE_SOURCE:-ros2", compose)
        self.assertIn("LIDAR_SCAN_MAX_POINTS:-3200", compose)
        self.assertIn("VISION_AI_BASE_IMAGE:-roboflow/roboflow-inference-server-jetson-6.0.0:1.3.2-rc1", compose)
        self.assertIn("VISION_AI_INSTALL_REQUIREMENTS:-0", compose)
        self.assertIn("ORCHARD_VISION_BACKEND:-operational", compose)
        self.assertIn("orchard_trunk_teacher_ood_v1/weights/best.engine", compose)
        self.assertIn("ORCHARD_CLIP_MODEL:-openai/clip-vit-base-patch32", compose)
        self.assertIn("HF_HOME:-/app/runtime/models/hf", compose)
        self.assertIn("ORCHARD_ROBOFLOW_MODEL_ID:-tree-trunk-detection-369pz-vm3hn/2", compose)
        self.assertIn("./runtime:/app/runtime", compose)
        self.assertNotIn("LIDAR_BRIDGE_SOURCE:-auto", compose)
        deploy_workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy.yml").read_text()
        self.assertIn("! -name .env", deploy_workflow)
        self.assertIn("http://127.0.0.1:8785/health", deploy_workflow)
        self.assertIn("ORCHARD_VISION_AI_URL", deploy_workflow)
        self.assertIn("recreate_service()", deploy_workflow)
        self.assertIn("$COMPOSE up --no-deps -d \"$service\"", deploy_workflow)
        self.assertNotIn("$COMPOSE down", deploy_workflow)
        self.assertIn("label=com.docker.compose.project=unist_orachard_dashboard", deploy_workflow)
        self.assertIn("precompute_archive_lidar_overlays.py", deploy_workflow)
        self.assertIn("v10_rep103_robust_tracks_unique_semantics_row_model_native_input_r3", deploy_workflow)
        self.assertIn("deployment_safety_contract_ok", deploy_workflow)
        self.assertIn("research_readiness_is_non_blocking", deploy_workflow)
        smoke_workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "smoke.yml").read_text()
        self.assertIn("docker compose config --quiet", smoke_workflow)
        self.assertIn("docker build --file Dockerfile ", smoke_workflow)
        self.assertIn("docker build --file Dockerfile.ros2 ", smoke_workflow)
        self.assertIn('test_camera_ws_bridge.py', smoke_workflow)
        self.assertIn("import_video_archive.py", (Path(__file__).resolve().parents[1] / "Dockerfile.vision_ai").read_text())
        for service_name in ["dashboard", "ros2-camera-bridge"]:
            match = re.search(rf"^  {re.escape(service_name)}:\n(?P<block>.*?)(?=^  [\w-]+:|\Z)", compose, re.M | re.S)
            self.assertIsNotNone(match, service_name)
            service_block = match.group("block")
            self.assertIn("ORCHARD_VISION_AI_URL", service_block, service_name)
            self.assertIn("ORCHARD_VISION_AI_TIMEOUT_S", service_block, service_name)
        recorder_match = re.search(r"^  ros2-data-recorder:\n(?P<block>.*?)(?=^  [\w-]+:|\Z)", compose, re.M | re.S)
        self.assertIsNotNone(recorder_match)
        recorder_block = recorder_match.group("block")
        self.assertNotIn("ORCHARD_VISION_AI_URL", recorder_block)
        self.assertIn("RECORDER_SHARED_VISION_SYNC_MAX_DELTA_S", recorder_block)
        self.assertIn("stats?stream=false&one-shot=true", telemetry_py)
        self.assertIn("def docker_container_logs", telemetry_py)
        self.assertIn("def docker_container_action", telemetry_py)
        self.assertIn("def _detect_self_container_id", telemetry_py)
        self.assertIn("ina3221", telemetry_py)
        self.assertIn("findmnt", telemetry_py)
        self.assertIn("def _host_mounted_partitions", telemetry_py)
        self.assertIn("DEFAULT_CAMERA_VIEW_MODES", telemetry_py)
        self.assertIn("def set_camera_settings", telemetry_py)
        self.assertIn("def _camera_driver_status", telemetry_py)
        self.assertIn("def set_rover_command", telemetry_py)
        self.assertIn("def start_recording", telemetry_py)
        self.assertIn("def recording_detail", telemetry_py)
        self.assertIn("def recording_orchard_map", telemetry_py)
        camera_driver_py = (Path(__file__).resolve().parents[1] / "scripts" / "camera_ros2_driver.py").read_text()
        self.assertIn("v4l2-ctl", camera_driver_py)
        self.assertIn("USBDEVFS_RESET", camera_driver_py)
        self.assertIn("usb_recover_attempt", camera_driver_py)
        self.assertIn("supported_view_modes", camera_driver_py)
        self.assertIn("settings_changed", camera_driver_py)
        self.assertIn("rotate_180", camera_driver_py)
        self.assertIn('publisher_qos="best_effort_keep_last_1"', camera_driver_py)
        self.assertIn("ReliabilityPolicy.BEST_EFFORT", camera_driver_py)
        self.assertNotIn("def _gpu_percent", telemetry_py)
        self.assertIn("source_max_points", lidar_bridge_py)
        self.assertIn("\"scan_received\"", lidar_bridge_py)
        self.assertIn("max(50, int(max_points or 900))", lidar_bridge_py)
        self.assertIn("max(50, min(max(source_max, default), value))", lidar_bridge_py)
        self.assertNotIn("source_max=max_points", lidar_bridge_py)
        self.assertIn("sllidar_s2_launch.py", lidar_driver_script)
        self.assertNotIn("/dev/serial/by-id/*CP210*", lidar_driver_script)
        self.assertIn("driver_pid", lidar_driver_script)
        self.assertIn("log unplugged", lidar_driver_script)
        self.assertIn("source_setup /opt/ros/iron/setup.bash", lidar_driver_script)
        self.assertIn("set +u", lidar_driver_script)
        self.assertIn("run_lidar_driver.sh", lidar_driver_dockerfile)
        self.assertIn("/sys/class/hwmon:/sys/class/hwmon:rw", compose)
        self.assertTrue((Path(__file__).resolve().parents[1] / "scripts" / "rover_control_bridge.py").exists())
        self.assertTrue((Path(__file__).resolve().parents[1] / "scripts" / "data_recorder_service.py").exists())
        rover_bridge_py = (Path(__file__).resolve().parents[1] / "scripts" / "rover_control_bridge.py").read_text()
        self.assertIn("RoverControl", rover_bridge_py)
        self.assertNotIn("create_subscription", rover_bridge_py)
        recorder_py = (Path(__file__).resolve().parents[1] / "scripts" / "data_recorder_service.py").read_text()
        camera_bridge_py = (Path(__file__).resolve().parents[1] / "scripts" / "camera_ws_bridge.py").read_text()
        lidar_analysis_py = (Path(__file__).resolve().parents[1] / "dashboard_server" / "lidar_analysis.py").read_text()
        self.assertIn("polar_gap_cluster_wall_context_tree_ood_predictive_tracks_v6", lidar_analysis_py)
        self.assertIn("structural_post_without_canopy", (Path(__file__).resolve().parents[1] / "scripts" / "vision_ai_service.py").read_text())
        self.assertIn("timeline.json", recorder_py)
        self.assertIn("ros2\", \"bag\", \"record", recorder_py)
        for topic in ("/odom", "/imu/data", "/tf", "/tf_static", "/orchard/vision/tree_bearings"):
            self.assertIn(topic, recorder_py)
        self.assertIn("orchard.camera_bearings.v1", camera_bridge_py)
        self.assertIn("pixel-right is negative", camera_bridge_py)
        self.assertIn("vision_inference_thread", camera_bridge_py)
        self.assertIn("single_slot_newest_frame_no_backlog", camera_bridge_py)
        self.assertIn("camera_archive_worker", recorder_py)
        self.assertIn("single_slot_archive_worker_and_dds_best_effort_keep_last_1", recorder_py)
        self.assertIn('"inference_owner": "camera_ws_bridge"', recorder_py)
        self.assertNotIn("analyze_camera_image(", recorder_py)
        self.assertIn("depth=1", recorder_py)
        self.assertIn("std_msgs.msg import String", camera_bridge_py)
        self.assertIn("ORCHARD_VISION_PUBLISH_HZ", compose)
        self.assertIn("ORCHARD_VISION_BEARING_CALIBRATION_VALID", compose)
        self.assertIn("ORCHARD_VISION_BEARINGS_TOPIC", compose)
        deploy_workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy.yml").read_text()
        self.assertIn("camera always-on vision publisher smoke failed after 180s", deploy_workflow)
        self.assertIn('"payload_source": payload.get("source") == "camera_ws_bridge_shared_worker_v4"', deploy_workflow)
        self.assertIn('"no_backlog": vision.get("coalescing") == "single_slot_newest_frame_no_backlog"', deploy_workflow)
        self.assertIn('"input_policy_revision": input_policy.get("revision") == "source_native_preview_decoupled_jpeg_v1"', deploy_workflow)
        self.assertIn('"input_policy_mode": input_policy.get("mode") == "source_native"', deploy_workflow)
        self.assertIn('"actual_input_jpeg_quality": int(actual_input.get("jpeg_quality") or 0) == 86', deploy_workflow)
        self.assertIn('"actual_inference_is_source_width"', deploy_workflow)
        self.assertIn('"camera_info_provenance_revision": vision.get("camera_info_provenance_revision") == "camera_info_image_header_integrity_v2"', deploy_workflow)
        self.assertIn('"camera_info_provenance_valid": vision.get("camera_info_provenance_valid") is True', deploy_workflow)
        self.assertIn("shared_vision_provenance_rejected_count", deploy_workflow)
        self.assertIn("shared_vision_received_age_s", deploy_workflow)

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
        self.assertIn("lidar-archive-analysis", slugs)
        self.assertIn("lidar-object-overlay", slugs)
        self.assertIn("orchard-tree-algorithm-report", slugs)
        self.assertIn("rover-control-todo", slugs)
        self.assertIn("imu-ros2-recording", slugs)
        self.assertIn("jetson-offline-access-20260827", slugs)


if __name__ == "__main__":
    unittest.main()
