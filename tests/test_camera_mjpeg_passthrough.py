"""Original UVC MJPEG passthrough: recorded bytes unchanged, rotation declared,
decode only in the latest-only preview worker. Device-free; no hardware FPS claim.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

import cv2
import numpy as np

from scripts.camera_cadence import CameraCadence
from scripts.camera_mjpeg import (CAMERA_STAGES, StageCounters, decoded_image, jpeg_dimensions, jpeg_frame,
                                  original_jpeg_format, preview_reduction)
from scripts.camera_preview_worker import LatestCameraWorker
from tests.test_camera_cadence import DRIVER

# The stubbed driver load may hold its own camera_mjpeg module instance; its
# frame type is the one the driver dispatches on.
JpegFrame = DRIVER.JpegFrame

RED, GREEN = (0, 0, 255), (0, 255, 0)


def sensor_image(width=1920, height=1080):
    """Asymmetric sensor-orientation scene: red block at the top-left only."""
    image = np.full((height, width, 3), 40, np.uint8)
    image[:height // 4, :width // 4] = RED
    image[height // 2:, width - width // 8:] = GREEN
    return image


def encode(image, *flags):
    ok, data = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 90, *flags])
    assert ok
    return data.tobytes()


def device_row(data):
    return np.frombuffer(data, np.uint8).reshape(1, -1).copy()  # V4L2 CONVERT_RGB=0 shape.


def near(pixel, colour, tolerance=40):
    return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(pixel, colour))


def without_dht(data):
    """Motion-JPEG/AVI1 style frame: Huffman tables omitted (decoder defaults)."""
    out, i = bytearray(data[:2]), 2
    while data[i + 1] != 0xDA:
        length = data[i + 2] << 8 | data[i + 3]
        if data[i + 1] != 0xC4:
            out += data[i:i + 2 + length]
        i += 2 + length
    return bytes(out + data[i:])


def driver_args(**overrides):
    values = dict(device='auto', image_topic='/camera/image_raw', compressed_topic='/camera/image_jpeg',
                  recording_topic='/camera/recording/jpeg', recording_jpeg_quality=90,
                  camera_info_topic='/camera/camera_info', preview_camera_info_topic='/camera/preview/camera_info',
                  frame_id='camera_link_optical', width=1920, height=1080, fps=30, fourcc='MJPG', fov_deg=70,
                  rotate_180=True, status_file=None, settings_file=None, max_tick_fps=60, raw_publish_fps=5,
                  compressed_jpeg_quality=70, compressed_preview_width=1280, mjpeg_passthrough=True,
                  preview_decode_reduction='auto', status_interval_s=0.0, open_retry_interval_s=0.0,
                  wait_log_interval_s=60.0, auto_usb_recover=False, usb_recover_interval_s=12.0,
                  usb_recover_max_interval_s=120.0, authorized_cycle_every=3)
    values.update(overrides)
    return SimpleNamespace(**values)


def built_driver(case, **overrides):
    """The production __init__ with ROS node/QoS shapes stubbed out."""
    publishers = {}
    quiet = patch.object(DRIVER, 'log')
    case.log = quiet.start()
    case.addCleanup(quiet.stop)

    def create_publisher(self, kind, topic, qos):
        publishers[topic] = SimpleNamespace(topic=topic, publish=Mock(), get_subscription_count=lambda: 1)
        return publishers[topic]
    qos = dict(QoSProfile=lambda **fields: SimpleNamespace(**fields), HistoryPolicy=SimpleNamespace(KEEP_LAST=1),
               ReliabilityPolicy=SimpleNamespace(BEST_EFFORT=2), DurabilityPolicy=SimpleNamespace(VOLATILE=2))
    with patch.multiple(DRIVER, **qos), patch.object(DRIVER.Node, '__init__', lambda self, name: None), \
            patch.object(DRIVER.Node, 'create_publisher', create_publisher, create=True), \
            patch.object(DRIVER.Node, 'create_timer', lambda self, period, callback: None, create=True), \
            patch.object(DRIVER, 'usb_video_devices', return_value=[]), patch.object(DRIVER, 'video_nodes', return_value=[]):
        node = DRIVER.UsbCameraDriver(driver_args(**overrides))
    node.get_clock = lambda: SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: 'ros_stamp'))
    node.pubs = publishers
    case.addCleanup(node.native_encoder.close)
    case.addCleanup(node.preview_worker.close)
    return node


def streaming(node, read):
    node.passthrough_active = True
    node.capture_mode = 'passthrough'
    node.actual_width, node.actual_height = 1920, 1080
    node.capture_cadence = CameraCadence(1000)  # Never the limiter for these synthetic tick clocks.
    node.cap = SimpleNamespace(read=read, release=Mock())
    node.device = '/dev/video-test'
    return node


class FakeCapture:
    def __init__(self, frames, convert_supported=True, width=1920, height=1080):
        self.frames, self.convert_supported = list(frames), convert_supported
        self.size = {cv2.CAP_PROP_FRAME_WIDTH: width, cv2.CAP_PROP_FRAME_HEIGHT: height, cv2.CAP_PROP_FPS: 30}
        self.settings, self.released = [], False

    def isOpened(self):
        return True

    def set(self, prop, value):
        self.settings.append((prop, value))
        return prop != cv2.CAP_PROP_CONVERT_RGB or self.convert_supported

    def get(self, prop):
        return self.size.get(prop, 0)

    def read(self):
        return (True, self.frames.pop(0)) if self.frames else (False, None)

    def release(self):
        self.released = True


def open_with(node, capture):
    with patch.object(DRIVER, 'candidate_devices', lambda configured: iter(['/dev/video-test'])), \
            patch.object(DRIVER, 'list_camera_modes', lambda path: []), \
            patch.object(DRIVER.cv2, 'VideoCapture', lambda path, api: capture), \
            patch.object(DRIVER, 'video_nodes', return_value=['/dev/video-test']), \
            patch.object(DRIVER, 'usb_video_devices', return_value=[]):
        return node.open_camera()


class JpegHeaderTests(unittest.TestCase):
    def test_sof_size_and_strict_one_frame_buffers(self):
        data = encode(sensor_image())
        frame = jpeg_frame(device_row(data))
        self.assertEqual((frame.width, frame.height), (1920, 1080))
        self.assertEqual(frame.data, data)
        self.assertEqual(jpeg_frame(np.frombuffer(data, np.uint8)).data, data)
        progressive = encode(sensor_image(640, 360), cv2.IMWRITE_JPEG_PROGRESSIVE, 1)
        self.assertEqual(jpeg_dimensions(progressive), (640, 360))  # SOF2
        rejected = {
            'passthrough_buffer_not_one_uint8_row': [np.frombuffer(data, np.uint8)[:200].reshape(2, -1),
                                                     device_row(data).astype(np.float32), data],
            'jpeg_soi_missing': [device_row(data[2:]), device_row(b'\xff')],
            'jpeg_eoi_missing': [device_row(data[:-100]), device_row(data + b'\x00' * 16)],
            'jpeg_sof_missing': [device_row(b'\xff\xd8\xff\xd9'), device_row(b'\xff\xd8\xff\xda\x00\x02\xff\xd9')],
        }
        for reason, frames in rejected.items():
            for bad in frames:
                with self.assertRaisesRegex(ValueError, reason):
                    jpeg_frame(bad)
        self.assertTrue(decoded_image(sensor_image(8, 8)))
        self.assertFalse(decoded_image(device_row(data)))

    def test_dht_less_motion_jpeg_is_valid_and_decodes_for_preview(self):
        data = without_dht(encode(sensor_image(640, 360)))
        self.assertNotIn(b'\xff\xc4', data[:data.index(b'\xff\xda')])
        frame = jpeg_frame(device_row(data))
        self.assertEqual((frame.width, frame.height), (640, 360))
        for flag, shape in ((cv2.IMREAD_COLOR, (360, 640, 3)), (cv2.IMREAD_REDUCED_COLOR_2, (180, 320, 3))):
            decoded = cv2.imdecode(np.frombuffer(data, np.uint8), flag)
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.shape, shape)

    def test_format_and_reduction_policy(self):
        frame = JpegFrame(b'', 1920, 1080)
        self.assertEqual(original_jpeg_format(frame, True),
                         'jpeg; original_camera_mjpeg; rotation=180; width=1920; height=1080')
        self.assertIn('rotation=0;', original_jpeg_format(frame, False))
        self.assertEqual([preview_reduction('auto', w) for w in (3840, 1920, 1280, 640)], [2, 2, 1, 1])
        self.assertEqual([preview_reduction(v, 1920) for v in ('1', '2', '4')], [1, 2, 4])


class StageCounterTests(unittest.TestCase):
    def test_cumulative_counts_rolling_rate_bytes_and_decay(self):
        clock = SimpleNamespace(now=100.0)
        counters = StageCounters(('reads', 'idle'), window_s=5.0, clock=lambda: clock.now)
        self.assertIsNone(counters.status()['stages']['reads']['hz'])  # No interval yet.
        for _ in range(30):
            clock.now += .1
            counters.add('reads', 1000)
        status = counters.status()
        self.assertEqual(status['window_s'], 3.0)
        self.assertEqual(status['stages']['reads'], dict(total=30, hz=10.0, bytes=30000, bytes_per_s=10000.0))
        clock.now += 3.05  # Window now 5 s: only events after t=101.05 remain.
        self.assertEqual(counters.status()['stages']['reads']['hz'], 4.0)
        clock.now += 10.0
        self.assertEqual(counters.status()['stages']['reads'], dict(total=30, hz=0.0, bytes=30000, bytes_per_s=0.0))
        self.assertEqual(counters.status()['stages']['idle'], dict(total=0, hz=0.0))
        counters.add('late_stage')
        self.assertEqual(counters.total('late_stage'), 1)


class PassthroughPublicationTests(unittest.TestCase):
    def setUp(self):
        self.data = encode(sensor_image())
        self.sha = hashlib.sha256(self.data).hexdigest()

    def test_capture_tick_records_original_bytes_without_decode_rotate_or_encode(self):
        node = streaming(built_driver(self), lambda: (True, device_row(self.data)))
        node.preview_worker.close()
        node.preview_worker = LatestCameraWorker(lambda *args: None)  # Worker decode is tested separately.
        self.addCleanup(node.preview_worker.close)
        forbidden = AssertionError('pixel work on the capture thread')
        with patch.object(DRIVER.cv2, 'imdecode', side_effect=forbidden), \
                patch.object(DRIVER.cv2, 'rotate', side_effect=forbidden), \
                patch.object(DRIVER.cv2, 'imencode', side_effect=forbidden):
            node.tick()
        recording = node.pubs['/camera/recording/jpeg'].publish
        recording.assert_called_once()
        message = recording.call_args.args[0]
        self.assertEqual(hashlib.sha256(bytes(message.data)).hexdigest(), self.sha)
        self.assertEqual(message.format, 'jpeg; original_camera_mjpeg; rotation=180; width=1920; height=1080')
        self.assertEqual((message.header.stamp, message.header.frame_id), ('ros_stamp', 'camera_link_optical'))
        self.assertEqual(node.native_encoder.status()['published'] + node.native_encoder.status()['pending'], 0)
        self.assertEqual(node.frame_count, 1)
        stages = node.counters.status()['stages']
        for name in ('device_reads', 'passthrough_frames', 'recording_published'):
            self.assertEqual(stages[name]['total'], 1, name)
        self.assertEqual(stages['recording_published']['bytes'], len(self.data))
        self.assertEqual(stages['invalid_frames']['total'], 0)

    def test_unrotated_camera_declares_rotation_zero(self):
        node = streaming(built_driver(self, rotate_180=False), lambda: (True, device_row(self.data)))
        node.tick()
        message = node.pubs['/camera/recording/jpeg'].publish.call_args.args[0]
        self.assertIn('rotation=0;', message.format)
        self.assertEqual(bytes(message.data), self.data)

    def test_no_recording_subscriber_publishes_nothing_and_never_encodes(self):
        node = streaming(built_driver(self), lambda: (True, device_row(self.data)))
        node.pubs['/camera/recording/jpeg'].get_subscription_count = lambda: 0
        node.tick()
        self.assertTrue(node.preview_worker.wait_idle())
        node.pubs['/camera/recording/jpeg'].publish.assert_not_called()
        self.assertEqual(node.native_encoder.status()['published'], 0)
        self.assertEqual(node.counters.total('recording_published'), 0)

    def test_preview_is_reduced_decode_rotated_at_effective_size_and_raw_is_full(self):
        node = streaming(built_driver(self), lambda: (True, device_row(self.data)))
        node.tick()
        self.assertTrue(node.preview_worker.wait_idle())
        self.assertIsNone(node.preview_worker.status()['error'])
        preview = node.pubs['/camera/image_jpeg'].publish.call_args.args[0]
        self.assertEqual(preview.format, 'bgr8; jpeg compressed bgr8; width=960; height=540')
        image = cv2.imdecode(np.frombuffer(preview.data, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(image.shape, (540, 960, 3))
        self.assertTrue(near(image[-20, -20], RED))  # Sensor top-left is upright bottom-right.
        self.assertFalse(near(image[20, 20], RED))
        self.assertTrue(near(image[20, 20], GREEN))  # Sensor lower-right edge is upright upper-left.
        info = node.pubs['/camera/preview/camera_info'].publish.call_args.args[0]
        self.assertEqual((info.width, info.height), (960, 540))
        raw = node.pubs['/camera/image_raw'].publish.call_args.args[0]
        self.assertEqual((raw.width, raw.height, raw.encoding, raw.step), (1920, 1080, 'bgr8', 5760))
        full = np.frombuffer(raw.data, np.uint8).reshape(1080, 1920, 3)
        self.assertTrue(near(full[-40, -40], RED))
        self.assertFalse(near(full[40, 40], RED))
        raw_info = node.pubs['/camera/camera_info'].publish.call_args.args[0]
        self.assertEqual((raw_info.width, raw_info.height), (1920, 1080))
        node.write_status('streaming', force=True)
        stages = node.counters.status()['stages']
        for name in ('preview_decoded', 'preview_published', 'raw_published'):
            self.assertEqual(stages[name]['total'], 1, name)
        self.assertEqual(node.preview_effective, dict(effective_width=960, effective_height=540,
                                                      decode_reduction=2, source='original_jpeg'))

    def test_explicit_reductions_serve_requested_or_smaller_preview(self):
        for setting, size in (('1', (1280, 720)), ('4', (480, 270))):
            with self.subTest(setting=setting):
                node = built_driver(self, preview_decode_reduction=setting)
                generation = node.preview_worker.generation
                node.process_preview_frame(JpegFrame(self.data, 1920, 1080), 'ros_stamp', False, generation)
                preview = node.pubs['/camera/image_jpeg'].publish.call_args.args[0]
                self.assertIn(f'width={size[0]}; height={size[1]}', preview.format)
                decoded = cv2.imdecode(np.frombuffer(preview.data, np.uint8), cv2.IMREAD_COLOR)
                self.assertEqual(decoded.shape[:2], size[::-1])
                self.assertTrue(near(decoded[-8, -8], RED))
                node.pubs['/camera/image_raw'].publish.assert_not_called()  # Raw not due.

    def test_raw_full_decode_only_at_raw_cadence(self):
        node = streaming(built_driver(self, raw_publish_fps=4), lambda: (True, device_row(self.data)))
        clock = SimpleNamespace(now=50.0)
        decodes = []
        real = cv2.imdecode

        def counted(buffer, flag):
            decodes.append(flag)
            return real(buffer, flag)
        with patch.object(DRIVER.time, 'monotonic', side_effect=lambda: clock.now), \
                patch.object(DRIVER.cv2, 'imdecode', side_effect=counted):
            for _ in range(32):  # 1 s at exactly representable 1/32 s steps.
                node.tick()
                self.assertTrue(node.preview_worker.wait_idle())
                clock.now += 1 / 32
        self.assertEqual(node.counters.total('preview_published'), 32)
        self.assertEqual(node.counters.total('raw_published'), 4)
        self.assertEqual(node.raw_frame_count, 4)
        self.assertEqual(decodes.count(cv2.IMREAD_COLOR), 4)
        self.assertEqual(decodes.count(cv2.IMREAD_REDUCED_COLOR_2), 32)
        self.assertEqual(node.counters.total('recording_published'), 32)

    def test_invalid_buffers_are_read_failures_and_reopen_after_five(self):
        bad = iter([device_row(self.data[:-500]), device_row(b'not a jpeg'), device_row(self.data + b'\0'),
                    device_row(encode(sensor_image(1280, 720))), np.zeros((2, 8), np.uint8)])
        node = streaming(built_driver(self), lambda: (True, next(bad)))
        cap = node.cap
        clock = SimpleNamespace(now=10.0)
        with patch.object(DRIVER.time, 'monotonic', side_effect=lambda: clock.now):
            for _ in range(5):
                node.tick()
                clock.now += 1 / 30
        self.assertIsNone(node.cap)
        cap.release.assert_called_once()
        self.assertEqual(node.last_error, 'Camera read failed repeatedly; rescanning')
        self.assertEqual(node.counters.total('invalid_frames'), 5)
        self.assertEqual(node.counters.total('device_reads'), 5)
        self.assertEqual(node.counters.total('passthrough_frames'), 0)
        self.assertEqual(node.capture_cadence.status()['successful_reads'], 0)
        self.assertEqual(node.last_invalid_reason, 'passthrough_buffer_not_one_uint8_row')
        node.pubs['/camera/recording/jpeg'].publish.assert_not_called()
        self.assertEqual(node.frame_count, 0)

    def test_size_change_inside_stream_is_invalid(self):
        node = streaming(built_driver(self), lambda: (True, device_row(encode(sensor_image(1280, 720)))))
        node.tick()
        self.assertEqual((node.fail_count, node.last_invalid_reason), (1, 'jpeg_dimensions_changed'))

    def test_mid_stream_decoded_frame_switches_to_fallback_legacy_path(self):
        frame = sensor_image()
        node = streaming(built_driver(self), lambda: (True, frame.copy()))
        node.tick()
        self.assertEqual((node.capture_mode, node.mode_reason), ('fallback', 'backend_returned_decoded_bgr'))
        self.log.assert_any_call('capture_mode', mode='fallback', previous='passthrough',
                                 reason='backend_returned_decoded_bgr', device='/dev/video-test')
        node.native_encoder.queue.join()
        message = node.pubs['/camera/recording/jpeg'].publish.call_args.args[0]
        self.assertEqual(message.format, 'bgr8; jpeg compressed bgr8; width=1920; height=1080')
        upright = cv2.imdecode(np.frombuffer(message.data, np.uint8), cv2.IMREAD_COLOR)
        self.assertTrue(near(upright[-40, -40], RED))  # Legacy re-encode is already rotated.


class OpenModeTests(unittest.TestCase):
    def setUp(self):
        self.data = encode(sensor_image())

    def test_passthrough_open_uses_sof_size_and_records_first_frame(self):
        node = built_driver(self)
        capture = FakeCapture([device_row(self.data)] * 3, width=0, height=0)  # Driver reports no size.
        self.assertTrue(open_with(node, capture))
        self.assertIn((cv2.CAP_PROP_CONVERT_RGB, 0), capture.settings)
        self.assertEqual((node.capture_mode, node.actual_width, node.actual_height), ('passthrough', 1920, 1080))
        self.assertTrue(node.convert_rgb_disabled)
        message = node.pubs['/camera/recording/jpeg'].publish.call_args.args[0]
        self.assertEqual(bytes(message.data), self.data)
        node.capture_cadence = CameraCadence(30)  # Next slot without waiting a real period.
        node.tick()
        self.assertEqual(node.counters.total('recording_published'), 2)

    def test_partial_first_buffer_does_not_count_against_passthrough(self):
        node = built_driver(self)
        self.assertTrue(open_with(node, FakeCapture([device_row(self.data[:1000]), device_row(self.data)])))
        self.assertEqual((node.capture_mode, node.passthrough_open_invalid), ('passthrough', 0))
        self.assertEqual(node.counters.total('invalid_frames'), 1)
        self.assertEqual(node.counters.total('device_reads'), 2)

    def test_sof_size_other_than_requested_is_rejected(self):
        node = built_driver(self)
        capture = FakeCapture([device_row(encode(sensor_image(1280, 720)))])
        self.assertFalse(open_with(node, capture))
        self.assertTrue(capture.released)
        self.assertIsNone(node.cap)

    def test_backend_returning_bgr_falls_back_to_legacy_decoded_path(self):
        node = built_driver(self)
        with tempfile.TemporaryDirectory() as folder:
            node.status_file = Path(folder) / 'camera_driver_status.json'
            capture = FakeCapture([sensor_image(), sensor_image()], convert_supported=False)
            self.assertTrue(open_with(node, capture))
            status = json.loads(node.status_file.read_text(encoding='utf-8'))
        self.assertEqual(status['mode'], 'fallback')
        self.assertEqual(status['mjpeg_passthrough']['mode_reason'], 'backend_returned_decoded_bgr')
        self.assertIs(status['mjpeg_passthrough']['convert_rgb_disabled'], False)
        self.assertEqual(status['mjpeg_passthrough']['recording_payload'], 'reencoded_bgr_jpeg')
        node.capture_cadence = CameraCadence(30)
        node.tick()
        node.native_encoder.queue.join()
        self.assertEqual(node.native_encoder.status()['published'], 2)
        formats = {call.args[0].format for call in node.pubs['/camera/recording/jpeg'].publish.call_args_list}
        self.assertEqual(formats, {'bgr8; jpeg compressed bgr8; width=1920; height=1080'})

    def test_repeated_invalid_open_buffers_fall_back_until_settings_change(self):
        node = built_driver(self)
        for attempt in range(3):
            capture = FakeCapture([device_row(b'\xff\xd8garbage')] * 3)
            self.assertFalse(open_with(node, capture))
            self.assertIn((cv2.CAP_PROP_CONVERT_RGB, 0), capture.settings)
            self.assertEqual(node.passthrough_open_invalid, attempt + 1)
        capture = FakeCapture([sensor_image()])
        self.assertTrue(open_with(node, capture))
        self.assertNotIn(cv2.CAP_PROP_CONVERT_RGB, [prop for prop, _ in capture.settings])
        self.assertEqual((node.capture_mode, node.mode_reason), ('fallback', 'passthrough_open_frames_invalid'))
        self.assertEqual(node.counters.total('invalid_frames'), 9)
        with tempfile.TemporaryDirectory() as folder:
            node.settings_file = Path(folder) / 'camera_settings.json'
            node.settings_file.write_text(json.dumps(dict(width=1920, height=1080, fps=15)), encoding='utf-8')
            self.assertTrue(node.apply_settings_file(force=True))
        self.assertEqual(node.passthrough_open_invalid, 0)

    def test_disabled_or_non_mjpg_capture_is_legacy_and_never_touches_convert_rgb(self):
        for overrides, reason in ((dict(mjpeg_passthrough=False), 'passthrough_disabled'),
                                  (dict(fourcc='YUYV'), 'fourcc_YUYV_not_mjpg')):
            with self.subTest(reason=reason):
                node = built_driver(self, **overrides)
                capture = FakeCapture([sensor_image()])
                self.assertTrue(open_with(node, capture))
                self.assertNotIn(cv2.CAP_PROP_CONVERT_RGB, [prop for prop, _ in capture.settings])
                self.assertEqual((node.capture_mode, node.mode_reason, node.passthrough_active), ('legacy', reason, False))


class StatusTests(unittest.TestCase):
    def test_status_reports_stage_counters_preview_sizes_and_keeps_existing_fields(self):
        node = streaming(built_driver(self), lambda: (True, device_row(encode(sensor_image()))))
        node.tick()
        self.assertTrue(node.preview_worker.wait_idle())
        with tempfile.TemporaryDirectory() as folder:
            node.status_file = Path(folder) / 'camera_driver_status.json'
            node.write_status('streaming', force=True)
            status = json.loads(node.status_file.read_text(encoding='utf-8'))
        for key in ('state', 'requested', 'actual', 'frame_count', 'raw_frame_count', 'compressed_frame_count',
                    'native_recording', 'preview_worker', 'capture_cadence', 'compressed_preview_width', 'fail_count'):
            self.assertIn(key, status)
        self.assertEqual(status['mode'], 'passthrough')
        self.assertEqual(status['mjpeg_passthrough']['recording_payload'], 'original_camera_mjpeg')
        self.assertEqual(status['preview'], dict(requested_width=1280, decode_reduction_setting='auto',
                                                 effective_width=960, effective_height=540, decode_reduction=2,
                                                 source='original_jpeg'))
        stages = status['stage_counters']['stages']
        self.assertTrue(set(CAMERA_STAGES) <= set(stages))
        for name in CAMERA_STAGES:
            self.assertIn('total', stages[name])
            self.assertIn('hz', stages[name])
        self.assertIn('bytes_per_s', stages['recording_published'])

    def test_usb_inventory_is_cached_between_routine_status_writes(self):
        node = built_driver(self)
        clock = SimpleNamespace(now=1000.0)
        inventory = Mock(return_value=[dict(sys_name='1-2')])
        with patch.object(DRIVER, 'usb_video_devices', inventory), \
                patch.object(DRIVER.time, 'monotonic', side_effect=lambda: clock.now):
            node.usb_devices_cache = None
            for _ in range(5):
                node.write_status('streaming')
                clock.now += 1.0
            self.assertEqual(inventory.call_count, 1)
            node.write_status('reopen', force=True)  # Transitions rescan.
            self.assertEqual(inventory.call_count, 2)
            clock.now += 10.0
            node.write_status('streaming')
            self.assertEqual(inventory.call_count, 3)


ROS_BAG = bool(importlib.util.find_spec('rosbag2_py') and importlib.util.find_spec('rclpy'))
MEDIA = bool(ROS_BAG and importlib.util.find_spec('av') and shutil.which('ffmpeg') and shutil.which('ffprobe'))


def write_archive(root, name, formats, frames):
    import rosbag2_py
    from rclpy.serialization import serialize_message
    from sensor_msgs.msg import CompressedImage
    from scripts.recording_postprocess import atomic_json
    archive = Path(root) / name
    archive.mkdir()
    origin = time.time_ns()
    atomic_json(archive / 'metadata.json', dict(id=name, kind='both', state='archived', duration_s=1,
                started_at=datetime.fromtimestamp(origin / 1e9, timezone.utc).isoformat()))
    writer = rosbag2_py.SequentialWriter()
    writer.open(rosbag2_py.StorageOptions(uri=str(archive / 'rosbag2'), storage_id='sqlite3'),
                rosbag2_py.ConverterOptions('', ''))
    fields = dict(name='/camera/recording/jpeg', type='sensor_msgs/msg/CompressedImage', serialization_format='cdr')
    try:
        metadata = rosbag2_py.TopicMetadata(id=0, **fields)  # Jazzy
    except TypeError:
        metadata = rosbag2_py.TopicMetadata(**fields)  # Iron
    writer.create_topic(metadata)
    for index, (fmt, data) in enumerate(zip(formats, frames)):
        stamp = origin + 20_000_000 + index * 33_333_333
        message = CompressedImage()
        message.header.stamp.sec, message.header.stamp.nanosec = divmod(stamp, 1_000_000_000)
        message.format, message.data = fmt, data
        writer.write('/camera/recording/jpeg', serialize_message(message), stamp)
    if hasattr(writer, 'close'):
        writer.close()
    del writer
    return archive


@unittest.skipUnless(ROS_BAG, 'rosbag2_py/rclpy required (run in the ROS Iron image)')
class RecordedRotationTests(unittest.TestCase):
    def export(self, formats, frames):
        from scripts.export_recording_camera import export
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        archive = write_archive(root, 'archive', formats, frames)
        output = Path(root) / 'export.zip'
        result = export(archive, output)
        with zipfile.ZipFile(output) as bundle:
            manifest = json.loads(bundle.read('manifest.json'))
            stored = [bundle.read(row['file']) for row in manifest['frames']]
        return root, archive, output, result, manifest, stored

    def test_export_keeps_original_bytes_and_rotation_metadata(self):
        data = [encode(sensor_image()) for _ in range(3)]
        fmt = original_jpeg_format(JpegFrame(b'', 1920, 1080), True)
        _, _, _, result, manifest, stored = self.export([fmt] * 3, data)
        self.assertEqual(stored, data)
        self.assertEqual([row['sha256'] for row in manifest['frames']], [hashlib.sha256(d).hexdigest() for d in data])
        self.assertEqual((manifest['rotation_deg'], manifest['camera_payload']), (180, 'original_camera_mjpeg'))
        self.assertEqual((result['width'], result['height']), (1920, 1080))

    def test_old_recordings_without_rotation_flag_are_unchanged(self):
        upright = cv2.rotate(sensor_image(), cv2.ROTATE_180)
        data = [encode(upright)] * 2
        _, _, _, _, manifest, stored = self.export(['bgr8; jpeg compressed bgr8; width=1920; height=1080'] * 2, data)
        self.assertEqual(stored, data)
        self.assertEqual((manifest['rotation_deg'], manifest['camera_payload']), (0, 'reencoded_jpeg'))

    def test_rotation_change_inside_one_recording_is_rejected(self):
        from scripts.export_recording_camera import export
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        data = encode(sensor_image(320, 180))
        archive = write_archive(root, 'mixed', ['jpeg; original_camera_mjpeg; rotation=180; width=320; height=180',
                                                'bgr8; jpeg compressed bgr8; width=320; height=180'], [data, data])
        with self.assertRaisesRegex(ValueError, 'camera_mode_changed_inside_recording'):
            export(archive, Path(root) / 'export.zip')

    @unittest.skipUnless(MEDIA, 'PyAV/FFmpeg required for the adaptive build')
    def test_adaptive_build_applies_declared_rotation_to_original_frames(self):
        from scripts.build_adaptive_recording import build
        fmt = original_jpeg_format(JpegFrame(b'', 640, 360), True)
        # UVC cameras often omit Huffman tables (AVI1 Motion-JPEG); the lossless
        # PyAV remux and FFmpeg decode must accept both forms unchanged.
        for label, transform in (('standard', lambda data: data), ('dht_less', without_dht)):
            with self.subTest(frames=label):
                data = [transform(encode(sensor_image(640, 360))) for _ in range(4)]
                root, archive, output, _, _, _ = self.export([fmt] * 4, data)
                result = build(output, Path(root) / 'generated', archive_metadata=archive / 'metadata.json')
                self.assertEqual((result['source_width'], result['source_height']), (640, 360))
                self.assertTrue(result['vfr_input']['source_jpeg_bytes_preserved'])
                png = Path(root) / 'first.png'
                subprocess.run(['ffmpeg', '-v', 'error', '-i', str(Path(root) / 'generated/native.mp4'),
                                '-frames:v', '1', str(png)], check=True)
                first = cv2.imread(str(png))
                self.assertTrue(near(first[-10, -10], RED, 60))  # Sensor top-left shown bottom-right.
                self.assertFalse(near(first[10, 10], RED, 60))

if __name__ == '__main__':
    unittest.main()
