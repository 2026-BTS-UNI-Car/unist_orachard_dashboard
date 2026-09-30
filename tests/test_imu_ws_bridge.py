import copy
import unittest
from types import SimpleNamespace
from scripts.imu_ws_bridge import BRIDGE_SCHEMA, ImuBridgeState
from tests.test_imu_processing import message


class ImuBridgeStateTests(unittest.TestCase):
    def test_only_raw_native_values_and_uncertainty_are_exposed(self):
        state=ImuBridgeState();msg=message();original=copy.deepcopy(msg)
        state.update_imu(msg)
        state.update_magnetic(SimpleNamespace(header=msg.header,magnetic_field=SimpleNamespace(x=12e-6,y=-4e-6,z=42e-6)))
        snapshot=state.snapshot()
        self.assertEqual(msg,original)
        self.assertEqual(snapshot['schema'],BRIDGE_SCHEMA)
        self.assertTrue(snapshot['imu_topic_alive'])
        self.assertEqual(snapshot['raw']['orientation'],vars(msg.orientation))
        self.assertEqual(snapshot['raw']['covariance']['angular_velocity'],msg.angular_velocity_covariance)
        self.assertAlmostEqual(snapshot['raw']['euler_deg']['yaw'],60.)
        self.assertAlmostEqual(snapshot['magnetic']['magnetic_field_uT']['z'],42.)
        self.assertNotIn('corrected',snapshot)
        self.assertNotIn('calibration',snapshot)
        self.assertFalse(snapshot['processing']['manual_zero'])

    def test_bad_available_quaternion_does_not_publish_a_fake_heading(self):
        state=ImuBridgeState();msg=message();msg.orientation.w=0.
        state.update_imu(msg)
        self.assertFalse(state.snapshot()['imu_topic_alive'])
        self.assertEqual(state.snapshot()['raw'],{})
        self.assertIsNotNone(state.snapshot()['error'])

if __name__=='__main__':unittest.main()
