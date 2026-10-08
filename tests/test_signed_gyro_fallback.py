"""Sensor-only selection regressions; no truth, wheel, PWM or camera inputs."""
import copy
import importlib
import math
import os
import types
import unittest
from contextlib import ExitStack
from unittest.mock import patch
import numpy as np
from scipy.spatial.transform import Rotation

# Default targets production. The private Linux entry supplies its reviewed
# candidate module name; the public test has no private path or baseline import.
candidate=importlib.import_module(os.environ.get('UBTS_LOCALIZATION_MODULE',
    'dashboard_server.raw_mapping.continuous_localization'))


def imu(stamp,yaw=0.,rate=0.,tilt=0.):
    q=Rotation.from_euler('xyz',[tilt,0.,yaw])
    # Native angular velocity expressed in the same sensor frame as quaternion.
    w=q.inv().apply([0.,0.,rate])
    sec=math.floor(stamp);nsec=round((stamp-sec)*1e9)
    return dict(header=dict(stamp=dict(sec=sec,nanosec=nsec),frame_id='imu'),
        orientation=dict(zip('xyzw',q.as_quat())),
        angular_velocity=dict(zip('xyz',w)),
        linear_acceleration=dict(x=0.,y=0.,z=9.80665),
        orientation_covariance=[0.]*9,angular_velocity_covariance=[0.]*9)


def scan(stamp):
    sec=math.floor(stamp);nsec=round((stamp-sec)*1e9)
    return dict(header=dict(stamp=dict(sec=sec,nanosec=nsec),frame_id='laser'),
        ranges=[5.]*3240,angle_min=-math.pi,angle_increment=2*math.pi/3240,
        range_min=.05,range_max=30.,scan_time=.1,time_increment=0.)


def fit(yaw):
    return dict(accepted=True,xy=np.array([.08,.01]),yaw=yaw,coverage=.9,
        rmse_m=.01,inliers=200,information_eigenvalues=[.01,.1,.2])


class SignedFallbackTests(unittest.TestCase):
    def run_adjacent(self,module,proposed,covered=True,tilt=0.):
        rate=math.radians(100)
        first=imu(0.,rate=rate,tilt=tilt)
        second=imu(.1,yaw=math.radians(10),rate=rate,tilt=tilt)
        scans=[scan(0.),scan(.1)]
        inputs=copy.deepcopy([first,second,scans])
        surface=types.SimpleNamespace(points=np.zeros((240,2)),dense=np.zeros((3240,2)),denominator=240)
        with ExitStack() as stack:
            stack.enter_context(patch.object(module,'surfaces',return_value=surface))
            stack.enter_context(patch.object(module,'registration',return_value=fit(proposed)))
            stack.enter_context(patch.object(module,'point_support_registration',return_value={'accepted':False}))
            stack.enter_context(patch.object(module,'proposal',return_value=None))
            engine=module.ContinuousLocalization()
            stack.enter_context(patch.object(engine,'_fit',return_value=None))
            stack.enter_context(patch.object(engine,'_recover',return_value=None))
            stack.enter_context(patch.object(engine,'_motion_bridge',return_value=None))
            engine.imu(first)
            self.assertTrue(engine.add(scans[0],first)['accepted'])
            if covered:engine.imu(second)
            result=engine.add(scans[1],second)
        self.assertEqual(inputs,[first,second,scans])
        return result,engine

    def test_unsigned_wrong_turn_previously_accepted_now_retains_pose(self):
        yaw=math.radians(-10)
        new,engine=self.run_adjacent(candidate,yaw)
        self.assertFalse(new['accepted'])
        self.assertEqual(new['reason'],'fixed_frame_registration_unresolved')
        np.testing.assert_array_equal(engine.pose,np.zeros(3))
        self.assertEqual(engine.accepted,1)

    def test_correct_signed_geometry_proposal_preserves_exact_output(self):
        yaw=math.radians(10)
        new,_=self.run_adjacent(candidate,yaw)
        self.assertTrue(new['accepted'])
        np.testing.assert_array_equal(new['pose'],np.array([.08,.01,yaw]))
        self.assertEqual(new['reason'],'adjacent_surface_fit')
        self.assertTrue(new['map_eligible'])

    def test_missing_gyro_coverage_does_not_invent_signed_constraint(self):
        new,_=self.run_adjacent(candidate,math.radians(-10),covered=False)
        self.assertTrue(new['accepted'])
        np.testing.assert_array_equal(new['pose'],np.array([.08,.01,math.radians(-10)]))

    def test_existing_two_degree_inclusive_bound_unchanged(self):
        new,_=self.run_adjacent(candidate,math.radians(12))
        self.assertTrue(new['accepted'])
        rejected,_=self.run_adjacent(candidate,math.radians(12.0001))
        self.assertFalse(rejected['accepted'])

    def test_tilted_native_sensor_preserves_projected_signed_rate(self):
        new,_=self.run_adjacent(candidate,math.radians(-10),tilt=.4)
        self.assertFalse(new['accepted'])
        accepted,engine=self.run_adjacent(candidate,math.radians(10),tilt=.4)
        self.assertTrue(accepted['accepted'])
        self.assertAlmostEqual(engine.gyro_integral,math.radians(10),places=14)

    def test_recovery_wrong_signed_fit_rejected_correct_fit_preserved(self):
        def recover(module,yaw):
            engine=module.ContinuousLocalization()
            q=Rotation.from_euler('z',.16).as_quat()
            class Tree:
                def query(self,points,workers):
                    return np.full(len(points),.1),np.zeros(len(points),dtype=int)
            engine.previous=dict(stamp=0.,gyro=0.,gyro_valid=0.,q=np.array([0.,0.,0.,1.]),
                pose=np.zeros(3),surfaces=types.SimpleNamespace(tree=Tree()))
            for i in range(9):engine.imu(imu(i*.1,yaw=i*.02,rate=.2))
            current=types.SimpleNamespace(points=np.zeros((240,2)))
            with patch.object(module,'fit_surfaces',return_value=fit(yaw)):
                return engine._recover(current,q,.8)
        self.assertIsNone(recover(candidate,-.16))
        new=recover(candidate,.16)
        self.assertEqual(new['yaw'],.16)
        np.testing.assert_array_equal(new['xy'],np.array([.08,.01]))


if __name__=='__main__':unittest.main()
