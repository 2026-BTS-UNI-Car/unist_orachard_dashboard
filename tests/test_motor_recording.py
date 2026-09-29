"""Motor recording contract; optional transport test requires an isolated ROS domain."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
import urllib.request
from unittest.mock import patch

from dashboard_server.motor_pwm import MOTOR_TIMELINE_SCHEMA
from dashboard_server.telemetry import TelemetryProvider
import test_data_recorder_service as fixtures
from test_motor_pwm import motor_status


class MotorArchiveApiTests(unittest.TestCase):
    def test_playback_api_reads_full_sidecar_before_preview_tail_and_legacy_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "test_motor"
            archive.mkdir()
            rows = [{"t_s": i/2, "pins": {"D1": {"reported_pwm": 255}}} for i in range(20)]
            timeline = {"ok": True, "duration_s": 10, "motor": {
                "schema": MOTOR_TIMELINE_SCHEMA, "enabled": True, "frames": rows[-2:], "frame_count": 20,
            }}
            (archive / "timeline.json").write_text(json.dumps(timeline))
            (archive / "motor.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
            provider = TelemetryProvider()
            provider.recording_archive_dir = root
            for profile in (None, "dashboard-compact-v1"):
                result = provider.recording_timeline("test_motor", start_s=1.2, duration_s=.8, profile=profile)
                self.assertEqual([row["t_s"] for row in result["timeline"]["motor"]["frames"]], [1, 1.5, 2])
            # Cached 4s-segment responses must still recover the early samples.
            for _ in range(2):
                result = provider.recording_timeline("test_motor", start_s=0, duration_s=4)
                self.assertEqual(result["timeline"]["motor"]["frames"][0]["t_s"], 0)
            timeline.pop("motor")
            (archive / "timeline.json").write_text(json.dumps(timeline))
            self.assertNotIn("motor", provider.recording_timeline("test_motor")["timeline"])

    def test_segment_retains_prior_motor_frame_even_when_window_contains_no_messages(self):
        provider = TelemetryProvider()
        timeline = {"motor": {"schema": MOTOR_TIMELINE_SCHEMA, "frames": [{"t_s": 1, "pins": {}}]}}
        result = provider._recording_timeline_segment(timeline, 5, 1)
        self.assertEqual(result["motor"]["frames"], timeline["motor"]["frames"])


@unittest.skipUnless(fixtures.RecordingState is not None, "ROS2 runtime required")
class MotorRecorderTests(unittest.TestCase):
    def make_state(self, temporary):
        state = fixtures.DataRecorderCameraPipelineTests().make_state(temporary)
        state.active = None
        return state

    def test_every_recording_kind_has_all_three_motor_topics_with_imu_gps_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(temporary)
            for kind in ("camera", "lidar", "both"):
                topics = state.topics_for(kind, include_imu=False, include_gps=False)
                for topic in ("/rover/status", "/rover/control/requested", "/rover/control/applied"):
                    self.assertIn(topic, topics)
                self.assertNotIn("/imu/data", topics)
                self.assertNotIn("/gps/data", topics)

    def test_start_stop_archive_motor_sidecar_descriptor_and_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(temporary)
            with patch.object(state, "start_rosbag", return_value=None):
                state.start("both", include_imu=False, include_gps=False)
            archive = Path(state.active["path"])
            state.observe_motor(SimpleNamespace(data=json.dumps(motor_status(updated_at=time.time()))))
            state.observe_motor(SimpleNamespace(data=json.dumps(motor_status(
                updated_at=time.time()-5, state="disarmed", armed=False,
                requested={"left": 0, "right": 0}, outputs={"D1": 255},
            ))))
            result = state.stop()
            self.assertTrue(result["ok"])
            metadata = json.loads((archive / "metadata.json").read_text())
            timeline = json.loads((archive / "timeline.json").read_text())
            rows = [json.loads(line) for line in (archive / "motor.jsonl").read_text().splitlines()]
            self.assertEqual(metadata["timeline"]["motor_frames"], 2)
            self.assertEqual(timeline["motor"]["schema"], MOTOR_TIMELINE_SCHEMA)
            self.assertEqual(rows, timeline["motor"]["frames"])
            self.assertTrue(rows[1]["source_stale"])
            self.assertEqual(rows[1]["pins"]["D1"]["reported_pwm"], 255)
            self.assertIsNone(rows[1]["pins"]["D8"]["reported_pwm"])
            self.assertEqual(state.timeline_summary(archive)["motor_frames"], 2)
            self.assertIsNone(state.motor_timeline.log)


@unittest.skipUnless(os.environ.get("UBTS_MOTOR_INTEGRATION_TEST") == "1", "opt-in isolated ROS integration test")
class MotorRosbagIntegration(unittest.TestCase):
    def test_ros_subscription_sidecar_and_three_bag_topics_without_imu_gps(self):
        self.assertGreaterEqual(int(os.environ.get("ROS_DOMAIN_ID", "0")), 100)
        import rclpy
        from rclpy.node import Node
        from sensor_msgs.msg import LaserScan, Image
        from std_msgs.msg import String
        import yaml

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            log = (root / "recorder.log").open("w")
            process = subprocess.Popen([
                sys.executable, "-B", "-m", "scripts.data_recorder_service", "--port", str(port),
                "--archive-dir", str(root / "recordings"), "--status-file", str(root / "status.json"),
                "--camera-preview-width", "160",
            ], stdout=log, stderr=subprocess.STDOUT)
            node = None

            def api(route, payload=None):
                request = urllib.request.Request(f"http://127.0.0.1:{port}{route}",
                    data=json.dumps(payload).encode() if payload is not None else None,
                    headers={"Content-Type": "application/json"})
                return json.load(urllib.request.urlopen(request, timeout=15))

            try:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        api("/status")
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            self.fail((root / "recorder.log").read_text())
                        time.sleep(.1)
                rclpy.init()
                node = Node("synthetic_motor_recording_validation")
                specs = [("/scan", LaserScan), ("/camera/image_raw", Image)] + [
                    (topic, String) for topic in ("/rover/status", "/rover/control/requested", "/rover/control/applied")
                ]
                publishers = {topic: node.create_publisher(cls, topic, 10) for topic, cls in specs}
                started = api("/start", {"kind": "both", "include_imu": False, "include_gps": False})
                self.assertTrue(started["ok"])
                archive = root / "recordings" / started["status"]["recording_id"]
                deadline = time.monotonic() + 7
                count = 0
                while time.monotonic() < deadline:
                    stamp = node.get_clock().now().to_msg()
                    scan = LaserScan()
                    scan.header.stamp = stamp
                    scan.header.frame_id = "laser"
                    scan.angle_min = -1.
                    scan.angle_increment = .1
                    scan.range_min, scan.range_max = .1, 20.
                    scan.ranges = [5.] * 20
                    camera = Image()
                    camera.header.stamp = stamp
                    camera.width, camera.height, camera.step = 160, 120, 480
                    camera.encoding = "bgr8"
                    camera.data = bytes([30, 80, 40]) * 160 * 120
                    sample = motor_status(updated_at=time.time(), ticket="must-not-be-in-sidecar")
                    if count % 2:
                        sample.update(state="disarmed", armed=False, moving=False,
                                      requested={"left": 0, "right": 0}, outputs={"D1": 255})
                    publishers["/scan"].publish(scan)
                    publishers["/camera/image_raw"].publish(camera)
                    # Synthetic inputs exist only in this network-isolated test domain.
                    for topic, payload in (("/rover/status", sample), ("/rover/control/requested", sample["requested"]),
                                           ("/rover/control/applied", sample["outputs"])):
                        publishers[topic].publish(String(data=json.dumps(payload)))
                    count += 1
                    rclpy.spin_once(node, timeout_sec=.01)
                    time.sleep(.49)
                self.assertTrue(api("/stop", {})["ok"])
                timeline = json.loads((archive / "timeline.json").read_text())
                sidecar = (archive / "motor.jsonl").read_text()
                rows = [json.loads(line) for line in sidecar.splitlines()]
                self.assertGreater(len(rows), 3)
                self.assertNotIn("must-not-be-in-sidecar", sidecar)
                self.assertTrue(any(row["requested"]["right"] == -20 for row in rows))
                self.assertTrue(any(row["status"]["armed"] is False and row["pins"]["D1"]["reported_pwm"] == 255 for row in rows))
                self.assertGreater(len(timeline["camera"]["frames"]), 0)
                self.assertGreater(len(timeline["lidar"]["frames"]), 0)
                self.assertFalse(timeline["imu"]["enabled"])
                self.assertFalse(timeline["gps"]["enabled"])
                self.assertEqual(len(rows), timeline["motor"]["frame_count"])
                bag = yaml.safe_load((archive / "rosbag2/metadata.yaml").read_text())["rosbag2_bagfile_information"]
                topics = {item["topic_metadata"]["name"]: item["message_count"] for item in bag["topics_with_message_count"]}
                for topic, _ in specs:
                    self.assertGreater(topics.get(topic, 0), 0, topic)
                self.assertNotIn("/imu/data", topics)
                self.assertNotIn("/gps/data", topics)
                print("ISOLATED_MOTOR_RECORDING", json.dumps({"motor_frames": len(rows), "rosbag": topics}), flush=True)
            finally:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                log.close()
                if node is not None:
                    node.destroy_node()
                    rclpy.shutdown()


if __name__ == "__main__":
    unittest.main()
