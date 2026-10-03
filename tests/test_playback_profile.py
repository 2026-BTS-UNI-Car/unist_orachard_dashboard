import unittest
from dashboard_server.playback_profile import point_fields,FULL_FIELDS,POLAR_FIELDS,POLAR_PROFILE


class PolarDisplayTest(unittest.TestCase):
    def test_bridge_points_are_polar_but_world_or_invalid_points_keep_xy(self):
        frame=dict(points=[dict(x_m=2,y_m=0,angle_deg=90,distance_m=2,quality=0)])
        self.assertEqual(point_fields([frame],POLAR_PROFILE),POLAR_FIELDS)
        self.assertEqual(point_fields([frame],'dashboard-compact-v1'),FULL_FIELDS)
        for p in (dict(x_m=4,y_m=0,angle_deg=90,distance_m=2),dict(x_m=2,y_m=None,angle_deg=90,distance_m=2),dict(x_m=2,y_m=0,angle_deg=float('nan'),distance_m=2)):
            self.assertEqual(point_fields([dict(points=[p])],POLAR_PROFILE),FULL_FIELDS)


if __name__=='__main__':unittest.main()
