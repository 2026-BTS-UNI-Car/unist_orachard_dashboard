"""Pure native-data contract tests, with no ROS or hardware dependency."""
import copy
import math
from types import SimpleNamespace
import unittest
from dashboard_server.imu_processing import raw_imu_payload, imu_processing_status, quaternion_to_euler_deg, imu_zero_removed


def message():
    return SimpleNamespace(header=SimpleNamespace(frame_id='sensor_frame', stamp=SimpleNamespace(sec=123, nanosec=987654321)),
        orientation=SimpleNamespace(x=0.,y=0.,z=.5,w=math.sqrt(3)/2),
        angular_velocity=SimpleNamespace(x=.11,y=-.22,z=.33),
        linear_acceleration=SimpleNamespace(x=1.,y=-2.,z=9.),
        orientation_covariance=[0.]*9,
        angular_velocity_covariance=[.1,.02,0.,.02,.2,0.,0.,0.,.3],
        linear_acceleration_covariance=[-1.]+[0.]*8)


class NativeImuProcessingTests(unittest.TestCase):
    def test_native_values_and_full_covariances_are_not_rotated_or_zeroed(self):
        msg=message(); before=copy.deepcopy(msg)
        row=raw_imu_payload(msg)
        self.assertEqual(msg,before)
        self.assertEqual(row['frame_id'],'sensor_frame')
        self.assertEqual(row['stamp'],{'sec':123,'nanosec':987654321})
        self.assertEqual(row['orientation'],vars(msg.orientation))
        self.assertEqual(row['angular_velocity'],vars(msg.angular_velocity))
        self.assertEqual(row['linear_acceleration'],vars(msg.linear_acceleration))
        self.assertEqual(row['covariance']['angular_velocity'],msg.angular_velocity_covariance)
        self.assertAlmostEqual(row['euler_deg']['yaw'],60.)
        self.assertEqual(row['availability']['orientation'],{'available':True,'uncertainty':'unknown'})
        self.assertEqual(row['availability']['angular_velocity']['uncertainty'],'reported')
        self.assertEqual(row['availability']['linear_acceleration'],{'available':False,'uncertainty':'unavailable'})
        self.assertEqual(row['processing']['mode'],'sensor_native_raw')
        self.assertFalse(row['processing']['software_rotation'])
        self.assertFalse(row['processing']['gyro_bias_correction'])

    def test_unavailable_zero_quaternion_is_preserved_without_fake_euler_angles(self):
        msg=message();msg.orientation=SimpleNamespace(x=0.,y=0.,z=0.,w=0.)
        msg.orientation_covariance=[-1.]+[0.]*8
        row=raw_imu_payload(msg)
        self.assertEqual(row['orientation'],vars(msg.orientation))
        self.assertEqual(row['euler_deg'],{})
        self.assertFalse(row['availability']['orientation']['available'])

    def test_invalid_available_quaternion_or_covariance_is_rejected(self):
        for q in ((0.,0.,0.,0.),(0.,0.,1.,1.),(math.nan,0.,0.,1.),(1e308,0.,0.,1e308)):
            msg=message();msg.orientation=SimpleNamespace(**dict(zip(('x','y','z','w'),q)))
            with self.subTest(q=q),self.assertRaises(ValueError):raw_imu_payload(msg)
        for cov in ([0.]*8,[math.nan]+[0.]*8):
            msg=message();msg.angular_velocity_covariance=cov
            with self.subTest(cov=cov),self.assertRaises(ValueError):raw_imu_payload(msg)

    def test_half_turn_and_negative_sign_are_not_replaced(self):
        for q in ((1.,0.,0.,0.),(0.,0.,-.5,-math.sqrt(3)/2)):
            msg=message();msg.orientation=SimpleNamespace(**dict(zip(('x','y','z','w'),q)))
            row=raw_imu_payload(msg)
            self.assertEqual(tuple(row['orientation'].values()),q)
            self.assertTrue(row['euler_deg'])

    def test_zero_api_response_cannot_create_a_reference(self):
        self.assertFalse(imu_zero_removed()['ok'])
        self.assertEqual(imu_zero_removed()['state'],'removed')
        self.assertFalse(imu_processing_status()['manual_zero'])
        self.assertFalse(imu_processing_status()['start_reference'])

    def test_invalid_display_quaternions_remain_missing(self):
        for values in ((),(0,0,0,0),(0,0,1,1),(None,0,0,1)):
            self.assertEqual(quaternion_to_euler_deg(values),{})

if __name__=='__main__':unittest.main()
