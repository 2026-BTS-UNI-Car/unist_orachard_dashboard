"""Measured local stream cadence and delayed-ACK backpressure, without ROS."""
import asyncio
from contextlib import suppress
import json
import os
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from scripts.camera_ws_bridge import CameraState, CameraPreviewCache, camera_stream_budget, client_settings, serve_websocket
from scripts.imu_ws_bridge import ImuBridgeState, serve as serve_imu
from scripts.gps_ws_bridge import stream as gps_stream
from scripts.lidar_ws_bridge import ScanState, serve_websocket as serve_lidar
from scripts.stream_policy import StreamBudget, wait_for_ack, pace_stream


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
    raise AssertionError('test listener did not start')


class StreamLatencyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    async def test_multiple_viewers_share_encoding_and_waiters_pick_latest(self):
        state = CameraState()
        state.update_frame('camera', 1, 0, np.zeros((360, 640, 3), np.uint8), 'bgr8')
        cache = CameraPreviewCache()
        settings, budget = client_settings('/ws/camera?profile=detail'), StreamBudget('detail')
        results = await asyncio.gather(*(cache.latest(state, settings, budget, False) for _ in range(8)))
        self.assertEqual(cache.encodes, 1)
        self.assertTrue(all(item[0]['frame_count'] == 1 for item in results))
        await cache.lock.acquire()
        waiting = asyncio.create_task(cache.latest(state, settings, budget, False))
        for count in range(2, 30):
            state.update_frame('camera', count, 0, np.zeros((360, 640, 3), np.uint8), 'bgr8')
        cache.lock.release()
        newest, _ = await waiting
        self.assertEqual(newest['frame_count'], 29)
        self.assertEqual(cache.encodes, 2)

    async def test_local_30fps_policy_cannot_change_real_board_limits(self):
        with patch.dict(os.environ, {'DASHBOARD_MODE': 'live', 'CAMERA_PREVIEW_LOCAL_FPS': '30'}):
            self.assertEqual(camera_stream_budget('detail').values[2], 15)
        with patch.dict(os.environ, {'DASHBOARD_MODE': 'simulation', 'CAMERA_PREVIEW_LOCAL_FPS': '30'}):
            self.assertEqual(camera_stream_budget('detail').values[2], 30)
            self.assertEqual(camera_stream_budget('slow').values[2], 4)

    async def test_equivalent_viewports_share_jpeg_but_actual_size_and_quality_do_not(self):
        state = CameraState()
        state.update_frame('camera', 1, 0, np.zeros((360, 640, 3), np.uint8), 'bgr8')
        cache, budget = CameraPreviewCache(), StreamBudget('detail')
        large = client_settings('/ws/camera?profile=detail&resolution=1280x720&quality=90')
        native = client_settings('/ws/camera?profile=detail&resolution=640x360&quality=70')
        small = client_settings('/ws/camera?profile=detail&resolution=320x180&quality=70')
        low_quality = client_settings('/ws/camera?profile=detail&resolution=640x360&quality=40')
        results = await asyncio.gather(cache.latest(state, large, budget, False),
                                       cache.latest(state, native, budget, False))
        self.assertEqual(cache.encodes, 1)
        self.assertIs(results[0][1], results[1][1])
        self.assertEqual(results[0][1][2:4], (640, 360))
        _, smaller = await cache.latest(state, small, budget, False)
        _, lower_quality = await cache.latest(state, low_quality, budget, False)
        self.assertEqual(cache.encodes, 3)
        self.assertEqual(smaller[2:4], (320, 180))
        self.assertNotEqual(lower_quality[0], results[0][1][0])

    async def test_imu_waits_for_ack_then_sends_latest_without_extra_interval(self):
        import websockets
        asyncio.get_running_loop().set_debug(False)
        state, stop, port = ImuBridgeState(), threading.Event(), free_port()
        state.raw_count = 1
        args = SimpleNamespace(host='127.0.0.1', port=port, path='/ws/imu', max_hz=20)
        requested_delays = []
        async def capture_pacing(interval, started):
            requested_delays.append(await pace_stream(interval, started))
        pacing = patch('scripts.stream_policy.pace_stream', capture_pacing)
        pacing.start()
        task = asyncio.create_task(serve_imu(state, args, stop))
        await listen(task, port)
        try:
            async with websockets.connect(f'ws://127.0.0.1:{port}/ws/imu?profile=detail&flow=ack') as ws:
                async def imu():
                    while True:
                        row = json.loads(await ws.recv())
                        if row.get('type') == 'imu':
                            return row
                first = await imu()
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(imu(), .20)
                state.raw_count = 50
                released = time.monotonic()
                await ws.send(json.dumps({'type': 'imu_ack', 'sequence': first['sequence']}))
                newest = await asyncio.wait_for(imu(), .5)
                delay = time.monotonic() - released
                self.assertEqual(newest['raw_count'], 50)
                self.assertTrue(requested_delays)
                # A 200 ms ACK already exceeded the 100 ms detail period. This
                # checks the actual handler's requested delay, not host timing.
                self.assertEqual(requested_delays[0], 0)
                print('STREAM_IMU_ACK_RELEASE_MS', round(delay * 1000, 2), flush=True)
                await ws.send(json.dumps({'type': 'imu_ack', 'sequence': newest['sequence']}))
        finally:
            stop.set()
            await asyncio.wait_for(task, 3)
            pacing.stop()

    async def test_gps_slow_reader_gets_latest_without_backlog(self):
        class Fake:
            path = '/ws/gps?profile=detail'
            def __init__(self):
                self.sent, self.incoming = asyncio.Queue(), asyncio.Queue()
            async def send(self, value): await self.sent.put(json.loads(value))
            async def recv(self): return await self.incoming.get()
            async def close(self, **_): pass
        ws = Fake()
        current = {'sample': 1}
        state = SimpleNamespace(snapshot=lambda: dict(current))
        task = asyncio.create_task(gps_stream(ws, state))
        try:
            first = await asyncio.wait_for(ws.sent.get(), .2)
            for sample in range(2, 100): current['sample'] = sample
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.sent.get(), .25)
            await ws.incoming.put(json.dumps({'ack': first['stream_seq']}))
            newest = await asyncio.wait_for(ws.sent.get(), .1)
            self.assertEqual(newest['sample'], 99)
            self.assertTrue(ws.sent.empty())
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError): await task

    async def test_lidar_slow_reader_gets_latest_scan(self):
        import websockets
        state, port = ScanState(), free_port()
        points = [{'angle_deg': i, 'distance_m': 2., 'quality': 1} for i in range(360)]
        state.update('laser', 1., points, 'fixture')
        task = asyncio.create_task(serve_lidar(state, '127.0.0.1', port, '/ws/lidar', 10, 3200))
        await listen(task, port)
        try:
            async with websockets.connect(f'ws://127.0.0.1:{port}/ws/lidar?profile=detail&flow=ack&format=polar-v1') as ws:
                async def scan():
                    while True:
                        row = json.loads(await ws.recv())
                        if row.get('type') == 'scan': return row
                first = await scan()
                for i in range(2, 40): state.update('laser', float(i), points, 'fixture')
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(scan(), .25)
                await ws.send(json.dumps({'type': 'scan_ack', 'sequence': first['sequence']}))
                newest = await asyncio.wait_for(scan(), .2)
                self.assertEqual(newest['stamp'], 39)
                await ws.send(json.dumps({'type': 'scan_ack', 'sequence': newest['sequence']}))
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError): await task

    async def test_invalid_ack_flood_does_not_extend_deadline(self):
        class Noise:
            async def recv(self):
                await asyncio.sleep(.005)
                return 'not-json'
        started = time.monotonic()
        with self.assertRaises(asyncio.TimeoutError):
            await wait_for_ack(Noise(), lambda reply: False, timeout=.05)
        self.assertLess(time.monotonic() - started, .12)

    async def test_local_camera_throughput_with_real_binary_frames(self):
        import websockets
        # IsolatedAsyncioTestCase enables costly task-origin tracing by default;
        # production asyncio.run does not. Measure the production scheduler.
        asyncio.get_running_loop().set_debug(False)
        state, stop, port = CameraState(), threading.Event(), free_port()
        frame = np.random.default_rng(1).integers(0, 256, (360, 640, 3), dtype=np.uint8)
        state.update_frame('camera', 1, 0, frame, 'bgr8')
        with patch.dict(os.environ, {'DASHBOARD_MODE': 'simulation', 'CAMERA_PREVIEW_LOCAL_FPS': '30'}):
            task = asyncio.create_task(serve_websocket(state, '127.0.0.1', port, '/ws/camera', stop))
            await listen(task, port)
            async def produce():
                for count in range(2, 62):
                    state.update_frame('camera', count, 0, frame, 'bgr8')
                    await asyncio.sleep(1/30)
            producer = asyncio.create_task(produce())
            try:
                async with websockets.connect(f'ws://127.0.0.1:{port}/ws/camera?profile=detail&resolution=640x360&fps=30&transport=binary-v1&flow=decode-ack') as ws:
                    received, byte_count, first_at, last_at = 0, 0, None, None
                    source_first = None
                    deadline = time.monotonic() + 1.5
                    while time.monotonic() < deadline:
                        value = await asyncio.wait_for(ws.recv(), .8)
                        if not isinstance(value, bytes): continue
                        length = int.from_bytes(value[5:9], 'big')
                        metadata = json.loads(value[9:9+length])
                        now = time.monotonic()
                        first_at = first_at or now
                        source_first = source_first or metadata['frame_count']
                        last_at = now
                        received += 1
                        byte_count += len(value)
                        await ws.send(json.dumps({'type': 'frame_ack', 'frame_count': metadata['frame_count']}))
                    fps = (received - 1) / (last_at - first_at)
                    source_fps = (metadata['frame_count'] - source_first) / (last_at - first_at)
                    print('STREAM_CAMERA_LOOPBACK', json.dumps({'fps': round(fps, 2), 'frames': received,
                          'source_fps': round(source_fps, 2), 'bytes_s': round(byte_count / (last_at-first_at)),
                          'source': 'synthetic_640x360_noise_not_Gazebo'}), flush=True)
                    # Deliberately noisy images are byte-budget limited. This
                    # verifies high-rate transport, not Gazebo's rendering FPS.
                    self.assertGreater(fps, 20)
            finally:
                producer.cancel()
                with suppress(asyncio.CancelledError): await producer
                stop.set()
                await asyncio.wait_for(task, 3)


if __name__ == '__main__': unittest.main()
