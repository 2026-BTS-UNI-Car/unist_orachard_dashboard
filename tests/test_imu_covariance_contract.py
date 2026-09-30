"""ROS Imu wire semantics without opening I2C, a ROS node, or a DDS transport."""
from contextlib import ExitStack
import math
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dashboard_server.imu_processing import raw_imu_payload
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

    def relay(self, msg):
        published=[]
        def observe(message):
            try:return raw_imu_payload(message)
            except (TypeError,ValueError,AttributeError,OverflowError):return None
        node=SimpleNamespace(state=SimpleNamespace(observe_imu=observe),
                             imu_relay_publisher=SimpleNamespace(publish=published.append))
        RecorderNode.observe_imu(node,msg)
        return published

    def test_raw_relay_is_wire_identical_including_header_vectors_and_full_covariance(self):
        msg=Imu();msg.header.frame_id='sensor_frame'
        msg.header.stamp.sec=123;msg.header.stamp.nanosec=987654321
        msg.orientation.z=.5;msg.orientation.w=math.sqrt(3)/2
        msg.angular_velocity.x=.17;msg.angular_velocity.y=-.28;msg.angular_velocity.z=.39
        msg.linear_acceleration.x=1.;msg.linear_acceleration.y=-2.;msg.linear_acceleration.z=9.5
        msg.orientation_covariance=[0.]*9
        msg.angular_velocity_covariance=[.1,.02,0.,.02,.2,0.,0.,0.,.3]
        msg.linear_acceleration_covariance=[.4,0.,.03,0.,.5,0.,.03,0.,.6]
        original=serialize_message(msg)
        output=self.relay(msg)
        self.assertEqual(len(output),1)
        self.assertEqual(serialize_message(output[0]),original)
        self.assertEqual(serialize_message(msg),original)

    def test_raw_relay_preserves_unavailable_groups_and_half_turns(self):
        for unavailable in (False,True):
            for axis in ('x','y','z'):
                msg=Imu();msg.header.frame_id='imu_link';msg.orientation.w=0.;setattr(msg.orientation,axis,1.)
                for name in ('orientation_covariance','angular_velocity_covariance','linear_acceleration_covariance'):
                    setattr(msg,name,[-1.]+[0.]*8 if unavailable else [0.]*9)
                original=serialize_message(msg)
                with self.subTest(unavailable=unavailable,axis=axis):
                    output=self.relay(msg)
                    self.assertEqual(len(output),1)
                    self.assertEqual(serialize_message(output[0]),original)
                    self.assertEqual(output[0].orientation.w,0.)

    def test_invalid_available_orientation_does_not_become_relay_measurement(self):
        for values in ((0.,0.,0.,0.),(0.,0.,1.,1.),(math.nan,0.,0.,1.)):
            msg=Imu();msg.header.frame_id='imu_link'
            for axis,value in zip(('x','y','z','w'),values):setattr(msg.orientation,axis,value)
            with self.subTest(values=values):self.assertEqual(self.relay(msg),[])


if __name__ == '__main__':
    unittest.main()
