"""LiDAR preview polar-v2 and the IMU display-ack flow (NET-07).

- polar-v2 is the binary form of polar-v1: the same points of the same scan,
  about a third of the bytes.
- Two scans may be on the way at a time; the third waits for an answer.
- Old clients (polar-v1, JSON) and the mapping profile are unchanged.
- The IMU "display-ack" flow sends the compact display message with one answer
  at a time.
"""
import asyncio
import json
import math
import socket
import threading
import unittest
from contextlib import suppress
from types import SimpleNamespace as NS

from scripts.imu_ws_bridge import ImuBridgeState, serve as serve_imu
from scripts.lidar_ws_bridge import (ScanState, compact_scan_payload, decimate_points, decode_scan_v2, encode_scan_v2,
                                     scan_to_points, serve_websocket as serve_lidar)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def listen(task, port):
    for _ in range(100):
        if task.done():
            await task
        try:
            _, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(.02)
    raise TimeoutError('server did not listen')


def field_scan(beams=3240, seed=3):
    """A scan shaped like the S2: 3240 beams, some without a return."""
    ranges, intensities = [], []
    for k in range(beams):
        value = 1.0 + 25.0 * (0.5 + 0.5 * math.sin(k * 0.013 + seed)) + (k % 7) * 0.0017
        if k % 17 == 0:
            value = math.inf
        ranges.append(value)
        intensities.append(k % 48)
    return NS(ranges=ranges, intensities=intensities, angle_min=-math.pi, angle_increment=2 * math.pi / beams,
              range_min=0.05, range_max=30.0)


def snapshot_of(msg, stamp=1.0):
    points = scan_to_points(msg, 3200)
    return dict(frame_id='laser', source='ros2', stamp=stamp, point_count=len(points), points=points,
                raw_scan=dict(angle_min=msg.angle_min, angle_increment=msg.angle_increment))


class PolarV2FormatTest(unittest.TestCase):
    def test_same_points_as_polar_v1(self):
        snap = snapshot_of(field_scan())
        for limit in (90, 180, 300, 720):
            points = decimate_points(snap['points'], limit)
            v1 = compact_scan_payload(snap, points, 5)
            v2 = decode_scan_v2(encode_scan_v2(snap, points, 5))
            self.assertEqual(len(v2['points']), len(v1['points']))
            for (a1, d1, q1), (a2, d2, q2) in zip(v1['points'], v2['points']):
                self.assertLessEqual(min(abs(a1 - a2), 360 - abs(a1 - a2)), 0.0011, 'same angle (polar-v1 rounds to 0.001 deg)')
                self.assertAlmostEqual(d1, d2, places=9)
                self.assertEqual(q1, q2)
            for key in ('type', 'frame_id', 'stamp', 'sequence', 'point_count', 'source_point_count', 'requested_max_points', 'coverage_deg'):
                self.assertEqual(v2[key], v1[key], key)

    def test_a_third_of_the_bytes(self):
        snap = snapshot_of(field_scan())
        sizes = {}
        for limit in (180, 300):
            points = decimate_points(snap['points'], limit)
            v1 = len(json.dumps(compact_scan_payload(snap, points, 5), separators=(',', ':')))
            v2 = len(encode_scan_v2(snap, points, 5))
            sizes[limit] = (v1, v2)
            self.assertLess(v2, 0.45 * v1, f'{limit} points: {v2} B against {v1} B')
        self.assertLess(sizes[300][1], 2000, 'a 300-point scan stays under 2 kB (10 Hz under 20 kB/s)')
        print('POLAR_V2_BYTES', sizes, flush=True)

    def test_without_beam_geometry_the_angles_are_centi_degrees(self):
        points = [{'angle_deg': 359.99, 'distance_m': 2.0, 'quality': 1}, {'angle_deg': 10.123, 'distance_m': 3.5, 'quality': 0}]
        snap = dict(frame_id='laser', source='fixture', stamp=2.0, point_count=2, points=points)
        decoded = decode_scan_v2(encode_scan_v2(snap, points, 1))
        self.assertEqual(decoded['angle_encoding'], 'centi_degree')
        self.assertEqual([p[0] for p in decoded['points']], [359.99, 10.12])

    def test_scan_points_name_their_beam(self):
        msg = field_scan(beams=40)
        points = scan_to_points(msg, 900)
        self.assertTrue(all(isinstance(p['i'], int) for p in points))
        self.assertNotIn(0, [p['i'] for p in points], 'beam 0 had no return')


class PolarV2StreamTest(unittest.IsolatedAsyncioTestCase):
    async def start(self, state):
        port = free_port()
        task = asyncio.create_task(serve_lidar(state, '127.0.0.1', port, '/ws/lidar', 10, 3200))
        await listen(task, port)
        self.addAsyncCleanup(self.stop, task)
        return port

    async def stop(self, task):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def test_two_scans_on_the_way_then_wait_for_an_answer(self):
        import websockets
        asyncio.get_running_loop().set_debug(False)
        state = ScanState()
        msg = field_scan()
        points = scan_to_points(msg, 3200)
        raw = dict(angle_min=msg.angle_min, angle_increment=msg.angle_increment)
        state.update('laser', 1.0, points, 'fixture', raw)
        port = await self.start(state)
        url = f'ws://127.0.0.1:{port}/ws/lidar?profile=detail&flow=ack&format=polar-v2&ack_window=2&max_points=300'
        async with websockets.connect(url) as ws:
            async def scan():
                while True:
                    value = await ws.recv()
                    if isinstance(value, bytes):
                        return decode_scan_v2(value)
            first = await asyncio.wait_for(scan(), 1)
            self.assertEqual(first['point_count'], 300)
            state.update('laser', 2.0, points, 'fixture', raw)
            second = await asyncio.wait_for(scan(), .5)
            self.assertEqual(second['stamp'], 2.0, 'the second scan leaves before the first answer')
            for stamp in range(3, 20):
                state.update('laser', float(stamp), points, 'fixture', raw)
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(scan(), .3)
            await ws.send(json.dumps({'type': 'scan_ack', 'sequence': first['sequence']}))
            third = await asyncio.wait_for(scan(), .5)
            self.assertEqual(third['stamp'], 19.0, 'then the newest scan, not the ones in between')

    async def test_a_new_scan_leaves_without_polling_delay(self):
        import time
        import websockets
        asyncio.get_running_loop().set_debug(False)
        state = ScanState()
        msg = field_scan(beams=360)
        points = scan_to_points(msg, 3200)
        raw = dict(angle_min=msg.angle_min, angle_increment=msg.angle_increment)
        state.update('laser', 1.0, points, 'fixture', raw)
        port = await self.start(state)
        url = f'ws://127.0.0.1:{port}/ws/lidar?profile=detail&flow=ack&format=polar-v2&ack_window=2&max_points=300'
        async with websockets.connect(url) as ws:
            async def scan():
                while True:
                    value = await ws.recv()
                    if isinstance(value, bytes):
                        return decode_scan_v2(value)
            first = await asyncio.wait_for(scan(), 1)
            await ws.send(json.dumps({'type': 'scan_ack', 'sequence': first['sequence']}))
            await asyncio.sleep(.15)  # past the 10 Hz period: the bridge now waits for a new scan
            published = time.monotonic()
            state.update('laser', 2.0, points, 'fixture', raw)
            await asyncio.wait_for(scan(), .5)
            self.assertLess(time.monotonic() - published, .03, 'woken by the new scan, not a 40 ms poll')

    async def test_old_clients_still_get_json(self):
        import websockets
        state = ScanState()
        msg = field_scan(beams=360)
        state.update('laser', 1.0, scan_to_points(msg, 3200), 'fixture', dict(angle_min=msg.angle_min, angle_increment=msg.angle_increment))
        port = await self.start(state)
        async with websockets.connect(f'ws://127.0.0.1:{port}/ws/lidar?profile=detail&flow=ack&format=polar-v1') as ws:
            while True:
                value = await asyncio.wait_for(ws.recv(), 1)
                self.assertIsInstance(value, str)
                row = json.loads(value)
                if row.get('type') == 'scan':
                    break
            self.assertEqual(row['point_encoding'], 'polar-v1')
            self.assertEqual(len(row['points'][0]), 3)
        async with websockets.connect(f'ws://127.0.0.1:{port}/ws/lidar?profile=mapping&format=polar-v2') as ws:
            while True:
                row = json.loads(await asyncio.wait_for(ws.recv(), 1))
                if row.get('type') == 'scan':
                    break
            self.assertEqual(row['source'], 'source_native_LaserScan', 'the mapping profile keeps the raw scan')
            self.assertIn('raw_scan', row)


class ImuDisplayAckTest(unittest.IsolatedAsyncioTestCase):
    async def test_display_ack_sends_the_display_message_one_at_a_time(self):
        import websockets
        asyncio.get_running_loop().set_debug(False)
        state, stop, port = ImuBridgeState(), threading.Event(), free_port()
        state.raw_count = 1
        args = NS(host='127.0.0.1', port=port, path='/ws/imu', max_hz=20)
        task = asyncio.create_task(serve_imu(state, args, stop))
        await listen(task, port)
        try:
            async with websockets.connect(f'ws://127.0.0.1:{port}/ws/imu?profile=auto&flow=display-ack') as ws:
                async def imu():
                    while True:
                        row = json.loads(await ws.recv())
                        if row.get('type') == 'imu':
                            return row
                first = await asyncio.wait_for(imu(), 1)
                self.assertTrue(first['ack_required'])
                self.assertNotIn('corrected', first, 'the compact display message')
                state.raw_count = 9
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(imu(), .4)
                await ws.send(json.dumps({'type': 'imu_ack', 'sequence': first['sequence']}))
                newest = await asyncio.wait_for(imu(), .5)
                self.assertEqual(newest['raw_count'], 9)
                await ws.send(json.dumps({'type': 'imu_ack', 'sequence': newest['sequence']}))
        finally:
            stop.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


if __name__ == '__main__':
    unittest.main()
