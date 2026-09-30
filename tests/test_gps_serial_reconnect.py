"""Linux PTY regressions; never opens a hardware port or starts ROS."""
import errno
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dashboard_server.gps import ubx_packet

if sys.platform == 'linux':
    import fcntl
    import pty
    import termios
    from scripts import gps_ros2_driver as driver


def pvt():
    payload = bytearray(92)
    payload[20], payload[21], payload[23] = 3, 1, 8
    struct.pack_into('<iiiiII', payload, 24, 10000000, 20000000, 1000, 1000, 100, 200)
    return ubx_packet(1, 7, payload)


def identity():
    return ubx_packet(10, 4, b'OLD RECEIVER'.ljust(30, b'\0') + b'00000001\0\0'
                      + b'MOD=OLD'.ljust(30, b'\0'))


@unittest.skipUnless(sys.platform == 'linux', 'Linux PTY serial implementation')
class SerialReconnectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.port = self.root / 'stable-gps'
        self.masters = []
        self.serials = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for serial in self.serials: serial.close()
        for fd in self.masters:
            if fd is not None: os.close(fd)
        self.temp.cleanup()

    def attach(self):
        master, slave = pty.openpty()
        target = os.ttyname(slave)
        os.close(slave)
        os.set_blocking(master, False)
        self.masters.append(master)
        self.port.unlink(missing_ok=True)
        self.port.symlink_to(target)
        return master

    def unplug(self, master):
        self.port.unlink()
        os.close(master)
        self.masters[self.masters.index(master)] = None

    def serial(self, **kwargs):
        serial = driver.GpsSerial(str(self.port), lock_path=str(self.root / 'serial.lock'), **kwargs)
        self.serials.append(serial)
        return serial

    def receive(self, serial):
        deadline = time.perf_counter() + 1
        frames = []
        while not frames and time.perf_counter() < deadline:
            frames.extend(serial.poll())
            if not frames: time.sleep(.005)
        self.assertTrue(frames)
        return frames

    def assert_closed(self, *fds):
        for fd in fds:
            with self.assertRaises(OSError) as error: os.fstat(fd)
            self.assertEqual(error.exception.errno, errno.EBADF)

    def test_unplug_replug_same_path_invalidates_old_fix_and_requeries_identity(self):
        first = self.attach()
        serial = self.serial(baud=38400)
        serial.poll()
        expected = (ubx_packet(10, 4) + ubx_packet(1, 7) + ubx_packet(1, 20)
                    + b''.join(driver.nmea_poll(t, m) for t in ('GN', 'GP') for m in ('GGA', 'RMC')))
        self.assertEqual(os.read(first, 4096), expected)
        os.write(first, identity() + pvt() + pvt()[:20])
        self.assertTrue(self.receive(serial)[0]['valid'])
        self.assertTrue(serial.decoder.identity)
        self.assertTrue(serial.decoder.buffer)
        old_fd, old_lock = serial.fd, serial.lock_fd
        self.unplug(first)
        with patch.object(driver.time, 'monotonic', return_value=100):
            self.assertEqual(serial.poll(), [])
        self.assert_closed(old_fd, old_lock)
        disconnected = serial.snapshot()
        self.assertFalse(disconnected['valid'])
        self.assertFalse(disconnected['connected'])
        self.assertFalse(disconnected['driver']['serial_open'])
        failure = serial.error
        self.assertTrue(failure)
        second = self.attach()
        with patch.object(driver.time, 'monotonic', return_value=104.9):
            self.assertEqual(serial.poll(), [])
        self.assertIsNone(serial.fd)
        with patch.object(driver.time, 'monotonic', return_value=105):
            self.assertEqual(serial.poll(), [])
        self.assertIsNotNone(serial.fd)
        self.assertEqual(serial.error, failure)  # Empty read is not evidence of reception.
        self.assertEqual(serial.decoder.identity, {})
        self.assertEqual(serial.decoder.sample, {})
        self.assertEqual(serial.decoder.motion, {})
        self.assertEqual(serial.decoder.gst, {})
        self.assertEqual(serial.decoder.buffer, b'')
        self.assertIsNone(serial.decoder.last_sample_at)
        self.assertEqual(serial.bytes_received, 0)
        self.assertEqual(serial.snapshot()['state'], 'no_data')
        self.assertEqual(os.read(second, 4096), expected)
        os.write(second, pvt())
        self.assertTrue(self.receive(serial)[0]['valid'])
        self.assertIsNone(serial.error)

    def test_passive_fixed_baud_detects_hangup_without_sending_bytes(self):
        master = self.attach()
        serial = self.serial(baud=38400, poll_ubx=False)
        self.assertEqual(serial.poll(), [])
        with self.assertRaises(BlockingIOError): os.read(master, 4096)
        self.unplug(master)
        self.assertEqual(serial.poll(), [])
        self.assertIsNone(serial.fd)
        self.assertIn('disconnected', serial.error)

    def test_restore_termios_failure_releases_exclusive_fd_and_lock_idempotently(self):
        self.attach()
        serial = self.serial(baud=38400)
        serial.open()
        fds = serial.fd, serial.lock_fd
        serial.error = 'primary USB read failure'
        with patch.object(driver.termios, 'tcsetattr', side_effect=termios.error(errno.EIO, 'unplugged')):
            serial.close()
        self.assert_closed(*fds)
        self.assertEqual(serial.error, 'primary USB read failure')
        self.assertIn('restore termios', serial.cleanup_errors[0])
        other = self.serial(baud=38400)
        other.open()  # Proves both TIOCEXCL and flock were released.
        other_fds = other.fd, other.lock_fd
        serial.close()  # Must not close descriptors recycled for the new owner.
        for fd in other_fds: os.fstat(fd)

    def test_unexpected_cleanup_error_propagates_after_descriptors_are_released(self):
        self.attach()
        serial = self.serial(baud=38400)
        serial.open()
        fds = serial.fd, serial.lock_fd
        with patch.object(driver.termios, 'tcsetattr', side_effect=ValueError('programming fault')):
            with self.assertRaisesRegex(ValueError, 'programming fault'): serial.close()
        self.assert_closed(*fds)
        self.serial(baud=38400).open()

    def test_termios_error_during_initial_setup_and_auto_baud_is_retryable(self):
        self.attach()
        serial = self.serial(poll_ubx=False)
        with patch.object(driver.termios, 'tcgetattr', side_effect=termios.error(errno.EIO, 'setup disconnected')):
            self.assertEqual(serial.poll(), [])
        self.assertIsNone(serial.fd)
        self.assertIsNone(serial.lock_fd)
        self.assertIn('setup disconnected', serial.error)
        serial.next_open = 0
        serial.poll()
        self.assertIsNotNone(serial.fd)
        serial.connected_at = time.monotonic() - 6
        with patch.object(driver.termios, 'tcgetattr', side_effect=termios.error(errno.EIO, 'baud disconnected')):
            self.assertEqual(serial.poll(), [])
        self.assertIsNone(serial.fd)
        self.assertIsNone(serial.lock_fd)
        self.assertIn('baud disconnected', serial.error)

    def test_missing_device_retries_every_five_seconds_and_reports_error(self):
        serial = self.serial()
        with patch.object(serial, 'open', wraps=serial.open) as opening:
            for now, expected_attempts in ((100, 1), (104.99, 1), (105, 2)):
                with patch.object(driver.time, 'monotonic', return_value=now):
                    self.assertEqual(serial.poll(), [])
                self.assertEqual(opening.call_count, expected_attempts)
                snapshot = serial.snapshot()
                self.assertFalse(snapshot['valid'])
                self.assertFalse(snapshot['driver']['serial_open'])
                self.assertTrue(snapshot['driver']['error'])
                self.assertIsNone(serial.lock_fd)

    def test_programming_error_in_read_is_not_swallowed_by_retry(self):
        self.attach()
        serial = self.serial(baud=38400)
        serial.open()
        with patch.object(serial, 'read', side_effect=ValueError('decode programming fault')):
            with self.assertRaisesRegex(ValueError, 'decode programming fault'): serial.poll()

    def test_external_ros_shutdown_uses_idempotent_shutdown(self):
        class ExternalShutdown(Exception): pass
        node = Mock()
        ros = SimpleNamespace(init=Mock(), spin=Mock(side_effect=ExternalShutdown),
                              try_shutdown=Mock(), shutdown=Mock(side_effect=AssertionError('already shut down')))
        modules = {
            'rclpy': ros, 'rclpy.executors': SimpleNamespace(ExternalShutdownException=ExternalShutdown),
            'rclpy.node': SimpleNamespace(Node=Mock(return_value=node)),
            'rclpy.qos': SimpleNamespace(qos_profile_sensor_data=object()),
            'sensor_msgs.msg': SimpleNamespace(NavSatFix=object),
            'std_msgs.msg': SimpleNamespace(String=object),
        }
        with patch.dict(sys.modules, modules), patch.object(sys, 'argv', ['gps_ros2_driver.py']):
            self.assertEqual(driver.main(), 0)
        ros.try_shutdown.assert_called_once_with()
        ros.shutdown.assert_not_called()
        node.destroy_node.assert_called_once_with()


if __name__ == '__main__': unittest.main()
