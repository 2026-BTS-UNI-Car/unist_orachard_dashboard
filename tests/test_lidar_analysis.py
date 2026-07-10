import math
import unittest

from dashboard_server.lidar_analysis import ALGORITHM_NAME, LIDAR_COORDINATE_FRAME, analyze_points, candidate_linear_context_gate, current_lidar_overlay_algorithm, orchard_candidate_from_cluster
from dashboard_server.lidar_odometry import estimate_relative_transform
from dashboard_server.orchard_mapping import build_orchard_map


def point(x, y, quality=32):
    return {
        "x_m": round(x, 3),
        "y_m": round(y, 3),
        "distance_m": round(math.hypot(x, y), 3),
        "angle_deg": round(math.degrees(math.atan2(x, y)) % 360.0, 3),
        "quality": quality,
    }


def line_points(start, end, count, jitter=0.015):
    rows = []
    for index in range(count):
        t = index / max(1, count - 1)
        x = start[0] + (end[0] - start[0]) * t
        y = start[1] + (end[1] - start[1]) * t
        offset = jitter if index % 2 else -jitter
        rows.append(point(x, y + offset))
    return rows


def trunk_points(cx, cy, radius=0.12, count=8):
    return [
        point(cx + math.cos(index * math.tau / count) * radius, cy + math.sin(index * math.tau / count) * radius)
        for index in range(count)
    ]


class LidarAnalysisTest(unittest.TestCase):
    def test_current_overlay_requires_success_and_exact_algorithm(self):
        self.assertTrue(current_lidar_overlay_algorithm({"ok": True, "algorithm": ALGORITHM_NAME}))
        self.assertFalse(current_lidar_overlay_algorithm({"ok": False, "algorithm": ALGORITHM_NAME}))
        self.assertFalse(current_lidar_overlay_algorithm({"ok": True, "algorithm": "old"}))

    def test_fragmented_wall_is_not_reported_as_objects(self):
        scan = []
        scan.extend(line_points((1.0, 4.0), (2.0, 4.0), 12))
        scan.extend(line_points((3.0, 4.0), (4.2, 4.0), 12))
        analysis, _, _ = analyze_points(scan, now=10.0)
        self.assertEqual(analysis["algorithm"], ALGORITHM_NAME)
        self.assertEqual(analysis["coordinate_frame"], LIDAR_COORDINATE_FRAME)
        self.assertGreaterEqual(len(analysis["walls"]), 1)
        self.assertGreaterEqual(max(wall["length_m"] for wall in analysis["walls"]), 2.8)
        self.assertLessEqual(len(analysis["objects"]), 1)

    def test_trunk_track_survives_relative_motion(self):
        tracks = {}
        analysis1, tracks, next_track_id = analyze_points(trunk_points(2.0, 3.0), tracks, 1, now=20.0)
        analysis2, tracks, _ = analyze_points(trunk_points(2.85, 3.15), tracks, next_track_id, now=20.45)
        self.assertEqual(len(analysis1["objects"]), 1)
        self.assertEqual(len(analysis2["objects"]), 1)
        self.assertEqual(analysis1["objects"][0]["id"], analysis2["objects"][0]["id"])
        self.assertIn(analysis2["objects"][0]["label"], {"trunk", "object"})
        self.assertGreaterEqual(len(analysis2["objects"][0]["track"]), 2)
        self.assertGreater(analysis2["objects"][0]["velocity"]["speed_mps"], 0.5)
        self.assertEqual(analysis1["orchard"]["confirmed_trees"], [])
        self.assertEqual(len(analysis2["orchard"]["confirmed_trees"]), 1)
        self.assertEqual(analysis2["orchard"]["confirmed_trees"][0]["map_admission"], "accepted")

    def test_orchard_candidates_exclude_rear_person_zone(self):
        scan = trunk_points(2.0, 3.0) + trunk_points(0.2, -1.6, radius=0.25, count=12)
        analysis, _, _ = analyze_points(scan, now=30.0)
        trees = analysis["orchard"]["trees"]
        self.assertEqual(len(trees), 1)
        self.assertGreater(trees[0]["center"]["y_m"], 0)
        self.assertEqual(analysis["orchard"]["excluded_rear_candidates"], 1)

    def test_wall_adjacent_fragments_are_not_orchard_trees(self):
        scan = []
        scan.extend(line_points((1.0, 4.0), (4.4, 4.0), 28))
        scan.extend(trunk_points(2.7, 4.33, radius=0.08, count=8))
        analysis, _, _ = analyze_points(scan, now=35.0)
        self.assertGreaterEqual(len(analysis["walls"]), 1)
        self.assertEqual(analysis["orchard"]["trees"], [])
        self.assertGreaterEqual(analysis["orchard"]["wall_suppressed_candidates"], 1)

    def test_fragmented_linear_context_is_rejected_with_auditable_reason(self):
        wall = line_points((-1.2, 4.0), (1.2, 4.0), 26, jitter=0.01)
        candidate = orchard_candidate_from_cluster(trunk_points(0.0, 4.04, radius=0.08, count=8))

        gate = candidate_linear_context_gate(candidate, wall + trunk_points(0.0, 4.04, radius=0.08, count=8))

        self.assertIsNotNone(gate)
        self.assertEqual(gate["reason"], "embedded_in_linear_structure")

    def test_scan_context_recovers_large_rotation(self):
        previous = []
        previous.extend(line_points((-4.0, 6.0), (3.0, 6.0), 90, jitter=0.02))
        previous.extend(line_points((4.0, -5.0), (4.0, 2.0), 75, jitter=0.018))
        previous.extend(trunk_points(-2.4, 2.1, radius=0.14, count=18))
        previous.extend(trunk_points(1.6, -3.2, radius=0.18, count=20))
        theta = math.radians(32.0)
        dx, dy = 0.34, -0.22
        cosine, sine = math.cos(-theta), math.sin(-theta)
        current = []
        for item in previous:
            px = float(item["x_m"]) - dx
            py = float(item["y_m"]) - dy
            current.append(point(cosine * px - sine * py, sine * px + cosine * py))

        transform = estimate_relative_transform(previous, current, dt_s=0.6)

        self.assertTrue(transform["accepted"], transform)
        self.assertAlmostEqual(math.degrees(transform["dtheta_rad"]), 32.0, delta=3.0)
        self.assertAlmostEqual(transform["dx_m"], dx, delta=0.18)
        self.assertAlmostEqual(transform["dy_m"], dy, delta=0.18)

    def test_orchard_map_builds_path_and_tree_hits(self):
        frames = []
        for index, offset in enumerate([0.0, -0.25, -0.5]):
            frames.append({"t_s": index * 0.5, "points": trunk_points(1.4, 3.0 + offset, radius=0.08) + trunk_points(-1.2, 3.4 + offset, radius=0.08)})
        payload = build_orchard_map({"ok": True, "duration_s": 1.0, "lidar": {"frames": frames}, "camera": {"frames": []}})
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["tree_count"], 2)
        self.assertEqual(len(payload["path"]), len(frames))


if __name__ == "__main__":
    unittest.main()
