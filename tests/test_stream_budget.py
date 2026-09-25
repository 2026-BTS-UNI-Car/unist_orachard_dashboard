import asyncio
import json
import threading
import time
import unittest
import cv2
import numpy as np
from scripts.stream_policy import StreamBudget, fit_size
from scripts.camera_ws_bridge import CameraState, network_preview, serve_websocket, client_settings
from scripts.lidar_ws_bridge import compact_scan_payload


class StreamBudgetTest(unittest.TestCase):
    def test_adaptation_starts_small_and_recovers_gradually(self):
        budget = StreamBudget()
        self.assertEqual(budget.values[:2], (160, 90))
        for _ in range(15): budget.acknowledge(.08)
        self.assertEqual(budget.values[:2], (320, 180))
        budget.acknowledge(.8)
        self.assertEqual(budget.values[:2], (160, 90))
        self.assertGreaterEqual(budget.delay(60000, 4), 2)

    def test_preserves_aspect_and_never_upscales(self):
        self.assertEqual(fit_size(640, 480, 320, 180), (240, 180))
        self.assertEqual(fit_size(100, 50, 320, 180), (100, 50))

    def test_compressed_preview_is_resized_without_changing_source(self):
        frame = np.random.default_rng(2).integers(0, 256, (720, 1280, 3), dtype=np.uint8)
        source = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
        snap = {"jpeg": source}
        jpeg, _, width, height, sw, sh = network_preview(snap, client_settings('/ws/camera?profile=slow'), StreamBudget('slow'), True)
        self.assertEqual((width, height, sw, sh), (320, 180, 1280, 720))
        self.assertEqual(cv2.imdecode(np.frombuffer(jpeg, np.uint8), 1).shape[:2], (180, 320))
        self.assertLess(len(jpeg), len(source) / 5)
        self.assertEqual(snap['jpeg'], source)

    def test_compact_lidar_preserves_polar_measurements(self):
        points = [{"angle_deg": 27.3, "distance_m": 2.15, "quality": 10, "x_m": .9, "y_m": 1.9}] * 180
        snap = dict(frame_id='laser', source='ros2', stamp=1, point_count=2000, analysis={})
        compact = compact_scan_payload(snap, points, 1)
        full = compact_scan_payload(snap, points, 1, compact=False)
        self.assertEqual(compact['points'][0], [27.3, 2.15, 10])
        self.assertLess(len(json.dumps(compact)), len(json.dumps(full)) / 2)


class CameraFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_second_frame_without_ack_then_latest_after_ack(self):
        import socket
        import websockets
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        state = CameraState()
        stop = threading.Event()
        frame = np.zeros((180, 320, 3), np.uint8)
        state.update_frame('camera', 1, 0, frame, 'bgr8')
        server = asyncio.create_task(serve_websocket(state, '127.0.0.1', port, '/ws/camera', stop))
        try:
            # Importing the server from a Windows-mounted test dependency can be slow.
            # Wait for the listener instead of assuming a fixed startup duration.
            for _ in range(100):
                if server.done(): await server
                try:
                    reader, writer = await asyncio.open_connection('127.0.0.1', port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(.05)
            state.update_frame('camera', 1, 0, frame, 'bgr8')
            async with websockets.connect(f'ws://127.0.0.1:{port}/ws/camera?transport=binary-v1&flow=decode-ack&profile=slow') as ws:
                async def receive_frame():
                    while True:
                        value = await ws.recv()
                        if isinstance(value, bytes):
                            length = int.from_bytes(value[5:9], 'big')
                            return json.loads(value[9:9+length])
                first = await asyncio.wait_for(receive_frame(), 1)
                for seq in range(2, 10): state.update_frame('camera', seq, 0, frame, 'bgr8')
                with self.assertRaises(asyncio.TimeoutError): await asyncio.wait_for(receive_frame(), .35)
                await ws.send(json.dumps({'type':'frame_ack','frame_count':first['frame_count']}))
                latest = await asyncio.wait_for(receive_frame(), 1)
                self.assertEqual(latest['frame_count'], 10)
                self.assertEqual((latest['width'], latest['height']), (320, 180))
                # Outstanding frame times out and closes; it is never replaced by a queue of old frames.
                with self.assertRaises(websockets.ConnectionClosed): await asyncio.wait_for(receive_frame(), 3)
        finally:
            stop.set()
            await asyncio.wait_for(server, 3)
