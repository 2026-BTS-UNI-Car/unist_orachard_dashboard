"""Linux lifecycle tests with disposable child processes and synthetic archives."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.recording_postprocess import (
    MediaQueue, SCHEMA, atomic_json, process, priority_reason,
    source_hashes, verify_sources,
)


def archive(root, name, *, finished=True):
    path = Path(root) / name
    (path / 'rosbag2').mkdir(parents=True)
    atomic_json(path / 'metadata.json', dict(id=name, kind='both', state='archived' if finished else 'recording'))
    (path / 'rosbag2/metadata.yaml').write_text('synthetic finished bag')
    (path / 'rosbag2/data.db3').write_bytes(b'synthetic raw sensor bytes')
    (path / 'motor.jsonl').write_text('{"requested":{"left":35,"right":35}}\n')
    (path / 'timeline.json').write_text('{"synthetic":true}')
    return path


def wait_for(condition, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(.02)
    raise AssertionError('disposable child did not reach expected state')


class MediaQueueTests(unittest.TestCase):
    def test_capacity_dedup_single_owner_and_no_legacy_backfill(self):
        with tempfile.TemporaryDirectory() as root:
            archive(root, 'legacy')
            for i in range(3):
                archive(root, 'new' + str(i))
            queue = MediaQueue(root, busy=lambda: 'recording_active', limit=2, start_worker=False)
            try:
                self.assertEqual(queue.status()['jobs'], [])
                self.assertTrue(queue.enqueue('new0')['ok'])
                self.assertTrue(queue.enqueue('new0')['existing'])
                self.assertTrue(queue.enqueue('new1')['ok'])
                self.assertEqual(queue.enqueue('new2')['error'], 'media_queue_full')
                queue.tick()
                self.assertIsNone(queue.process)
                self.assertTrue(all(job['reason'] == 'recording_active' for job in queue.status()['jobs'] if job['state'] != 'failed'))
                rejected = next(job for job in queue.status()['jobs'] if job['recording_id'] == 'new2')
                self.assertEqual(rejected['error'], 'media_queue_full')
                self.assertEqual(queue.status()['counts']['failed'], 1)
                with self.assertRaisesRegex(ValueError, 'owner_already_running'):
                    MediaQueue(root, busy=lambda: None, start_worker=False)
                self.assertFalse((Path(root) / 'legacy/video_preparation.json').exists())
            finally:
                queue.close()

    def test_priority_and_operator_pause_stop_whole_child_group(self):
        with tempfile.TemporaryDirectory() as root:
            path = archive(root, 'new')
            busy = [None]
            started = []
            def launch(_path, _attempt):
                counter = str(path / 'synthetic_counter.txt')
                child = subprocess.Popen([sys.executable, '-u', '-c',
                    "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
                    "exec('for i in range(10000):\\n p.write_text(str(i))\\n time.sleep(.02)')", counter],
                    start_new_session=True)
                started.append(child)
                return child
            queue = MediaQueue(root, busy=lambda: busy[0], launcher=launch, start_worker=False)
            try:
                queue.enqueue('new'); queue.tick()
                wait_for(lambda: (path / 'synthetic_counter.txt').exists())
                busy[0] = 'live_mapping_active'; queue.tick()
                time.sleep(.05)
                frozen = (path / 'synthetic_counter.txt').read_text()
                time.sleep(.12)
                self.assertEqual((path / 'synthetic_counter.txt').read_text(), frozen)
                self.assertEqual(queue.status()['jobs'][0]['state'], 'waiting_priority')
                busy[0] = None; queue.tick()
                wait_for(lambda: (path / 'synthetic_counter.txt').read_text() != frozen)
                queue.action('new', 'pause')
                queue.tick(); time.sleep(.05)
                frozen = (path / 'synthetic_counter.txt').read_text()
                time.sleep(.1)
                self.assertEqual((path / 'synthetic_counter.txt').read_text(), frozen)
                queue.action('new', 'resume'); queue.tick()
                wait_for(lambda: (path / 'synthetic_counter.txt').read_text() != frozen)
                queue.action('new', 'cancel')
                self.assertIsNotNone(started[0].poll())
                self.assertEqual(len(started), 1)
                self.assertEqual(queue.status()['jobs'][0]['state'], 'cancelled')
            finally:
                queue.close()

    def test_restart_resumes_marked_pending_preserves_pause_cancel_and_raw(self):
        with tempfile.TemporaryDirectory() as root:
            paths = [archive(root, name) for name in ('pending', 'paused', 'cancelled', 'legacy')]
            original = {path.name: source_hashes(path) for path in paths}
            queue = MediaQueue(root, busy=lambda: 'recording_active', start_worker=False)
            for name in ('pending', 'paused', 'cancelled'):
                queue.enqueue(name)
            queue.action('paused', 'pause'); queue.action('cancelled', 'cancel')
            queue.close()
            resumed = MediaQueue(root, busy=lambda: 'recording_active', start_worker=False)
            try:
                states = {row['recording_id']: row['state'] for row in resumed.status()['jobs']}
                self.assertEqual(states, dict(pending='queued', paused='paused', cancelled='cancelled'))
                self.assertEqual({path.name: source_hashes(path) for path in paths}, original)
                self.assertEqual(resumed.action('cancelled', 'resume')['job']['state'], 'queued')
            finally:
                resumed.close()

    def test_unfinished_path_escape_and_changed_db3_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = archive(root, 'new')
            archive(root, 'unfinished', finished=False)
            queue = MediaQueue(root, busy=lambda: 'recording_active', start_worker=False)
            try:
                for name in ('../new', '', 'new/../unfinished'):
                    with self.assertRaises(ValueError): queue.enqueue(name)
                with self.assertRaisesRegex(ValueError, 'finished_archive_required'):
                    queue.enqueue('unfinished')
                hashes = source_hashes(path)
                self.assertIn('rosbag2/data.db3', hashes)
                (path / 'rosbag2/data.db3').write_bytes(b'changed but same length bytes')
                with self.assertRaisesRegex(ValueError, 'raw_source_changed'):
                    verify_sources(path, hashes)
            finally:
                queue.close()

    def test_encoder_cannot_publish_after_raw_source_change(self):
        with tempfile.TemporaryDirectory() as root:
            path = archive(root, 'new')
            def mutate(*_args):
                (path / 'rosbag2/data.db3').write_bytes(b'changed during encode')
                return dict(duration_s=1, build_elapsed_s=.1)
            with patch('scripts.export_recording_camera.export', return_value=dict(frames=12, width=320, height=180)), \
                 patch('scripts.build_adaptive_recording.build', side_effect=mutate), \
                 patch('scripts.install_adaptive_recording.install') as install:
                with self.assertRaisesRegex(ValueError, 'raw_source_changed'):
                    process(path, 'a' * 32)
                install.assert_not_called()
            stage = json.loads((path / 'generated_video_work' / ('a' * 32) / 'progress.json').read_text())
            self.assertEqual(stage['stage'], 'failed')
            self.assertFalse((path / 'adaptive_video').exists())
            self.assertTrue((path / 'generated_video_work' / ('a' * 32) / 'source_sha256.json').exists())

    def test_existing_sidecar_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            path = archive(root, 'new')
            (path / 'adaptive_video').mkdir()
            (path / 'adaptive_video/adaptive_video.json').write_text('{"unverified":true}')
            before = (path / 'adaptive_video/adaptive_video.json').read_bytes()
            with patch('scripts.export_recording_camera.export') as export:
                with self.assertRaisesRegex(ValueError, 'requires_review'):
                    process(path, 'b' * 32)
                export.assert_not_called()
            self.assertEqual((path / 'adaptive_video/adaptive_video.json').read_bytes(), before)

    def test_priority_guard_checks_recorder_control_and_live_without_writes(self):
        with tempfile.TemporaryDirectory() as root:
            state = SimpleNamespace(lock=threading.Lock(), active=True, archive_dir=Path(root) / 'recordings')
            self.assertEqual(priority_reason(state), 'recording_active')
            state.active = False
            atomic_json(Path(root) / 'rover_bridge_status.json', dict(armed=True, updated_at=time.time()))
            self.assertEqual(priority_reason(state), 'rover_control_active')
            atomic_json(Path(root) / 'rover_bridge_status.json', dict(armed=False, moving=False, updated_at=time.time()))
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(dict(ok=True, jobs=[
                dict(id='live', tracking=True, active=True, processing=False)])).encode()
            with patch('urllib.request.urlopen', return_value=response) as get:
                self.assertEqual(priority_reason(state), 'live_mapping_active')
                self.assertEqual(get.call_args.args[0].get_method(), 'GET')
                response.__enter__.return_value.read.return_value = json.dumps(dict(ok=True, jobs=[
                    dict(id='live', tracking=False, active=False, processing=False)])).encode()
                self.assertIsNone(priority_reason(state))
            atomic_json(Path(root) / 'rover_bridge_status.json', dict(armed=False, updated_at=time.time() - 30))
            self.assertEqual(priority_reason(state), 'rover_status_unavailable')

    def test_child_main_one_core_caps_are_inherited_without_changing_runner(self):
        script = """
import json,os
import scripts.recording_postprocess as media
media.process=lambda archive,attempt: print(json.dumps(dict(cpus=list(os.sched_getaffinity(0)),
    threads=os.environ.get('UBTS_MEDIA_THREADS'),nice=os.nice(0))))
media.child_main('/unused', 'c'*32)
"""
        result = subprocess.run([sys.executable, '-c', script], start_new_session=True,
                                capture_output=True, text=True, check=True)
        value = json.loads(result.stdout)
        self.assertEqual(len(value['cpus']), 1)
        self.assertEqual(value['threads'], '1')
        self.assertGreaterEqual(value['nice'], 10)
        orphan = subprocess.run([sys.executable, '-c', script.replace(
            "media.child_main('/unused', 'c'*32)", "media.child_main('/unused', 'c'*32, -1)")],
            start_new_session=True, capture_output=True, text=True)
        self.assertEqual(orphan.returncode, -signal.SIGKILL)
        self.assertEqual(orphan.stdout, '')

    def test_idle_and_operator_paused_jobs_do_not_poll_priority_http(self):
        with tempfile.TemporaryDirectory() as root:
            archive(root, 'new')
            busy = unittest.mock.Mock(return_value=None)
            queue = MediaQueue(root, busy=busy, start_worker=False)
            try:
                for _ in range(100): queue.tick()
                busy.assert_not_called()
                queue.enqueue('new'); queue.action('new', 'pause')
                for _ in range(100): queue.tick()
                busy.assert_not_called()
                self.assertEqual(queue.status()['jobs'][0]['state'], 'paused')
            finally:
                queue.close()

    def test_complete_shaped_sidecar_without_source_hash_proof_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = archive(root, 'new')
            sidecar = path / 'adaptive_video'; sidecar.mkdir()
            assets = {}
            for name in ('master.m3u8', 'native.mp4'):
                data = name.encode(); (sidecar / name).write_bytes(data)
                assets[name] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            value = dict(schema='orchard.adaptive_video.v1', archive_id='new', complete=True,
                         master='master.m3u8', fallback='native.mp4', assets=assets, source_sha256={})
            atomic_json(sidecar / 'adaptive_video.json', value)
            with self.assertRaisesRegex(ValueError, 'requires_review'):
                process(path, 'd' * 32)
            self.assertEqual(json.loads((sidecar / 'adaptive_video.json').read_text()), value)


    def test_recorder_enqueues_only_after_finished_files_and_writer_close(self):
        from tests.test_data_recorder_service import DataRecorderCameraPipelineTests, RecordingState
        if RecordingState is None:
            self.skipTest('ROS recorder runtime required')
        with tempfile.TemporaryDirectory() as root:
            state = DataRecorderCameraPipelineTests().make_state(root)
            state.active = None
            checked = []
            def enqueue_finished(name):
                path = state.archive_dir / name
                self.assertEqual(json.loads((path / 'metadata.json').read_text())['state'], 'archived')
                self.assertTrue((path / 'timeline.json').is_file())
                self.assertTrue(state.finalizing)
                if (path / 'mapping_source/manifest.json').exists():
                    self.assertTrue(json.loads((path / 'mapping_source/manifest.json').read_text())['complete'])
                    self.assertIsNone(state.mapping_writer)
                checked.append(name)
                return dict(ok=True)
            state.media_queue = SimpleNamespace(enqueue=enqueue_finished)
            with patch.object(state, 'start_rosbag', return_value=None):
                state.start('both', include_imu=True, include_gps=False)
            result = state.stop()
            self.assertTrue(result['ok'])
            self.assertEqual(checked, [result['recording_id']])
            self.assertFalse(state.finalizing)

    def test_parent_death_wakes_and_terminates_a_frozen_converter(self):
        child_code = """
import os,pathlib,signal,sys,time
import scripts.recording_postprocess as media
def work(archive,attempt):
    pathlib.Path(archive).write_text(str(os.getpid()))
    os.kill(os.getpid(),signal.SIGSTOP)
    while True: time.sleep(.02)
media.process=work
media.child_main(sys.argv[1], 'e'*32, int(sys.argv[2]))
"""
        parent_code = """
import os,pathlib,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],str(os.getpid())],start_new_session=True)
path=pathlib.Path(sys.argv[2]);deadline=time.monotonic()+10
while not path.exists() and time.monotonic()<deadline: time.sleep(.01)
time.sleep(.05)
os._exit(0)
"""
        with tempfile.TemporaryDirectory() as root:
            ready = Path(root) / 'child_pid.txt'
            parent = subprocess.Popen([sys.executable, '-c', parent_code, child_code, str(ready)])
            parent.wait(timeout=12)
            self.assertTrue(ready.exists())
            pid = int(ready.read_text())
            def dead():
                try:
                    return Path('/proc', str(pid), 'stat').read_text().split(') ', 1)[1].split()[0] == 'Z'
                except FileNotFoundError:
                    return True
            try:
                wait_for(dead)
            finally:
                if not dead():
                    os.killpg(pid, signal.SIGKILL)

    def test_concurrent_stop_is_idempotent_and_start_cannot_cross_finalization(self):
        from concurrent.futures import ThreadPoolExecutor
        from tests.test_data_recorder_service import DataRecorderCameraPipelineTests, RecordingState
        if RecordingState is None:
            self.skipTest('ROS recorder runtime required')
        from scripts import data_recorder_service as recorder
        waiting, release = threading.Event(), threading.Event()
        class BagProcess:
            pid = 12345  # Test stub; never passed to a signal/device transport.
            code = None
            def poll(self): return self.code
            def send_signal(self, value): self.signal = value
            def wait(self, timeout):
                waiting.set()
                if not release.wait(timeout): raise subprocess.TimeoutExpired('synthetic_bag', timeout)
                self.code = 0
                return 0
        with tempfile.TemporaryDirectory() as root:
            state = DataRecorderCameraPipelineTests().make_state(root)
            state.active = None
            state.args.rosbag_stop_timeout_s = 5
            with patch.object(state, 'start_rosbag', return_value=BagProcess()):
                state.start('lidar', include_imu=True, include_gps=False)
            path = Path(state.active['path'])
            before = (path / 'metadata.json').read_bytes()
            with patch.object(recorder, 'write_json_atomic', wraps=recorder.write_json_atomic) as write:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    first = pool.submit(state.stop)
                    try:
                        self.assertTrue(waiting.wait(timeout=2))
                        repeated = state.stop()
                        self.assertEqual(repeated['state'], 'finalizing')
                        self.assertEqual(repeated['recording_id'], path.name)
                        self.assertFalse(state.start('both')['ok'])
                        self.assertEqual(state.start('both')['state'], 'finalizing')
                        self.assertTrue(state.status()['finalizing'])
                        self.assertEqual((path / 'metadata.json').read_bytes(), before)
                    finally:
                        release.set()
                    self.assertTrue(first.result(timeout=3)['ok'])
                metadata_writes = [call for call in write.call_args_list if Path(call.args[0]) == path / 'metadata.json']
                self.assertEqual(len(metadata_writes), 1)
                self.assertFalse(state.finalizing)
                self.assertEqual(state.status()['state'], 'idle')


    def test_terminal_history_is_bounded_and_explicit_pruned_job_resume_works(self):
        with tempfile.TemporaryDirectory() as root:
            queue = MediaQueue(root, busy=lambda: 'recording_active', start_worker=False)
            try:
                for i in range(24):
                    name = 'new' + str(i)
                    archive(root, name)
                    queue.enqueue(name); queue.action(name, 'cancel')
                self.assertEqual(len(queue.status()['jobs']), 16)
                self.assertTrue((Path(root) / 'new0/video_preparation.json').is_file())
                self.assertEqual(queue.action('new0', 'resume')['job']['state'], 'queued')
                self.assertEqual(queue.status()['counts']['queued'], 1)
                self.assertLessEqual(queue.status()['counts']['cancelled'], 16)
                archive(root, 'legacy')
                with self.assertRaisesRegex(ValueError, 'unknown_media_job'):
                    queue.action('legacy', 'resume')
                self.assertFalse((Path(root) / 'legacy/video_preparation.json').exists())
            finally:
                queue.close()


if __name__ == '__main__':
    unittest.main()
