import math,unittest
import numpy as np
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from dashboard_server.raw_mapping import fit_workers
from dashboard_server.raw_mapping.continuous_localization import (ContinuousLocalization,scan_middle,vertical_turn,
    heading_offset,HEADING_LAG_S)
from dashboard_server.playback_mapping import SourceCursorLocalizer
from test_integrated_mapping import scan


def imu(stamp):
    return dict(header=dict(stamp=dict(sec=int(stamp),nanosec=round(stamp%1*1e9))),
        orientation=dict(x=0,y=0,z=0,w=1),angular_velocity=dict(x=0,y=0,z=0),
        linear_acceleration=dict(x=0,y=0,z=9.80665))


class ContinuousTests(unittest.TestCase):
    def test_stationary_geometry_rejects_gyro_bias_after_old_keyframe_age_limit(self):
        tracker=ContinuousLocalization()
        for i in range(90):
            stamp=1000+i*.1;m=imu(stamp)
            m['angular_velocity']['z']=math.radians(.2)
            tracker.imu(m);result=tracker.add(scan(stamp),m)
            self.assertTrue(result['accepted'])
        self.assertLess(abs(tracker.pose[2]),math.radians(.1))
        self.assertLess(np.linalg.norm(tracker.pose[:2]),.02)

    def test_real_rotation_is_kept_instead_of_forcing_a_stationary_heading(self):
        tracker=ContinuousLocalization();ranges=scan()['ranges']
        for i in range(80):
            stamp=1000+i*.1;yaw=math.radians(i*.5);m=imu(stamp)
            m['orientation']=dict(x=0,y=0,z=math.sin(yaw*.5),w=math.cos(yaw*.5))
            m['angular_velocity']['z']=math.radians(5)
            current=scan(stamp);current['ranges']=np.roll(ranges,-i).tolist()
            tracker.imu(m);result=tracker.add(current,m)
            self.assertTrue(result['accepted'])
        self.assertLess(abs(tracker.pose[2]-yaw),math.radians(.15))

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

    # 2026-11-21: heading between scans from the IMU orientation (the turn about the vertical).
    def turned(self,stamp,yaw,roll=0.,pitch=0.):
        from scipy.spatial.transform import Rotation
        m=imu(stamp);q=Rotation.from_euler('zyx',[yaw,pitch,roll]).as_quat()
        m['orientation']=dict(zip('xyzw',(float(v) for v in q)));return m

    def test_vertical_turn_is_the_signed_yaw_also_for_a_tilted_sensor(self):
        from scipy.spatial.transform import Rotation
        for roll,pitch in ((0.,0.),(.05,-.08),(-.12,.1)):
            for yaw in (-.4,-.01,.002,.3):
                a=Rotation.from_euler('zyx',[.7,pitch,roll]).as_quat()
                b=(Rotation.from_euler('z',yaw)*Rotation.from_euler('zyx',[.7,pitch,roll])).as_quat()
                self.assertAlmostEqual(vertical_turn(a,b),yaw,places=9)
                self.assertAlmostEqual(vertical_turn(a,-b),yaw,places=9)
        self.assertEqual(vertical_turn([0,0,0,1],[0,0,0,1]),0.)

    def test_sweep_middle_from_the_scan_itself(self):
        base=scan()
        self.assertEqual(scan_middle(base),0.)
        self.assertAlmostEqual(scan_middle({**base,'scan_time':.098}),.049)
        self.assertAlmostEqual(scan_middle({**base,'time_increment':.0001}),.036)
        for bad in (float('nan'),-1,9,'x',None):self.assertEqual(scan_middle({**base,'scan_time':bad}),0.)
        # the heading is read a little after the middle; a scan without sweep timing is taken at its stamp
        self.assertEqual(heading_offset(base),0.)
        self.assertAlmostEqual(heading_offset({**base,'scan_time':.098}),.049+HEADING_LAG_S)
        self.assertTrue(.01<=HEADING_LAG_S<=.03)

    def test_heading_is_read_between_the_orientation_samples_around_the_sweep_middle(self):
        tracker=ContinuousLocalization()
        for i in range(4):tracker.orientation(self.turned(1000+i*.03,math.radians(i)))
        value,hold,rate=tracker._heading_at(1000.045)
        self.assertAlmostEqual(value,math.radians(1.5),places=9);self.assertEqual((hold,rate),(0.,0.))
        value,hold,rate=tracker._heading_at(1000.15)
        self.assertAlmostEqual(value,math.radians(3),places=9);self.assertAlmostEqual(hold,.06)
        self.assertAlmostEqual(rate,math.radians(1)/.03,places=6)
        self.assertIsNone(tracker._heading_at(1000.09+tracker.HEADING_HOLD_S+.01))
        # the same sample offered again (first as a look ahead, then in order) counts once
        tracker.orientation(self.turned(1000.09,math.radians(3)));self.assertAlmostEqual(tracker.heading,math.radians(3),places=9)

    def test_orientation_turn_is_a_much_tighter_prior_than_the_gyro_and_never_looser(self):
        tracker=ContinuousLocalization()
        m=self.turned(1000.,0.);tracker.imu(m);tracker.add(scan(1000.),m)
        m=self.turned(1000.1,math.radians(.2));tracker.imu(m);tracker._scan_heading=tracker._heading_at(1000.1);tracker._scan_heading_for=1000.1
        yaw,sigma=tracker._turn(tracker.previous,1000.1)
        self.assertAlmostEqual(yaw,math.radians(.2),places=9)
        self.assertLess(sigma,math.radians(.02));self.assertGreater(sigma,math.radians(.005))
        self.assertAlmostEqual(sigma,math.radians(.005+.01*.1+.02*.2),places=9)
        # later the allowance has grown with the time, and it is never more than the earlier gyro allowance
        for i in range(1,1001):tracker.orientation(self.turned(1000.1+i*.1,math.radians(.2)))
        tracker._scan_heading=tracker._heading_at(1100.1);tracker._scan_heading_for=1100.1
        self.assertAlmostEqual(tracker._turn(tracker.previous,1100.1)[1],math.radians(.005+.01*100.1+.02*.2),places=6)
        tracker.HEADING_SIGMA=(math.radians(.005),math.radians(1.),.02)
        self.assertAlmostEqual(tracker._turn(tracker.previous,1100.1)[1],math.radians(.35+.3*10),places=9)

    def test_silence_or_jump_of_the_orientation_falls_back_to_the_gyro(self):
        for later,yaw in ((1002.,0.),(1000.2,math.radians(40))):
            tracker=ContinuousLocalization()
            for i in range(2):
                m=self.turned(1000+i*.1,0.);tracker.imu(m);self.assertTrue(tracker.add(scan(1000+i*.1),m)['accepted'])
            self.assertEqual(tracker.heading_epoch,0)
            m=self.turned(later,yaw);tracker.imu(m)
            self.assertEqual(tracker.heading_epoch,1)
            tracker._scan_heading=tracker._heading_at(later);tracker._scan_heading_for=later
            turn=tracker._turn(tracker.previous,later)
            # the pair straddles the break: no orientation turn, and the gyro (not covered across a silence) or nothing
            self.assertTrue(turn is None or turn[1]>=math.radians(.35))
            result=tracker.add(scan(later),m)
            self.assertTrue(result['accepted']);self.assertEqual(tracker.previous['heading_epoch'],1)

    def test_turn_of_a_tilted_sensor_follows_the_orientation(self):
        tracker=ContinuousLocalization();ranges=scan()['ranges']
        for i in range(60):
            stamp=1000+i*.1;yaw=math.radians(i*.5);m=self.turned(stamp,yaw,roll=.03,pitch=-.02)
            m['angular_velocity']['z']=math.radians(5)
            current=scan(stamp);current['ranges']=np.roll(ranges,-i).tolist();current['scan_time']=0.
            tracker.imu(m);result=tracker.add(current,m)
            self.assertTrue(result['accepted']);self.assertEqual(result['heading_prior'],'IMU_orientation')
        self.assertLess(abs(tracker.pose[2]-yaw),math.radians(.1))
        self.assertEqual(tracker.heading_disagreements,0)

    def test_orientation_step_that_the_gyro_did_not_see_is_not_followed(self):
        # The IMU's orientation is fused with its magnetometer: it can step where the field is disturbed while the
        # sensor stands. The gyro saw nothing, so the pair across the step is matched on the gyro and the scans.
        tracker=ContinuousLocalization();kinds=[]
        for i in range(30):
            stamp=1000+i*.1;m=self.turned(stamp,math.radians(3) if i>=10 else 0.)
            tracker.imu(m);result=tracker.add(scan(stamp),m)
            self.assertTrue(result['accepted']);kinds.append(result['heading_prior'])
        self.assertLess(abs(tracker.pose[2]),math.radians(.1))
        self.assertEqual(kinds[9],'IMU_orientation');self.assertEqual(kinds[10],'gyro_after_disagreement')
        self.assertGreaterEqual(tracker.heading_disagreements,1)
        # a turn that both saw is followed as before, also when it is as large as the step was
        tracker=ContinuousLocalization();ranges=scan()['ranges']
        for i in range(12):
            stamp=1000+i*.1;m=self.turned(stamp,math.radians(i*.5));m['angular_velocity']['z']=math.radians(5)
            current=scan(stamp);current['ranges']=np.roll(ranges,-i).tolist()
            tracker.imu(m);self.assertEqual(tracker.add(current,m)['heading_prior'],'IMU_orientation')
        self.assertLess(abs(tracker.pose[2]-math.radians(5.5)),math.radians(.1))

    def test_source_cursor_shows_orientation_up_to_the_sweep_middle_without_moving_the_gyro_clock(self):
        source=SourceCursorLocalizer();engine=source.engine
        def rows(stamps):
            messages=[self.turned(t,math.radians(10*(t-1000))) for t in stamps]
            for m in messages:m['angular_velocity']['z']=math.radians(10)
            return [dict(topic='/imu/data_raw',message=m) for m in messages]
        first=scan(1000.);first['scan_time']=.1
        source.consume(rows([999.99,1000.02,1000.045,1000.08,1000.11])+[dict(topic='/scan',message=first)])
        # gyro integration stopped at the scan stamp; orientation went on to the first sample past the reading
        # time (the middle of the sweep, 1000.05, plus the stamp lag)
        self.assertAlmostEqual(engine.gyro_time,999.99);self.assertAlmostEqual(engine.heading_time,1000.08)
        self.assertEqual(len(source.pending_imus),4)
        # heading counts from the first sample (999.99): 10 deg/s over 0.06 s + the lag
        at=10*(.06+HEADING_LAG_S)
        self.assertAlmostEqual(engine.previous['heading'],math.radians(at),places=7)
        second=scan(1000.1);second['scan_time']=.1;second['ranges']=np.roll(second['ranges'],-2).tolist()
        result,_,_=source.consume(rows([1000.14,1000.17])+[dict(topic='/scan',message=second)])
        self.assertTrue(result['accepted']);self.assertAlmostEqual(engine.gyro_time,1000.08)
        self.assertAlmostEqual(engine.previous['heading'],math.radians(at+1.),places=7)
        self.assertLess(abs(result['pose'][2]-math.radians(1.)),math.radians(.05))


if __name__=='__main__':unittest.main()
