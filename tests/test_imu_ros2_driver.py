from __future__ import annotations

import math
import unittest

from scripts.imu_ros2_driver import TransientSensorSample, normalized_quaternion, vector_dict


class ImuRos2DriverPureTests(unittest.TestCase):
    def test_zero_warmup_quaternion_is_transient(self) -> None:
        with self.assertRaises(TransientSensorSample):
            normalized_quaternion((0.0, 0.0, 0.0, 0.0))

    def test_valid_quaternion_is_normalized_and_sign_stable(self) -> None:
        value = normalized_quaternion((0.0, 0.0, 0.0, -2.0))
        self.assertEqual(value, (0.0, 0.0, 0.0, 1.0))
        self.assertAlmostEqual(math.sqrt(sum(item * item for item in value)), 1.0)

    def test_non_finite_vector_is_transient(self) -> None:
        with self.assertRaises(TransientSensorSample):
            vector_dict(("x", "y", "z"), (1.0, float("nan"), 3.0))


if __name__ == "__main__":
    unittest.main()
