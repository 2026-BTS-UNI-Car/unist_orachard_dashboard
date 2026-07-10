from __future__ import annotations

import json
import gzip
import os
from pathlib import Path
import base64
import hashlib
import re
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
        self.assertIn("rover_bridge", connection)
        self.assertIn("rover", connection)
        self.assertIn("data_recorder", connection["services"])
        self.assertIn("test_mode", connection["lidar"])
        self.assertEqual(connection["lidar"]["test_mode"], "ros2_subscribe_only")
        self.assertEqual(connection["camera"]["test_mode"], "ros2_subscribe_only")

        lidar_test = self.post_json("/api/lidar/test")
        self.assertIn("status", lidar_test)
        self.assertNotIn("protocol", lidar_test)
        self.assertNotIn("baud", lidar_test)

    def test_lidar_scan_endpoint_shape(self):
        payload = self.post_json("/api/lidar/scan")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["state"], "disabled")
        self.assertEqual(payload["points"], [])

    def test_rover_stop_writes_zero_command_file(self):
        command_file = Path(__file__).resolve().parents[1] / "runtime" / "rover_command.json"
        command_file.parent.mkdir(parents=True, exist_ok=True)
        old = command_file.read_text(encoding="utf-8") if command_file.exists() else None
        try:
            payload = self.post_json("/api/rover/stop", {"source": "test_zero_only"})
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"]["linear"], 0.0)
            self.assertEqual(payload["command"]["angular"], 0.0)
            saved = json.loads(command_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["linear"], 0.0)
            self.assertEqual(saved["angular"], 0.0)
            self.assertEqual(saved["transport"], "serial_bridge_file")
        finally:
            if old is None:
                command_file.unlink(missing_ok=True)
            else:
                command_file.write_text(old, encoding="utf-8")

    def test_recordings_api_reads_archive_and_downloads_tarball(self):
        root = Path(__file__).resolve().parents[1] / "runtime" / "recordings" / "unit_lidar_archive"
        root.mkdir(parents=True, exist_ok=True)
        try:
            (root / "metadata.json").write_text(
                json.dumps({"id": "unit_lidar_archive", "kind": "lidar", "state": "archived", "duration_s": 1.2}),
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
            timeline = self.get_json("/api/recordings/unit_lidar_archive/timeline")
            self.assertTrue(timeline["ok"])
            self.assertEqual(timeline["timeline"]["algorithm"], "polar_gap_cluster_line_fit_fragment_merge_predictive_tracks_v5")
            segment = self.get_json("/api/recordings/unit_lidar_archive/timeline?start_s=0&duration_s=0.5")
            self.assertTrue(segment["ok"])
            self.assertTrue(segment["timeline"]["segment"]["partial"])
            self.assertEqual(len(segment["timeline"]["lidar"]["frames"]), 1)
            orchard_map = self.get_json("/api/recordings/unit_lidar_archive/orchard-map")
            self.assertTrue(orchard_map["ok"])
            self.assertEqual(orchard_map["map"]["algorithm"], "orchard_three_layer_weighted_centerline_v8")
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
            self.assertIn(payload["sensors"]["camera"]["state"], {"video_node_missing", "waiting_for_ros2", "frame_live"})
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
        self.assertIn("wave-rover-control", slugs)
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
        drive_doc = self.get_json("/api/docs/lidar-drive-test-recording")
        self.assertTrue(drive_doc["ok"])
        self.assertIn("rosbag2", drive_doc["markdown"])
        rover_doc = self.get_json("/api/docs/wave-rover-control")
        self.assertTrue(rover_doc["ok"])
        self.assertIn("WAVE ROVER", rover_doc["markdown"])
        control_doc = self.get_json("/api/docs/rover-control-dashboard")
        self.assertTrue(control_doc["ok"])
        self.assertIn("rover_command.json", control_doc["markdown"])
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
        self.assertIn("id=\"roverFadeStartToggle\"", html)
        self.assertIn("id=\"roverFadeStopToggle\"", html)
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
        self.assertIn("Observed FPS", app_js)
        self.assertIn("syncSelectOptions", app_js)
        self.assertIn("initCameraLive();", app_js)
        self.assertIn("Live 화면 밖에서는 Camera WebSocket을 닫습니다.", app_js)
        self.assertIn("renderRoverControl", app_js)
        self.assertIn("initRoverControl();", app_js)
        self.assertIn("/api/rover/command", app_js)
        self.assertIn("/api/recordings/start", app_js)
        self.assertIn("/timeline", app_js)
        self.assertIn("/orchard-map", app_js)
        self.assertIn("PERCEPTION_OVERLAY_KEY", app_js)
        self.assertIn("CAMERA_OVERLAY_MODE_KEY", app_js)
        self.assertIn("drawCameraOverlayOnCanvas", app_js)
        self.assertIn("archiveFusedTreesForTime", app_js)
        self.assertIn("drawOrchardMap", app_js)
        self.assertIn("safeDocAssetHref", app_js)
        self.assertIn("/docs-assets/", app_js)
        self.assertIn("ARCHIVE_SEGMENT_SECONDS = 4", app_js)
        self.assertIn("ROVER_FADE_START_KEY", app_js)
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
        self.assertIn("ros2-rover-bridge", compose)
        self.assertIn("ros2-data-recorder", compose)
        self.assertIn("CAMERA_WS_UPSTREAM", compose)
        self.assertIn("DASHBOARD_CONTAINER_NAME", compose)
        self.assertIn("CAMERA_DRIVER_STATUS_FILE", compose)
        self.assertIn("CAMERA_SETTINGS_FILE", compose)
        self.assertIn("CAMERA_MAX_TICK_FPS", compose)
        self.assertIn("CAMERA_AUTO_USB_RECOVER", compose)
        self.assertIn("/sys:/host-sys:rw", compose)
        self.assertIn("ROVER_BRIDGE_STATUS_FILE", compose)
        self.assertIn("ROVER_COMMAND_FILE", compose)
        self.assertIn("RECORDER_API_URL", compose)
        self.assertIn("RECORDING_ARCHIVE_DIR", compose)
        self.assertIn("DATA_RECORDER_STATUS_FILE", compose)
        self.assertIn("RECORDER_TIMELINE_HZ", compose)
        self.assertIn("RECORDER_CAMERA_PREVIEW_WIDTH", compose)
        self.assertIn("ROVER_NETWORK_ENABLED", compose)
        self.assertIn("--no-allow-network", compose)
        self.assertIn("--command-file", compose)
        self.assertIn("ROVER_BASE_URL", compose)
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
        for service_name in ["dashboard", "ros2-camera-bridge", "ros2-data-recorder"]:
            match = re.search(rf"^  {re.escape(service_name)}:\n(?P<block>.*?)(?=^  [\w-]+:|\Z)", compose, re.M | re.S)
            self.assertIsNotNone(match, service_name)
            service_block = match.group("block")
            self.assertIn("ORCHARD_VISION_AI_URL", service_block, service_name)
            self.assertIn("ORCHARD_VISION_AI_TIMEOUT_S", service_block, service_name)
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
        self.assertNotIn("def _gpu_percent", telemetry_py)
        self.assertIn("source_max_points", lidar_bridge_py)
        self.assertIn("\"scan_received\"", lidar_bridge_py)
        self.assertIn("max(50, int(max_points or 900))", lidar_bridge_py)
        self.assertIn("max(50, min(max(source_max, default), value))", lidar_bridge_py)
        self.assertNotIn("source_max=max_points", lidar_bridge_py)
        self.assertIn("sllidar_s2_launch.py", lidar_driver_script)
        self.assertIn("/dev/serial/by-id/*CP210*", lidar_driver_script)
        self.assertIn("driver_pid", lidar_driver_script)
        self.assertIn("log unplugged", lidar_driver_script)
        self.assertIn("source_setup /opt/ros/iron/setup.bash", lidar_driver_script)
        self.assertIn("set +u", lidar_driver_script)
        self.assertIn("run_lidar_driver.sh", lidar_driver_dockerfile)
        self.assertIn("/sys/class/hwmon:/sys/class/hwmon:rw", compose)
        self.assertTrue((Path(__file__).resolve().parents[1] / "scripts" / "wave_rover_bridge.py").exists())
        self.assertTrue((Path(__file__).resolve().parents[1] / "scripts" / "data_recorder_service.py").exists())
        rover_bridge_py = (Path(__file__).resolve().parents[1] / "scripts" / "wave_rover_bridge.py").read_text()
        self.assertIn("read_file_command", rover_bridge_py)
        self.assertIn("file_command_active", rover_bridge_py)
        recorder_py = (Path(__file__).resolve().parents[1] / "scripts" / "data_recorder_service.py").read_text()
        lidar_analysis_py = (Path(__file__).resolve().parents[1] / "dashboard_server" / "lidar_analysis.py").read_text()
        self.assertIn("polar_gap_cluster_wall_context_tree_ood_predictive_tracks_v6", lidar_analysis_py)
        self.assertIn("structural_post_without_canopy", (Path(__file__).resolve().parents[1] / "scripts" / "vision_ai_service.py").read_text())
        self.assertIn("timeline.json", recorder_py)
        self.assertIn("ros2\", \"bag\", \"record", recorder_py)

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


if __name__ == "__main__":
    unittest.main()
