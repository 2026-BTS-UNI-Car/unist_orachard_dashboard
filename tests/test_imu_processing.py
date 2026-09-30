import math
import unittest

from dashboard_server.imu_processing import (
    IMU_CALIBRATION_REVISION,
    ImuCalibrationConfig,
    StartReferenceCalibrator,
    quaternion_multiply,
    quaternion_to_euler_deg,
    rotate_vector,
)


def quaternion_from_euler(roll_deg: float, pitch_deg: float, yaw_deg: float) -> tuple[float, float, float, float]:
    roll = math.radians(roll_deg) / 2.0
    pitch = math.radians(pitch_deg) / 2.0
    yaw = math.radians(yaw_deg) / 2.0
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def sample(quaternion, gyro=(0.01, -0.02, 0.005), accel=(0.0, 0.0, 9.80665)):
    return {
        "stamp": {"sec": 12, "nanosec": 34},
        "orientation": dict(zip(("x", "y", "z", "w"), quaternion)),
        "angular_velocity": dict(zip(("x", "y", "z"), gyro)),
        "linear_acceleration": dict(zip(("x", "y", "z"), accel)),
    }


class ImuStartReferenceTests(unittest.TestCase):
    def make_calibrator(self) -> StartReferenceCalibrator:
        return StartReferenceCalibrator(
            ImuCalibrationConfig(stationary_window_s=0.4, min_samples=5)
        )

    def test_stationary_tilt_is_zeroed_and_gyro_bias_removed(self):
        calibrator = self.make_calibrator()
        reference = quaternion_from_euler(12.0, -7.0, 38.0)
        # Gravity expressed in the tilted sensor frame.  The calibration must
        # rotate it into the recording-local level/body frame.
        sensor_to_world = reference
        world_gravity = (0.0, 0.0, 9.80665)
        inverse = (-sensor_to_world[0], -sensor_to_world[1], -sensor_to_world[2], sensor_to_world[3])
        sensor_gravity = rotate_vector(inverse, world_gravity)
        calibrator.begin("test", now_monotonic=10.0)
        for index in range(5):
            calibrator.observe(sample(reference, accel=sensor_gravity), now_monotonic=10.0 + index * 0.1)

        self.assertTrue(calibrator.ready)
        corrected = calibrator.correct(sample(reference, accel=sensor_gravity))
        self.assertIsNotNone(corrected)
        euler = corrected["euler_deg"]
        self.assertAlmostEqual(euler["roll"], 0.0, places=5)
        self.assertAlmostEqual(euler["pitch"], 0.0, places=5)
        self.assertAlmostEqual(euler["yaw"], 0.0, places=5)
        self.assertAlmostEqual(corrected["angular_velocity"]["x"], 0.0, places=6)
        self.assertAlmostEqual(corrected["angular_velocity"]["y"], 0.0, places=6)
        self.assertAlmostEqual(corrected["angular_velocity"]["z"], 0.0, places=6)
        self.assertAlmostEqual(corrected["linear_acceleration"]["x"], 0.0, places=5)
        self.assertAlmostEqual(corrected["linear_acceleration"]["y"], 0.0, places=5)
        self.assertAlmostEqual(corrected["linear_acceleration"]["z"], 9.80665, places=5)
        self.assertEqual(corrected["calibration_revision"], IMU_CALIBRATION_REVISION)

    def test_jetson_mount_yaw_restores_rep103_forward_left_up_axes(self):
        calibrator = StartReferenceCalibrator(
            ImuCalibrationConfig(
                stationary_window_s=0.4,
                min_samples=5,
                body_yaw_offset_deg=90.0,
            )
        )
        # The live BNO085 report is near roll=-90 degrees.  Its stationary
        # acceleration lies on report -Y, while the operator-confirmed board
        # mount needs the remaining horizontal +90 degree correction.
        reference = quaternion_from_euler(-90.0, 0.0, -81.0)
        raw_gravity = (0.0, -9.80665, 0.0)
        calibrator.begin("jetson-mount", now_monotonic=0.0)
        for index in range(5):
            calibrator.observe(
                sample(reference, gyro=(0.0, 0.0, 0.0), accel=raw_gravity),
                now_monotonic=index * 0.1,
            )

        self.assertTrue(calibrator.ready)
        transform = calibrator.calibration["sensor_to_body_quaternion"]
        quaternion = tuple(transform[axis] for axis in ("x", "y", "z", "w"))
        self.assertEqual(calibrator.calibration["covariance_frame"], "base_link")
        self.assertEqual(calibrator.calibration["mount_transform"]["body_yaw_offset_deg"], 90.0)
        self.assertAlmostEqual(rotate_vector(quaternion, (1.0, 0.0, 0.0))[1], 1.0, places=6)
        self.assertAlmostEqual(rotate_vector(quaternion, (0.0, 1.0, 0.0))[2], -1.0, places=6)
        self.assertAlmostEqual(rotate_vector(quaternion, (0.0, 0.0, 1.0))[0], -1.0, places=6)

        corrected = calibrator.correct(
            sample(reference, gyro=(0.0, 0.0, -1.0), accel=raw_gravity)
        )
        self.assertIsNotNone(corrected)
        self.assertEqual(corrected["frame_id"], "base_link")
        self.assertAlmostEqual(corrected["linear_acceleration"]["x"], 0.0, places=5)
        self.assertAlmostEqual(corrected["linear_acceleration"]["y"], 0.0, places=5)
        self.assertAlmostEqual(corrected["linear_acceleration"]["z"], 9.80665, places=5)
        self.assertAlmostEqual(corrected["angular_velocity"]["x"], 1.0, places=5)
        self.assertAlmostEqual(corrected["angular_velocity"]["y"], 0.0, places=5)
        self.assertAlmostEqual(corrected["angular_velocity"]["z"], 0.0, places=5)

    def test_motion_resets_window_and_fails_closed_until_new_stationary_window(self):
        calibrator = self.make_calibrator()
        reference = quaternion_from_euler(4.0, 3.0, 0.0)
        calibrator.begin("moving", now_monotonic=0.0)
        calibrator.observe(sample(reference), now_monotonic=0.0)
        calibrator.observe(sample(reference), now_monotonic=0.1)
        calibrator.observe(sample(reference, gyro=(0.4, 0.0, 0.0)), now_monotonic=0.2)
        self.assertFalse(calibrator.ready)
        self.assertEqual(calibrator.status(0.2)["window_reset_count"], 1)
        for index in range(6):
            calibrator.observe(sample(reference), now_monotonic=1.0 + index * 0.1)
        self.assertTrue(calibrator.ready)

    def test_invalid_quaternion_never_produces_corrected_data(self):
        calibrator = self.make_calibrator()
        calibrator.begin("bad", now_monotonic=0.0)
        for index in range(8):
            calibrator.observe(sample((0.0, 0.0, 0.0, 0.0)), now_monotonic=index * 0.1)
        self.assertFalse(calibrator.ready)
        self.assertIsNone(calibrator.correct(sample((0.0, 0.0, 0.0, 0.0))))
        self.assertEqual(calibrator.status(1.0)["last_rejection_reason"], "malformed_or_non_finite")

    def test_finite_quaternion_overflow_cannot_calibrate_or_correct(self):
        calibrator = self.make_calibrator()
        malformed = sample((1e308, 0., 0., 1e308))
        calibrator.begin("overflow", now_monotonic=0.)
        for index in range(8):
            calibrator.observe(malformed, now_monotonic=index * .1)
        self.assertFalse(calibrator.ready)
        self.assertEqual(calibrator.status(1.)["last_rejection_reason"], "malformed_or_non_finite")
        self.assertIsNotNone(calibrator.zero_from_sample(sample((0., 0., 0., 1.)),
                                                        reference_id="valid", now_monotonic=1.))
        self.assertIsNone(calibrator.correct(malformed))
        self.assertIsNone(calibrator.zero_from_sample(malformed, reference_id="overflow",
                                                     now_monotonic=1.1))
        self.assertEqual(calibrator.recording_id, "valid")

    def test_post_start_motion_is_expressed_relative_to_start_body(self):
        calibrator = self.make_calibrator()
        reference = quaternion_from_euler(10.0, -5.0, 30.0)
        calibrator.begin("relative", now_monotonic=0.0)
        for index in range(5):
            calibrator.observe(sample(reference), now_monotonic=index * 0.1)
        body_yaw_delta = quaternion_from_euler(0.0, 0.0, 15.0)
        moved = quaternion_multiply(body_yaw_delta, reference)
        corrected = calibrator.correct(sample(moved))
        self.assertIsNotNone(corrected)
        self.assertAlmostEqual(corrected["euler_deg"]["yaw"], 15.0, places=3)

    def test_variance_is_reported_in_corrected_body_frame(self):
        calibrator = self.make_calibrator()
        reference = quaternion_from_euler(90.0, 0.0, 0.0)
        calibrator.begin("body-covariance", now_monotonic=0.0)
        sensor_y_noise = (-0.02, -0.01, 0.0, 0.01, 0.02)
        for index, noise in enumerate(sensor_y_noise):
            calibrator.observe(
                sample(reference, gyro=(0.0, noise, 0.0)),
                now_monotonic=index * 0.1,
            )

        self.assertTrue(calibrator.ready)
        covariance = calibrator.calibration["gyro_variance"]
        self.assertEqual(calibrator.calibration["covariance_frame"], "base_link")
        self.assertAlmostEqual(covariance["x"], 0.0, places=12)
        self.assertAlmostEqual(covariance["y"], 0.0, places=12)
        self.assertGreater(covariance["z"], 0.0)

    def test_dashboard_manual_zero_makes_current_pose_identity_immediately(self):
        calibrator = self.make_calibrator()
        reference = quaternion_from_euler(-22.0, 11.0, 73.0)
        calibration = calibrator.zero_from_sample(
            sample(reference, gyro=(0.03, -0.01, 0.02)),
            reference_id="manual-test",
            now_monotonic=42.0,
        )
        self.assertIsNotNone(calibration)
        self.assertTrue(calibration["manual_zero"])
        self.assertFalse(calibration["covariance_estimated"])
        corrected = calibrator.correct(sample(reference, gyro=(0.03, -0.01, 0.02)))
        self.assertIsNotNone(corrected)
        for axis in ("roll", "pitch", "yaw"):
            self.assertAlmostEqual(corrected["euler_deg"][axis], 0.0, places=5)
        self.assertEqual(corrected["calibration_source"], "dashboard_manual_zero")
        self.assertEqual(corrected["calibration_reference_id"], "manual-test")
        self.assertEqual(calibrator.status(now_monotonic=420.0)["stationary_span_s"], 0.0)

    def test_dashboard_manual_zero_rejects_invalid_sample(self):
        calibrator = self.make_calibrator()
        calibration = calibrator.zero_from_sample(
            sample((0.0, 0.0, 0.0, 0.0)),
            reference_id="bad-manual-test",
        )
        self.assertIsNone(calibration)
        self.assertFalse(calibrator.ready)
        self.assertEqual(calibrator.last_rejection_reason, "manual_zero_malformed_or_non_finite")


if __name__ == "__main__":
    unittest.main()
