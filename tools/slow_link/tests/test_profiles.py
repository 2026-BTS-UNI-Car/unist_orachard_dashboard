"""Link profiles, scenarios and stack settings: the agreed draft numbers and the shape the tools expect."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import link_emulator  # noqa: E402
import run_bench  # noqa: E402
import stack  # noqa: E402


class ProfilesTest(unittest.TestCase):
    def setUp(self):
        self.document = json.loads((TOOL_DIR / "profiles.json").read_text(encoding="utf-8"))
        self.profiles = self.document["profiles"]

    def test_four_profiles_with_the_draft_rates_and_delays(self):
        expected = {"good": (10000, 10, 0, 0), "normal": (2000, 40, 0, 0), "bad": (500, 150, 20, 1000), "very_bad": (150, 300, 20, 2000)}
        self.assertEqual(list(self.profiles), list(expected))
        for name, (rate, rtt, every, stall) in expected.items():
            profile = self.profiles[name]
            self.assertEqual((profile["rate_kbps"], profile["rtt_ms"], profile["stall_every_s"], profile["stall_ms"]), (rate, rtt, every, stall), name)

    def test_queue_holds_about_a_quarter_second_of_traffic(self):
        for name, profile in self.profiles.items():
            seconds = profile["queue_bytes"] * 8 / (profile["rate_kbps"] * 1000)
            self.assertGreaterEqual(seconds, 0.2, name)
            self.assertLessEqual(seconds, 0.45, name)
            # Never smaller than a few segments, or one large packet would fill it.
            self.assertGreaterEqual(profile["queue_bytes"], 4 * link_emulator.SEGMENT_BYTES, name)

    def test_every_profile_builds_a_link(self):
        for name, profile in self.profiles.items():
            link = link_emulator.parse_link(link_emulator.link_spec("c0", 29100, profile), ("127.0.0.1", 28080))
            self.assertEqual(link.rate_kbps, profile["rate_kbps"], name)
            schedule = link_emulator.StallSchedule(*link.stall_args)
            self.assertEqual(schedule.enabled, profile["stall_ms"] > 0, name)

    def test_loader_returns_the_same_profiles(self):
        self.assertEqual(run_bench.load_profiles(), self.profiles)


class ScenariosTest(unittest.TestCase):
    def test_named_scenarios(self):
        self.assertEqual(sorted(run_bench.SCENARIOS), ["drive", "drive_lidar", "drive_map", "hidden", "load", "map", "quick", "sensors", "session_expiry",
                                                       "two_viewers"])
        # two viewers (NET-11 (6)): the first client is "drive" without the second open; the peer is on the very bad link
        pair = dict(run_bench.SCENARIOS["two_viewers"])
        self.assertEqual(pair.pop("peer_profile"), "very_bad")
        self.assertIn("very_bad", run_bench.load_profiles())
        self.assertEqual(pair, {**run_bench.SCENARIOS["drive"], "warm_load": False})
        self.assertEqual(run_bench.SCENARIOS["drive"]["live_s"], 120)
        # drive_lidar is drive with the LiDAR as the driving screen's big view (the six screens show one of camera and LiDAR)
        self.assertEqual({k: v for k, v in run_bench.SCENARIOS["drive_lidar"].items() if k != "drive_view"}, run_bench.SCENARIOS["drive"])
        self.assertEqual(run_bench.SCENARIOS["drive_lidar"]["drive_view"], "lidar")
        # the sensor page (Connection) and the /map alias of Rover Control that scrolls to the live map (UI-16)
        self.assertEqual((run_bench.SCENARIOS["sensors"]["route"], run_bench.SCENARIOS["map"]["route"]), ("/connection", "/map"))
        self.assertTrue(run_bench.SCENARIOS["map"]["start_live_map"] and "drive" not in run_bench.SCENARIOS["map"])
        self.assertEqual(run_bench.SCENARIOS["drive_map"]["live_s"], 600)
        self.assertTrue(run_bench.SCENARIOS["drive"]["cold_load"] and run_bench.SCENARIOS["drive"]["warm_load"])

    def test_each_client_has_its_own_link(self):
        import stack
        slow, fast = {"rate_kbps": 200}, {"rate_kbps": 5000}
        ports = dict(stack.DEFAULT_PORTS)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        common = dict(python="py", app=".", scratch=".", out=Path(folder.name), inputs={}, ports=ports)
        pair = stack.Stack(profile=fast, client_profiles=[fast, slow], **common)
        self.assertEqual(pair.clients, 2)
        self.assertEqual(pair.client_origins(), [f"http://127.0.0.1:{ports['link']}", f"http://127.0.0.1:{ports['link'] + 1}"])
        self.assertEqual(pair.link_ports(), [ports["link"], ports["link"] + 1])
        direct_and_slow = stack.Stack(profile=None, client_profiles=[None, slow], **common)
        self.assertEqual(direct_and_slow.client_origins(), [f"http://127.0.0.1:{ports['dashboard']}", f"http://127.0.0.1:{ports['link'] + 1}"])
        self.assertEqual(direct_and_slow.link_ports(), [ports["link"] + 1])
        # as before: one profile for every client, or none at all
        self.assertEqual(stack.Stack(profile=fast, clients=2, **common).link_ports(), [ports["link"], ports["link"] + 1])
        self.assertEqual(stack.Stack(profile=None, clients=2, **common).client_origins(), [f"http://127.0.0.1:{ports['dashboard']}"] * 2)

    def test_drive_exercise_stays_far_below_the_owner_limit(self):
        cycle = run_bench.DRIVE_CYCLE
        self.assertLess(cycle["hold_s"] + cycle["idle_s"], 30)

    def test_a_scenario_never_sets_a_control_or_safety_limit(self):
        for name, scenario in run_bench.SCENARIOS.items():
            for key in scenario.get("env", {}):
                self.assertEqual(key, "DASHBOARD_SESSION_TTL_S", name)


class BrowserProfileCleanupTest(unittest.TestCase):
    def test_only_a_bench_profile_in_the_temp_folder_is_removed(self):
        with tempfile.TemporaryDirectory() as out:
            profile = Path(tempfile.mkdtemp(prefix=stack.CHROME_PROFILE_PREFIX))
            (profile / "Default").mkdir()
            Path(out, "browser_profile.txt").write_bytes(str(profile).encode("utf-8"))
            self.assertEqual(run_bench.remove_browser_profile(out), str(profile))
            self.assertFalse(profile.exists())
            self.assertIsNone(run_bench.remove_browser_profile(out))  # already gone
            other = Path(tempfile.mkdtemp(prefix="not-the-bench-"))
            try:
                Path(out, "browser_profile.txt").write_bytes(str(other).encode("utf-8"))
                self.assertIsNone(run_bench.remove_browser_profile(out))
                self.assertTrue(other.exists())
                inside = Path(out, stack.CHROME_PROFILE_PREFIX + "x")
                inside.mkdir()
                Path(out, "browser_profile.txt").write_bytes(str(inside).encode("utf-8"))
                self.assertIsNone(run_bench.remove_browser_profile(out))  # not directly in the temp folder
                self.assertTrue(inside.exists())
            finally:
                other.rmdir()

    def test_no_note_means_nothing_to_do(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertIsNone(run_bench.remove_browser_profile(out))


class StackSettingsTest(unittest.TestCase):
    def make(self, folder, profile, clients=1):
        return stack.Stack(python=sys.executable, app=folder / "app", scratch=folder, out=folder / "out", inputs={}, profile=profile, clients=clients)

    def test_clients_reach_the_dashboard_through_the_link_on_loopback(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            shaped = self.make(folder, {"rate_kbps": 500, "rtt_ms": 150, "queue_bytes": 16384}, clients=2)
            self.assertEqual(shaped.client_origins(), ["http://127.0.0.1:29100", "http://127.0.0.1:29101"])
            self.assertEqual(shaped.link_ports(), [29100, 29101])
            direct = self.make(folder, None)
            self.assertEqual(direct.client_origins(), ["http://127.0.0.1:28080"])
            self.assertEqual(direct.link_ports(), [])

    def test_environment_is_local_and_does_not_inherit_device_settings(self):
        os.environ["ROVER_SERIAL_PORT"] = "/dev/should-not-leak"
        os.environ["DASHBOARD_SESSION_TTL_S"] = "5"
        try:
            with tempfile.TemporaryDirectory() as temp:
                env = self.make(Path(temp), None).env("dashboard")
        finally:
            del os.environ["ROVER_SERIAL_PORT"], os.environ["DASHBOARD_SESSION_TTL_S"]
        self.assertNotIn("ROVER_SERIAL_PORT", env)
        self.assertNotIn("DASHBOARD_SESSION_TTL_S", env)
        self.assertEqual(env["DASHBOARD_HOST"], "127.0.0.1")
        self.assertEqual(env["NETWORK_CONTROL_ENABLED"], "0")
        for key in ("LIDAR_WS_UPSTREAM", "CAMERA_WS_UPSTREAM", "IMU_WS_UPSTREAM", "GPS_WS_UPSTREAM", "RECORDER_API_URL", "EXTERNAL_IP_URL"):
            self.assertIn("//127.0.0.1:", env[key], key)
        self.assertTrue(env["ROVER_CONTROL_SOCKET"].endswith("control.sock"))
        self.assertEqual(env["SLOW_LINK_CONTROL_PORT"], str(stack.DEFAULT_PORTS["control"]))

    def test_port_offset_moves_every_port(self):
        moved = stack.shifted_ports(100)
        self.assertEqual({name: moved[name] - port for name, port in stack.DEFAULT_PORTS.items()}, dict.fromkeys(stack.DEFAULT_PORTS, 100))


if __name__ == "__main__":
    unittest.main()
