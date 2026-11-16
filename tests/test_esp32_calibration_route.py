"""POST /api/rover/calibrate of this dashboard (dashboard_server/app.py) relayed to the ESP32-S3 bridge op 'calibrate'
(scripts/esp32_steer_bridge.py) with the fake runtime-calibration board (tests/fake_esp32.py), in real time and in one
process: rover_api.request is replaced by the bridge's own request() with a JSON round trip (as on the control socket).
Checks: the same session and Origin rules as the other rover routes, only the calibration fields reach the bridge
(op and owner come from the server), armed and autonomy refusals, the status route carries the 'calibration' block,
and set_center and read run end to end. Temporary folders only; the fake board is a test double.
Run in its own process: python -B -m unittest tests.test_esp32_calibration_route
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
from tests.test_steer_routes import Client, dashboard_modules  # noqa: E402  (stubs fcntl / termios on Windows)


class Stack:
    def __init__(self, pot=1925.0):
        self.mods = mods = dashboard_modules()
        from scripts import esp32_abs_hold as esp
        from scripts.esp32_steer_bridge import Esp32Bridge
        from tests.fake_esp32 import FakeEsp32, FakeTransport
        self.tmp = Path(tempfile.mkdtemp(prefix='ubts_cal_route_'))
        self._env = {}
        env = dict(DASHBOARD_MODE='live', DASHBOARD_VEHICLE='front_steer', DASHBOARD_ACCESS_LOG='0',
                   DASHBOARD_LIVE_MAP_WORKER='0', TELEMETRY_FILE=str(self.tmp / 'telemetry.json'),
                   RECORDING_ARCHIVE_DIR=str(self.tmp / 'recordings'),
                   ROVER_BRIDGE_STATUS_FILE=str(self.tmp / 'rover_bridge_status.json'),
                   DATA_RECORDER_STATUS_FILE=str(self.tmp / 'data_recorder_status.json'),
                   CAMERA_SETTINGS_FILE=str(self.tmp / 'camera_settings.json'),
                   DOCKER_SOCKET=str(self.tmp / 'no-docker.sock'))
        for key, value in env.items():
            self._env[key] = os.environ.get(key)
            os.environ[key] = value
        self.lock = threading.RLock()
        self.fake = FakeEsp32(time.monotonic, cal_runtime=True, drive_watchdog_ms=300, pot=pot, rate_counts_s=390.0,
                              stop_left=2320.0, stop_right=1460.0)
        self.motor = esp.Esp32SteerMotor(FakeTransport(self.fake), time.monotonic)
        self.bridge = Esp32Bridge(self.motor, time.monotonic)
        self.relayed = []
        self.stop_event = threading.Event()
        self.loop = threading.Thread(target=self._loop, daemon=True)
        self.loop.start()
        app = mods['app']
        self._patched = mods['rover_api'].request
        mods['rover_api'].request = self._request
        self._handler_state = {name: getattr(app.DashboardHandler, name, None)
                               for name in ('provider', 'dashboard_settings', 'extension_routes', 'page_filter',
                                            'executor_client', 'tuning_log', 'autonomy_supervisor')}
        self.server = app.create_server('127.0.0.1', 0)
        self.base = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        if not self.wait(lambda: self.motor.link == 'ready' and self.motor.status_at is not None, 5.0):
            raise AssertionError('fake board not ready')

    def _loop(self):
        while not self.stop_event.wait(0.02):
            with self.lock:
                self.bridge.step()

    def _request(self, payload):
        request = json.loads(json.dumps(payload, allow_nan=False))
        if request.get('op') != 'status':
            self.relayed.append(request)
        return json.loads(json.dumps(self.bridge.request(request), allow_nan=False))

    def wait(self, predicate, timeout_s):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self.lock:
                if predicate():
                    return True
            time.sleep(0.02)
        return False

    def close(self):
        self.stop_event.set()
        self.loop.join(2.0)
        try:
            self.server.shutdown()
            self.server.server_close()
        finally:
            app = self.mods['app']
            self.mods['rover_api'].request = self._patched
            for name, value in self._handler_state.items():
                if value is None and name == 'autonomy_supervisor':
                    if 'autonomy_supervisor' in app.DashboardHandler.__dict__:
                        delattr(app.DashboardHandler, 'autonomy_supervisor')
                    continue
                setattr(app.DashboardHandler, name, value)
            app.STATIC_ASSETS.set_page_filter(None)
            for key, value in self._env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            shutil.rmtree(self.tmp, ignore_errors=True)


class CalibrateRouteTest(unittest.TestCase):
    def setUp(self):
        self.stack = Stack()
        self.addCleanup(self.stack.close)
        self.page = Client(self.stack.base)
        status, _ = self.page.get('/static/steer/steer-calibration.mjs')     # the session cookie
        self.assertEqual(status, 200)

    def test_session_and_origin_rules(self):
        stranger = Client(self.stack.base)
        status, _ = stranger.post('/api/rover/calibrate', {'action': 'read'})
        self.assertEqual(status, 403)
        status, _ = self.page.post('/api/rover/calibrate', {'action': 'read'}, origin='http://evil.example')
        self.assertEqual(status, 403)
        self.assertEqual(self.stack.relayed, [])

    def test_only_calibration_fields_reach_the_bridge(self):
        status, reply = self.page.post('/api/rover/calibrate', {'action': 'read', 'op': 'drive', 'owner': 'someone',
                                                                'throttle': 100, 'steer_mrad': 300, 'lease': 'x'})
        self.assertEqual(status, 200)
        self.assertTrue(reply['ok'], reply)
        relayed = self.stack.relayed[-1]
        self.assertEqual(sorted(relayed), ['action', 'op', 'owner'])
        self.assertEqual((relayed['op'], relayed['action']), ('calibrate', 'read'))
        self.assertNotEqual(relayed['owner'], 'someone')
        self.assertTrue(self.stack.wait(lambda: self.stack.motor.cal_last and self.stack.motor.cal_last['ok'], 3.0))
        status, reply = self.page.post('/api/rover/calibrate', {'action': 'apply', 'left_margin_counts': 40,
                                                                'right_margin_counts': 20})
        self.assertEqual(reply['error'], 'calibration_nothing_found')
        self.assertEqual({k: self.stack.relayed[-1][k] for k in ('left_margin_counts', 'right_margin_counts')},
                         {'left_margin_counts': 40, 'right_margin_counts': 20})
        # the optional CAL FIND pulse length passes too (this fake is the CAL_RUNTIME board without SMOOTH: refused)
        status, reply = self.page.post('/api/rover/calibrate', {'action': 'find_left', 'pulse_ms': 60})
        self.assertEqual(self.stack.relayed[-1]['pulse_ms'], 60)
        self.assertEqual(reply['error'], 'calibration_pulse_needs_firmware_update')
        self.assertEqual(self.stack.fake.drive_on_ms, 0)

    def test_status_route_carries_the_calibration_block(self):
        status, snap = self.page.get_json('/api/rover/status')
        self.assertEqual(status, 200)
        block = snap['calibration']
        self.assertEqual((block['supported'], block['blocker'], block['state']), (True, None, 'idle'))
        self.assertEqual(block['cal']['store'], 'DEFAULT')

    def test_set_center_end_to_end_and_armed_refusal(self):
        status, reply = self.page.post('/api/rover/calibrate', {'action': 'set_center'})
        self.assertTrue(reply['ok'], reply)
        self.assertTrue(self.stack.wait(lambda: self.stack.motor.cal_job is None, 5.0))
        self.assertTrue(self.stack.motor.cal_last['ok'], self.stack.motor.cal_last)
        self.assertEqual(self.stack.fake.nvs.get('center'), 1925)
        self.assertTrue(self.stack.wait(lambda: self.stack.bridge.snapshot['calibration']['cal']['center'] == 1925, 2.0))
        status, reply = self.page.post('/api/rover/arm', {'vehicle_profile': 'front_steer'})
        self.assertTrue(reply['ok'], reply)
        status, reply = self.page.post('/api/rover/calibrate', {'action': 'find_left'})
        self.assertEqual(reply, {'ok': False, 'error': 'calibration_needs_disarmed'})
        self.page.post('/api/rover/stop', {'reason': 'operator_stop'})
        self.assertEqual(self.stack.fake.drive_on_ms, 0)

    def test_no_calibration_during_an_autonomy_run(self):
        app = self.stack.mods['app']

        class Supervisor:
            takeovers = 0

            def status(self):
                return {'active': True}

            def manual_takeover(self):
                Supervisor.takeovers += 1
                return {'loop_joined': True}
        app.DashboardHandler.autonomy_supervisor = Supervisor()
        status, reply = self.page.post('/api/rover/calibrate', {'action': 'find_left'})
        self.assertEqual((status, reply), (409, None))       # Client returns None for an HTTP error body
        self.assertEqual(Supervisor.takeovers, 0, 'calibration never takes over an autonomy run')
        self.assertEqual(self.stack.relayed, [])


if __name__ == '__main__':
    unittest.main()
