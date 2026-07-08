import math
import unittest

from dashboard_server.lidar_analysis import ALGORITHM_NAME, LIDAR_COORDINATE_FRAME, analyze_points
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

    def test_orchard_candidates_exclude_rear_person_zone(self):
        scan = trunk_points(2.0, 3.0) + trunk_points(0.2, -1.6, radius=0.25, count=12)
        analysis, _, _ = analyze_points(scan, now=30.0)
        trees = analysis["orchard"]["trees"]
        self.assertEqual(len(trees), 1)
        self.assertGreater(trees[0]["center"]["y_m"], 0)
        self.assertEqual(analysis["orchard"]["excluded_rear_candidates"], 1)

    def test_orchard_map_builds_path_and_tree_hits(self):
        frames = []
        for index, offset in enumerate([0.0, -0.25, -0.5]):
            frames.append({"t_s": index * 0.5, "points": trunk_points(1.4, 3.0 + offset) + trunk_points(-1.2, 3.4 + offset)})
        payload = build_orchard_map({"ok": True, "duration_s": 1.0, "lidar": {"frames": frames}, "camera": {"frames": []}})
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["tree_count"], 2)
        self.assertGreaterEqual(len(payload["path"]), 3)


if __name__ == "__main__":
    unittest.main()
