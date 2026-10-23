"""Autonomy resource priority (S06): state machine, preview caps, server wiring.

No Docker, rover or sensor access: the Docker query is injected or served by a
local UNIX socket that never answers.
"""
import http.server
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import types
import unittest

from dashboard_server.autonomy_priority import (ACTIVE, IDLE, UNKNOWN, AutonomyPriority, DisabledPriority,
                                                 capped_stream_query, docker_running_containers)

LINUX = importlib.util.find_spec('fcntl') is not None


class Docker:
    def __init__(self):
        self.rows = []
        self.fail = False
        self.calls = 0

    def __call__(self, path, timeout):
        self.calls += 1
        if self.fail:
            raise OSError('docker down')
        return self.rows


def monitor(docker, clock, **kw):
    return AutonomyPriority(docker_socket='/fake.sock', docker_query=docker, clock=clock, start=False, **kw)


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.clock = lambda: self.now[0]
        self.docker = Docker()

    def step(self, m, dt=2.0):
        self.now[0] += dt
        return m.evaluate()['state']

    def test_active_immediately_and_idle_after_15s_of_negative_evidence(self):
        m = monitor(self.docker, self.clock)
        self.assertEqual(m.state, IDLE)
        self.docker.rows = [{'Names': ['/row-executor'], 'Labels': {}}]
        self.assertEqual(self.step(m), ACTIVE)
        self.assertTrue(m.background_yield())
        self.docker.rows = []
        states = [self.step(m) for _ in range(7)]  # 14 s after the last positive
        self.assertEqual(set(states), {ACTIVE})
        self.assertEqual(self.step(m), IDLE)  # 16 s
        self.assertFalse(m.background_yield())

    def test_flicker_between_runs_stays_active(self):
        m = monitor(self.docker, self.clock)
        for i in range(20):
            self.docker.rows = [{'Names': ['/row-executor']}] if i % 3 == 0 else []
            self.assertEqual(self.step(m), ACTIVE if i else ACTIVE)

    def test_two_docker_failures_are_unknown(self):
        m = monitor(self.docker, self.clock)
        self.docker.fail = True
        self.assertEqual(self.step(m), IDLE)
        self.assertEqual(self.step(m), UNKNOWN)
        self.assertTrue(m.background_yield())
        self.docker.fail = False
        self.assertEqual(self.step(m), IDLE)

    def test_active_without_negative_evidence_becomes_unknown_not_idle(self):
        m = monitor(self.docker, self.clock)
        self.docker.rows = [{'Names': ['/row-executor']}]
        self.step(m)
        self.docker.fail = True
        for _ in range(7):
            self.assertEqual(self.step(m), ACTIVE)
        self.assertEqual(self.step(m), UNKNOWN)

    def test_supervisor_label_and_exact_names(self):
        supervisor = {'active': False}
        m = monitor(self.docker, self.clock, supervisor_status=lambda: supervisor)
        self.docker.rows = [{'Names': ['/row-executor-old']}, {'Names': ['/other'], 'Labels': {'ubts.autonomy': '0'}}]
        self.assertEqual(self.step(m), IDLE)
        self.docker.rows = [{'Names': ['/x'], 'Labels': {'ubts.autonomy': '1'}}]
        self.assertEqual(self.step(m), ACTIVE)
        self.docker.rows = []
        self.now[0] += 20
        self.assertEqual(self.step(m), IDLE)
        supervisor['loop_cleanup_pending'] = True
        self.assertEqual(self.step(m), ACTIVE)
        self.assertTrue(m.snapshot()['sources']['supervisor'])

    def test_override_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'override'
            m = monitor(self.docker, self.clock, override_file=str(path))
            for value in ('active', 'unknown', 'idle'):
                path.write_text(value)
                self.assertEqual(self.step(m), value)
            calls = self.docker.calls
            path.write_text('active'); self.step(m)
            self.assertEqual(self.docker.calls, calls, 'override does not query docker')

    def test_listener_and_snapshot_shape(self):
        m = monitor(self.docker, self.clock)
        changes = []
        m.add_listener(lambda a, b: changes.append((a, b)))
        self.docker.rows = [{'Names': ['/row-executor']}]
        self.step(m)
        self.assertEqual(changes, [(IDLE, ACTIVE)])
        snap = m.snapshot()
        for key in ('state', 'background_yield', 'preview_cap', 'since_s', 'age_s', 'sources'):
            self.assertIn(key, snap)
        self.assertFalse(snap['control_path_changed'])
        self.assertNotIn('token', json.dumps(snap).lower())

    def test_from_env_disabled_without_socket_or_override(self):
        old = {k: os.environ.get(k) for k in ('DOCKER_SOCKET', 'UBTS_AUTONOMY_PRIORITY_OVERRIDE_FILE', 'UBTS_AUTONOMY_PRIORITY')}
        try:
            os.environ['DOCKER_SOCKET'] = '/nonexistent/docker.sock'
            os.environ.pop('UBTS_AUTONOMY_PRIORITY_OVERRIDE_FILE', None)
            os.environ.pop('UBTS_AUTONOMY_PRIORITY', None)
            p = AutonomyPriority.from_env(start=False)
            self.assertIsInstance(p, DisabledPriority)
            self.assertFalse(p.background_yield())
            os.environ['UBTS_AUTONOMY_PRIORITY'] = 'off'
            self.assertIsInstance(AutonomyPriority.from_env(start=False), DisabledPriority)
        finally:
            for k, v in old.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v

    @unittest.skipUnless(hasattr(socket, 'AF_UNIX') and os.name != 'nt', 'UNIX socket timeout check')
    def test_docker_query_times_out_in_half_a_second(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'docker.sock')
            server = socket.socket(socket.AF_UNIX); server.bind(path); server.listen(1)
            try:
                started = time.monotonic()
                with self.assertRaises(OSError):
                    docker_running_containers(path, 0.5)
                self.assertLess(time.monotonic() - started, 1.5)
            finally:
                server.close()


class PreviewCapTests(unittest.TestCase):
    def test_table(self):
        cam = {'resolution': '1920x1080', 'fps': '30', 'quality': '70', 'profile': 'detail', 'transport': 'binary-v1'}
        a = capped_stream_query('camera', ACTIVE, cam)
        self.assertEqual((a['profile'], a['fps'], a['resolution'], a['quality'], a['transport']),
                         ('slow', '10', '640x360', '70', 'binary-v1'))
        self.assertEqual(capped_stream_query('camera', ACTIVE, {**cam, 'resolution': '640x480'})['resolution'], '320x240')
        self.assertEqual(capped_stream_query('camera', ACTIVE, {**cam, 'resolution': '320x180', 'fps': '5', 'profile': 'tiny'}),
                         {**cam, 'resolution': '320x180', 'fps': '5', 'profile': 'tiny'})
        self.assertEqual(capped_stream_query('camera', ACTIVE, {**cam, 'profile': 'auto'})['profile'], 'slow')
        u = capped_stream_query('camera', UNKNOWN, cam)
        self.assertEqual((u['profile'], u['fps'], u['resolution']), ('balanced', '15', '1920x1080'))
        self.assertEqual(capped_stream_query('camera', UNKNOWN, {**cam, 'profile': 'auto'})['profile'], 'auto')
        lidar = {'max_points': '900', 'format': 'polar-v1', 'flow': 'ack', 'profile': 'detail'}
        self.assertEqual(capped_stream_query('lidar', ACTIVE, lidar), {**lidar, 'max_points': '300', 'profile': 'slow'})
        self.assertEqual(capped_stream_query('lidar', UNKNOWN, lidar), {**lidar, 'profile': 'balanced'})
        for state in (IDLE, 'disabled'):
            self.assertEqual(capped_stream_query('camera', state, cam), cam)
        for stream in ('imu', 'gps'):
            self.assertEqual(capped_stream_query(stream, ACTIVE, {'profile': 'detail'}), {'profile': 'detail'})


@unittest.skipUnless(LINUX, 'dashboard app imports fcntl (Linux)')
class ServerWiringTests(unittest.TestCase):
    def test_proxy_query_cap_lease_exemption_and_4001_close(self):
        from dashboard_server import app
        handler = object.__new__(app.DashboardHandler)
        handler.path = '/ws/camera?resolution=1920x1080&fps=30&profile=detail&quality=70'
        url = handler._upstream_with_client_query('ws://127.0.0.1:8770/ws/camera', 'camera', 'active')
        self.assertIn('resolution=640x360', url); self.assertIn('fps=10', url); self.assertIn('profile=slow', url)
        self.assertEqual(handler._upstream_with_client_query('ws://h/ws/camera'), 'ws://h/ws/camera?' + handler.path.split('?')[1])
        handler.autonomy_priority = types.SimpleNamespace(state='active')
        self.assertEqual(handler._preview_cap_state('camera', 'session-a'), 'active')
        self.assertIsNone(handler._preview_cap_state('imu', 'session-a'))
        app._note_manual_driver('session-a')
        self.assertIsNone(handler._preview_cap_state('camera', 'session-a'), 'lease holder keeps full previews')
        self.assertEqual(handler._preview_cap_state('lidar', 'session-b'), 'active')
        # A cap change closes the relay with 4001 autonomy_priority.
        client, server_side = socket.socketpair()
        upstream, bridge = socket.socketpair()
        handler.connection = server_side
        changed = [False]
        thread = threading.Thread(target=handler._relay_websocket, args=(upstream, b'', None, lambda: changed[0]))
        thread.start()
        bridge.sendall(b'frame'); time.sleep(0.2)
        self.assertEqual(client.recv(64), b'frame')
        changed[0] = True
        thread.join(3)
        self.assertFalse(thread.is_alive())
        frame = client.recv(64)
        self.assertEqual(frame[0], 0x88)
        self.assertEqual(int.from_bytes(frame[2:4], 'big'), 4001)
        self.assertEqual(frame[4:], b'autonomy_priority')
        for s in (client, server_side, upstream, bridge):
            s.close()

    def test_http_routes_jobs_item_start_refusal_and_recorder_priority_reason(self):
        from dashboard_server import app
        from dashboard_server.source_live_mapping import SourceLiveMapping
        from urllib.request import Request, build_opener, HTTPCookieProcessor
        from urllib.error import HTTPError
        import http.cookiejar
        with tempfile.TemporaryDirectory() as root:
            override = Path(root) / 'priority'; override.write_text('active')
            os.environ['UBTS_AUTONOMY_PRIORITY_OVERRIDE_FILE'] = str(override)
            os.environ['DASHBOARD_LIVE_MAP_WORKER'] = '0'
            try:
                server = app.create_server('127.0.0.1', 0)
            finally:
                os.environ.pop('UBTS_AUTONOMY_PRIORITY_OVERRIDE_FILE', None)
            initial_live = app.DashboardHandler.live_mapping
            live = SourceLiveMapping(Path(root) / 'live', start_worker=False)
            app.DashboardHandler.live_mapping = live
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            url = f'http://127.0.0.1:{server.server_port}'
            try:
                with opener.open(url + '/api/autonomy/priority') as r:
                    self.assertEqual(json.load(r)['state'], 'active')
                with opener.open(url + '/api/live/mapping-jobs') as r:
                    jobs = json.load(r)
                item = next(j for j in jobs['jobs'] if j['id'] == 'autonomy-priority')
                self.assertTrue(item['processing']); self.assertEqual(item['kind'], 'priority')
                self.assertTrue(app.DashboardHandler.archive_mapping._compute_blocked())
                with self.assertRaises(HTTPError) as error:
                    opener.open(Request(url + '/api/live/map/start', data=b'{}', headers={'Content-Type': 'application/json'}))
                self.assertEqual(error.exception.code, 409)
                self.assertFalse(live.active)
                # The installed recorder's priority_reason() pauses its media queue on this item.
                spec = importlib.util.find_spec('scripts.recording_postprocess')
                if spec is not None:
                    from scripts import recording_postprocess as rp
                    runtime = Path(root) / 'runtime'; (runtime / 'recordings').mkdir(parents=True)
                    (runtime / 'rover_bridge_status.json').write_text(json.dumps({'armed': False, 'moving': False, 'updated_at': time.time()}))
                    state = types.SimpleNamespace(lock=threading.Lock(), active=False, finalizing=False,
                                                  motor_timeline=None, archive_dir=runtime / 'recordings')
                    os.environ['MEDIA_MAPPING_JOBS_URL'] = url + '/api/live/mapping-jobs'
                    os.environ.pop('ROVER_BRIDGE_STATUS_FILE', None)
                    try:
                        self.assertEqual(rp.priority_reason(state), 'recorded_mapping_active')
                        override.write_text('idle'); app.DashboardHandler.autonomy_priority.evaluate()
                        self.assertIsNone(rp.priority_reason(state))
                    finally:
                        os.environ.pop('MEDIA_MAPPING_JOBS_URL', None)
                with opener.open(url + '/api/telemetry') as r:
                    self.assertEqual(json.load(r)['autonomy_priority']['state'], 'idle')
            finally:
                server.shutdown(); server.server_close(); thread.join()
                app.DashboardHandler.autonomy_priority.close()
                live.close(); app.DashboardHandler.live_mapping = initial_live
                initial_live.close(); app.DashboardHandler.archive_mapping.close()

    def test_active_stops_live_mapping_and_keeps_the_map(self):
        from dashboard_server.source_live_mapping import SourceLiveMapping
        with tempfile.TemporaryDirectory() as root:
            live = SourceLiveMapping(Path(root) / 'live', start_worker=False)
            try:
                live.action('start')
                self.assertTrue(live.active)
                now = [0.0]; docker = Docker()
                m = AutonomyPriority(docker_socket='/x', docker_query=docker, clock=lambda: now[0], start=False)
                def changed(previous, state, live=live):
                    if state == 'active' and live.active:
                        live.action('stop')
                        with live.lock:
                            live.reason = 'autonomy_priority'; live._publish()
                m.add_listener(changed)
                epoch = live.map_epoch
                docker.rows = [{'Names': ['/row-executor']}]; now[0] += 2; m.evaluate()
                self.assertFalse(live.active)
                self.assertEqual(live.map_epoch, epoch, 'stop keeps the map')
                self.assertEqual(live.status()['reason'], 'autonomy_priority')
            finally:
                live.close()


if __name__ == '__main__':
    unittest.main()
