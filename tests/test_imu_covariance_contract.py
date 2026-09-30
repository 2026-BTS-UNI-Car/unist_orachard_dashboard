"""ROS Imu wire semantics without opening I2C, a ROS node, or a DDS transport."""
from contextlib import ExitStack
import math
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dashboard_server.imu_processing import ImuCalibrationConfig, StartReferenceCalibrator
from scripts import imu_ros2_driver as driver

try:
    import rclpy
    import rclpy.node
    from builtin_interfaces.msg import Time
    from rclpy.serialization import deserialize_message, serialize_message
    from sensor_msgs.msg import Imu
    from scripts.data_recorder_service import RecorderNode
except ModuleNotFoundError as exc:
    if exc.name not in {'rclpy', 'sensor_msgs', 'builtin_interfaces'}:
        raise
    Imu = None


class CorrectedTimelineCovarianceTest(unittest.TestCase):
    def test_available_corrected_orientation_reports_unknown_not_unavailable_covariance(self):
        calibrator = StartReferenceCalibrator(ImuCalibrationConfig(stationary_window_s=.2, min_samples=3))
        sample = {'stamp': {'sec': 12, 'nanosec': 34},
                  'orientation': {'x': 0., 'y': 0., 'z': 0., 'w': 1.},
                  'angular_velocity': {'x': .01, 'y': -.02, 'z': .03},
                  'linear_acceleration': {'x': 0., 'y': 0., 'z': 9.80665}}
        calibrator.begin('contract', now_monotonic=0.)
        for instant in (0., .1, .2):
            calibrator.observe(sample, now_monotonic=instant)
        corrected = calibrator.correct(sample)
        self.assertIsNotNone(corrected)
        self.assertEqual(corrected['covariance']['orientation_diagonal'], {'x': 0., 'y': 0., 'z': 0.})
        self.assertEqual(corrected['orientation'], {'x': 0., 'y': 0., 'z': 0., 'w': 1.})
        self.assertFalse(calibrator.calibration['mapping_ready'])


@unittest.skipUnless(Imu is not None, 'ROS2 message/runtime packages required')
class RosImuCovarianceContractTest(unittest.TestCase):
    def run_driver_once(self, *, quaternion=(0., 0., 0., 1.), acceleration=(1., 2., 9.)):
        messages = {}
        sensor = SimpleNamespace(acceleration=acceleration, gyro=(.1, -.2, .3), magnetic=(10., 20., 30.),
                                 quaternion=quaternion, enable_feature=Mock())

        class FakeNode:
            def __init__(self, name):
                pass

            def create_publisher(self, kind, topic, qos):
                messages[topic] = []
                return SimpleNamespace(publish=messages[topic].append)

            def create_timer(self, period, callback):
                return None

            def get_clock(self):
                return SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: Time(sec=12, nanosec=34)))

            def get_logger(self):
                return SimpleNamespace(info=lambda message: None)

            def destroy_node(self):
                return None

        modules = {
            'adafruit_bno08x': SimpleNamespace(BNO_REPORT_ACCELEROMETER=1, BNO_REPORT_GYROSCOPE=2,
                                               BNO_REPORT_MAGNETOMETER=3, BNO_REPORT_ROTATION_VECTOR=5),
            'adafruit_bno08x.i2c': SimpleNamespace(BNO08X_I2C=lambda bus, address: sensor),
            'adafruit_extended_bus': SimpleNamespace(ExtendedI2C=lambda bus: SimpleNamespace(deinit=lambda: None)),
        }
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, modules))
            stack.enter_context(patch.object(rclpy.node, 'Node', FakeNode))
            stack.enter_context(patch.object(rclpy, 'init'))
            stack.enter_context(patch.object(rclpy, 'shutdown'))
            stack.enter_context(patch.object(rclpy, 'ok', side_effect=[True, False]))
            stack.enter_context(patch.object(rclpy, 'spin_once', side_effect=lambda node, **kwargs: node.tick()))
            stack.enter_context(patch.object(driver.signal, 'signal'))
            stack.enter_context(patch.object(driver, 'write_json_atomic'))
            stack.enter_context(patch.object(sys, 'argv', ['imu-driver', '--raw-topic', '/test/raw',
                                                          '--mag-topic', '/test/mag', '--status-topic', '/test/status']))
            self.assertEqual(driver.main(), 0)
        return messages

    def test_valid_driver_measurements_survive_ros_round_trip_with_unknown_covariance(self):
        messages = self.run_driver_once(quaternion=(0., 0., 1., math.sqrt(3.)))
        self.assertEqual(len(messages['/test/raw']), 1)
        message = deserialize_message(serialize_message(messages['/test/raw'][0]), Imu)
        self.assertEqual((message.header.stamp.sec, message.header.stamp.nanosec), (12, 34))
        self.assertEqual(message.header.frame_id, 'imu_link')
        self.assertAlmostEqual(message.orientation.z, .5)
        self.assertAlmostEqual(message.orientation.w, math.sqrt(3.) / 2.)
        self.assertEqual((message.angular_velocity.x, message.angular_velocity.y, message.angular_velocity.z), (.1, -.2, .3))
        self.assertEqual((message.linear_acceleration.x, message.linear_acceleration.y, message.linear_acceleration.z), (1., 2., 9.))
        for name in ('orientation_covariance', 'angular_velocity_covariance', 'linear_acceleration_covariance'):
            self.assertEqual(list(getattr(message, name)), [0.] * 9)
        self.assertEqual(len(messages['/test/mag']), 1)

    def test_zero_quaternion_warmup_does_not_publish_available_measurements(self):
        messages = self.run_driver_once(quaternion=(0., 0., 0., 0.))
        self.assertEqual(messages['/test/raw'], [])
        self.assertEqual(messages['/test/mag'], [])

    def test_finite_quaternion_norm_overflow_does_not_publish_measurements(self):
        messages = self.run_driver_once(quaternion=(1e308, 0., 0., 1e308))
        self.assertEqual(messages['/test/raw'], [])
        self.assertEqual(messages['/test/mag'], [])

    def test_nonfinite_sample_does_not_publish_available_measurements(self):
        messages = self.run_driver_once(acceleration=(float('nan'), 0., 9.))
        self.assertEqual(messages['/test/raw'], [])

    def test_corrected_publisher_preserves_variances_and_unknown_orientation_on_wire(self):
        corrected = {'stamp': {'sec': 5, 'nanosec': 7}, 'frame_id': 'base_link',
                     'orientation': {'x': 0., 'y': 0., 'z': .5, 'w': math.sqrt(3.) / 2.},
                     'angular_velocity': {'x': .1, 'y': -.2, 'z': .3},
                     'linear_acceleration': {'x': 0., 'y': 0., 'z': 9.80665},
                     'covariance': {'angular_velocity_diagonal': {'x': .01, 'y': .02, 'z': .03},
                                    'linear_acceleration_diagonal': {'x': .1, 'y': .2, 'z': .3}}}
        published = []
        node = SimpleNamespace(state=SimpleNamespace(observe_imu=lambda message: corrected),
                               corrected_imu_publisher=SimpleNamespace(publish=published.append),
                               last_calibration_publish_at=time.monotonic())
        RecorderNode.observe_imu(node, Imu())
        self.assertEqual(len(published), 1)
        message = deserialize_message(serialize_message(published[0]), Imu)
        self.assertEqual(message.header.frame_id, 'base_link')
        self.assertEqual((message.header.stamp.sec, message.header.stamp.nanosec), (5, 7))
        self.assertEqual(message.orientation.z, .5)
        self.assertEqual(list(message.orientation_covariance), [0.] * 9)
        self.assertEqual(list(message.angular_velocity_covariance), [.01, 0., 0., 0., .02, 0., 0., 0., .03])
        self.assertEqual(list(message.linear_acceleration_covariance), [.1, 0., 0., 0., .2, 0., 0., 0., .3])

    def test_corrected_180_degree_rotations_preserve_zero_scalar_on_wire(self):
        for axis in ('x', 'y', 'z'):
            with self.subTest(axis=axis):
                orientation = dict.fromkeys(('x', 'y', 'z', 'w'), 0.)
                orientation[axis] = 1.
                published = []
                node = SimpleNamespace(state=SimpleNamespace(observe_imu=lambda message: {'orientation': orientation}),
                                       corrected_imu_publisher=SimpleNamespace(publish=published.append),
                                       last_calibration_publish_at=time.monotonic())
                RecorderNode.observe_imu(node, Imu())
                self.assertEqual(len(published), 1)
                message = deserialize_message(serialize_message(published[0]), Imu)
                actual = tuple(getattr(message.orientation, key) for key in ('x', 'y', 'z', 'w'))
                self.assertEqual(actual, tuple(orientation.values()))
                self.assertEqual(message.orientation.w, 0.)
                self.assertAlmostEqual(math.hypot(*actual), 1.)
                self.assertEqual(list(message.orientation_covariance), [0.] * 9)

    def test_malformed_corrected_orientation_is_not_published_as_available(self):
        invalid = [None, {}, {'x': 0., 'y': 0., 'z': 0.},
                   {'x': 0., 'y': 0., 'z': 0., 'w': None},
                   {'x': 0., 'y': 0., 'z': 0., 'w': 0.},
                   {'x': 0., 'y': 0., 'z': 1., 'w': 1.},
                   {'x': 0., 'y': 0., 'z': 0., 'w': math.nan},
                   {'x': math.inf, 'y': 0., 'z': 0., 'w': 1.}]
        for orientation in invalid:
            with self.subTest(orientation=orientation):
                published = []
                node = SimpleNamespace(state=SimpleNamespace(observe_imu=lambda message: {'orientation': orientation}),
                                       corrected_imu_publisher=SimpleNamespace(publish=published.append),
                                       last_calibration_publish_at=time.monotonic())
                RecorderNode.observe_imu(node, Imu())
                self.assertEqual(published, [])

    def test_no_corrected_estimate_produces_no_message(self):
        published = []
        node = SimpleNamespace(state=SimpleNamespace(observe_imu=lambda message: None),
                               corrected_imu_publisher=SimpleNamespace(publish=published.append),
                               last_calibration_publish_at=time.monotonic())
        RecorderNode.observe_imu(node, Imu())
        self.assertEqual(published, [])


if __name__ == '__main__':
    unittest.main()
