"""Focused observe-only API/UI tests. No socket, ROS, motor or real worker."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import threading
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

# Load the real route source with only telemetry construction isolated. The
# installed telemetry provider uses Linux fcntl/termios; these tests exercise
# no telemetry, sockets or hardware and remain runnable on the SSD host.
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('observe_api_tested',ROOT/'dashboard_server/app.py')
app=importlib.util.module_from_spec(SPEC)
with patch.dict('sys.modules',{'dashboard_server.telemetry':types.SimpleNamespace(
        ROOT=ROOT,TelemetryProvider=object)}):
    SPEC.loader.exec_module(app)


class FakeObservation:
    def __init__(self):
        self.active = False
        self.starts = self.stops = 0

    def start(self, *, cruise_percent=None):
        self.starts += 1
        self.active = True
        self.cruise_percent = cruise_percent
        return self.status()

    def stop(self, reason='operator_stop'):
        self.stops += 1
        self.active = False
        self.stop_reason = reason

    def status(self):
        return dict(ok=True, mode='OBSERVE_ONLY', active=self.active,
            state='waiting_input' if self.active else 'idle',
            current_decision=dict(decision='HOLD', reason='WAIT_LOCALIZATION',
                proposed_left_percent=0, proposed_right_percent=0,
                applied_left_percent=0, applied_right_percent=0, trajectory=[]),
            runtime_authority=False, motor_command_sent=False,
            autonomy_output_enabled=False, physical_autonomy_qualified=False)


class ObserveAPITests(unittest.TestCase):
    def fixture(self, payload=None):
        handler = object.__new__(app.DashboardHandler)
        handler.headers = {'Host': 'dashboard.test', 'Origin': 'http://dashboard.test'}
        handler.autonomy_observation = FakeObservation()
        handler.autonomy_settings = types.SimpleNamespace(percent=20, status=lambda: dict(
            active=False, runtime_authority=False, motor_command=False))
        handler.native_observation_client = None
        handler.autonomy_observe_lock = threading.RLock()
        handler.live_mapping = types.SimpleNamespace(action=Mock(side_effect=AssertionError('shared map action')))
        handler.provider = types.SimpleNamespace(rover_action=Mock(side_effect=AssertionError('motor action')))
        handler._ensure_session = Mock(return_value=True)
        handler._read_json_body = Mock(return_value={} if payload is None else payload)
        handler.responses = []
        handler._send_json = lambda value, status=200: handler.responses.append((status, value))
        handler.send_error = lambda status, message='': handler.responses.append((status, message))
        return handler

    def request(self, handler, path, action=False):
        conflict=type('MappingConflict',(ValueError,),{})
        with patch.dict('sys.modules',{'dashboard_server.mapping_operations':types.SimpleNamespace(MappingConflict=conflict)}):
            handler._live_request(urlparse(path), action=action)
        return handler.responses[-1]

    def test_status_default_off_no_motor_or_source_action(self):
        handler = self.fixture()
        code, result = self.request(handler, '/api/autonomy/observe')
        self.assertEqual(code, 200)
        self.assertFalse(result['active'])
        self.assertEqual(result['current_decision']['applied_left_percent'], 0)
        self.assertEqual(result['current_decision']['applied_right_percent'], 0)
        self.assertEqual(result['speed_percent'], 20)
        self.assertIsNone(result['calibrated_speed_m_s'])
        self.assertEqual((handler.autonomy_observation.starts, handler.autonomy_observation.stops), (0, 0))
        handler.live_mapping.action.assert_not_called()
        handler.provider.rover_action.assert_not_called()

    def test_explicit_start_and_stop_only_own_observer(self):
        handler = self.fixture()
        self.assertTrue(self.request(handler, '/api/autonomy/observe/start', True)[1]['active'])
        self.assertFalse(self.request(handler, '/api/autonomy/observe/stop', True)[1]['active'])
        self.assertEqual((handler.autonomy_observation.starts, handler.autonomy_observation.stops), (1, 1))
        handler.live_mapping.action.assert_not_called()
        handler.provider.rover_action.assert_not_called()
        self.assertEqual(handler.autonomy_settings.percent, 20)
        self.assertEqual(handler.autonomy_observation.cruise_percent, 20)

    def test_native_order_and_failure_do_not_start_observer(self):
        handler = self.fixture()
        events = []
        handler.native_observation_client = types.SimpleNamespace(
            start=lambda: events.append('native_start') or {'ok': True},
            stop=lambda: events.append('native_stop') or {'ok': True},
            status=lambda: {'ok': True, 'state': 'stopped'})
        original_start = handler.autonomy_observation.start
        original_stop = handler.autonomy_observation.stop
        handler.autonomy_observation.start = lambda **kw: events.append('observer_start') or original_start(**kw)
        handler.autonomy_observation.stop = lambda: events.append('observer_stop') or original_stop()
        self.assertEqual(self.request(handler, '/api/autonomy/observe/start', True)[0], 200)
        self.assertEqual(self.request(handler, '/api/autonomy/observe/stop', True)[0], 200)
        self.assertEqual(events, ['native_start', 'observer_start', 'observer_stop', 'native_stop'])
        handler.native_observation_client.start = lambda: {'ok': False, 'error': 'owned_source_unavailable'}
        code, result = self.request(handler, '/api/autonomy/observe/start', True)
        self.assertEqual(code, 400)
        self.assertIn('owned_source_unavailable', result['error'])
        self.assertEqual(handler.autonomy_observation.starts, 1)

    def test_over_cap_setting_rejects_before_native_start(self):
        handler = self.fixture()
        handler.autonomy_settings.percent = 100
        native = Mock()
        handler.native_observation_client = native
        self.assertEqual(self.request(handler, '/api/autonomy/observe/start', True)[0], 400)
        native.start.assert_not_called()
        self.assertEqual(handler.autonomy_observation.starts, 0)

    def test_observer_start_failure_closes_only_native_source(self):
        handler = self.fixture()
        native = types.SimpleNamespace(start=Mock(return_value={'ok': True}),
            stop=Mock(return_value={'ok': True}), status=lambda: {'ok': True})
        handler.native_observation_client = native
        handler.autonomy_observation.start = Mock(side_effect=ValueError('planner_failure'))
        self.assertEqual(self.request(handler, '/api/autonomy/observe/start', True)[0], 400)
        native.stop.assert_called_once()
        handler.provider.rover_action.assert_not_called()

    def test_worker_start_fault_status_closes_native_source(self):
        handler = self.fixture()
        handler.native_observation_client = types.SimpleNamespace(
            start=Mock(return_value={'ok': True}), stop=Mock(return_value={'ok': True}))
        handler.autonomy_observation.start = Mock(return_value={
            'active': False, 'state': 'fault', 'first_failure_reason': 'thread_start_failed'})
        code, result = self.request(handler, '/api/autonomy/observe/start', True)
        self.assertEqual(code, 400)
        self.assertIn('thread_start_failed', result['error'])
        handler.native_observation_client.stop.assert_called_once()

    def test_sensor_or_control_payload_cannot_enter_observation(self):
        for payload in ({'scan': {}}, {'motion': {}}, {'native': True}, {'cruise_preference_mps': 1}, {'speed_percent': 50}):
            with self.subTest(payload=payload):
                handler = self.fixture(payload)
                code, result = self.request(handler, '/api/autonomy/observe/start', True)
                self.assertEqual(code, 400)
                self.assertIn('do_not_accept_sensor_or_control', result['error'])
                self.assertEqual(handler.autonomy_observation.starts, 0)

    def test_existing_motor_start_remains_409(self):
        handler = self.fixture()
        code, result = self.request(handler, '/api/autonomy/start', True)
        self.assertEqual(code, 409)
        self.assertFalse(result['ok'])
        self.assertEqual(handler.autonomy_observation.starts, 0)
        handler.provider.rover_action.assert_not_called()

    def test_same_origin_and_session_guards_cover_observe_actions(self):
        for missing_session in (False, True):
            handler = self.fixture()
            if missing_session:
                handler._ensure_session.return_value = False
            else:
                handler.headers['Origin'] = 'http://other.test'
            self.assertEqual(self.request(handler, '/api/autonomy/observe/start', True)[0], 403)
            self.assertEqual(handler.autonomy_observation.starts, 0)

    def test_get_dispatch_and_status_rejects_authority_escalation(self):
        handler = self.fixture()
        handler.path = '/api/autonomy/observe'
        handler._live_request = Mock()
        handler.do_GET()
        self.assertEqual(handler._live_request.call_args.args[0].path, '/api/autonomy/observe')
        handler = self.fixture()
        handler.autonomy_observation.status = lambda: dict(active=True, runtime_authority=True,
            motor_command_sent=False, autonomy_output_enabled=False)
        self.assertEqual(self.request(handler, '/api/autonomy/observe')[0], 400)

    def test_create_server_wires_one_readonly_provider_without_start(self):
        live = types.SimpleNamespace(observation_input=lambda: None)
        observer = FakeObservation()
        constructor = Mock(return_value=observer)
        planner = object()
        modules = {
            'dashboard_server.source_live_mapping': types.SimpleNamespace(SourceLiveMapping=Mock(return_value=live)),
            'dashboard_server.mapping_operations': types.SimpleNamespace(
                ManagedArchiveMapping=Mock(return_value=object()), MappingOperations=Mock(return_value=object())),
            'dashboard_server.autonomy_settings': types.SimpleNamespace(AutonomySettings=Mock(return_value=object())),
            'dashboard_server.autonomy_observation': types.SimpleNamespace(AutonomyObservation=constructor),
            'dashboard_server.record_based_autonomy': types.SimpleNamespace(RecordBasedAutonomy=Mock(return_value=planner)),
        }
        native = types.SimpleNamespace(input=lambda: None)
        native_constructor = Mock(return_value=native)
        modules['dashboard_server.native_observation_client'] = types.SimpleNamespace(NativeObservationClient=native_constructor)
        keys = ('provider', 'live_mapping', 'archive_mapping', 'mapping_operations', 'autonomy_settings', 'autonomy_observation', 'native_observation_client', 'autonomy_observe_lock')
        old = {key: app.DashboardHandler.__dict__.get(key, ...) for key in keys}
        try:
            for socket_path in ('', '/run/ubts-native-stream/observe.sock'):
                with self.subTest(socket_path=socket_path):
                    constructor.reset_mock();native_constructor.reset_mock()
                    with patch.dict('sys.modules', modules), patch.dict(app.os.environ, {'UBTS_NATIVE_STREAM_SOCKET': socket_path}), patch.object(app, 'TelemetryProvider', return_value=types.SimpleNamespace(recording_archive_dir='unused')), patch.object(app, 'DashboardServer', return_value=types.SimpleNamespace()), patch.object(app, 'dashboard_log'):
                        server = app.create_server('127.0.0.1', 0)
                    constructor.assert_called_once_with(planner=planner, input_provider=native.input if socket_path else live.observation_input)
                    if socket_path:native_constructor.assert_called_once_with(socket_path)
                    else:native_constructor.assert_not_called()
                    self.assertIs(server.autonomy_observation, observer)
                    self.assertEqual(observer.starts, 0)
        finally:
            for key, value in old.items():
                if value is ...:
                    delattr(app.DashboardHandler, key)
                else:
                    setattr(app.DashboardHandler, key, value)

    def test_server_close_stops_only_bound_observation(self):
        observer = FakeObservation()
        server = object.__new__(app.DashboardServer)
        server.autonomy_observation = observer
        server.autonomy_observe_lock = threading.RLock()
        server.native_observation_client = types.SimpleNamespace(stop=Mock())
        with patch.object(app.ThreadingHTTPServer, 'server_close') as parent:
            server.server_close()
        self.assertEqual(observer.stops, 1)
        self.assertEqual(observer.stop_reason, 'dashboard_server_closed')
        server.native_observation_client.stop.assert_called_once()
        parent.assert_called_once()


class ObserveUITests(unittest.TestCase):
    def test_pure_view_and_deferred_start_stop_poll_order(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for the focused UI check')
        script = r"""
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {observationView,observationController}=require('./static/field-preparation.js');
const value=(active=false,reason='WAIT_LOCALIZATION')=>({active,state:active?'waiting_input':'stopped',
 runtime_authority:false,motor_command_sent:false,autonomy_output_enabled:false,
 speed_percent:35,input_age:{scan_source_ns:12e6,scan_receipt_ns:20e6},
 current_decision:{decision:'HOLD',reason,proposed_left_percent:0,proposed_right_percent:0,
 applied_left_percent:0,applied_right_percent:0,trajectory:[]}});
(async()=>{
 const model=observationView(value());
 assert.match(model.applied,/0%.*0%/);assert.match(model.speed,/35%/);
 assert.ok(model.speed.includes('\uad50\uc815 \uc5c6\uc74c'));
 assert.match(model.freshness,/12\.0 ms.*20\.0 ms/);
 assert.throws(()=>observationView({...value(),runtime_authority:true}));
 assert.throws(()=>observationView({...value(),current_decision:{applied_left_percent:1}}));
 assert.throws(()=>observationView({...value(),current_decision:{proposed_left_percent:Infinity}}));
 const calls=[],shown=[],errors=[],busy=[];
 const control=observationController({request:(path,payload)=>new Promise((resolve,reject)=>calls.push({path,payload,resolve,reject})),
 display:x=>shown.push(x),error:x=>errors.push(x),busy:x=>busy.push(x)});
 assert.equal(calls.length,0,'constructor must not Start or fetch');
 const poll=control.refresh();control.refresh();assert.equal(calls.length,1,'only one status request');
 const start=control.start(),stop=control.stop();await Promise.resolve();await Promise.resolve();
 assert.equal(calls[1].path,'/api/autonomy/observe/start');assert.deepEqual(calls[1].payload,{});
 calls[0].resolve(value(false,'old poll'));await poll;assert.equal(shown.length,0,'old poll cannot repaint Stop intent');
 calls[1].resolve(value(true,'late Start'));await start;await Promise.resolve();
 assert.equal(shown.length,0,'late Start cannot repaint Stop intent');
 assert.equal(calls[2].path,'/api/autonomy/observe/stop');assert.deepEqual(calls[2].payload,{});
 calls[2].resolve(value(false,'operator_stop'));await stop;
 assert.equal(shown.length,1);assert.equal(shown[0].active,false);assert.deepEqual(busy,[true,true,false]);
 const fresh=control.refresh();calls[3].reject(new Error('status unavailable'));await fresh;
 assert.equal(shown.length,1,'failed fetch retains last confirmed display');assert.equal(errors.length,1);
 const source=fs.readFileSync('./static/field-preparation.js','utf8');
 assert.ok(!source.includes('/api/rover/'));assert.ok(!source.includes('/api/live/map/start'));
 assert.ok(!source.includes('/api/autonomy/start'));
 console.log('observe UI: pure view, serial commands, late response fence, no motor routes PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        completed = subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1],
                                   capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == '__main__':
    unittest.main()
