import math,unittest
import numpy as np
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from dashboard_server.raw_mapping import fit_workers
from dashboard_server.raw_mapping.continuous_localization import ContinuousLocalization
from test_integrated_mapping import scan


def imu(stamp):
    return dict(header=dict(stamp=dict(sec=int(stamp),nanosec=round(stamp%1*1e9))),
        orientation=dict(x=0,y=0,z=0,w=1),angular_velocity=dict(x=0,y=0,z=0),
        linear_acceleration=dict(x=0,y=0,z=9.80665))


class ContinuousTests(unittest.TestCase):
    def test_parallel_fit_hypotheses_preserve_numeric_tracking(self):
        def run(pool):
            tracker=ContinuousLocalization();poses=[]
            with patch.object(fit_workers,'POOL',pool):
                for i in range(16):
                    m=imu(1000+i*.1);tracker.imu(m)
                    result=tracker.add(scan(1000+i*.1,i*.035),m)
                    self.assertTrue(result['accepted']);poses.append(result['pose'])
            return np.array(poses)
        serial=run(None)
        with ThreadPoolExecutor(max_workers=3) as pool:parallel=run(pool)
        np.testing.assert_array_equal(serial,parallel)

    def test_shock_and_long_loss_keep_origin_then_room_relocalizes(self):
        tracker=ContinuousLocalization()
        for i in range(3):
            m=imu(1000+i*.1);tracker.imu(m)
            self.assertTrue(tracker.add(scan(1000+i*.1,i*.02),m)['accepted'])
        before=tracker.pose.copy();bad=imu(1000.3);bad['linear_acceleration']['z']=30
        value=tracker.add(scan(1000.3,.06),bad)
        self.assertFalse(value['accepted']);np.testing.assert_equal(tracker.pose,before)
        for t in np.arange(1000.3,1002.1,.1):tracker.imu(imu(float(t)))
        recovered=tracker.add(scan(1002,.1),imu(1002))
        self.assertTrue(recovered['accepted']);self.assertEqual(recovered['segment'],0)
        self.assertGreater(tracker.pose[0],before[0]);self.assertLess(abs(tracker.pose[2]),.02)

    def test_short_sensor_motion_prior_does_not_draw_unobserved_pose(self):
        tracker=ContinuousLocalization()
        for i in range(3):
            m=imu(1000+i*.1);tracker.imu(m);tracker.add(scan(1000+i*.1,i*.02),m)
        tracker.gps(dict(valid=True,speed_mps=.2,h_acc_m=2,stamp=dict(sec=1000,nanosec=200000000)))
        tracker.imu(imu(1000.3))
        with patch('dashboard_server.raw_mapping.continuous_localization.registration',return_value=dict(accepted=False)),patch.object(tracker,'_fit',return_value=None),patch.object(tracker,'_recover',return_value=None),patch('dashboard_server.raw_mapping.continuous_localization.point_support_registration',return_value=dict(accepted=False)),patch('dashboard_server.raw_mapping.continuous_localization.proposal',return_value=None):
            value=tracker.add(scan(1000.3,.06),imu(1000.3))
        self.assertTrue(value['motion_prior']);self.assertFalse(value['map_eligible'])
        self.assertEqual(value['segment'],0)
        # Old GNSS speed cannot maintain a fictional pose indefinitely.
        self.assertIsNone(tracker._motion_bridge(1003))


if __name__=='__main__':unittest.main()
