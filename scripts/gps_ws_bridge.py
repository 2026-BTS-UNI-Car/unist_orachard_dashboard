#!/usr/bin/env python3
"""ROS-only GPS display bridge. ACK backpressure, bounded latest sample."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from dashboard_server.gps import fresh_snapshot, GPS_SCHEMA, finite


class GpsBridgeState:
    def __init__(self):
        self.lock = threading.Lock()
        self.sample = {}
        self.received_at = None
        self.driver = {}
        self.driver_at = None

    def update(self, msg, status=False):
        try:
            payload = json.loads(msg.data)
            if not isinstance(payload, dict) or payload.get('schema') != GPS_SCHEMA: return
            json.dumps(payload, allow_nan=False)
            if not status and finite(payload.get('received_at')) is None: return
        except (ValueError, TypeError): return
        with self.lock:
            if status:
                self.driver = payload.get('driver') or {}
                self.driver_at = time.time()
            else:
                self.sample = payload
                # Preserve source receipt time so replayed/delayed DDS data is not fresh.
                source_at = payload.get('received_at')
                self.received_at = source_at

    def snapshot(self):
        with self.lock:
            driver = dict(self.driver)
            driver['topic_alive'] = self.driver_at is not None and time.time()-self.driver_at < 3
            return fresh_snapshot(self.sample, self.received_at, driver=driver)


async def stream(websocket, state):
    if urlparse(websocket.path).path != '/ws/gps':
        await websocket.close(code=1008, reason='Unknown stream'); return
    profile = parse_qs(urlparse(websocket.path).query).get('profile', ['slow'])[0]
    rate = {'tiny': 1, 'slow': 2, 'balanced': 5, 'detail': 5}.get(profile, 2)
    sequence = 0
    try:
        while True:
            start = time.monotonic()
            sequence += 1
            payload = state.snapshot()
            payload['stream_seq'] = sequence
            await asyncio.wait_for(websocket.send(json.dumps(payload, allow_nan=False, separators=(',', ':'))), 2)
            async def ack():
                while True:
                    message = json.loads(await websocket.recv())
                    if isinstance(message, dict) and message.get('ack') == sequence: return
            await asyncio.wait_for(ack(), 2)
            await asyncio.sleep(max(0, 1/rate-(time.monotonic()-start)))
    except Exception:
        await websocket.close()


def main():
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from std_msgs.msg import String
    import websockets
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8778)
    parser.add_argument('--status-file', default=os.environ.get('GPS_BRIDGE_STATUS_FILE', '/app/runtime/gps_bridge_status.json'))
    args = parser.parse_args()
    state = GpsBridgeState()
    rclpy.init(); node = Node('ubts_gps_websocket_bridge')
    node.create_subscription(String, '/gps/data', state.update, qos_profile_sensor_data)
    node.create_subscription(String, '/gps/driver_status', lambda msg: state.update(msg, True), 10)
    def write_status():
        path = Path(args.status_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(state.snapshot(), allow_nan=False)); temp.replace(path)
        except OSError: pass
    node.create_timer(1., write_status)
    thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True); thread.start()
    async def run():
        async with websockets.serve(lambda ws: stream(ws, state), args.host, args.port, max_size=2048, max_queue=1, compression=None, close_timeout=1):
            await asyncio.Future()
    try: asyncio.run(run())
    finally: rclpy.shutdown(); thread.join(timeout=2); node.destroy_node()


if __name__ == '__main__': main()
