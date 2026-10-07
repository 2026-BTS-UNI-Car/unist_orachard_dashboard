"""Offline full 1080 native bag -> bounded child -> VFR/HLS sidecar smoke.

Synthetic frames only. No ROS graph, sensor device or control transport.
Run in a Linux ROS image with FFmpeg; dependency skips are not release passes.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from scripts.recording_postprocess import MediaQueue, atomic_json, source_hashes
from tests.test_native_jpeg_vfr import assert_hls_sample_timeline


PIPELINE_DEPENDENCIES = bool(shutil.which('ffmpeg') and shutil.which('ffprobe') and
                             importlib.util.find_spec('av') and
                             importlib.util.find_spec('rosbag2_py') and importlib.util.find_spec('rclpy'))
REQUIRED = os.environ.get('UBTS_REQUIRE_MEDIA_PIPELINE') == '1'


@unittest.skipUnless(PIPELINE_DEPENDENCIES or REQUIRED, 'ROS bag/FFmpeg/ffprobe release dependency required')
class NativePipelineTest(unittest.TestCase):
    def test_1080_vfr_native_camera_archive_to_verified_hls_preserves_sources(self):
        self.assertTrue(PIPELINE_DEPENDENCIES, 'required native media pipeline dependencies unavailable')
        import cv2
        import numpy as np
        import rosbag2_py
        from rclpy.serialization import serialize_message
        from sensor_msgs.msg import CompressedImage
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / 'synthetic_native'
            archive.mkdir()
            origin_ns = time.time_ns()
            metadata = dict(id=archive.name, kind='both', state='archived', duration_s=1.2,
                            started_at=datetime.fromtimestamp(origin_ns / 1e9, timezone.utc).isoformat())
            atomic_json(archive / 'metadata.json', metadata)
            (archive / 'timeline.json').write_text('{"synthetic":true}')
            (archive / 'motor.jsonl').write_text('{"t_s":0,"requested":{"left":0,"right":0}}\n')
            writer = rosbag2_py.SequentialWriter()
            writer.open(rosbag2_py.StorageOptions(uri=str(archive / 'rosbag2'), storage_id='sqlite3'),
                        rosbag2_py.ConverterOptions('', ''))
            topic = '/camera/recording/jpeg'
            fields = dict(name=topic, type='sensor_msgs/msg/CompressedImage', serialization_format='cdr')
            try:
                topic_metadata = rosbag2_py.TopicMetadata(id=0, **fields)  # Jazzy
            except TypeError:
                topic_metadata = rosbag2_py.TopicMetadata(**fields)  # Iron
            writer.create_topic(topic_metadata)
            intervals = [0., .063, .131, .197, .275, .331, .4, .463, .542, .6, .669, .733]
            for i, seconds in enumerate(intervals):
                image = np.zeros((1080, 1920, 3), dtype=np.uint8)
                image[:, :, i % 3] = 60 + i * 10
                image[90:240, 120:320] = [230, 160, 30]
                ok, jpeg = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 92])
                self.assertTrue(ok)
                stamp = origin_ns + int((seconds + .02) * 1e9)
                msg = CompressedImage()
                msg.header.stamp.sec = stamp // 1000000000
                msg.header.stamp.nanosec = stamp % 1000000000
                msg.header.frame_id = 'synthetic_camera'
                msg.format = 'jpeg; rotation=180'
                msg.data = jpeg.tobytes()
                writer.write(topic, serialize_message(msg), stamp)
            if hasattr(writer, 'close'):
                writer.close()
            del writer
            protected = source_hashes(archive)
            queue = MediaQueue(root, busy=lambda: None, start_worker=False)
            try:
                self.assertTrue(queue.enqueue(archive.name)['ok'])
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    queue.tick()
                    row = queue.status()['jobs'][0]
                    if row['state'] in ('complete', 'failed'):
                        break
                    time.sleep(.2)
                if row['state'] != 'complete':
                    logs = list((archive / 'generated_video_work').glob('*.log'))
                    detail = logs[-1].read_text()[-3500:] if logs else str(row)
                    self.fail('native child pipeline incomplete: ' + detail)
                self.assertEqual(row['frames'], len(intervals))
                self.assertEqual((row['width'], row['height']), (1920, 1080))
                self.assertEqual(source_hashes(archive), protected)
                manifest = json.loads((archive / 'adaptive_video/adaptive_video.json').read_text())
                self.assertEqual(manifest['source_topic'], topic)
                self.assertEqual((manifest['source_width'], manifest['source_height']), (1920, 1080))
                self.assertEqual(manifest['frame_timing'], 'recorded_camera_receipt_VFR')
                self.assertEqual(manifest['vfr_input']['kind'],'pyav_mjpeg_matroska')
                self.assertTrue(manifest['vfr_input']['source_jpeg_bytes_preserved'])
                self.assertEqual(manifest['source_frames'], len(intervals))
                self.assertAlmostEqual(manifest['fps'], (len(intervals) - 1) / intervals[-1], places=6)
                self.assertEqual({v['height'] for v in manifest['variants']}, {1080, 720, 480, 360, 240})
                self.assertAlmostEqual(manifest['timeline_offset_s'], .02, places=5)
                probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
                    '-show_streams','-show_frames','-show_format','-show_entries',
                    'stream=width,height:frame=best_effort_timestamp_time:format=duration','-of','json',
                    str(archive/'adaptive_video/native.mp4')],text=True))
                self.assertEqual((probe['streams'][0]['width'],probe['streams'][0]['height']),(1920,1080))
                stamps=[float(row['best_effort_timestamp_time']) for row in probe['frames']]
                expected=intervals+[manifest['vfr_input']['terminal_pts_s']]
                self.assertEqual(len(stamps),len(expected))
                for got,wanted in zip(stamps,expected):self.assertAlmostEqual(got,wanted,places=3)
                self.assertAlmostEqual(float(probe['format']['duration']),expected[-1]+.001,places=3)
                for variant in manifest['variants']:
                    assert_hls_sample_timeline(self,archive/'adaptive_video'/str(variant['height']),expected)
                    self.assertAlmostEqual(variant['measured_duration_s'],expected[-1]+.001,places=3)
                for name, value in manifest['assets'].items():
                    asset = archive / 'adaptive_video' / name
                    self.assertEqual(asset.stat().st_size, value['bytes'])
                    self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), value['sha256'])
            finally:
                queue.close()


if __name__ == '__main__':
    unittest.main()
