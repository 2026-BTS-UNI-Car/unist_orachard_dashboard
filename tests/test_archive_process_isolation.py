"""Synthetic archive isolation checks; never launches a child, server or device.

Running this suite requires the Root measurement slot.  Files stay inside small
TemporaryDirectory instances; Popen and device APIs are fail-closed test doubles.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_server import archive_protocol as protocol
from dashboard_server import archive_process as broker
from dashboard_server import archive_worker as worker


def telemetry_class():
    # Windows import compatibility only.  These stubs cannot perform I/O and
    # disappear from sys.modules when import returns.  Linux uses real imports.
    missing = {}
    for name in ('fcntl', 'termios'):
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            stub = types.ModuleType(name)
            for function in ('flock', 'ioctl', 'tcgetattr', 'tcsetattr', 'tcflush'):
                setattr(stub, function, Mock(side_effect=AssertionError('NO_DEVICE_IO')))
            for constant in ('LOCK_EX', 'LOCK_NB', 'LOCK_UN', 'TCSANOW', 'TCIFLUSH'):
                setattr(stub, constant, 0)
            missing[name] = stub
    with patch.dict(sys.modules, missing):
        return importlib.import_module('dashboard_server.telemetry').TelemetryProvider


class FakeProcess:
    """Retained handle double; no child or pipe thread exists."""
    def __init__(self):
        self.pid = 424242
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.returncode = None
        self.calls = []
        self.terminate_error = None
        self.wait_error = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.calls.append('terminate')
        if self.terminate_error:
            raise self.terminate_error
        self.returncode = -15

    def kill(self):
        self.calls.append('kill')
        self.returncode = -9

    def wait(self, timeout=None):
        self.calls.append('wait')
        if self.wait_error:
            raise self.wait_error
        return self.returncode


class SmallFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='archive-unit-')
        self.root = Path(self.temp.name).resolve()
        self.archive = self.root / 'recordings'
        self.archive.mkdir()
        self.cache = self.root / 'cache'
        self.addCleanup(self.temp.cleanup)
        self.forbid_process = patch('subprocess.Popen', side_effect=AssertionError('NO_ACTUAL_PROCESS'))
        self.forbid_process.start()
        self.addCleanup(self.forbid_process.stop)

    def manager(self):
        manager = broker.ArchiveProcess(self.archive, self.cache)
        self.addCleanup(manager.close)
        return manager

    def body(self, directory, rid='a' * 32, value=None, compressed=False):
        value = {'ok': True, 'small': [1, 2, 3]} if value is None else value
        metadata = worker.encode_body(directory, rid, value, compressed, time.monotonic() + 5)
        return rid, metadata


class ProtocolChecks(SmallFiles):
    def test_rpc_frame_cap_duplicate_nonfinite_and_operation_allowlist(self):
        self.assertEqual(protocol.decode(protocol.encode({'op': 'detail'})), {'op': 'detail'})
        for raw in (b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'[]\n', b'{}', b'x' * (protocol.MAX_MESSAGE + 1)):
            with self.subTest(raw_length=len(raw)), self.assertRaises((ValueError, protocol.ArchiveProcessError)):
                protocol.decode(raw)
        with self.assertRaises(protocol.ArchiveProcessError):
            protocol.encode({'large': 'x' * protocol.MAX_MESSAGE})
        for op in ('arm', 'drive', 'stop', 'regenerate', 'map-playback', '__dict__'):
            with self.subTest(op=op), self.assertRaises(protocol.ArchiveProcessError):
                protocol.params(op, {})

    def test_recording_paths_and_parameters_reject_escape_and_large_input(self):
        for rid in ('..', '.', '../outside', '/tmp/outside', 'a/b', 'a\\b', '', 'a' * 161):
            with self.subTest(rid=rid), self.assertRaises(protocol.ArchiveProcessError):
                protocol.recording_path(self.archive, rid)
        for params in ({'recording_id': 'record', 'extra': 1}, {'recording_id': 'record', 'start_s': 'x' * 129, 'duration_s': '4', 'profile': None}):
            op = 'timeline' if 'start_s' in params else 'detail'
            with self.assertRaises(protocol.ArchiveProcessError):
                protocol.params(op, params)
        for archive, cache in ((self.archive, self.archive), (self.archive, self.archive / 'cache')):
            with self.assertRaises(protocol.ArchiveProcessError):
                protocol.root_paths(archive, cache)

    def test_symlink_component_is_refused_without_platform_symlink_privilege(self):
        target = self.archive / 'linked' / 'metadata.json'
        real_lstat = Path.lstat

        def lstat(path, *args, **kwargs):
            if path == self.archive / 'linked':
                return types.SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
            return real_lstat(path, *args, **kwargs)

        with patch.object(Path, 'lstat', lstat), self.assertRaises(protocol.ArchiveProcessError):
            protocol.no_link(target)

    def test_response_size_digest_filename_and_encoding_are_enforced(self):
        directory = self.root / 'responses'
        directory.mkdir()
        rid, good = self.body(directory)
        with protocol.open_response(directory, rid, good) as stream:
            self.assertEqual(json.load(stream), {'ok': True, 'small': [1, 2, 3]})
        changes = ({'bytes': good['bytes'] + 1}, {'bytes': protocol.MAX_BODY + 1},
                   {'sha256': '0' * 64}, {'file': '../outside.body'}, {'encoding': 'br'},
                   {'decoded_bytes': -1}, {'decoded_bytes': good['bytes']+1}, {'extra': True})
        for change in changes:
            with self.subTest(change=change), self.assertRaises((OSError, protocol.ArchiveProcessError)):
                with protocol.open_response(directory, rid, dict(good, **change)):
                    pass

    def test_response_hardlink_is_not_accepted_as_an_owned_unique_body(self):
        directory = self.root / 'responses'
        directory.mkdir()
        rid, metadata = self.body(directory)
        original = directory / (rid + '.body')
        alias = self.root / 'second-link'
        os.link(original, alias)
        self.assertGreater(original.stat().st_nlink, 1)
        with self.assertRaises(protocol.ArchiveProcessError):
            with protocol.open_response(directory, rid, metadata):
                pass
        self.assertTrue(alias.is_file())  # Refusal must not mutate the foreign alias.

    def test_source_epoch_detects_same_size_same_mtime_replacement(self):
        record = self.archive / 'record'
        record.mkdir()
        source = record / 'timeline.json'
        source.write_bytes(b'{"x":1}')
        stamp = protocol.source_stamp(self.archive, 'detail', {'recording_id': 'record'})
        before = source.stat()
        replacement = record / 'replacement'
        replacement.write_bytes(b'{"x":2}')
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(replacement, source)
        self.assertNotEqual(stamp, protocol.source_stamp(self.archive, 'detail', {'recording_id': 'record'}))

    def test_cache_write_guard_refuses_hardlink_escape_and_nested_link(self):
        self.cache.mkdir()
        record = self.cache / 'record'
        record.mkdir()
        owned = record / 'frame.jpg'
        owned.write_bytes(b'jpeg')
        alias = self.root / 'foreign-alias'
        os.link(owned, alias)
        for check in (lambda: protocol.cache_write_path(self.cache, owned),
                      lambda: protocol.guard_cache(self.cache, 'record'),
                      lambda: protocol.cache_write_path(self.cache, self.root / 'outside')):
            with self.assertRaises(protocol.ArchiveProcessError):
                check()
        self.assertEqual(alias.read_bytes(), b'jpeg')

    def test_response_checksum_checks_cancel_and_existing_body_is_preserved(self):
        directory = self.root / 'responses'
        directory.mkdir()
        rid, metadata = self.body(directory)
        original = (directory / (rid+'.body')).read_bytes()
        with self.assertRaises(protocol.ArchiveProcessError):
            worker.encode_body(directory, rid, {'replacement': True}, False, time.monotonic()+1)
        self.assertEqual((directory / (rid+'.body')).read_bytes(), original)
        with self.assertRaises(protocol.ArchiveProcessError):
            protocol.open_response(directory, rid, metadata,
                lambda: protocol.need(False, 'synthetic_cancelled', 499))

    def test_namespace_cache_budget_excludes_only_exact_owned_response_directory(self):
        self.cache.mkdir()
        output=self.cache/'archive-http-owned'
        output.mkdir()
        (output/'bounded.body').write_bytes(b'x'*20)
        cache_file=self.cache/'recorded-cache'
        cache_file.write_bytes(b'1234')
        with patch.object(protocol,'MAX_CACHE_BYTES',4):
            self.assertEqual(protocol.cache_budget(self.cache,output)['bytes_left'],0)
            cache_file.write_bytes(b'12345')
            with self.assertRaises(protocol.ArchiveProcessError):
                protocol.cache_budget(self.cache,output)


class BrokerChecks(SmallFiles):
    def arm_fake_transport(self, manager, reply_change=None):
        fake = FakeProcess()
        manager._process = fake
        manager._nonce = 'b' * 64
        manager._generation = 1
        manager._inbox = queue.Queue(maxsize=2)

        def receive(process, deadline, cancelled):
            command = protocol.decode(fake.stdin.getvalue())
            reply = {'schema': protocol.SCHEMA, 'nonce': manager._nonce, 'generation': 1,
                     'id': command['id'], 'kind': 'response', 'ok': True,
                     'source_fingerprint': 'c' * 64,
                     'body': {'file': command['id'] + '.body', 'bytes': 1,
                              'sha256': 'd' * 64, 'encoding': 'identity'}}
            if reply_change:
                reply.update(reply_change)
            return reply

        return fake, patch.object(manager, '_start', return_value=fake), patch.object(manager, '_receive', side_effect=receive)

    def test_constructor_is_lazy_and_environment_strips_device_control_endpoints(self):
        manager = self.manager()
        self.assertIsNone(manager._process)
        env = {'UBTS_ROVER_SOCKET': '/private/motor', 'ROS_DOMAIN_ID': '7',
               'DASHBOARD_RECORDER_URL': 'http://private', 'PRIVATE_TOKEN': 'secret'}
        with patch.dict(os.environ, env):
            sanitized = manager._environment()
        self.assertFalse(set(env) & set(sanitized))

    def test_successful_metadata_rpc_holds_admission_until_response_close(self):
        manager = self.manager()
        fake, start, unused_receive = self.arm_fake_transport(manager)
        def receive(process, deadline, cancelled):
            command = protocol.decode(fake.stdin.getvalue())
            metadata = worker.encode_body(manager.directory, command['id'], {'ok': True}, False, deadline)
            return dict(schema=protocol.SCHEMA, nonce=manager._nonce, generation=1,
                        id=command['id'], kind='response', ok=True,
                        source_fingerprint='c'*64, body=metadata)
        with start, patch.object(manager, '_receive', side_effect=receive), \
             patch.object(broker, 'source_stamp', return_value='c'*64):
            response = manager.request('recordings', {})
        self.assertEqual(json.loads(b''.join(response.chunks())), {'ok': True})
        slots = [manager._slots.acquire(blocking=False) for _ in range(protocol.MAX_WAITERS)]
        self.assertEqual(slots, [True, True, True, False])
        for acquired in slots:
            if acquired: manager._slots.release()
        self.assertTrue((manager.directory/(response.id+'.body')).is_file())
        response.close()
        self.assertFalse((manager.directory/(response.id+'.body')).exists())

    def test_admission_is_bounded_and_does_not_start_a_worker(self):
        manager = self.manager()
        for _ in range(protocol.MAX_WAITERS):
            self.assertTrue(manager._slots.acquire(blocking=False))
        try:
            with self.assertRaises(protocol.ArchiveProcessError) as error:
                manager.request('recordings', {})
            self.assertEqual(error.exception.status, 429)
            self.assertIsNone(manager._process)
        finally:
            for _ in range(protocol.MAX_WAITERS):
                manager._slots.release()

    def test_timeout_and_cancel_fail_before_process_or_response_publication(self):
        manager = self.manager()
        with self.assertRaises(protocol.ArchiveProcessError) as error:
            manager.request('recordings', {}, cancelled=lambda: True)
        self.assertEqual(error.exception.status, 499)
        with self.assertRaises(protocol.ArchiveProcessError) as error:
            manager._check(time.monotonic() - 1, None)
        self.assertEqual(error.exception.status, 504)
        self.assertIsNone(manager._process)

    def test_queued_deadline_releases_admission_without_launch(self):
        manager = self.manager()
        serial = Mock()
        serial.acquire.return_value = False
        with patch.object(manager, '_serial', serial), patch.object(manager, '_check', side_effect=[None, protocol.ArchiveProcessError('archive_process_timeout', 504)]):
            with self.assertRaises(protocol.ArchiveProcessError):
                manager.request('recordings', {})
        serial.release.assert_not_called()
        acquired = [manager._slots.acquire(blocking=False) for _ in range(protocol.MAX_WAITERS)]
        self.assertEqual(acquired, [True] * protocol.MAX_WAITERS)
        for _ in acquired:
            manager._slots.release()

    def test_stale_generation_id_or_nonce_cannot_publish_a_response(self):
        for wrong in ({'generation': 0}, {'id': '0' * 32}, {'nonce': '0' * 64}):
            with self.subTest(wrong=wrong):
                manager = self.manager()
                fake, start, receive = self.arm_fake_transport(manager, wrong)
                with start, receive, patch.object(broker, 'source_stamp', return_value='c' * 64):
                    with self.assertRaises(protocol.ArchiveProcessError) as error:
                        manager.request('recordings', {})
                self.assertIn('epoch', error.exception.code)
                self.assertIn('terminate', fake.calls)
                self.assertEqual(manager._responses, set())

    def test_source_change_cannot_open_or_publish_an_encoded_body(self):
        manager = self.manager()
        fake, start, receive = self.arm_fake_transport(manager)
        with start, receive, patch.object(broker, 'source_stamp', side_effect=['c' * 64, 'e' * 64]), patch.object(broker, 'open_response') as opened:
            with self.assertRaises(protocol.ArchiveProcessError):
                manager.request('recordings', {})
        opened.assert_not_called()
        self.assertIn('terminate', fake.calls)

    def test_worker_crash_and_protocol_fault_are_rejected_without_wait_loop(self):
        manager = self.manager()
        fake = FakeProcess()
        fake.returncode = 2
        manager._process = fake
        with self.assertRaises(protocol.ArchiveProcessError):
            manager._receive(fake, time.monotonic() + 1, None)
        fake.returncode = None
        manager._fault = True
        with self.assertRaises(protocol.ArchiveProcessError):
            manager._receive(fake, time.monotonic() + 1, None)
        fake.returncode = 2

    def test_restart_budget_refuses_fourth_start_before_any_process(self):
        manager = self.manager()
        manager._generation = 3
        with self.assertRaises(protocol.ArchiveProcessError):
            manager._start(time.monotonic() + 1, None)
        self.assertIsNone(manager._process)

    def test_failed_termination_retains_owned_popen_for_a_later_cleanup(self):
        manager = self.manager()
        fake = FakeProcess()
        manager._process = fake
        fake.terminate_error = OSError('synthetic terminate failure')
        try:
            with self.assertRaises((OSError, protocol.ArchiveProcessError)):
                manager._retire(fake)
            self.assertIs(manager._process, fake)
            self.assertFalse(manager.cleanup_complete)
        finally:
            fake.terminate_error = None
            fake.returncode = 0
            # Teardown still proves the same retained handle is reclaimable.
            manager._process = fake

    def test_failed_kill_wait_retains_owned_popen(self):
        manager = self.manager()
        fake = FakeProcess()
        manager._process = fake
        fake.wait_error = subprocess.TimeoutExpired('synthetic-owned', 1)
        try:
            with self.assertRaises((subprocess.TimeoutExpired, protocol.ArchiveProcessError)):
                manager._retire(fake)
            self.assertIs(manager._process, fake)
        finally:
            fake.wait_error = None
            fake.returncode = 0
            manager._process = fake

    def test_close_is_idempotent_and_does_not_touch_a_neighbor_directory(self):
        manager = self.manager()
        neighbor = self.cache / 'unrelated'
        neighbor.mkdir()
        marker = neighbor / 'keep'
        marker.write_bytes(b'keep')
        directory = manager.directory
        manager.close()
        manager.close()
        self.assertTrue(manager.cleanup_complete)
        self.assertFalse(directory.exists())
        self.assertEqual(marker.read_bytes(), b'keep')

    def test_encoded_response_close_is_single_release_and_deadline_is_checked(self):
        owner = types.SimpleNamespace(closed=False, _finish_response=Mock())
        response = broker.EncodedArchiveResponse(owner, 'a' * 32, io.BytesIO(b'abc'), {}, time.monotonic() + 1)
        self.assertEqual(b''.join(response.chunks()), b'abc')
        response.close()
        response.close()
        owner._finish_response.assert_called_once_with(response)
        expired = broker.EncodedArchiveResponse(owner, 'b' * 32, io.BytesIO(b'x'), {}, time.monotonic() - 1)
        try:
            with self.assertRaises(protocol.ArchiveProcessError):
                list(expired.chunks())
        finally:
            expired.close()


class ProviderWorkerChecks(SmallFiles):
    def test_archive_factory_never_runs_live_initializer_or_device_apis(self):
        Provider = telemetry_class()
        with patch.object(Provider, '__init__', side_effect=AssertionError('NO_NORMAL_INIT')), \
             patch.object(Provider, 'fan_status', side_effect=AssertionError('NO_FAN')), \
             patch.object(Provider, '_read_cpu_times', side_effect=AssertionError('NO_PROC')), \
             patch.object(Provider, 'recording_status', side_effect=AssertionError('NO_RECORDER')), \
             patch.object(Provider, 'rover_status', side_effect=AssertionError('NO_MOTOR')), \
             patch('socket.socket', side_effect=AssertionError('NO_SOCKET')), \
             patch('urllib.request.urlopen', side_effect=AssertionError('NO_HTTP')):
            provider = Provider.for_archive(self.archive, self.cache)
        self.assertTrue(provider._archive_only)
        self.assertFalse(hasattr(provider, 'recorder_api_url'))
        self.assertFalse(hasattr(provider, 'docker_socket'))

    def synthetic_record(self):
        record = self.archive / 'synthetic'
        record.mkdir()
        metadata = {'id': 'synthetic', 'state': 'archived', 'kind': 'both', 'duration_s': 8,
                    'started_at': '2026-01-01T00:00:00+00:00', 'timeline': {'lidar_frames': 2, 'imu_frames': 2, 'camera_frames': 0}}
        point = dict(x_m=1.0, y_m=2.0, distance_m=2.236, quality=32, angle_deg=26.565)
        timeline = {'ok': True, 'duration_s': 8, 'lidar': {'frames': [{'t_s': 0.0, 'points': [point]}, {'t_s': 4.5, 'points': [point]}]},
                    'imu': {'frames': [{'t_s': 0.0, 'raw': {'frame_id': 'synthetic_imu', 'euler_deg': {'yaw': 0}, 'angular_velocity': {'x': 0, 'y': 0, 'z': 0}}},
                                       {'t_s': 4.5, 'raw': {'frame_id': 'synthetic_imu', 'euler_deg': {'yaw': 1}}}]},
                    'camera': {'frames': []}, 'gps': {'frames': []}, 'sync': {}}
        for name, value in (('metadata.json', metadata), ('timeline.json', timeline)):
            (record / name).write_text(json.dumps(value), encoding='utf-8')
        return record, timeline

    def test_worker_dispatch_encoded_profiles_match_existing_synthetic_timeline_api(self):
        Provider = telemetry_class()
        record, timeline = self.synthetic_record()
        original = (record / 'timeline.json').read_bytes()
        for index, profile in enumerate((None, 'dashboard-compact-v1', 'dashboard-polar-v2', 'dashboard-telemetry-v1')):
            with self.subTest(profile=profile):
                legacy = Provider.for_archive(self.archive, self.root / ('legacy-cache-' + str(index)))
                legacy._archive_only = False
                legacy._archive_cache_epoch = False
                provider = Provider.for_archive(self.archive, self.root / ('worker-cache-' + str(index)))
                provider._archive_cache_budget={'bytes_left':protocol.MAX_CACHE_BYTES,
                                                'files_left':protocol.MAX_CACHE_FILES}
                args = dict(recording_id='synthetic', start_s='0', duration_s='4', profile=profile)
                expected = legacy.recording_timeline(**args)
                actual = worker.dispatch(provider, 'timeline', args)
                self.assertTrue(actual['ok'])
                for compressed in (False, True):
                    directory = self.root / ('wire-' + str(index) + '-' + str(compressed))
                    directory.mkdir()
                    rid, metadata = self.body(directory, value=actual, compressed=compressed)
                    with protocol.open_response(directory, rid, metadata) as stream:
                        wire = stream.read()
                    if metadata['encoding'] == 'gzip':
                        wire = gzip.decompress(wire)
                    self.assertEqual(json.loads(wire), expected)
        self.assertEqual((record / 'timeline.json').read_bytes(), original)
        self.assertEqual(timeline['lidar']['frames'][0]['points'][0]['x_m'], 1.0)

    def test_epoch_jpeg_read_rejects_final_file_hardlink(self):
        Provider = telemetry_class()
        record, _ = self.synthetic_record()
        provider = Provider.for_archive(self.archive, self.cache)
        cache = provider._playback_cache_path(record, record/'timeline.json')
        frames = cache/'frames'
        frames.mkdir(parents=True)
        filename = 'frame-'+'a'*24+'.jpg'
        image = frames/filename
        image.write_bytes(b'small-jpeg')
        self.assertEqual(provider.recording_camera_frame('synthetic', filename)['path'], image.resolve())
        alias = self.root/'unrelated-jpeg'
        os.link(image, alias)
        self.assertIsNone(provider.recording_camera_frame('synthetic', filename))
        self.assertEqual(alias.read_bytes(), b'small-jpeg')

    def test_cache_reservation_refuses_before_any_write_when_budget_is_missing_or_spent(self):
        Provider=telemetry_class()
        provider=Provider.for_archive(self.archive,self.cache)
        with self.assertRaises(protocol.ArchiveProcessError):
            provider._archive_cache_reserve(1)
        provider._archive_cache_budget={'bytes_left':3,'files_left':2}
        provider._archive_cache_reserve(3,2)
        with self.assertRaises(protocol.ArchiveProcessError):
            provider._archive_cache_reserve(1)
        self.assertFalse(self.cache.exists())

    def test_worker_encoding_gzip_and_expired_deadline_are_bounded(self):
        directory = self.root / 'responses'
        directory.mkdir()
        value = {'ok': True, 'text': 'small-synthetic-' * 100}
        rid, metadata = self.body(directory, value=value, compressed=True)
        self.assertEqual(metadata['encoding'], 'gzip')
        self.assertEqual(json.loads(gzip.decompress((directory / (rid + '.body')).read_bytes())), value)
        with self.assertRaises(protocol.ArchiveProcessError):
            worker.encode_body(directory, 'f' * 32, value, False, time.monotonic() - 1)

    def test_worker_main_defaults_off_without_parent_guard_or_provider_creation(self):
        with patch.object(sys, 'argv', ['archive_worker']), patch.object(sys, 'stdout', new_callable=io.StringIO) as stdout, \
             patch.object(worker, 'run', side_effect=AssertionError('NO_WORKER_RUNTIME')):
            self.assertEqual(worker.main(), 0)
        self.assertTrue(json.loads(stdout.getvalue())['default_OFF'])


if __name__ == '__main__':
    unittest.main()
