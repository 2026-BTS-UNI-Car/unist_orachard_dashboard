from __future__ import annotations

import json
import threading
import unittest
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

    def test_health(self):
        payload = self.get_json("/api/health")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service"], "unist_orachard_dashboard")

    def test_telemetry_shape(self):
        payload = self.get_json("/api/telemetry")
        for key in ["system", "rover", "sensors", "fusion", "experiment", "map"]:
            self.assertIn(key, payload)
        self.assertIn("lidar_reliability", payload["fusion"])

    def test_plan_contains_research_topic(self):
        payload = self.get_json("/api/plan")
        self.assertIn("Vision-LiDAR", payload["research_topic"])
        self.assertEqual(payload["team"], "유니주행차")


if __name__ == "__main__":
    unittest.main()
