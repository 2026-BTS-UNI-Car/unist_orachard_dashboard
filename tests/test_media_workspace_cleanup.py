"""Completed/failed/superseded media attempts: only content-proven files go."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.recording_postprocess import atomic_json, process

FRAMES = [b'\xff\xd8 original jpeg %d \xff\xd9' % i for i in range(3)]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def archive(root, name):
    path = Path(root) / name
    (path / 'rosbag2').mkdir(parents=True)
    atomic_json(path / 'metadata.json', dict(id=name, kind='both', state='archived'))
    (path / 'rosbag2/metadata.yaml').write_text('synthetic finished bag')
    (path / 'rosbag2/data.mcap').write_bytes(b'synthetic raw camera and sensor bytes')
    (path / 'motor.jsonl').write_text('{"requested":{"left":35,"right":35}}\n')
    return path


def raw_hashes(path):
    return {n: sha(path / n) for n in ('metadata.json', 'motor.jsonl', 'rosbag2/data.mcap', 'rosbag2/metadata.yaml')}


def write_export(path, output, *, complete=True):
    frames = [dict(file=f'frames/{i:07d}.jpg', bytes=len(d), sha256=hashlib.sha256(d).hexdigest())
              for i, d in enumerate(FRAMES)]
    with zipfile.ZipFile(output, 'w') as bundle:
        for row, data in zip(frames, FRAMES):
            bundle.writestr(row['file'], data)
        if complete:
            bundle.writestr('manifest.json', json.dumps(dict(
                schema='ubts.native_camera_export.v1', complete=True, all_frames_exported=True,
                archive_id=path.name, frames=frames, source_sha256=raw_hashes(path))))
    return dict(frames=len(frames), width=1920, height=1080)


def fake_build_factory(archive_path, *, export_sha=None):
    def build(source_zip, package, _ffmpeg, _metadata):
        package = Path(package)
        assets = {'master.m3u8': b'#EXTM3U\n240/index.m3u8\n', '240/index.m3u8': b'#EXTM3U\nseg0.m4s\n',
                  '240/seg0.m4s': b'segment bytes', 'native.mp4': b'fallback mp4 bytes'}
        for name, data in assets.items():
            (package / name).parent.mkdir(parents=True, exist_ok=True)
            (package / name).write_bytes(data)
        (package / 'frames').mkdir()
        for i, data in enumerate(FRAMES):  # build input copies, like the real builder
            (package / f'frames/{i:07d}.jpg').write_bytes(data)
        (package / 'native_frames.mkv').write_bytes(b'container of the same jpeg packets')
        (package / 'encoder.log').write_bytes(b'')
        bag = sorted(p for p in (archive_path / 'rosbag2').rglob('*') if p.is_file())
        manifest = dict(schema='orchard.adaptive_video.v1', complete=True, archive_id=archive_path.name,
                        master='master.m3u8', fallback='native.mp4', source_frames=3,
                        source_sha256=raw_hashes(archive_path),
                        source_signatures={p.relative_to(archive_path).as_posix(): [p.stat().st_size, p.stat().st_mtime_ns]
                                           for p in bag},
                        camera_export_sha256=export_sha or sha(source_zip),
                        assets={n: dict(bytes=len(d), sha256=hashlib.sha256(d).hexdigest()) for n, d in assets.items()})
        (package / 'adaptive_video.json').write_text(json.dumps(manifest))
        return dict(duration_s=1, build_elapsed_s=.1)
    return build


def run(path, attempt, *, container_ok=True, **build_options):
    with patch('scripts.export_recording_camera.export', side_effect=lambda a, o: write_export(Path(a), Path(o))), \
         patch('scripts.build_adaptive_recording.build', side_effect=fake_build_factory(path, **build_options)), \
         patch('scripts.recording_postprocess.frame_container_matches', return_value=container_ok):
        process(path, attempt)
    return path / 'generated_video_work' / attempt


def tree(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in Path(root).rglob('*') if p.is_file()}


class CompletedAttemptCleanupTests(unittest.TestCase):
    def test_completed_attempt_drops_only_content_proven_files(self):
        with tempfile.TemporaryDirectory() as root:
            path = archive(root, 'rec')
            raw_before = raw_hashes(path)
            work = run(path, 'a' * 32)
            progress = json.loads((work / 'progress.json').read_text())
            self.assertEqual(progress['stage'], 'complete')
            self.assertEqual(progress['cleanup']['workspace']['state'], 'pruned')
            for gone in ('source.zip', 'package/240/seg0.m4s', 'package/adaptive_video.json',
                         'package/frames/0000001.jpg', 'package/native_frames.mkv'):
                self.assertFalse((work / gone).exists(), gone)
            # Unknown file, evidence and the installed video stay.
            self.assertTrue((work / 'package/encoder.log').exists())
            for name in ('progress.json', 'source_sha256.json', 'cleanup.json'):
                self.assertTrue((work / name).exists(), name)
            installed = json.loads((path / 'adaptive_video/adaptive_video.json').read_text())
            for name, row in installed['assets'].items():
                self.assertEqual(sha(path / 'adaptive_video' / name), row['sha256'])
            self.assertEqual(raw_hashes(path), raw_before)
            record = json.loads((work / 'cleanup.json').read_text())
            reasons = {row['path']: row['reason'] for row in record['removed']}
            self.assertEqual(reasons['source.zip'], 'regenerable_export_recorded_by_installed_manifest')
            self.assertEqual(reasons['package/native.mp4'], 'duplicate_of_installed_adaptive_asset')
            self.assertEqual(reasons['package/frames/0000002.jpg'], 'build_input_original_frame_copy')
            self.assertEqual(reasons['package/native_frames.mkv'], 'build_input_original_frame_container')
            self.assertIn({'path': 'package/encoder.log', 'reason': 'unverified_purpose'}, record['retained'])

    def test_unrecorded_export_keeps_export_and_frame_copies(self):
        with tempfile.TemporaryDirectory() as root:
            path = archive(root, 'rec')
            work = run(path, 'b' * 32, export_sha='f' * 64)
            for kept in ('source.zip', 'package/frames/0000000.jpg', 'package/native_frames.mkv'):
                self.assertTrue((work / kept).exists(), kept)
            self.assertFalse((work / 'package/240/seg0.m4s').exists())  # still a proven duplicate
            retained = {row['path']: row['reason'] for row in json.loads((work / 'cleanup.json').read_text())['retained']}
            self.assertEqual(retained['source.zip'], 'not_a_verified_build_input')

    def test_changed_frame_package_file_or_container_is_retained(self):
        with tempfile.TemporaryDirectory() as root:
            path = archive(root, 'rec')
            from scripts.install_adaptive_recording import install as real_install
            def install_then_change(package, parent):
                result = real_install(package, parent)
                (Path(package) / '240/seg0.m4s').write_bytes(b'changed after install')
                (Path(package) / 'frames/0000001.jpg').write_bytes(b'not the original frame')
                return result
            with patch('scripts.install_adaptive_recording.install', side_effect=install_then_change):
                work = run(path, 'c' * 32, container_ok=False)
            retained = {row['path']: row['reason'] for row in json.loads((work / 'cleanup.json').read_text())['retained']}
            self.assertEqual(retained['package/240/seg0.m4s'], 'differs_from_installed_asset')
            self.assertEqual(retained['package/frames/0000001.jpg'], 'frame_not_proven_identical')
            self.assertEqual(retained['package/native_frames.mkv'], 'packets_not_proven_identical')
            self.assertFalse((work / 'package/frames/0000000.jpg').exists())

    def test_failed_attempt_keeps_everything(self):
        with tempfile.TemporaryDirectory() as root:
            path = archive(root, 'rec')
            with patch('scripts.export_recording_camera.export', side_effect=lambda a, o: write_export(Path(a), Path(o))), \
                 patch('scripts.build_adaptive_recording.build', side_effect=RuntimeError('encoder failed')):
                with self.assertRaises(RuntimeError):
                    process(path, 'd' * 32)
            work = path / 'generated_video_work' / ('d' * 32)
            self.assertEqual(json.loads((work / 'progress.json').read_text())['stage'], 'failed')
            self.assertTrue((work / 'source.zip').exists())
            self.assertFalse((work / 'cleanup.json').exists())


class FrameContainerProofTests(unittest.TestCase):
    def probe(self, hashes, code=0):
        from types import SimpleNamespace
        out = ''.join(f'SHA256:{h}\n' for h in hashes)
        with patch('scripts.recording_postprocess.subprocess.run', return_value=SimpleNamespace(returncode=code, stdout=out)):
            from scripts.recording_postprocess import frame_container_matches
            return frame_container_matches('native_frames.mkv', [dict(sha256=h) for h in 'abc'])

    def test_exact_packets_with_optional_terminal_repeat_only(self):
        self.assertTrue(self.probe('abc'))
        self.assertTrue(self.probe('abcc'))  # builder's terminal hold of the last frame
        self.assertFalse(self.probe('ab'))
        self.assertFalse(self.probe('abca'))
        self.assertFalse(self.probe('abcc', code=1))


class SupersededAttemptCleanupTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.path = archive(self.root.name, 'rec')
        work = self.path / 'generated_video_work'
        self.interrupted = work / ('1' * 32)
        (self.interrupted / 'package/frames').mkdir(parents=True)
        (self.interrupted / 'package/240').mkdir()
        atomic_json(self.interrupted / 'progress.json', dict(stage='encoding_adaptive_video'))
        (self.interrupted / 'source_sha256.json').write_text('{}')
        (self.interrupted / 'source.zip.partial').write_bytes(b'partial export, not provable')
        for i, data in enumerate(FRAMES):
            (self.interrupted / f'package/frames/{i:07d}.jpg').write_bytes(data)
        (self.interrupted / 'package/240/seg0.m4s').write_bytes(b'partial encode')
        (self.interrupted / 'package/notes.txt').write_text('unknown purpose')
        (work / ('1' * 32 + '.log')).write_text('')
        self.failed_export = work / ('3' * 32)
        self.failed_export.mkdir()
        atomic_json(self.failed_export / 'progress.json', dict(stage='failed'))
        write_export(self.path, self.failed_export / 'source.zip')
        self.older_complete = work / ('2' * 32)
        self.older_complete.mkdir()
        atomic_json(self.older_complete / 'progress.json', dict(stage='complete'))
        (self.older_complete / 'source.zip').write_bytes(b'legacy completed export')
        self.older_complete_before = tree(self.older_complete)

    def tearDown(self):
        self.root.cleanup()

    def test_success_prunes_only_content_proven_superseded_files(self):
        run(self.path, 'e' * 32)
        self.assertFalse((self.interrupted / 'package/frames/0000000.jpg').exists())
        self.assertFalse((self.failed_export / 'source.zip').exists())
        for kept in ('progress.json', 'source_sha256.json', 'source.zip.partial', 'package/240/seg0.m4s', 'package/notes.txt'):
            self.assertTrue((self.interrupted / kept).exists(), kept)
        self.assertTrue((self.path / 'generated_video_work' / ('1' * 32 + '.log')).exists())
        record = json.loads((self.interrupted / 'cleanup.json').read_text())
        self.assertEqual((record['policy'], record['superseded_by'], record['previous_stage']),
                         ('superseded_attempt', 'e' * 32, 'encoding_adaptive_video'))
        self.assertGreater(record['retained_bytes'], 0)
        self.assertEqual(json.loads((self.failed_export / 'cleanup.json').read_text())['removed'][0]['reason'],
                         'complete_export_of_current_raw_superseded')
        self.assertEqual(tree(self.older_complete), self.older_complete_before)

    def test_failure_does_not_touch_earlier_attempts(self):
        before = tree(self.interrupted), tree(self.failed_export)
        with patch('scripts.export_recording_camera.export', side_effect=lambda a, o: write_export(Path(a), Path(o))), \
             patch('scripts.build_adaptive_recording.build', side_effect=RuntimeError('encoder failed')):
            with self.assertRaises(RuntimeError):
                process(self.path, 'f' * 32)
        self.assertEqual((tree(self.interrupted), tree(self.failed_export)), before)
        self.assertEqual(tree(self.older_complete), self.older_complete_before)

    def test_already_installed_resume_releases_only_self_proven_exports(self):
        run(self.path, 'e' * 32)
        write_export(self.path, self.failed_export / 'source.zip')  # left again by a later failure
        (self.interrupted / 'package/frames').mkdir(parents=True, exist_ok=True)  # emptied dir was removed
        (self.interrupted / 'package/frames/0000000.jpg').write_bytes(FRAMES[0])
        run(self.path, '9' * 32)  # adaptive_video exists: verified already_installed path
        progress = json.loads((self.path / 'generated_video_work' / ('9' * 32) / 'progress.json').read_text())
        self.assertTrue(progress['already_installed'])
        self.assertFalse((self.failed_export / 'source.zip').exists())
        # No export frames are available on this path, so frame copies stay.
        self.assertTrue((self.interrupted / 'package/frames/0000000.jpg').exists())
        history = json.loads((self.failed_export / 'cleanup.json').read_text())['history']
        self.assertEqual(len(history), 1)


if __name__ == '__main__':
    unittest.main()
