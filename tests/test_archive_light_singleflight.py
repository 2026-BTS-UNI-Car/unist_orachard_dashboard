"""Small synthetic contract tests: no provider init, devices, network or replay.

The production methods/functions are compiled from their actual AST. Native
platform imports in telemetry.py are deliberately not imported on Windows.
"""
import ast
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard_server.telemetry_cache import light_segment, light_timeline, TimelineLoadUnavailable
from dashboard_server.motor_pwm import with_recorded_motor, MOTOR_TIMELINE_SCHEMA
from dashboard_server.playback_profile import point_fields, POLAR_PROFILE


def actual_methods():
    tree = ast.parse((ROOT / 'dashboard_server/telemetry.py').read_bytes())
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'TelemetryProvider')
    wanted = {'recording_timeline', '_load_recording_timeline', '_wait_for_timeline_load', '_load_recording_light_timeline',
              '_playback_source_signature', '_playback_cache_path', '_valid_playback_manifest', '_playback_segment_request',
              '_drop_timeline_cache', '_recording_light_telemetry', '_recording_timeline_segment',
              '_timeline_frames_between', '_raw_sensor_frames', '_playback_response_profile', '_with_recorded_gps'}
    methods = [node for node in original.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    if {node.name for node in methods} != wanted:
        raise AssertionError('Production method extraction mismatch')
    cls = copy.deepcopy(original)
    cls.bases = []; cls.decorator_list = []; cls.body = methods
    constants = [node for node in tree.body if isinstance(node, ast.Assign) and
                 any(isinstance(t, ast.Name) and t.id.startswith('PLAYBACK_') for t in node.targets)]
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_load_json']
    future_import = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future_import, *constants, *functions, cls], type_ignores=[]))
    namespace = dict(__name__='dashboard_server.telemetry',__package__='dashboard_server',
                     Path=Path, Any=object, math=math, json=json, threading=threading, time=time,
                     Future=Future, FutureTimeoutError=FutureTimeoutError, OrderedDict=OrderedDict,
                     light_timeline=light_timeline, with_recorded_motor=with_recorded_motor,
                     POLAR_PROFILE=POLAR_PROFILE, point_fields=point_fields,TimelineLoadUnavailable=TimelineLoadUnavailable)
    exec(compile(module, str(ROOT / 'dashboard_server/telemetry.py'), 'exec'), namespace)
    return namespace['TelemetryProvider'], namespace


class ArchiveLightTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name)
        self.path = self.archive / 'timeline.json'
        self.path.write_text('{}', encoding='utf-8')
        self.provider_class, self.namespace = actual_methods()
        self.provider = self.provider_class.__new__(self.provider_class)
        self.provider._timeline_cache_limit = 2
        self.provider._timeline_cache = OrderedDict()
        self.provider._light_timeline_cache = OrderedDict()
        self.provider._timeline_cache_lock = threading.RLock()
        self.provider._timeline_loads = {}
        self.provider._timeline_load_slots = threading.BoundedSemaphore(2)
        self.provider._timeline_wait_slots = threading.BoundedSemaphore(8)
        self.provider._playback_cache_lock = threading.RLock()
        self.provider.recording_playback_cache_dir = self.archive / 'cache'
        self.threads = []
        self.release_events = []
        self.addCleanup(self.close_threads)

    def close_threads(self):
        for event in self.release_events:
            event.set()
        for thread in self.threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), 'Synthetic request leaked')

    def start(self, fn):
        result = {}
        def request():
            try:
                result['value'] = fn()
            except BaseException as error:
                result['error'] = error
            finally:
                result['done'] = True
        thread = threading.Thread(target=request, daemon=True)
        self.threads.append(thread); thread.start()
        return thread, result

    def finish(self, pair):
        thread, result = pair
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive(), 'Synthetic request did not finish')
        return result

    def observed_future(self, waiting):
        class ObservedFuture(Future):
            def result(self, timeout=None):
                waiting.set()
                return super().result(timeout)
        self.namespace['Future'] = ObservedFuture

    def blocked_loader(self, outcome):
        entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
        self.release_events.append(release)
        self.observed_future(waiting)
        def loader(path):
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError('Synthetic release not received')
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        loader = Mock(side_effect=loader)
        self.namespace['_load_json'] = loader
        return loader, entered, release, waiting

    def test_same_source_single_parse_and_future_cleanup(self):
        timeline = dict(duration_s=8, imu=dict(frames=[]))
        loader, entered, release, waiting = self.blocked_loader(timeline)
        owner = self.start(lambda:self.provider._load_recording_timeline(self.path))
        self.assertTrue(entered.wait(1))
        waiter = self.start(lambda:self.provider._load_recording_timeline(self.path))
        self.assertTrue(waiting.wait(1)); release.set()
        self.assertIs(self.finish(owner)['value'], timeline)
        self.assertIs(self.finish(waiter)['value'], timeline)
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(self.provider._timeline_loads, {})

    def test_owner_error_wakes_waiter_and_allows_retry(self):
        for error in (RuntimeError('synthetic failure'), TimeoutError('owner timeout')):
            self.provider._timeline_cache.clear()
            loader, entered, release, waiting = self.blocked_loader(error)
            owner = self.start(lambda:self.provider._load_recording_timeline(self.path))
            self.assertTrue(entered.wait(1))
            waiter = self.start(lambda:self.provider._load_recording_timeline(self.path))
            self.assertTrue(waiting.wait(1)); release.set()
            self.assertIs(self.finish(owner)['error'], error)
            self.assertIs(self.finish(waiter)['error'], error)
            self.assertEqual(loader.call_count, 1)
            self.assertEqual(self.provider._timeline_loads, {})
            expected = {'imu':{'frames':[]}}
            self.namespace['_load_json'] = Mock(return_value=expected)
            self.assertIs(self.provider._load_recording_timeline(self.path), expected)

    def test_old_signature_owner_and_waiter_never_publish_stale(self):
        old = {'duration_s':1}; new = {'duration_s':2, 'imu':{'frames':[]}}
        loader, entered, release, waiting = self.blocked_loader(old)
        owner = self.start(lambda:self.provider._load_recording_timeline(self.path))
        self.assertTrue(entered.wait(1))
        waiter = self.start(lambda:self.provider._load_recording_timeline(self.path))
        self.assertTrue(waiting.wait(1))
        self.path.write_text('{"replaced":true}', encoding='utf-8')
        # Old requests have already entered the frozen blocking callback.
        self.namespace['_load_json'] = Mock(return_value=new)
        self.assertIs(self.provider._load_recording_timeline(self.path), new)
        release.set()
        self.assertEqual(str(self.finish(owner)['error']), 'timeline_source_changed')
        self.assertEqual(str(self.finish(waiter)['error']), 'timeline_source_changed')
        self.assertIs(self.provider._load_recording_timeline(self.path), new)
        self.assertEqual(self.provider._timeline_loads, {})
        self.assertIs(self.provider._load_recording_light_timeline(self.path)['imu'], new['imu'])

    def test_waiter_capacity_fails_closed_without_poisoning_owner(self):
        timeline = {'duration_s':8}
        loader, entered, release, waiting = self.blocked_loader(timeline)
        owner = self.start(lambda:self.provider._load_recording_timeline(self.path))
        self.assertTrue(entered.wait(1))
        for _ in range(8):
            self.assertTrue(self.provider._timeline_wait_slots.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(TimelineLoadUnavailable,'timeline_load_busy'):
                self.provider._load_recording_timeline(self.path)
            self.assertEqual(loader.call_count, 1)
            self.assertLessEqual(len(self.provider._timeline_loads), 2)
        finally:
            for _ in range(8):self.provider._timeline_wait_slots.release()
            release.set()
        self.assertIs(self.finish(owner)['value'], timeline)
        self.assertIs(self.provider._load_recording_timeline(self.path), timeline)

    def test_owner_and_lru_limits_remain_bounded(self):
        self.namespace['_load_json'] = Mock(return_value={'imu':{'frames':[]}})
        for index in range(5):
            path = self.archive / f'timeline-{index}.json'
            path.write_text('{}', encoding='utf-8')
            self.provider._load_recording_timeline(path)
            self.assertLessEqual(len(self.provider._timeline_cache), 2)
            self.assertLessEqual(len(self.provider._light_timeline_cache), 2)
            self.assertEqual(self.provider._timeline_loads, {})
        self.assertTrue(self.provider._timeline_load_slots.acquire(blocking=False))
        self.assertTrue(self.provider._timeline_load_slots.acquire(blocking=False))
        self.assertFalse(self.provider._timeline_load_slots.acquire(blocking=False))
        self.provider._timeline_load_slots.release(); self.provider._timeline_load_slots.release()

    def test_distinct_source_admission_waits_outside_cache_lock(self):
        entered, release, admission = threading.Event(), threading.Event(), threading.Event()
        self.release_events.append(release)
        real_slots = threading.BoundedSemaphore(1)
        class ObservedSlots:
            def acquire(self, blocking=True, timeout=None):
                if blocking and timeout is not None:admission.set()
                return real_slots.acquire(blocking=blocking, timeout=timeout) if blocking else real_slots.acquire(blocking=False)
            def release(self):real_slots.release()
        self.provider._timeline_load_slots = ObservedSlots()
        other = self.archive / 'another.json';other.write_text('{}', encoding='utf-8')
        def loader(path):
            if path == self.path:
                entered.set()
                if not release.wait(2):raise AssertionError('Synthetic release missing')
            return {'duration_s':1 if path == self.path else 2}
        loader = Mock(side_effect=loader); self.namespace['_load_json'] = loader
        first = self.start(lambda:self.provider._load_recording_timeline(self.path))
        self.assertTrue(entered.wait(1))
        second = self.start(lambda:self.provider._load_recording_timeline(other))
        self.assertTrue(admission.wait(1))
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(len(self.provider._timeline_loads), 1)
        # Main thread can take the shared lock while admission waits.
        self.assertTrue(self.provider._timeline_cache_lock.acquire(blocking=False))
        self.provider._timeline_cache_lock.release()
        release.set()
        self.assertEqual(self.finish(first)['value']['duration_s'],1)
        self.assertEqual(self.finish(second)['value']['duration_s'],2)
        self.assertEqual(self.provider._timeline_loads,{})

    def test_wait_deadline_releases_admission_without_canceling_owner(self):
        future = Future()
        signature = self.provider._playback_source_signature(self.path)
        with patch.object(future,'result',side_effect=FutureTimeoutError()):
            with self.assertRaisesRegex(TimelineLoadUnavailable,'timeline_load_timeout'):
                self.provider._wait_for_timeline_load(future,self.path,signature)
        self.assertFalse(future.done())
        held = []
        try:
            for _ in range(8):
                self.assertTrue(self.provider._timeline_wait_slots.acquire(blocking=False));held.append(1)
        finally:
            for _ in held:self.provider._timeline_wait_slots.release()
        future.set_result({'duration_s':1})
        self.assertEqual(self.provider._wait_for_timeline_load(future,self.path,signature),{'duration_s':1})

    def test_admission_and_future_wait_share_one_absolute_budget(self):
        clock=[0.0];received=[];released=[]
        self.namespace['time']=types.SimpleNamespace(monotonic=lambda:clock[0])
        signature=self.provider._playback_source_signature(self.path)
        key=(str(self.path.resolve()),*signature)
        class OtherOwnerFuture(Future):
            def result(self, timeout=None):
                received.append(timeout)
                return {'duration_s':8}
        class Admission:
            def acquire(self, blocking=True, timeout=None):
                if not blocking:return False
                received.append(timeout)
                clock[0]=20.0
                self_future=OtherOwnerFuture()
                self_provider._timeline_loads[key]=self_future
                return True
            def release(self):released.append(True)
        self_provider=self.provider
        self.provider._timeline_load_slots=Admission()
        self.assertEqual(self.provider._load_recording_timeline(self.path),{'duration_s':8})
        self.assertEqual(received,[30.0,10.0])
        self.assertEqual(released,[True])
        self.provider._timeline_loads.clear()

    def test_busy_timeout_and_stale_never_enter_prepared_lidar_recovery(self):
        self.provider.recording_archive_path=lambda _:self.archive
        for reason in ('timeline_load_busy','timeline_load_timeout','timeline_source_changed'):
            with patch('dashboard_server.lidar_preview_recovery.window',side_effect=AssertionError('Transient refusal must not recover LiDAR')) as recovery:
                with patch.object(self.provider,'_load_recording_light_timeline',side_effect=TimelineLoadUnavailable(reason)):
                    result=self.provider.recording_timeline('fixture',0,4,'dashboard-telemetry-v1')
                    self.assertEqual(result['error'],reason);self.assertFalse(result['ok'])
                with patch.object(self.provider,'_load_recording_timeline',side_effect=TimelineLoadUnavailable(reason)):
                    result=self.provider.recording_timeline('fixture')
                    self.assertEqual(result['error'],reason);self.assertFalse(result['ok'])
                recovery.assert_not_called()

    def test_light_completes_while_full_cache_lock_is_held_and_reads_no_lidar(self):
        class UnreadableFrames(list):
            def __iter__(self):raise AssertionError('LiDAR/camera frames accessed')
        timeline = dict(duration_s=8, lidar=dict(frames=UnreadableFrames([{'points':[]}])) ,
                        camera=dict(frames=UnreadableFrames([{'data':'not read'}])),
                        imu=dict(frames=[dict(t_s=1,raw=dict(euler_deg=dict(yaw=3)))]))
        self.namespace['_load_json'] = Mock(return_value=timeline)
        self.provider._load_recording_timeline(self.path)
        self.provider._playback_cache_path = lambda *args:None
        self.provider._build_playback_cache = Mock(side_effect=AssertionError('Full build forbidden'))
        locked, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        def full_builder():
            with self.provider._playback_cache_lock:
                locked.set();release.wait(5)
        blocked = self.start(full_builder)
        self.assertTrue(locked.wait(1))
        request = self.start(lambda:self.provider._recording_light_telemetry(self.archive,self.path,0,4))
        result = self.finish(request)
        self.assertNotIn('error', result)
        response = result['value']
        self.assertTrue(response['ok'])
        self.assertEqual(response['timeline']['lidar']['frames'], [])
        self.assertEqual(response['timeline']['camera']['frames'], [])
        self.assertEqual(len(response['timeline']['imu']['frames']), 1)
        self.assertFalse(release.is_set())
        self.provider._build_playback_cache.assert_not_called()
        self.namespace['_load_json'].assert_called_once()
        release.set();self.finish(blocked)

    def test_drop_full_ram_keeps_small_view_without_lidar_references(self):
        lidar = [{'points':[[1,2,3]]}]; camera = [{'data':'private pixels'}]
        timeline = dict(imu=dict(frames=[dict(t_s=1)]), lidar=dict(frames=lidar), camera=dict(frames=camera))
        loader = Mock(return_value=timeline); self.namespace['_load_json'] = loader
        self.provider._load_recording_timeline(self.path)
        self.provider._drop_timeline_cache(self.path)
        view = self.provider._load_recording_light_timeline(self.path)
        self.assertEqual(set(view), {'imu'})
        self.assertIs(view['imu'], timeline['imu'])
        self.assertEqual(self.provider._timeline_cache, {})
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(len(self.provider._light_timeline_cache), 1)

    def test_warm_light_cache_never_reparses_lidar_segments(self):
        cache = self.archive / 'derived'; segments = cache / 'segments'; segments.mkdir(parents=True)
        for index, t in enumerate((3.95, 4.1)):
            (segments / f'telemetry-{index:06d}.json').write_text(json.dumps(dict(duration_s=8,
                imu=dict(frames=[dict(t_s=t,raw=dict(euler_deg=dict(yaw=index)))]))), encoding='utf-8')
            (segments / f'segment-{index:06d}.json').write_text('must not be read', encoding='utf-8')
        self.provider._playback_cache_path = lambda *args:cache
        self.provider._valid_playback_manifest = lambda *args:dict(segment_count=2)
        self.provider._load_recording_light_timeline = Mock(side_effect=AssertionError('Source parse forbidden'))
        response = self.provider._recording_light_telemetry(self.archive,self.path,4,4)
        self.assertTrue(response['ok'])
        self.assertEqual([f['t_s'] for f in response['timeline']['imu']['frames']], [3.95,4.1])
        self.assertIsNone(light_segment(cache,2,cached_only=True))

    def test_light_window_profile_and_sidecars_preserve_source(self):
        imu = [dict(t_s=t,raw=dict(euler_deg=dict(yaw=t),orientation_covariance=[1]*9)) for t in (0,3.95,4.1,7.9,8.1)]
        timeline = dict(duration_s=10, coordinate_frame='synthetic', imu=dict(frames=imu),
                        motor=dict(schema=MOTOR_TIMELINE_SCHEMA,frames=[]), lidar=dict(frames=[]))
        before = copy.deepcopy(timeline)
        (self.archive / 'motor.jsonl').write_text('\n'.join(json.dumps(dict(t_s=t,pins={'D8':{'reported_pwm':v}}))
            for t,v in ((3.9,10),(4.2,20),(9,0)))+'\n', encoding='utf-8')
        (self.archive / 'gps.jsonl').write_text('\n'.join(json.dumps(dict(t_s=t,sample={'fix_valid':True}))
            for t in (3.8,4.2,8.2))+'\n', encoding='utf-8')
        self.namespace['_load_json'] = Mock(return_value=timeline)
        response = self.provider._recording_light_telemetry(self.archive,self.path,4,4)
        self.assertTrue(response['ok']);result=response['timeline']
        self.assertEqual([f['t_s'] for f in result['imu']['frames']], [3.95,4.1,7.9])
        self.assertEqual([f['t_s'] for f in result['motor']['frames']], [3.9,4.2])
        self.assertEqual([f['t_s'] for f in result['gps']['frames']], [3.8,4.2,8.2])
        self.assertEqual(result['playback']['profile'], 'dashboard-telemetry-v1')
        self.assertEqual(result['segment'], dict(start_s=4.0,end_s=8.0,duration_s=4.0,partial=True))
        self.assertNotIn('orientation_covariance', result['imu']['frames'][0]['raw'])
        self.assertEqual(timeline, before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
