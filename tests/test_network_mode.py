import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "deploy" / "offline_ap" / "ubts_network_mode.py"
SPEC = importlib.util.spec_from_file_location("ubts_network_mode", MODULE_PATH)
assert SPEC and SPEC.loader
network_mode = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(network_mode)


class NetworkModeContractTests(unittest.TestCase):
    def test_active_correct_ap_accepts_nmcli_textual_disable(self):
        values = {"802-11-wireless.mode": "ap", "802-11-wireless.ssid": network_mode.SSID,
                  "connection.autoconnect": "no", "ipv4.addresses": network_mode.AP_CIDR,
                  "802-11-wireless.band": "bg", "802-11-wireless.channel": "6", "802-11-wireless.powersave": "disable"}
        with mock.patch.object(network_mode, "validate_password"), mock.patch.object(network_mode, "read_ap_uuid", return_value="managed"), \
             mock.patch.object(network_mode, "connection_exists", return_value=True), \
             mock.patch.object(network_mode, "nmcli_value", side_effect=lambda *a: values[a[1]]), \
             mock.patch.object(network_mode, "interface_snapshot", return_value={"ap_active": True}), \
             mock.patch.object(network_mode, "run") as run:
            self.assertEqual(network_mode.ensure_ap_profile(), "managed")
            run.assert_not_called()

    def test_existing_ap_is_pinned_to_24ghz_without_reconnecting_station(self):
        values = {"802-11-wireless.mode": "ap", "802-11-wireless.ssid": network_mode.SSID,
                  "connection.autoconnect": "no", "ipv4.addresses": network_mode.AP_CIDR,
                  "802-11-wireless.band": "a", "802-11-wireless.channel": "149", "802-11-wireless.powersave": "0"}
        with mock.patch.object(network_mode, "validate_password"), mock.patch.object(network_mode, "read_ap_uuid", return_value="managed"), \
             mock.patch.object(network_mode, "connection_exists", return_value=True), \
             mock.patch.object(network_mode, "nmcli_value", side_effect=lambda *a: values[a[1]]), \
             mock.patch.object(network_mode, "interface_snapshot", return_value={"ap_active": False}), \
             mock.patch.object(network_mode, "run") as run:
            self.assertEqual(network_mode.ensure_ap_profile(), "managed")
            args = run.call_args.args[0]
            self.assertIn("modify", args)
            self.assertIn("bg", args)
            self.assertNotIn("up", args)
            self.assertNotIn("delete", args)

    def test_queue_request_requires_fresh_controller_and_matching_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_status = network_mode.STATUS_FILE
            old_command = network_mode.COMMAND_FILE
            try:
                network_mode.STATUS_FILE = Path(tmp) / "status.json"
                network_mode.COMMAND_FILE = Path(tmp) / "command.json"
                network_mode.atomic_json(
                    network_mode.STATUS_FILE,
                    {
                        "ok": True,
                        "updated_at": time.time(),
                        "mode": "wifi",
                        "ethernet_connected": False,
                        "can_request_ap": True,
                        "can_request_wifi": False,
                    },
                )
                result = network_mode.queue_request("ap")
                self.assertTrue(result["ok"])
                queued = json.loads(network_mode.COMMAND_FILE.read_text(encoding="utf-8"))
                self.assertEqual(queued["action"], "ap")
                rejected = network_mode.queue_request("wifi")
                self.assertFalse(rejected["ok"])
            finally:
                network_mode.STATUS_FILE = old_status
                network_mode.COMMAND_FILE = old_command

    def test_ethernet_locks_both_manual_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_status = network_mode.STATUS_FILE
            old_command = network_mode.COMMAND_FILE
            try:
                network_mode.STATUS_FILE = Path(tmp) / "status.json"
                network_mode.COMMAND_FILE = Path(tmp) / "command.json"
                network_mode.atomic_json(
                    network_mode.STATUS_FILE,
                    {
                        "ok": True,
                        "updated_at": time.time(),
                        "mode": "ethernet_ap",
                        "ethernet_connected": True,
                        "can_request_ap": False,
                        "can_request_wifi": False,
                    },
                )
                for action in ("ap", "wifi"):
                    result = network_mode.queue_request(action)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["state"], "ethernet_ap_locked")
                self.assertFalse(network_mode.COMMAND_FILE.exists())
            finally:
                network_mode.STATUS_FILE = old_status
                network_mode.COMMAND_FILE = old_command

    def test_stale_status_refuses_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_status = network_mode.STATUS_FILE
            old_command = network_mode.COMMAND_FILE
            try:
                network_mode.STATUS_FILE = Path(tmp) / "status.json"
                network_mode.COMMAND_FILE = Path(tmp) / "command.json"
                network_mode.atomic_json(
                    network_mode.STATUS_FILE,
                    {"ok": True, "updated_at": time.time() - 30, "can_request_ap": True},
                )
                result = network_mode.queue_request("ap")
                self.assertFalse(result["ok"])
                self.assertEqual(result["state"], "controller_unavailable")
            finally:
                network_mode.STATUS_FILE = old_status
                network_mode.COMMAND_FILE = old_command

    def test_recovery_keeps_original_transition_error_visible(self):
        controller = network_mode.Controller()
        with mock.patch.object(controller, "snapshot", return_value={}), mock.patch.object(
            controller, "scan_wifi", return_value=True
        ):
            controller.recover(network_mode.CommandError("proxy validation failed"))
        self.assertEqual(controller.last_error, "proxy validation failed")
        self.assertEqual(controller.last_reason, "recovered_after_transition_failure")

    def test_wifi_scan_timeout_returns_to_ap(self):
        controller = network_mode.Controller()
        with mock.patch.object(controller, "set_transition"), mock.patch.object(
            controller, "stop_ap"
        ), mock.patch.object(controller, "activate_ap") as activate_ap, mock.patch.object(
            network_mode.time, "monotonic", side_effect=[0.0, 61.0]
        ), mock.patch.object(network_mode, "WIFI_SCAN_S", 60):
            connected = controller.scan_wifi("dashboard_request_wifi")
        self.assertFalse(connected)
        activate_ap.assert_called_once_with("wifi_scan_timeout")

    def test_ethernet_ap_snapshot_locks_both_buttons(self):
        controller = network_mode.Controller()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            network_mode, "STATUS_FILE", Path(tmp) / "status.json"
        ), mock.patch.object(
            network_mode,
            "interface_snapshot",
            return_value={
                "state": "100 (connected)",
                "connection": network_mode.AP_CONNECTION,
                "connected": True,
                "ap_active": True,
                "wifi_connected": False,
            },
        ), mock.patch.object(network_mode, "ethernet_connected", return_value=True), mock.patch.object(
            network_mode, "ap_client_count", return_value=2
        ):
            status = controller.snapshot()
        self.assertEqual(status["mode"], "ethernet_ap")
        self.assertFalse(status["can_request_ap"])
        self.assertFalse(status["can_request_wifi"])
        self.assertEqual(status["controls_locked_reason"], "ethernet_keeps_ap_active")


class RosDiscoveryRefreshTests(unittest.TestCase):
    """2026-11-19: after a network change the idle data recorder is restarted (ROS discovery), never while recording."""

    def load(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "deploy" / "offline_ap" / "ubts_network_mode.py"
        spec = importlib.util.spec_from_file_location("ubts_network_mode_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_with(self, status_json):
        module = self.load()
        calls = []

        class Done:
            def __init__(self, out="", code=0):
                self.stdout, self.returncode = out, code

        def fake_run(cmd, **kw):
            calls.append(cmd[:2])
            return Done(status_json) if cmd[0] == "curl" else Done()
        module.run = fake_run
        return module.refresh_ros_discovery("test"), calls

    def test_idle_recorder_is_restarted(self):
        result, calls = self.run_with('{"ok": true, "state": "idle", "active": false}')
        self.assertEqual(result, "restarted")
        self.assertIn(["docker", "restart"], calls)

    def test_recording_is_left_alone(self):
        result, calls = self.run_with('{"ok": true, "state": "recording", "active": true}')
        self.assertEqual(result, "busy")
        self.assertNotIn(["docker", "restart"], calls)

    def test_unreachable_recorder(self):
        result, calls = self.run_with("not json")
        self.assertEqual(result, "unknown")


if __name__ == "__main__":
    unittest.main()
