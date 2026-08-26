import json
import unittest
from types import SimpleNamespace

from scripts.imu_ws_bridge import BRIDGE_SCHEMA, ImuBridgeState


def imu_message(yaw_quaternion=(0.0, 0.0, 0.0, 1.0)):
    x, y, z, w = yaw_quaternion
    return SimpleNamespace(
        header=SimpleNamespace(frame_id="imu_link", stamp=SimpleNamespace(sec=10, nanosec=20)),
        orientation=SimpleNamespace(x=x, y=y, z=z, w=w),
        angular_velocity=SimpleNamespace(x=0.1, y=0.2, z=0.3),
        linear_acceleration=SimpleNamespace(x=0.0, y=0.0, z=9.80665),
    )


class ImuBridgeStateTests(unittest.TestCase):
    def test_raw_corrected_magnetic_and_calibration_are_kept_separate(self):
        state = ImuBridgeState()
        state.update_imu(imu_message(), corrected=False)
        state.update_imu(imu_message((0.0, 0.0, 0.1305261922, 0.9914448614)), corrected=True)
        state.update_magnetic(
            SimpleNamespace(
                header=SimpleNamespace(frame_id="imu_link", stamp=SimpleNamespace(sec=10, nanosec=21)),
                magnetic_field=SimpleNamespace(x=12e-6, y=-4e-6, z=42e-6),
            )
        )
        state.update_calibration(SimpleNamespace(data=json.dumps({"state": "ready", "valid": True})))

        snapshot = state.snapshot()
        self.assertEqual(snapshot["schema"], BRIDGE_SCHEMA)
        self.assertTrue(snapshot["imu_topic_alive"])
        self.assertTrue(snapshot["corrected_topic_alive"])
        self.assertAlmostEqual(snapshot["corrected"]["euler_deg"]["yaw"], 15.0, places=4)
        self.assertAlmostEqual(snapshot["magnetic"]["magnetic_field_uT"]["z"], 42.0, places=6)
        self.assertEqual(snapshot["calibration"]["state"], "ready")

    def test_invalid_calibration_json_does_not_replace_last_valid_state(self):
        state = ImuBridgeState()
        state.update_calibration(SimpleNamespace(data='{"state":"collecting"}'))
        state.update_calibration(SimpleNamespace(data="not-json"))
        self.assertEqual(state.snapshot()["calibration"]["state"], "collecting")


if __name__ == "__main__":
    unittest.main()
