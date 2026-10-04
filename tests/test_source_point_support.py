import unittest
import numpy as np
from dashboard_server.raw_mapping.raw_localization import surfaces,rotate
from dashboard_server.raw_mapping.raw_point_support import point_support_registration

class PointSupportTests(unittest.TestCase):
    def test_single_wall_does_not_gain_unobservable_translation(self):
        x=np.linspace(-4,4,2000);points=np.column_stack((x,np.ones(len(x))))
        self.assertFalse(point_support_registration(surfaces(points),surfaces(points),0,.1)['accepted'])
    def test_full_point_fit_recovers_pose_with_separate_normal_support(self):
        axis=np.linspace(-3,3,1000)
        points=np.concatenate((np.column_stack((axis,np.full(1000,3))),np.column_stack((np.full(1000,4),axis)),np.column_stack((axis[:600],axis[:600]*.3-2))))
        xy=np.array([.08,-.04]);yaw=.012;current=(points-xy)@rotate(yaw)
        result=point_support_registration(surfaces(points),surfaces(current),abs(yaw),.1)
        self.assertTrue(result['accepted']);np.testing.assert_allclose(result['xy'],xy,atol=.004)
        # Dense nearest-point fitting has finite point-spacing bias; at a 4m
        # lever arm this angular tolerance contributes at most 4mm of error.
        self.assertLess(abs(result['yaw']-yaw),.001)

if __name__=='__main__':unittest.main()
