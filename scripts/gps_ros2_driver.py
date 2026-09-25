#!/usr/bin/env python3
"""One UART owner; NMEA/UBX input to NavSatFix and a lossless quality payload.

Only read-only UBX polls are sent. No CFG, reset, rate, flash or firmware writes.
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import json
import math
import os
from pathlib import Path
import stat
import sys
import termios
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from dashboard_server.gps import GnssDecoder, fresh_snapshot, ubx_packet, valid_position


def serial_owners(port):
    target = os.stat(port).st_rdev
    owners = set()
    for fdpath in glob.glob('/proc/[0-9]*/fd/*'):
        pid = int(fdpath.split('/')[2])
        if pid == os.getpid(): continue
        try:
            item = os.stat(fdpath)
            if stat.S_ISCHR(item.st_mode) and item.st_rdev == target: owners.add(pid)
        except OSError: pass
    return sorted(owners)


class GpsSerial:
    BAUDS = (38400, 9600, 115200, 57600, 19200, 230400, 460800)

    def __init__(self, port, baud=0, lock_path='/run/ubts-gps/serial.lock', poll_ubx=True):
        self.port, self.fixed_baud, self.poll_ubx = port, baud, poll_ubx
        self.lock_path = Path(lock_path)
        self.fd = self.lock_fd = None
        self.original = None
        self.baud_index = 0
        self.baud = baud or self.BAUDS[0]
        self.decoder = GnssDecoder()
        self.error = None
        self.bytes_received = 0
        self.connected_at = 0
        self.next_poll = 0
        self.last_good_packet = None

    def open(self):
        if 'console='+Path(self.port).name in Path('/proc/cmdline').read_text():
            raise OSError('GPS port is the system console')
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            owners = serial_owners(self.port)
            if owners: raise OSError('GPS UART already owned by PID(s): '+','.join(map(str, owners)))
            self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            self.original = termios.tcgetattr(self.fd)
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            self.set_baud(self.baud)
        except Exception:
            self.close(); raise

    def set_baud(self, baud):
        cfg = termios.tcgetattr(self.fd)
        cfg[0] = cfg[1] = cfg[3] = 0
        cfg[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        cfg[4] = cfg[5] = getattr(termios, 'B'+str(baud))
        cfg[6][termios.VMIN] = cfg[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, cfg)
        termios.tcflush(self.fd, termios.TCIFLUSH)
        self.baud = baud
        self.connected_at = time.monotonic()
        self.decoder.buffer.clear()
        self.last_good_packet = None
        self.next_poll = 0

    def read(self):
        frames = []
        before = self.decoder.packets
        for _ in range(8):
            try: data = os.read(self.fd, 8192)
            except BlockingIOError: break
            if not data: break
            self.bytes_received += len(data)
            frames.extend(self.decoder.feed(data))
        now = time.monotonic()
        if self.decoder.packets > before: self.last_good_packet = now
        if not self.fixed_baud and now - (self.last_good_packet or self.connected_at) > 5:
            self.baud_index = (self.baud_index + 1) % len(self.BAUDS)
            self.set_baud(self.BAUDS[self.baud_index])
        if self.poll_ubx and now >= self.next_poll:
            # Query data only, including receivers configured without periodic output.
            polls = ubx_packet(1, 7) + ubx_packet(1, 20)
            if not self.decoder.identity: polls = ubx_packet(10, 4) + polls
            try: os.write(self.fd, polls)
            except BlockingIOError: pass
            self.next_poll = now + 1.0
        self.error = None
        return frames

    def snapshot(self):
        driver = dict(serial_port=self.port, serial_open=self.fd is not None, baud=self.baud,
                      auto_baud=not self.fixed_baud, bytes_received=self.bytes_received,
                      valid_packets=self.decoder.packets, rejected_packets=self.decoder.rejected,
                      receiver=self.decoder.identity, error=self.error, configuration_writes=False)
        return fresh_snapshot(self.decoder.sample, self.decoder.last_sample_at, driver=driver)

    def close(self):
        if self.fd is not None:
            try:
                if self.original is not None: termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
                fcntl.ioctl(self.fd, termios.TIOCNXCL)
            except OSError: pass
            os.close(self.fd); self.fd = None
        if self.lock_fd is not None:
            os.close(self.lock_fd); self.lock_fd = None


def make_navsat(sample, message_class):
    msg = message_class()
    stamp = sample.get('stamp') or {}
    msg.header.stamp.sec, msg.header.stamp.nanosec = int(stamp.get('sec', 0)), int(stamp.get('nanosec', 0))
    msg.header.frame_id = 'gps_link'
    ok = valid_position(sample)
    msg.status.status = (2 if sample.get('quality') in (4, 5) else 0) if ok else -1
    # PVT/GGA do not identify which constellations actually supplied the fix.
    msg.status.service = 0
    msg.latitude = float(sample['latitude']) if ok else math.nan
    msg.longitude = float(sample['longitude']) if ok else math.nan
    msg.altitude = float(sample['altitude_ellipsoid_m']) if ok and sample.get('altitude_ellipsoid_m') is not None else math.nan
    h, v = sample.get('h_acc_m'), sample.get('v_acc_m')
    if ok and h is not None and v is not None:
        msg.position_covariance = [h*h, 0., 0., 0., h*h, 0., 0., 0., v*v]
        msg.position_covariance_type = 1  # Approximated, not a surveyed covariance.
    return msg


def main():
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import NavSatFix
    from std_msgs.msg import String
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', default=os.environ.get('GPS_SERIAL_PORT', '/dev/ubts-gps'))
    parser.add_argument('--baud', type=int, default=int(os.environ.get('GPS_BAUD', '0')))
    parser.add_argument('--status-file', default=os.environ.get('GPS_DRIVER_STATUS_FILE', '/app/runtime/gps_driver_status.json'))
    parser.add_argument('--poll-ubx', action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if args.baud and args.baud not in GpsSerial.BAUDS: parser.error('Unsupported baud')
    rclpy.init()
    node = Node('ubts_gps_driver')
    serial = GpsSerial(args.port, args.baud, poll_ubx=args.poll_ubx)
    fix_pub = node.create_publisher(NavSatFix, '/gps/fix', qos_profile_sensor_data)
    data_pub = node.create_publisher(String, '/gps/data', qos_profile_sensor_data)
    status_pub = node.create_publisher(String, '/gps/driver_status', 10)
    next_open = 0

    def poll():
        nonlocal next_open
        try:
            if serial.fd is None:
                if time.monotonic() < next_open: return
                serial.open()
            for frame in serial.read():
                stamp = node.get_clock().now().to_msg()
                frame['stamp'] = dict(sec=stamp.sec, nanosec=stamp.nanosec)
                serial.decoder.sample['stamp'] = frame['stamp']
                fix_pub.publish(make_navsat(frame, NavSatFix))
                data_pub.publish(String(data=json.dumps(frame, allow_nan=False, separators=(',', ':'))))
        except (OSError, ValueError) as error:
            serial.error = str(error)
            serial.close()
            next_open = time.monotonic() + 5

    def status():
        payload = serial.snapshot()
        encoded = json.dumps(payload, allow_nan=False, separators=(',', ':'))
        status_pub.publish(String(data=encoded))
        try:
            path = Path(args.status_file); path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix('.tmp'); temp.write_text(encoded); temp.replace(path)
        except OSError as error: node.get_logger().warning(str(error))

    node.create_timer(.02, poll)
    node.create_timer(1., status)
    try: rclpy.spin(node)
    finally:
        serial.close(); node.destroy_node(); rclpy.shutdown()
    return 0


if __name__ == '__main__': raise SystemExit(main())
