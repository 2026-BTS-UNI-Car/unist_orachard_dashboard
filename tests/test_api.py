from __future__ import annotations

import json
from pathlib import Path
import threading
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
        self.assertNotIn("lidarScanCanvas3d", html)
        self.assertNotIn("data-lidar-map-mode=\"3d\"", html)
        self.assertFalse((Path(__file__).resolve().parents[1] / "static" / "lidar3d.js").exists())
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/static/lidar3d.js", timeout=3)
        self.assertEqual(ctx.exception.code, 404)

    def test_docs_catalog_contains_runtime_and_fan_docs(self):
        index = self.get_json("/api/docs")
        slugs = {item["slug"] for item in index["documents"]}
        self.assertIn("jetson-runtime-architecture", slugs)
        self.assertIn("fan-control", slugs)


if __name__ == "__main__":
    unittest.main()
