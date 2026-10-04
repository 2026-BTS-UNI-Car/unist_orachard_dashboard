import unittest,math
import numpy as np
from dashboard_server.raw_mapping.raw_localization import surfaces,registration,rotate,scan_points,RawLocalization

class RegistrationTests(unittest.TestCase):
    def test_asymmetric_surfaces_recover_translation_and_yaw(self):
        wall=np.linspace(-3,3,1000)
        previous=np.concatenate((np.column_stack((wall,np.full(1000,3))),np.column_stack((np.full(1000,4),wall)),np.column_stack((wall[:600],wall[:600]*.3-2))))
        shift=np.array([.08,-.04]);yaw=.012
        current=(previous-shift)@rotate(yaw)
        result=registration(surfaces(previous),surfaces(current),abs(yaw),.1)
        self.assertTrue(result['accepted']);np.testing.assert_allclose(result['xy'],shift,atol=.003)
        self.assertAlmostEqual(result['yaw'],yaw,places=3)
    def test_parallel_walls_do_not_prove_along_wall_motion(self):
        x=np.linspace(-4,4,2000);points=np.column_stack((x,np.ones(len(x))))
        result=registration(surfaces(points),surfaces(points),0,.1)
        self.assertFalse(result['accepted'])
    def test_measured_3240_beams_supported_and_unbounded_scan_rejected(self):
        scan=dict(ranges=[4.]*3240,angle_min=-math.pi,angle_increment=2*math.pi/3240,range_min=.05,range_max=30)
        self.assertEqual(len(scan_points(scan)),3240)
        scan['ranges']=[4.]*5000
        with self.assertRaises(ValueError):scan_points(scan)
    def test_startup_without_IMU_waits_without_inventing_an_origin(self):
        mapper=RawLocalization();scan=dict(header=dict(stamp=dict(sec=10,nanosec=0)))
        result=mapper.add(scan,None)
        self.assertFalse(result['accepted']);self.assertEqual(mapper.segment,-1);self.assertIsNone(result['pose'])

if __name__=='__main__':unittest.main()
