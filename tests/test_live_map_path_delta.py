"""Live map polling sends only new path points (S02) and stays exactly equal to the full path."""
import importlib.util
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from urllib.request import urlopen

os.environ.setdefault('DASHBOARD_LIVE_MAP_WORKER', '0')
from dashboard_server.source_live_mapping import SourceLiveMapping


def scan_sample(i, x, y, yaw=0.0):
    return dict(pose=dict(x=x, y=y, yaw=yaw), stamp=1000.0 + i * 0.1, preserve_hit_endpoints=True,
                scan=dict(angle_min=-math.pi, angle_increment=math.pi / 2, range_min=0.05, range_max=12.0,
                          ranges=[3.0, 2.5, 4.0, 3.5]))


class Client:
    """Python mirror of static/live-map-delta.mjs (mergeLivePath and the path query)."""

    def __init__(self):
        self.snapshot = {}
        self.need_full = False

    def query(self):
        q = dict(transport='tiles')
        if self.snapshot.get('tile_epoch'):
            q.update(tile_epoch=self.snapshot['tile_epoch'], tile_since=self.snapshot.get('tile_version') or 0)
        path = self.snapshot.get('path') or []
        if not self.need_full and self.snapshot.get('path_token') and path:
            q.update(path_token=self.snapshot['path_token'], path_count=len(path), path_stamp=repr(path[-1]['stamp']))
        return q

    def apply(self, value):
        previous = self.snapshot.get('path') or []
        if isinstance(value.get('path_from'), int):
            ok = (value.get('path_token') == self.snapshot.get('path_token')
                  and len(previous) >= value['path_from'] and not value.get('tiles_reset'))
            path = previous[:value['path_from']] + list(value['path']) if ok else previous
            ok = ok and len(path) == value.get('path_count')
        else:
            path, ok = list(value.get('path') or []), True
        self.need_full = not ok
        self.snapshot = dict(value, path=path)


def poll(live, client):
    q = client.query()
    value = live.status(None, transport='tiles', tile_since=q.get('tile_since'), tile_epoch=q.get('tile_epoch'),
                        path_token=q.get('path_token'), path_count=q.get('path_count'), path_stamp=q.get('path_stamp'))
    value = json.loads(json.dumps(value))  # the exact HTTP JSON round trip
    client.apply(value)
    return value


class PathDeltaTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.live = SourceLiveMapping(Path(self.root.name) / 'live', start_worker=False)
        self.i = 0

    def tearDown(self):
        self.live.close(); self.root.cleanup()

    def step(self, dx=0.1):
        self.i += 1
        self.live.map.update(scan_sample(self.i, self.i * dx, 0.02 * self.i))
        self.live._publish()

    def full_path(self):
        return json.loads(json.dumps(self.live.cell_snapshot['path']))

    def test_incremental_path_equals_full_path_and_is_small(self):
        client = Client()
        first = poll(self.live, client)
        self.assertNotIn('path_from', first)
        for _ in range(40):
            self.step()
            value = poll(self.live, client)
            self.assertEqual(client.snapshot['path'], self.full_path())
            self.assertFalse(client.need_full)
        self.assertIn('path_from', value)
        self.assertLessEqual(len(json.dumps(value['path'], separators=(',', ':'))), 1024)
        # A long path is still sent as only the newest points.
        self.assertGreater(len(client.snapshot['path']), 30)
        self.assertLessEqual(len(value['path']), 2)

    def test_unchanged_poll_resends_only_last_point_and_refreshes_its_flags(self):
        client = Client()
        for _ in range(5):
            self.step()
        poll(self.live, client)
        self.live.map.path[-1]['break_before'] = True  # set after publication by the engine
        self.live._publish()
        value = poll(self.live, client)
        self.assertEqual(value['path_from'], len(client.snapshot['path']) - 1)
        self.assertTrue(client.snapshot['path'][-1]['break_before'])
        self.assertEqual(client.snapshot['path'], self.full_path())

    def test_compaction_sends_full_path_then_resumes_increments(self):
        client = Client()
        self.step(); poll(self.live, client)
        # Prefill history close to the 12,000-point bound; the real update()
        # performs the compaction and records path_compacted_at.
        base = self.live.map.path[-1]
        self.live.map.path.extend(dict(base, x=base['x'] + 0.05 * k, stamp=base['stamp'] + 0.001 * k)
                                  for k in range(1, 12000 - len(self.live.map.path) + 1))
        self.i = 100000
        self.live._publish(); poll(self.live, client)
        self.assertEqual(client.snapshot['path'], self.full_path())
        token = client.snapshot['path_token']
        for _ in range(3):
            self.step(dx=0.2)
            value = poll(self.live, client)
            self.assertEqual(client.snapshot['path'], self.full_path())
        self.assertNotEqual(client.snapshot['path_token'], token)
        self.assertLess(len(client.snapshot['path']), 12000)
        self.assertIn('path_from', value)

    def test_new_map_stop_start_and_unknown_revision_send_full_path(self):
        client = Client()
        for _ in range(4):
            self.step()
        poll(self.live, client)
        self.live.action('stop')
        poll(self.live, client)
        self.assertEqual(client.snapshot['path'], self.full_path())
        self.live.action('reset')
        value = poll(self.live, client)
        self.assertNotIn('path_from', value)
        self.assertEqual(client.snapshot['path'], [])
        self.i = 0
        for _ in range(3):
            self.step()
        poll(self.live, client)
        self.assertEqual(client.snapshot['path'], self.full_path())
        # Unknown/invalid client state never yields a delta.
        data = self.live.cell_snapshot
        for count, stamp, token in ((-1, 0, data['path_token']), (0, 0, data['path_token']),
                                    (len(data['path']) + 1, 0, data['path_token']),
                                    (1, 123.0, data['path_token']), (1, data['path'][0]['stamp'], 'other:0'),
                                    ('x', 'nan', data['path_token'])):
            value = self.live.status(None, transport='tiles', tile_since=data['tile_version'],
                                     tile_epoch=data['tile_epoch'], path_token=token, path_count=count, path_stamp=stamp)
            self.assertNotIn('path_from', value)
            self.assertEqual(len(value['path']), len(data['path']))
        # A tiles reset (wrong tile epoch) always carries the full path.
        value = self.live.status(None, transport='tiles', tile_since=0, tile_epoch='old',
                                 path_token=data['path_token'], path_count=1, path_stamp=data['path'][0]['stamp'])
        self.assertTrue(value['tiles_reset']); self.assertNotIn('path_from', value)

    def test_clients_without_path_token_keep_the_previous_contract(self):
        for _ in range(3):
            self.step()
        data = self.live.cell_snapshot
        value = self.live.status(None, transport='tiles', tile_since=data['tile_version'], tile_epoch=data['tile_epoch'])
        self.assertNotIn('path_from', value)
        self.assertEqual(len(value['path']), len(data['path']))

    @unittest.skipUnless(importlib.util.find_spec('fcntl'), 'dashboard app imports fcntl (Linux)')
    def test_http_route_passes_path_query(self):
        from dashboard_server import app
        for _ in range(6):
            self.step()
        server = app.create_server('127.0.0.1', 0)
        initial = app.DashboardHandler.live_mapping
        app.DashboardHandler.live_mapping = self.live
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            data = self.live.cell_snapshot
            q = dict(transport='tiles', tile_epoch=data['tile_epoch'], tile_since=data['tile_version'],
                     path_token=data['path_token'], path_count=len(data['path']), path_stamp=repr(data['path'][-1]['stamp']))
            with urlopen(f'http://127.0.0.1:{server.server_port}/api/live/map?{urlencode(q)}') as response:
                value = json.load(response)
            self.assertEqual(value['path_from'], len(data['path']) - 1)
            self.assertEqual(len(value['path']), 1)
        finally:
            server.shutdown(); server.server_close(); thread.join()
            app.DashboardHandler.live_mapping = initial


if __name__ == '__main__':
    unittest.main()
