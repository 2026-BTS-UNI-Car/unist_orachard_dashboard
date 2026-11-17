"""two_viewers.py: the first viewer of a two-viewer run against the same viewer alone (NET-11 (6)). Made-up summaries."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import two_viewers  # noqa: E402


def summary(scenario, camera_fps=6.0, camera_p95=130, lidar_hz=9.9, lidar_p95=170, rt50=74, rt95=136, stop50=26, peer=None, **run):
    client = dict(render_delay_ms=dict(camera=dict(rate_hz=camera_fps, p95=camera_p95), lidar=dict(rate_hz=lidar_hz, p95=lidar_p95)))
    clients = [client] + ([dict(render_delay_ms=dict(camera=dict(rate_hz=peer, p95=900), lidar=dict(rate_hz=2.5, p95=400)))] if peer else [])
    control = dict(per_op=dict(command=dict(round_trip_ms=dict(p50=rt50, p95=rt95))), stop=dict(click_to_endpoint_ms=dict(p50=stop50)))
    return dict(run=dict(scenario=scenario, profile="normal", **run), live=dict(clients=clients, control=control))


class TwoViewersTest(unittest.TestCase):
    def test_same_numbers_pass_and_show_the_peer(self):
        result = two_viewers.compare(summary("drive"), summary("two_viewers", peer=1.0, client_profiles=["normal", "very_bad"]))
        self.assertTrue(result["within_limit"])
        self.assertEqual(result["peer_profile"], "very_bad")
        self.assertEqual(result["peer"]["camera_fps"], 1.0)
        self.assertEqual({row["verdict"] for row in result["rows"]}, {"ok"})

    def test_ten_percent_with_a_small_allowance(self):
        # camera 6.0 -> 5.3 fps is 11.7 % lower (limit 5.4 - 0.2 = 5.2): still ok; 5.1 is not
        self.assertTrue(two_viewers.compare(summary("drive"), summary("two_viewers", camera_fps=5.3))["within_limit"])
        result = two_viewers.compare(summary("drive"), summary("two_viewers", camera_fps=5.1))
        self.assertFalse(result["within_limit"])
        self.assertEqual([row["key"] for row in result["rows"] if row["verdict"] != "ok"], ["camera_fps"])
        # command round trip p95 136 -> 165 ms: limit 136 * 1.1 + 15 = 164.6
        result = two_viewers.compare(summary("drive"), summary("two_viewers", rt95=165))
        self.assertEqual([row["key"] for row in result["rows"] if row["verdict"] != "ok"], ["command_rt_p95"])

    def test_missing_number_is_not_a_pass(self):
        alone = summary("drive")
        alone["live"]["clients"][0]["render_delay_ms"].pop("lidar")
        result = two_viewers.compare(alone, summary("two_viewers"))
        self.assertFalse(result["within_limit"])
        self.assertIn("missing", {row["verdict"] for row in result["rows"]})

    def test_runs_that_cannot_be_compared(self):
        with self.assertRaises(SystemExit):
            two_viewers.compare(summary("drive"), dict(summary("two_viewers"), run=dict(scenario="two_viewers", profile="bad")))
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "SUMMARY.json").write_bytes(json.dumps(summary("quick")).encode("utf-8"))
            with self.assertRaises(SystemExit):
                two_viewers.read_summary(folder, "drive")
            failed = summary("drive")
            failed["run"]["failed"] = "RuntimeError: x"
            (Path(folder) / "SUMMARY.json").write_bytes(json.dumps(failed).encode("utf-8"))
            with self.assertRaises(SystemExit):
                two_viewers.read_summary(folder, "drive")


if __name__ == "__main__":
    unittest.main()
