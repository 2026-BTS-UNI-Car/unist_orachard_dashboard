"""Original UVC MJPEG passthrough: recorded bytes unchanged, rotation declared,
decode only in the latest-only preview worker. Device-free; no hardware FPS claim.
"""
from collections import deque
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

import cv2
import numpy as np

from scripts.camera_cadence import CameraCadence
from scripts.camera_mjpeg import (CAMERA_STAGES, StageCounters, decoded_image, jpeg_dimensions, jpeg_frame,
                                  jpeg_header, original_jpeg_format, preview_reduction, reencoded_jpeg_format)
from scripts.camera_preview_worker import LatestCameraWorker
# Import before any test patches time.monotonic: the driver imports this
# lazily, and `queue` binds `from time import monotonic` at its first import.
import scripts.native_camera_encoder  # noqa: E402,F401
from tests.test_camera_cadence import DRIVER

# The stubbed driver load may hold its own camera_mjpeg module instance; its
# frame type is the one the driver dispatches on.
JpegFrame = DRIVER.JpegFrame

RED, GREEN = (0, 0, 255), (0, 255, 0)
ROS_OFFSET_NS = 1_700_000_000_000_000_000
PASSTHROUGH_FORMAT = 'jpeg; original_camera_mjpeg; rotation=180; width=1920; height=1080'
FALLBACK_FORMAT = 'jpeg; reencoded_bgr_q90; rotation=180; width=1920; height=1080'
LEGACY_FORMAT = 'bgr8; jpeg compressed bgr8; width=1920; height=1080'


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


def sensor_oriented(image):
    """Red block top-left = sensor orientation (not yet rotated)."""
    h, w = image.shape[:2]
    return near(image[h // 16, w // 16], RED) and not near(image[-h // 16, -w // 16], RED)


def upright(image):
    """Red block bottom-right = rotated by 180 for viewing."""
    h, w = image.shape[:2]
    return near(image[-h // 16, -w // 16], RED) and not near(image[h // 16, w // 16], RED)


def decodes(data):
    try:
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is not None
    except cv2.error:
        return False


def segments(data):
    """[(marker, start, end)] up to and including SOS of a well-formed JPEG."""
    out, i = [], 2
    while True:
        marker, length = data[i + 1], data[i + 2] << 8 | data[i + 3]
        out.append((marker, i, i + 2 + length))
        if marker == 0xDA:
            return out
        i += 2 + length


def without_dht(data):
    """Motion-JPEG/AVI1 style frame: Huffman tables omitted (decoder defaults)."""
    sos = segments(data)[-1][1]
    kept = b''.join(data[s:e] for m, s, e in segments(data)[:-1] if m != 0xC4)
    return data[:2] + kept + data[sos:]


def stamp_ns(msg):
    return msg.sec * 1_000_000_000 + msg.nanosec


class FakeRosClock:
    """ROS time = (possibly patched) time.monotonic() + a fixed offset."""

    def now(self):
        ns = round(DRIVER.time.monotonic() * 1e9) + ROS_OFFSET_NS
        return SimpleNamespace(nanoseconds=ns, to_msg=lambda: SimpleNamespace(sec=0, nanosec=0))


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
    clock = FakeRosClock()
    node.get_clock = lambda: clock
    node.pubs = publishers
    case.addCleanup(node.native_encoder.close)
    case.addCleanup(node.preview_worker.close)
    return node


def streaming(node, read, get=None):
    """An already-open passthrough capture; without `get` there is no POS_MSEC."""
    node.capture_mode = 'passthrough'
    node.actual_width, node.actual_height = 1920, 1080
    node.capture_cadence = CameraCadence(1000)
    node.cap = SimpleNamespace(read=read, release=Mock(), **({} if get is None else dict(get=get)))
    node.device = '/dev/video-test'
    return node


def recorded(node):
    return [call.args[0] for call in node.pubs['/camera/recording/jpeg'].publish.call_args_list]


def flush(node):
    node.native_encoder.queue.join()
    node.preview_worker.wait_idle()


class FakeCapture:
    """Scripted OpenCV V4L2 stand-in. `fourcc` is the negotiated palette that
    CAP_PROP_FOURCC reports (None: the backend reports nothing)."""

    def __init__(self, frames, convert_supported=True, width=1920, height=1080, fourcc=None):
        self.frames, self.convert_supported = list(frames), convert_supported
        self.size = {cv2.CAP_PROP_FRAME_WIDTH: width, cv2.CAP_PROP_FRAME_HEIGHT: height, cv2.CAP_PROP_FPS: 30}
        if fourcc:
            self.size[cv2.CAP_PROP_FOURCC] = float(cv2.VideoWriter_fourcc(*fourcc))
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

    def convert_disabled(self):
        return (cv2.CAP_PROP_CONVERT_RGB, 0) in self.settings


class FakeV4L2:
    """A camera clock feeding a 4-buffer V4L2 queue on a fake monotonic clock.

    read() returns the oldest queued buffer at once, or advances the clock to
    the next arrival (a blocking DQBUF); a new frame is dropped when all four
    buffers are full, like uvcvideo. CAP_PROP_POS_MSEC is the dequeued
    buffer's arrival time in CLOCK_MONOTONIC milliseconds.
    """

    def __init__(self, clock, data, period=1 / 30, buffers=4, pos_ms=None):
        self.clock, self.data, self.period, self.buffers = clock, data, period, buffers
        self.next_arrival = clock.now + period
        self.queue, self.dropped, self.arrived, self.last = deque(), 0, 0, None
        self.pos_ms = pos_ms

    def arrive(self):
        while self.next_arrival <= self.clock.now + 1e-12:
            self.arrived += 1
            if len(self.queue) < self.buffers:
                self.queue.append(self.next_arrival)
            else:
                self.dropped += 1
            self.next_arrival += self.period

    def read(self):
        self.arrive()
        if not self.queue:
            self.clock.now = self.next_arrival
            self.arrive()
        self.last = self.queue.popleft()
        return True, device_row(self.data)

    def get(self, prop):
        if prop == cv2.CAP_PROP_POS_MSEC:
            return self.pos_ms(self.last) if self.pos_ms else self.last * 1000.0
        return 0

    def release(self):
        pass


def open_with(node, capture):
    with patch.object(DRIVER, 'candidate_devices', lambda configured: iter(['/dev/video-test'])), \
            patch.object(DRIVER, 'list_camera_modes', lambda path: []), \
            patch.object(DRIVER.cv2, 'VideoCapture', lambda path, api: capture), \
            patch.object(DRIVER, 'video_nodes', return_value=['/dev/video-test']), \
            patch.object(DRIVER, 'usb_video_devices', return_value=[]):
        return node.open_camera()


def fake_monotonic(case, start=1000.0):
    clock = SimpleNamespace(now=start)
    patcher = patch.object(DRIVER.time, 'monotonic', side_effect=lambda: clock.now)
    patcher.start()
    case.addCleanup(patcher.stop)
    return clock


class JpegHeaderTests(unittest.TestCase):
    def setUp(self):
        self.data = encode(sensor_image())

    def test_sof_size_and_strict_one_frame_buffers(self):
        frame = jpeg_frame(device_row(self.data))
        self.assertEqual((frame.width, frame.height, frame.trimmed_fill), (1920, 1080, 0))
        self.assertEqual(frame.data, self.data)
        self.assertEqual(jpeg_frame(np.frombuffer(self.data, np.uint8)).data, self.data)
        progressive = encode(sensor_image(640, 360), cv2.IMWRITE_JPEG_PROGRESSIVE, 1)
        self.assertEqual(jpeg_dimensions(progressive), (640, 360))  # SOF2
        restart = encode(sensor_image(640, 360), cv2.IMWRITE_JPEG_RST_INTERVAL, 4)
        self.assertEqual(jpeg_frame(device_row(restart)).data, restart)  # DRI + RSTn.
        rejected = {
            'passthrough_buffer_not_one_uint8_row': [np.frombuffer(self.data, np.uint8)[:200].reshape(2, -1),
                                                     device_row(self.data).astype(np.float32), self.data,
                                                     np.zeros((1080, 1920, 2), np.uint8), sensor_image(8, 8)],
            'jpeg_soi_missing': [device_row(self.data[2:]), device_row(b'\xff')],
            'jpeg_eoi_missing': [device_row(self.data[:-100]), device_row(self.data + b'junk'),
                                 device_row(self.data[:-100] + b'\x00' * 64),  # Truncated, then zero fill.
                                 device_row(self.data + b'\xff\xd8\x00\x00')],
            'jpeg_soi_or_eoi_before_sos': [device_row(b'\xff\xd8\xff\xd9')],
            'jpeg_sof_missing': [device_row(b'\xff\xd8\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\xff\xd9')],
        }
        for reason, frames in rejected.items():
            for bad in frames:
                with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                    jpeg_frame(bad)

    def test_header_must_reach_an_in_bounds_sos_libjpeg_would_accept(self):
        data = self.data
        parts = segments(data)
        sof = next(s for m, s, e in parts if m == 0xC0)
        sof_end = next(e for m, s, e in parts if m == 0xC0)
        sos, sos_end = parts[-1][1], parts[-1][2]
        dqt = [(s, e) for m, s, e in parts if m == 0xDB]
        thirty = bytearray(data)
        thirty[sof + 1] = 0xC3  # Lossless SOF3.
        components = bytearray(data)
        components[sos + 5] = 0x09  # Scan names a component the frame lacks.
        cases = [
            # Probes from the 86f7e6a review; each passed the old SOF+EOI check.
            ('jpeg_soi_or_eoi_before_sos', data[:sof_end] + b'\xff\xd9'),  # DHT/SOS lost after SOF.
            ('jpeg_soi_or_eoi_before_sos', data[:sos] + b'\xff\xd9'),  # Tables, no scan.
            ('jpeg_reserved_marker_before_sos', data[:sof_end] + b'\xff\x02\x00\x02' + data[sof_end:]),
            ('jpeg_soi_or_eoi_before_sos', data[:sof_end] + b'\xff\xd8' + data[sof_end:]),
            ('jpeg_sos_missing', data[:sof_end]),
            ('jpeg_sos_invalid', bytes(components)),
            ('jpeg_sof_unsupported', bytes(thirty)),
            ('jpeg_sof_duplicate', data[:sof_end] + data[sof:sof_end] + data[sof_end:]),
            ('jpeg_dqt_missing', b''.join([data[:2]] + [data[s:e] for m, s, e in parts if m != 0xDB]) + data[sos_end:]),
            ('jpeg_segment_out_of_bounds', data[:sof + 6]),
            ('jpeg_segment_out_of_bounds', data[:sos + 6] + b'\xff\xd9'),  # SOS header past the end.
        ]
        for reason, bad in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, reason):
                    jpeg_frame(device_row(bad))
                # libjpeg-turbo 2.1 (Ubuntu 22.04, the export decoder) cannot
                # decode any of these; 3.x (newer OpenCV wheels) adds lossless.
                if reason != 'jpeg_sof_unsupported' or cv2.__version__.startswith('4.5.'):
                    self.assertFalse(decodes(bad), 'validator rejects only frames libjpeg cannot decode')
        self.assertEqual(jpeg_header(data).scan_start, sos_end)
        self.assertTrue(dqt and decodes(data))

    def test_dht_less_motion_jpeg_and_app_thumbnail_are_valid_and_decode(self):
        data = without_dht(encode(sensor_image(640, 360)))
        self.assertNotIn(0xC4, [m for m, _, _ in segments(data)])
        frame = jpeg_frame(device_row(data))
        self.assertEqual((frame.width, frame.height), (640, 360))
        for flag, shape in ((cv2.IMREAD_COLOR, (360, 640, 3)), (cv2.IMREAD_REDUCED_COLOR_2, (180, 320, 3))):
            decoded = cv2.imdecode(np.frombuffer(data, np.uint8), flag)
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.shape, shape)
        thumbnail = encode(sensor_image(64, 36))
        app1 = b'\xff\xe1' + (2 + len(thumbnail)).to_bytes(2, 'big') + thumbnail  # Embedded SOF/EOI skipped.
        with_thumb = data[:2] + app1 + data[2:]
        self.assertEqual(jpeg_dimensions(with_thumb), (640, 360))
        # A buffer cut right after the thumbnail's EOI ends in FF D9 but has no
        # scan of its own; with fill after it the fill is not a marker either.
        cut = with_thumb[:2 + len(app1)]
        self.assertEqual(cut[-2:], b'\xff\xd9')
        for bad, reason in ((cut, 'jpeg_sos_missing'), (cut + b'\x00' * 32, 'jpeg_marker_expected'),
                            (with_thumb[:2 + len(app1) - 3], 'jpeg_segment_out_of_bounds')):
            with self.assertRaisesRegex(ValueError, reason):
                jpeg_frame(device_row(bad))

    def test_fill_after_final_eoi_is_trimmed_only_after_the_scan(self):
        for fill in (b'\x00' * 300, b'\xff' * 3, b'\x00\xff\x00'):
            with self.subTest(fill=fill[:4]):
                frame = jpeg_frame(device_row(self.data + fill))
                self.assertEqual((frame.data, frame.trimmed_fill), (self.data, len(fill)))
                self.assertTrue(decodes(frame.data))

    def test_decoded_image_is_only_hxwx3_uint8(self):
        self.assertTrue(decoded_image(sensor_image(8, 8)))
        for frame in (np.zeros((8, 8, 2), np.uint8), np.zeros((8, 8), np.uint8), np.zeros((8, 8, 3), np.float32),
                      np.zeros((8, 8, 4), np.uint8), device_row(self.data), None):
            self.assertFalse(decoded_image(frame))

    def test_format_and_reduction_policy(self):
        frame = JpegFrame(b'', 1920, 1080)
        self.assertEqual(original_jpeg_format(frame, True), PASSTHROUGH_FORMAT)
        self.assertIn('rotation=0;', original_jpeg_format(frame, False))
        self.assertEqual(reencoded_jpeg_format(90, True, 1920, 1080), FALLBACK_FORMAT)
        # auto never lowers the requested preview width: largest covering reduction.
        cases = {(1920, 1280): 1, (3840, 1280): 2, (1920, 960): 2, (1920, 640): 2, (1920, 320): 4,
                 (1280, 1280): 1, (640, 1280): 1}
        self.assertEqual({key: preview_reduction('auto', *key) for key in cases}, cases)
        self.assertEqual([preview_reduction(v, 1920, 1280) for v in ('1', '2', '4')], [1, 2, 4])


class StageCounterTests(unittest.TestCase):
    def test_cumulative_counts_rolling_rate_bytes_reasons_and_decay(self):
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
        counters.add('idle', reason='a')
        counters.add('idle', reason='a')
        counters.add('idle', reason='b')
        self.assertEqual(counters.status()['stages']['idle']['reasons'], dict(a=2, b=1))


class PassthroughPublicationTests(unittest.TestCase):
    def setUp(self):
        self.data = encode(sensor_image())
        self.sha = hashlib.sha256(self.data).hexdigest()

    def test_capture_tick_records_original_bytes_without_decode_rotate_or_encode(self):
        clock = fake_monotonic(self)
        camera = FakeV4L2(clock, self.data)
        node = streaming(built_driver(self), camera.read, camera.get)
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
        self.assertEqual(message.format, PASSTHROUGH_FORMAT)
        self.assertEqual(message.header.frame_id, 'camera_link_optical')
        # Header stamp: the dequeued buffer's CLOCK_MONOTONIC time mapped to ROS time.
        self.assertAlmostEqual(stamp_ns(message.header.stamp), round(camera.last * 1e9) + ROS_OFFSET_NS, delta=1000)
        self.assertEqual(node.last_stamp['source'], 'v4l2_buffer_monotonic')
        self.assertEqual(node.native_encoder.status()['published'] + node.native_encoder.status()['pending'], 0)
        self.assertEqual(node.frame_count, 1)
        stages = node.counters.status()['stages']
        for name in ('device_reads', 'passthrough_frames', 'recording_published'):
            self.assertEqual(stages[name]['total'], 1, name)
        self.assertEqual(stages['recording_published']['bytes'], len(self.data))
        self.assertEqual(stages['invalid_frames']['total'], 0)

    def test_blocking_read_drains_a_v4l2_backlog_and_keeps_buffer_spacing(self):
        clock = fake_monotonic(self)
        camera = FakeV4L2(clock, self.data)
        node = streaming(built_driver(self), camera.read, camera.get)
        node.preview_worker.close()
        node.preview_worker = LatestCameraWorker(lambda *args: None)
        self.addCleanup(node.preview_worker.close)
        for _ in range(10):  # Steady state: one fresh buffer per tick, nothing queued.
            node.tick()
            self.assertEqual(len(camera.queue), 0)
        clock.now += .140  # A capture-thread stall: four buffers queue up.
        camera.arrive()
        self.assertEqual((len(camera.queue), camera.dropped), (4, 0))
        node.tick()  # Reads every buffer older than 1.5 periods in one tick.
        self.assertLessEqual(len(camera.queue), 1)
        node.tick()
        self.assertEqual(len(camera.queue), 0, 'the backlog is drained, not kept')
        for _ in range(5):
            node.tick()
        self.assertEqual(camera.dropped, 0)
        stamps = [stamp_ns(m.header.stamp) for m in recorded(node)]
        self.assertEqual(len(stamps), camera.arrived - camera.dropped - len(camera.queue))
        self.assertEqual(len(set(stamps)), len(stamps), 'no duplicated frame')
        gaps = [(b - a) / 1e6 for a, b in zip(stamps, stamps[1:])]
        for gap in gaps:  # Real capture spacing, including the drained burst.
            self.assertAlmostEqual(gap, 1000 / 30, delta=.01)
        self.assertEqual(node.stamp_sources['read_completion'], 0)
        self.assertEqual(node.capture_cadence.status()['successful_reads'], len(stamps))

    def test_untrusted_buffer_timestamp_falls_back_to_read_completion(self):
        for pos_ms, reason in ((lambda ts: 0.0, 'v4l2_timestamp_unavailable'),
                               (lambda ts: 1.7e12 + ts * 1000, 'v4l2_timestamp_not_monotonic_clock_or_stale')):
            with self.subTest(reason=reason):
                clock = fake_monotonic(self)
                camera = FakeV4L2(clock, self.data, pos_ms=pos_ms)
                node = streaming(built_driver(self), camera.read, camera.get)
                node.tick()
                message = recorded(node)[-1]
                self.assertEqual(stamp_ns(message.header.stamp), round(clock.now * 1e9) + ROS_OFFSET_NS)
                self.assertEqual(node.last_stamp, dict(source='read_completion', buffer_age_ms=None,
                                                       untrusted_reason=reason))
                with tempfile.TemporaryDirectory() as folder:
                    node.status_file = Path(folder) / 'status.json'
                    node.write_status('streaming', force=True)
                    status = json.loads(node.status_file.read_text(encoding='utf-8'))
                self.assertEqual(status['capture_timestamp']['source'], 'read_completion')
                self.assertEqual(status['capture_timestamp']['sources']['read_completion'], 1)
                self.assertEqual(status['capture_pacing'], 'blocking_read_drain')

    def test_no_recording_subscriber_publishes_nothing_and_never_encodes(self):
        node = streaming(built_driver(self), lambda: (True, device_row(self.data)))
        node.pubs['/camera/recording/jpeg'].get_subscription_count = lambda: 0
        node.tick()
        self.assertTrue(node.preview_worker.wait_idle())
        node.pubs['/camera/recording/jpeg'].publish.assert_not_called()
        self.assertEqual(node.native_encoder.status()['published'], 0)
        self.assertEqual(node.counters.total('recording_published'), 0)

    def test_auto_preview_keeps_requested_width_and_raw_reuses_the_full_decode(self):
        node = streaming(built_driver(self), lambda: (True, device_row(self.data)))
        decodes_seen = []
        real = cv2.imdecode
        with patch.object(DRIVER.cv2, 'imdecode', side_effect=lambda b, f: decodes_seen.append(f) or real(b, f)):
            node.tick()
            self.assertTrue(node.preview_worker.wait_idle())
        self.assertIsNone(node.preview_worker.status()['error'])
        self.assertEqual(decodes_seen, [cv2.IMREAD_COLOR])  # One full decode serves preview and raw.
        preview = node.pubs['/camera/image_jpeg'].publish.call_args.args[0]
        self.assertEqual(preview.format, 'bgr8; jpeg compressed bgr8; width=1280; height=720')
        image = cv2.imdecode(np.frombuffer(preview.data, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(image.shape, (720, 1280, 3))
        self.assertTrue(upright(image))
        self.assertTrue(near(image[20, 20], GREEN))  # Sensor lower-right edge is upright upper-left.
        info = node.pubs['/camera/preview/camera_info'].publish.call_args.args[0]
        self.assertEqual((info.width, info.height), (1280, 720))
        raw = node.pubs['/camera/image_raw'].publish.call_args.args[0]
        self.assertEqual((raw.width, raw.height, raw.encoding, raw.step), (1920, 1080, 'bgr8', 5760))
        self.assertTrue(upright(np.frombuffer(raw.data, np.uint8).reshape(1080, 1920, 3)))
        raw_info = node.pubs['/camera/camera_info'].publish.call_args.args[0]
        self.assertEqual((raw_info.width, raw_info.height), (1920, 1080))
        stages = node.counters.status()['stages']
        for name in ('preview_decoded', 'preview_published', 'raw_published'):
            self.assertEqual(stages[name]['total'], 1, name)
        self.assertEqual(node.preview_effective, dict(effective_width=1280, effective_height=720,
                                                      decode_reduction=1, source='original_jpeg'))
        self.assertEqual(preview.header.stamp, raw.header.stamp)

    def test_explicit_reductions_trade_preview_size_for_cpu(self):
        for setting, size in (('1', (1280, 720)), ('2', (960, 540)), ('4', (480, 270))):
            with self.subTest(setting=setting):
                node = built_driver(self, preview_decode_reduction=setting)
                generation = node.preview_worker.generation
                node.process_preview_frame(JpegFrame(self.data, 1920, 1080), 'ros_stamp', False, generation)
                preview = node.pubs['/camera/image_jpeg'].publish.call_args.args[0]
                self.assertIn(f'width={size[0]}; height={size[1]}', preview.format)
                decoded = cv2.imdecode(np.frombuffer(preview.data, np.uint8), cv2.IMREAD_COLOR)
                self.assertEqual(decoded.shape[:2], size[::-1])
                self.assertTrue(upright(decoded))
                node.pubs['/camera/image_raw'].publish.assert_not_called()  # Raw not due.

    def test_raw_full_decode_only_at_raw_cadence(self):
        node = streaming(built_driver(self, raw_publish_fps=4, preview_decode_reduction='2'),
                         lambda: (True, device_row(self.data)))
        clock = SimpleNamespace(now=50.0)
        decodes_seen = []
        real = cv2.imdecode

        def counted(buffer, flag):
            decodes_seen.append(flag)
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
        self.assertEqual(decodes_seen.count(cv2.IMREAD_COLOR), 4)
        self.assertEqual(decodes_seen.count(cv2.IMREAD_REDUCED_COLOR_2), 32)
        self.assertEqual(node.counters.total('recording_published'), 32)

    def test_busy_preview_worker_overwrites_only_previews_never_recordings(self):
        node = streaming(built_driver(self, preview_decode_reduction='2'), lambda: (True, device_row(self.data)))
        gate = threading.Event()
        started = threading.Event()

        def blocked(*args):
            started.set()
            gate.wait(5)
            node.process_preview_frame(*args)
        node.preview_worker.close()
        node.preview_worker = LatestCameraWorker(blocked)
        self.addCleanup(node.preview_worker.close)
        node.tick()
        self.assertTrue(started.wait(5))
        for _ in range(9):
            node.tick()
        gate.set()
        self.assertTrue(node.preview_worker.wait_idle())
        totals = {name: node.counters.total(name) for name in CAMERA_STAGES}
        self.assertEqual(totals['recording_published'], 10)
        self.assertEqual(totals['preview_overwritten'], 8)
        self.assertEqual(totals['preview_decoded'], 2)
        self.assertEqual(totals['preview_overwritten'] + totals['preview_decoded'], node.preview_worker.submitted)
        self.assertEqual(node.preview_worker.status()['overwritten'], 8)

    def test_invalid_buffers_are_read_failures_and_a_mostly_invalid_reopen_is_evidence(self):
        bad = iter([device_row(self.data[:-500]), device_row(b'not a jpeg'), device_row(self.data + b'junk'),
                    device_row(encode(sensor_image(1280, 720))), np.zeros((2, 8), np.uint8)])
        node = streaming(built_driver(self), lambda: (True, next(bad)))
        cap = node.cap
        for _ in range(5):
            node.tick()
        self.assertIsNone(node.cap)
        cap.release.assert_called_once()
        self.assertEqual(node.last_error, 'Camera read failed repeatedly; rescanning')
        self.assertEqual(node.counters.total('invalid_frames'), 5)
        self.assertEqual(node.counters.status()['stages']['invalid_frames']['reasons'], {
            'jpeg_eoi_missing': 2, 'jpeg_soi_missing': 1, 'jpeg_dimensions_changed': 1,
            'passthrough_buffer_not_one_uint8_row': 1})
        self.assertEqual(node.counters.total('device_reads'), 5)
        self.assertEqual(node.counters.total('passthrough_frames'), 0)
        self.assertEqual(node.capture_cadence.status()['successful_reads'], 0)
        self.assertEqual(node.last_invalid_reason, 'passthrough_buffer_not_one_uint8_row')
        node.pubs['/camera/recording/jpeg'].publish.assert_not_called()
        self.assertEqual(node.frame_count, 0)
        self.assertEqual(node.passthrough_evidence, 1)
        self.assertEqual(node.passthrough_evidence_reasons, {'passthrough_stream_invalid_buffers': 1})

    def test_read_failures_reopen_without_fallback_evidence(self):
        node = streaming(built_driver(self), lambda: (False, None))
        for _ in range(5):
            node.tick()
        self.assertIsNone(node.cap)
        self.assertEqual(node.passthrough_evidence, 0)

    def test_padded_buffers_are_trimmed_and_recorded_as_the_jpeg(self):
        node = streaming(built_driver(self), lambda: (True, device_row(self.data + b'\x00' * 4096)))
        node.tick()
        message = recorded(node)[-1]
        self.assertEqual(bytes(message.data), self.data)
        trimmed = node.counters.status()['stages']['jpeg_fill_trimmed']
        self.assertEqual((trimmed['total'], trimmed['bytes']), (1, 4096))

    def test_size_change_inside_stream_is_invalid(self):
        node = streaming(built_driver(self), lambda: (True, device_row(encode(sensor_image(1280, 720)))))
        node.tick()
        self.assertEqual((node.fail_count, node.last_invalid_reason), (1, 'jpeg_dimensions_changed'))

    def test_mid_stream_non_jpeg_buffers_never_reach_the_bgr_path(self):
        for frame in (sensor_image(), np.zeros((1080, 1920, 2), np.uint8)):  # RGB24 raw / YUYV planes.
            with self.subTest(shape=frame.shape):
                node = streaming(built_driver(self), lambda: (True, frame.copy()))
                node.tick()
                flush(node)
                self.assertEqual((node.capture_mode, node.fail_count), ('passthrough', 1))
                self.assertEqual(node.last_invalid_reason, 'passthrough_buffer_not_one_uint8_row')
                node.pubs['/camera/recording/jpeg'].publish.assert_not_called()
                self.assertIsNone(node.native_encoder.status()['error'])
                self.assertIsNone(node.preview_worker.status()['error'])


class OrientationContractTests(unittest.TestCase):
    """One recording orientation per session, whatever the capture path."""

    def setUp(self):
        self.data = encode(sensor_image())

    def test_fallback_records_sensor_orientation_with_declared_rotation(self):
        node = built_driver(self)
        self.assertTrue(open_with(node, FakeCapture([sensor_image(), sensor_image()], convert_supported=False)))
        self.assertEqual((node.capture_mode, node.mode_reason), ('fallback', 'backend_rejected_convert_rgb_0'))
        node.capture_cadence = CameraCadence(30)
        node.tick()
        flush(node)
        messages = recorded(node)
        self.assertEqual([m.format for m in messages], [FALLBACK_FORMAT] * 2)
        for message in messages:
            self.assertTrue(sensor_oriented(cv2.imdecode(np.frombuffer(message.data, np.uint8), cv2.IMREAD_COLOR)))
        preview = node.pubs['/camera/image_jpeg'].publish.call_args.args[0]
        self.assertTrue(upright(cv2.imdecode(np.frombuffer(preview.data, np.uint8), cv2.IMREAD_COLOR)))
        raw = node.pubs['/camera/image_raw'].publish.call_args.args[0]
        self.assertTrue(upright(np.frombuffer(raw.data, np.uint8).reshape(1080, 1920, 3)))
        self.assertEqual(stamp_ns(messages[0].header.stamp) > 0, True)

    def test_unrotated_camera_declares_rotation_zero_in_both_paths(self):
        node = streaming(built_driver(self, rotate_180=False), lambda: (True, device_row(self.data)))
        node.tick()
        self.assertIn('rotation=0;', recorded(node)[-1].format)
        self.assertEqual(bytes(recorded(node)[-1].data), self.data)
        node = built_driver(self, rotate_180=False)
        self.assertTrue(open_with(node, FakeCapture([sensor_image()], convert_supported=False)))
        flush(node)
        self.assertEqual(recorded(node)[-1].format, 'jpeg; reencoded_bgr_q90; rotation=0; width=1920; height=1080')

    def test_passthrough_disabled_keeps_the_exact_legacy_contract(self):
        node = built_driver(self, mjpeg_passthrough=False)
        capture = FakeCapture([sensor_image(), sensor_image()])
        self.assertTrue(open_with(node, capture))
        self.assertFalse(capture.convert_disabled())
        self.assertEqual((node.capture_mode, node.mode_reason), ('legacy', 'passthrough_disabled'))
        node.capture_cadence = CameraCadence(30)
        node.tick()
        flush(node)
        messages = recorded(node)
        self.assertEqual([m.format for m in messages], [LEGACY_FORMAT] * 2)
        for message in messages:  # Pixels rotated by the driver, no rotation tag.
            self.assertTrue(upright(cv2.imdecode(np.frombuffer(message.data, np.uint8), cv2.IMREAD_COLOR)))
            self.assertEqual(message.header.stamp, SimpleNamespace(sec=0, nanosec=0))  # Publish-time to_msg().
        self.assertEqual(node.stamp_sources, dict(v4l2_buffer_monotonic=0, read_completion=0))

    def test_passthrough_fallback_passthrough_switch_keeps_orientation(self):
        messages = mixed_session_messages(self, self.data)
        kinds = [m.format.split(';')[1].strip() for m in messages]
        self.assertEqual(kinds, ['original_camera_mjpeg'] * 2 + ['reencoded_bgr_q90'] * 2 + ['original_camera_mjpeg'] * 2)
        for message in messages:
            self.assertIn('rotation=180;', message.format)
            image = cv2.imdecode(np.frombuffer(message.data, np.uint8), cv2.IMREAD_COLOR)
            self.assertTrue(sensor_oriented(image), message.format)


def mixed_session_messages(case, data):
    """Real driver path: passthrough, evidence-driven fallback, operator retry."""
    node = built_driver(case)
    case.assertTrue(open_with(node, FakeCapture([device_row(data)] * 2)))
    node.tick()
    case.assertEqual(node.capture_mode, 'passthrough')
    for _ in range(5):  # The camera starts returning raw planes: 5 invalid reads, reopen.
        node.cap.read = lambda: (True, np.zeros((1080, 1920, 2), np.uint8))
        node.tick()
    for _ in range(2):  # Two more opens with raw planes complete the evidence.
        case.assertFalse(open_with(node, FakeCapture([np.zeros((1080, 1920, 2), np.uint8)])))
    case.assertEqual(node.passthrough_fallback['reason'], 'passthrough_buffer_not_one_uint8_row')
    decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    capture = FakeCapture([decoded.copy(), decoded.copy()])
    case.assertTrue(open_with(node, capture))
    case.assertFalse(capture.convert_disabled())
    case.assertEqual((node.capture_mode, node.mode_reason), ('fallback', 'passthrough_fallback_engaged'))
    node.capture_cadence = CameraCadence(30)
    node.tick()
    flush(node)
    with tempfile.TemporaryDirectory() as folder:  # Operator re-saves the same settings.
        node.settings_file = Path(folder) / 'camera_settings.json'
        node.settings_file.write_text(json.dumps(dict(width=1920, height=1080, fps=30)), encoding='utf-8')
        node.apply_settings_file(force=True)
        os.utime(node.settings_file, ns=(time.time_ns(), node.settings_mtime_ns + 10_000_000))
        node.next_settings_check_at = 0
        case.assertTrue(node.apply_settings_file())
        node.settings_file = None
    case.assertIsNone(node.cap)
    case.assertIsNone(node.passthrough_fallback)
    case.assertTrue(open_with(node, FakeCapture([device_row(data)] * 2)))
    node.tick()
    flush(node)
    case.assertEqual(node.capture_mode, 'passthrough')
    return recorded(node)


class FallbackDecisionTests(unittest.TestCase):
    def setUp(self):
        self.data = encode(sensor_image())
        self.planes = np.zeros((1080, 1920, 2), np.uint8)

    def engage(self, node):
        for attempt in range(3):
            capture = FakeCapture([self.planes.copy()])
            self.assertFalse(open_with(node, capture))
            self.assertTrue(capture.convert_disabled())
            self.assertEqual(node.passthrough_evidence, attempt + 1)
        self.assertEqual(node.passthrough_fallback['reason'], 'passthrough_buffer_not_one_uint8_row')

    def test_raw_planes_count_and_fall_back_without_reaching_the_bgr_path(self):
        node = built_driver(self)
        self.engage(node)
        self.assertEqual(node.counters.status()['stages']['invalid_frames']['reasons'],
                         {'passthrough_buffer_not_one_uint8_row': 3})
        node.pubs['/camera/recording/jpeg'].publish.assert_not_called()
        capture = FakeCapture([sensor_image(), sensor_image()])
        self.assertTrue(open_with(node, capture))
        self.assertFalse(capture.convert_disabled())
        self.assertEqual((node.capture_mode, node.mode_reason), ('fallback', 'passthrough_fallback_engaged'))
        flush(node)
        self.assertEqual(recorded(node)[-1].format, FALLBACK_FORMAT)
        self.assertIsNone(node.native_encoder.status()['error'])
        self.assertIsNone(node.preview_worker.status()['error'])

    def test_no_soi_on_every_open_read_is_evidence_but_soi_buffers_are_not(self):
        node = built_driver(self)
        self.assertFalse(open_with(node, FakeCapture([device_row(b'garbage-without-soi')] * 3)))
        self.assertEqual(node.passthrough_evidence, 1)
        for prefix in (b'\xff\xd8garbage', self.data[:-500]):  # SOI but invalid / truncated.
            self.assertFalse(open_with(node, FakeCapture([device_row(prefix)] * 3)))
        self.assertEqual((node.passthrough_evidence, node.invalid_open_run), (1, 2))

    def test_read_failures_and_partial_starts_at_open_never_count(self):
        node = built_driver(self)
        for _ in range(10):
            self.assertFalse(open_with(node, FakeCapture([device_row(self.data[4000:])])))  # Mid-frame, then EOF.
            self.assertFalse(open_with(node, FakeCapture([device_row(self.data[:-500])])))  # Truncated, then EOF.
        self.assertEqual((node.passthrough_evidence, node.invalid_open_run, node.passthrough_fallback), (0, 0, None))
        self.assertTrue(open_with(node, FakeCapture([device_row(self.data[4000:]), device_row(self.data)])))
        self.assertEqual(node.capture_mode, 'passthrough')

    def test_persistently_invalid_opens_are_bounded(self):
        node = built_driver(self)
        for _ in range(15):
            self.assertFalse(open_with(node, FakeCapture([device_row(self.data + b'junk')] * 3)))
        self.assertEqual(node.passthrough_fallback['reason'], 'passthrough_opens_without_valid_frame')
        self.assertEqual(node.passthrough_evidence_reasons, {'passthrough_opens_without_valid_frame': 3})

    def test_streaming_invalid_buffers_cannot_loop_forever(self):
        node = built_driver(self)
        for cycle in range(3):  # Review repro: one valid open frame, then invalid buffers.
            capture = FakeCapture([device_row(self.data)] + [device_row(self.data + b'junk')] * 5)
            self.assertTrue(open_with(node, capture))
            for _ in range(5):
                node.tick()
            self.assertIsNone(node.cap)
            self.assertEqual(node.passthrough_evidence, cycle + 1)
        self.assertEqual(node.passthrough_fallback['reason'], 'passthrough_stream_invalid_buffers')
        capture = FakeCapture([sensor_image()])
        self.assertTrue(open_with(node, capture))
        self.assertEqual(node.capture_mode, 'fallback')

    def test_only_a_sustained_valid_run_clears_the_evidence(self):
        node = built_driver(self)
        for _ in range(2):
            self.assertFalse(open_with(node, FakeCapture([self.planes.copy()])))
        self.assertTrue(open_with(node, FakeCapture([device_row(self.data)] * 61)))
        for _ in range(58):
            node.tick()
        self.assertEqual((node.valid_run, node.passthrough_evidence), (59, 2))
        node.tick()
        self.assertEqual((node.valid_run, node.passthrough_evidence), (60, 0))

    def test_fallback_is_retried_on_a_reopen_after_the_retry_interval(self):
        clock = fake_monotonic(self)
        node = built_driver(self)
        self.engage(node)
        clock.now += 10
        capture = FakeCapture([sensor_image()])
        self.assertTrue(open_with(node, capture))
        self.assertFalse(capture.convert_disabled())
        clock.now += DRIVER.PASSTHROUGH_FALLBACK_RETRY_S
        capture = FakeCapture([device_row(self.data)] * 2)
        self.assertTrue(open_with(node, capture))
        self.assertTrue(capture.convert_disabled())
        self.assertEqual((node.capture_mode, node.passthrough_fallback), ('passthrough', None))
        self.assertEqual(node.passthrough_evidence, DRIVER.PASSTHROUGH_FALLBACK_AFTER - 1)
        clock.now += DRIVER.PASSTHROUGH_FALLBACK_RETRY_S + 1
        node.passthrough_fallback = dict(reason='x', engaged_at=0, engaged_monotonic=clock.now - 400)
        self.assertFalse(open_with(node, FakeCapture([self.planes.copy()])))  # Still unable: one open re-engages.
        self.assertEqual(node.passthrough_fallback['reason'], 'passthrough_buffer_not_one_uint8_row')

    def test_settings_write_clears_evidence_and_retries_a_fallback_capture(self):
        node = built_driver(self)
        with tempfile.TemporaryDirectory() as folder:
            node.settings_file = Path(folder) / 'camera_settings.json'
            node.settings_file.write_text(json.dumps(dict(width=1920, height=1080, fps=30)), encoding='utf-8')
            self.assertFalse(node.apply_settings_file(force=True))

            def rewrite():
                os.utime(node.settings_file, ns=(time.time_ns(), node.settings_mtime_ns + 10_000_000))
                node.next_settings_check_at = 0
                return node.apply_settings_file()
            self.assertFalse(open_with(node, FakeCapture([self.planes.copy()])))
            self.assertTrue(open_with(node, FakeCapture([device_row(self.data)] * 3)))
            self.assertFalse(rewrite())  # Evidence cleared; a passthrough capture is left open.
            self.assertEqual((node.passthrough_evidence, node.capture_mode), (0, 'passthrough'))
            node.close_camera()
            self.engage(node)
            self.assertTrue(open_with(node, FakeCapture([sensor_image()] * 3)))
            self.assertEqual(node.capture_mode, 'fallback')
            self.assertTrue(rewrite())
            self.assertIsNone(node.cap)
            self.assertEqual((node.passthrough_fallback, node.passthrough_evidence), (None, 0))
            self.assertTrue(open_with(node, FakeCapture([device_row(self.data)] * 2)))
            self.assertEqual(node.capture_mode, 'passthrough')
            node.settings_file = None

    def test_negotiated_palette_and_refused_convert_are_capture_level_fallbacks(self):
        node = built_driver(self)
        capture = FakeCapture([sensor_image()], fourcc='YUYV')
        self.assertTrue(open_with(node, capture))
        self.assertFalse(capture.convert_disabled())
        self.assertEqual((node.capture_mode, node.mode_reason), ('fallback', 'backend_fourcc_YUYV_not_mjpg'))
        capture = FakeCapture([device_row(self.data)] * 2, fourcc='MJPG')
        self.assertTrue(open_with(node, capture))
        self.assertEqual(node.capture_mode, 'passthrough')
        self.assertEqual((node.passthrough_evidence, node.passthrough_fallback), (0, None))

    def test_non_bgr_decoded_frame_in_fallback_is_a_failed_read(self):
        node = built_driver(self)
        self.assertFalse(open_with(node, FakeCapture([self.planes.copy()], convert_supported=False)))
        self.assertEqual(node.last_invalid_reason, 'decoded_frame_not_bgr')
        self.assertTrue(open_with(node, FakeCapture([sensor_image(), self.planes.copy()], convert_supported=False)))
        node.capture_cadence = CameraCadence(30)
        node.tick()
        flush(node)
        self.assertEqual(node.fail_count, 1)
        self.assertIsNone(node.native_encoder.status()['error'])

    def test_oversized_sof_never_raises_out_of_open(self):
        data = bytearray(encode(sensor_image(64, 48)))
        sof = next(s for m, s, e in segments(bytes(data)) if m == 0xC0)
        data[sof + 5:sof + 9] = (40000).to_bytes(2, 'big') * 2
        node = built_driver(self)
        capture = FakeCapture([device_row(bytes(data))] * 3)
        self.assertFalse(open_with(node, capture))  # Size mismatch: rejected without a decode.
        self.assertTrue(capture.released)
        node.request_width = node.request_height = 40000  # Even when requested, cv2.error is caught.
        probe = node.open_jpeg(FakeCapture([device_row(bytes(data))] * 2), device_row(bytes(data)), 0.0)
        self.assertEqual((probe.kind, probe.reason), ('invalid', 'jpeg_open_decode_failed'))

    def test_partial_first_buffer_does_not_count_against_passthrough(self):
        node = built_driver(self)
        self.assertTrue(open_with(node, FakeCapture([device_row(self.data[:1000]), device_row(self.data)])))
        self.assertEqual((node.capture_mode, node.passthrough_evidence), ('passthrough', 0))
        self.assertEqual(node.counters.total('invalid_frames'), 1)
        self.assertEqual(node.counters.total('device_reads'), 2)

    def test_passthrough_open_uses_sof_size_and_records_first_frame(self):
        node = built_driver(self)
        capture = FakeCapture([device_row(self.data)] * 3, width=0, height=0)  # Driver reports no size.
        self.assertTrue(open_with(node, capture))
        self.assertTrue(capture.convert_disabled())
        self.assertEqual((node.capture_mode, node.actual_width, node.actual_height), ('passthrough', 1920, 1080))
        self.assertTrue(node.convert_rgb_disabled)
        self.assertEqual(bytes(recorded(node)[-1].data), self.data)
        node.tick()
        self.assertEqual(node.counters.total('recording_published'), 2)

    def test_sof_size_other_than_requested_is_rejected(self):
        node = built_driver(self)
        capture = FakeCapture([device_row(encode(sensor_image(1280, 720)))])
        with patch.object(DRIVER.cv2, 'imdecode', side_effect=AssertionError('decoded an unexpected size')):
            self.assertFalse(open_with(node, capture))
        self.assertTrue(capture.released)
        self.assertIsNone(node.cap)

    def test_fourcc_other_than_mjpg_is_decoded_capture_with_the_session_contract(self):
        node = built_driver(self, fourcc='YUYV')
        capture = FakeCapture([sensor_image()])
        self.assertTrue(open_with(node, capture))
        self.assertFalse(capture.convert_disabled())
        self.assertEqual((node.capture_mode, node.mode_reason), ('fallback', 'fourcc_YUYV_not_mjpg'))
        flush(node)
        self.assertEqual(recorded(node)[-1].format, FALLBACK_FORMAT)


class StatusTests(unittest.TestCase):
    def status(self, node):
        with tempfile.TemporaryDirectory() as folder:
            node.status_file = Path(folder) / 'camera_driver_status.json'
            node.write_status('probe', force=True)
            value = json.loads(node.status_file.read_text(encoding='utf-8'))
        node.status_file = None
        return value

    def test_status_reports_real_stage_rates_preview_sizes_and_keeps_existing_fields(self):
        data = encode(sensor_image())
        clock = fake_monotonic(self, 100.0)
        node = streaming(built_driver(self), lambda: (True, device_row(data)))
        node.counters = StageCounters(clock=lambda: clock.now)
        for _ in range(32):
            node.tick()
            self.assertTrue(node.preview_worker.wait_idle())
            clock.now += 1 / 32
        status = self.status(node)
        for key in ('state', 'requested', 'actual', 'frame_count', 'raw_frame_count', 'compressed_frame_count',
                    'native_recording', 'preview_worker', 'capture_cadence', 'compressed_preview_width', 'fail_count'):
            self.assertIn(key, status)
        self.assertEqual(status['mode'], 'passthrough')
        self.assertEqual(status['preview'], dict(requested_width=1280, decode_reduction_setting='auto',
                                                 effective_width=1280, effective_height=720, decode_reduction=1,
                                                 source='original_jpeg'))
        counters = status['stage_counters']
        self.assertEqual(counters['window_s'], 1.0)
        stages = counters['stages']
        self.assertTrue(set(CAMERA_STAGES) <= set(stages))
        self.assertEqual(stages['recording_published'], dict(total=32, hz=32.0, bytes=32 * len(data),
                                                             bytes_per_s=32.0 * len(data)))
        for name, hz in (('device_reads', 32.0), ('passthrough_frames', 32.0), ('preview_decoded', 32.0),
                         ('preview_published', 32.0), ('raw_published', 5.0), ('invalid_frames', 0.0)):
            self.assertEqual(stages[name]['hz'], hz, name)

    def test_payload_and_activity_follow_the_open_capture(self):
        data = encode(sensor_image())
        node = built_driver(self)
        self.assertTrue(open_with(node, FakeCapture([device_row(data)] * 2)))
        status = self.status(node)['mjpeg_passthrough']
        self.assertEqual((status['active'], status['recording_payload'], status['recording_rotation_deg']),
                         (True, 'original_camera_mjpeg', 180))
        node.cap.read = lambda: (False, None)
        for _ in range(5):
            node.tick()
        status = self.status(node)
        self.assertEqual((status['opened'], status['mode'], status['capture_pacing']), (False, None, None))
        self.assertEqual(status['preview']['effective_width'], None)
        self.assertEqual({k: status['mjpeg_passthrough'][k] for k in
                          ('active', 'recording_payload', 'recording_orientation', 'convert_rgb_disabled')},
                         dict(active=False, recording_payload=None, recording_orientation=None,
                              convert_rgb_disabled=None))
        self.assertTrue(open_with(node, FakeCapture([sensor_image()] * 2, convert_supported=False)))
        status = self.status(node)
        self.assertEqual(status['capture_pacing'], 'cadence_slots')
        passthrough = status['mjpeg_passthrough']
        self.assertEqual({k: passthrough[k] for k in ('active', 'recording_payload', 'recording_orientation',
                                                      'recording_rotation_deg', 'mode_reason', 'convert_rgb_disabled')},
                         dict(active=False, recording_payload='reencoded_bgr_q90',
                              recording_orientation='sensor_pixels_rotation_declared', recording_rotation_deg=180,
                              mode_reason='backend_rejected_convert_rgb_0', convert_rgb_disabled=False))
        legacy = built_driver(self, mjpeg_passthrough=False)
        self.assertTrue(open_with(legacy, FakeCapture([sensor_image()])))
        passthrough = self.status(legacy)['mjpeg_passthrough']
        self.assertEqual((passthrough['active'], passthrough['recording_payload'], passthrough['recording_rotation_deg']),
                         (False, 'legacy_reencoded_bgr_jpeg', None))
        self.assertEqual(self.status(legacy)['capture_timestamp']['policy'], 'legacy_publish_time')

    def test_fallback_state_and_invalid_reasons_are_reported(self):
        node = built_driver(self)
        for _ in range(3):
            open_with(node, FakeCapture([np.zeros((1080, 1920, 2), np.uint8)]))
        status = self.status(node)
        passthrough = status['mjpeg_passthrough']
        self.assertEqual(passthrough['fallback']['reason'], 'passthrough_buffer_not_one_uint8_row')
        self.assertEqual(passthrough['fallback']['retry_on_reopen_in_s'], DRIVER.PASSTHROUGH_FALLBACK_RETRY_S)
        self.assertEqual((passthrough['fallback_evidence_streak'], passthrough['fallback_after']), (3, 3))
        self.assertEqual(passthrough['fallback_evidence_reasons'], {'passthrough_buffer_not_one_uint8_row': 3})
        self.assertEqual(status['stage_counters']['stages']['invalid_frames']['reasons'],
                         {'passthrough_buffer_not_one_uint8_row': 3})

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


def write_archive(root, name, formats, frames, receipts_ns=None, stamps_ns=None):
    """A finished archive; receipts/stamps are offsets (ns) from its start."""
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
        receipt = origin + 20_000_000 + (receipts_ns[index] if receipts_ns else index * 33_333_333)
        stamp = origin + 20_000_000 + (stamps_ns[index] if stamps_ns else receipt - origin - 20_000_000)
        message = CompressedImage()
        message.header.stamp.sec, message.header.stamp.nanosec = divmod(stamp, 1_000_000_000)
        message.format, message.data = fmt, data
        writer.write('/camera/recording/jpeg', serialize_message(message), receipt)
    if hasattr(writer, 'close'):
        writer.close()
    del writer
    return archive


def decoded_video_frames(path):
    import av
    with av.open(str(path)) as container:
        return [frame.to_ndarray(format='bgr24') for frame in container.decode(video=0)]


@unittest.skipUnless(ROS_BAG, 'rosbag2_py/rclpy required (run in the ROS Iron image)')
class RecordedRotationTests(unittest.TestCase):
    def export(self, formats, frames, **timing):
        from scripts.export_recording_camera import export
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        archive = write_archive(root, 'archive', formats, frames, **timing)
        output = Path(root) / 'export.zip'
        result = export(archive, output)
        with zipfile.ZipFile(output) as bundle:
            manifest = json.loads(bundle.read('manifest.json'))
            stored = [bundle.read(row['file']) for row in manifest['frames']]
        return root, archive, output, result, manifest, stored

    def build(self, root, archive, output):
        from scripts.build_adaptive_recording import build
        return build(output, Path(root) / 'generated', archive_metadata=archive / 'metadata.json')

    def test_export_keeps_original_bytes_and_rotation_metadata(self):
        data = [encode(sensor_image()) for _ in range(3)]
        _, _, _, result, manifest, stored = self.export([PASSTHROUGH_FORMAT] * 3, data)
        self.assertEqual(stored, data)
        self.assertEqual([row['sha256'] for row in manifest['frames']], [hashlib.sha256(d).hexdigest() for d in data])
        self.assertEqual((manifest['rotation_deg'], manifest['camera_payload']), (180, 'original_camera_mjpeg'))
        self.assertEqual(manifest['camera_payload_frames'], {'original_camera_mjpeg': 3})
        self.assertIs(manifest['original_camera_codec_preserved'], True)
        self.assertEqual((result['width'], result['height']), (1920, 1080))

    def test_old_recordings_without_rotation_flag_are_unchanged(self):
        data = [encode(cv2.rotate(sensor_image(), cv2.ROTATE_180))] * 2
        root, archive, output, _, manifest, stored = self.export([LEGACY_FORMAT] * 2, data)
        self.assertEqual(stored, data)
        self.assertEqual((manifest['rotation_deg'], manifest['camera_payload']), (0, 'reencoded_jpeg'))
        self.assertIs(manifest['original_camera_codec_preserved'], False)
        if MEDIA:
            self.build(root, archive, output)
            for frame in decoded_video_frames(Path(root) / 'generated/native.mp4'):
                self.assertTrue(upright(frame), 'untagged legacy frames are not flipped again')

    def test_payload_switch_inside_one_recording_exports_and_builds_upright(self):
        data = encode(sensor_image())
        messages = mixed_session_messages(self, data)
        formats, frames = [m.format for m in messages], [bytes(m.data) for m in messages]
        root, archive, output, result, manifest, stored = self.export(formats, frames)
        self.assertEqual(stored, frames)
        self.assertEqual((manifest['rotation_deg'], manifest['camera_payload']), (180, 'mixed'))
        self.assertEqual(manifest['camera_payload_frames'], {'original_camera_mjpeg': 4, 'reencoded_bgr_q90': 2})
        self.assertEqual(manifest['camera_payload_segments'], [
            dict(first_frame=0, frames=2, payload='original_camera_mjpeg'),
            dict(first_frame=2, frames=2, payload='reencoded_bgr_q90'),
            dict(first_frame=4, frames=2, payload='original_camera_mjpeg')])
        self.assertIs(manifest['original_camera_codec_preserved'], False)
        if not MEDIA:
            self.skipTest('PyAV/FFmpeg required for the adaptive build')
        public = self.build(root, archive, output)
        self.assertEqual((public['rotation_deg'], public['camera_payload']), (180, 'mixed'))
        video = decoded_video_frames(Path(root) / 'generated/native.mp4')
        self.assertEqual(len(video), len(frames) + 1)  # Plus the terminal hold.
        for index, frame in enumerate(video):
            self.assertTrue(upright(frame), f'output frame {index} is not upright')

    def test_rotation_change_inside_one_recording_is_rejected(self):
        from scripts.export_recording_camera import export
        data = encode(sensor_image(320, 180))
        for first in ('jpeg; original_camera_mjpeg; rotation=180; width=320; height=180',
                      'jpeg; reencoded_bgr_q90; rotation=180; width=320; height=180'):
            with self.subTest(first=first):
                root = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, root, ignore_errors=True)
                archive = write_archive(root, 'mixed', [first, 'bgr8; jpeg compressed bgr8; width=320; height=180'],
                                        [data, data])
                with self.assertRaisesRegex(ValueError, 'camera_mode_changed_inside_recording'):
                    export(archive, Path(root) / 'export.zip')

    def test_receipts_inside_one_millisecond_never_fail_the_recording(self):
        data = [encode(sensor_image(640, 360)) for _ in range(4)]
        fmt = original_jpeg_format(JpegFrame(b'', 640, 360), True)
        receipts = [0, 400_000, 66_000_000, 99_000_000]  # A drained burst: 0.4 ms apart at the recorder.
        for label, stamps, source, adjusted, pts in (
                ('capture_stamps', [0, 33_333_333, 66_666_667, 100_000_000], 'header_stamp', 0, [0, 33, 67, 100]),
                ('receipt_only', receipts, 'bag_receipt', 1, [0, 1, 66, 99])):
            with self.subTest(label=label):
                root, archive, output, _, manifest, _ = self.export([fmt] * 4, data, receipts_ns=receipts,
                                                                    stamps_ns=stamps)
                self.assertEqual(manifest['presentation_timing']['pts_source'], source)
                self.assertEqual(manifest['presentation_timing']['pts_adjusted_frames'], adjusted)
                self.assertEqual([round(row['t_s'] - manifest['frames'][0]['t_s'], 4) for row in manifest['frames']],
                                 [0.0, 0.0004, 0.066, 0.099])  # Receipts kept for alignment.
                if not MEDIA:
                    continue
                public = self.build(root, archive, output)
                self.assertEqual((public['vfr_input']['pts_source'], public['vfr_input']['pts_adjusted_frames']),
                                 (source, adjusted))
                probe = json.loads(subprocess.check_output(
                    ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'packet=pts_time',
                     '-of', 'json', str(Path(root) / 'generated/native.mp4')], text=True))
                got = [round(float(p['pts_time']) * 1000) for p in probe['packets']][:4]
                self.assertEqual(got, pts)

    @unittest.skipUnless(MEDIA, 'PyAV/FFmpeg required for the adaptive build')
    def test_adaptive_build_applies_declared_rotation_to_original_frames(self):
        fmt = original_jpeg_format(JpegFrame(b'', 640, 360), True)
        # UVC cameras often omit Huffman tables (AVI1 Motion-JPEG); the lossless
        # PyAV remux and FFmpeg decode must accept both forms unchanged.
        for label, transform in (('standard', lambda data: data), ('dht_less', without_dht)):
            with self.subTest(frames=label):
                data = [transform(encode(sensor_image(640, 360))) for _ in range(4)]
                root, archive, output, _, _, _ = self.export([fmt] * 4, data)
                result = self.build(root, archive, output)
                self.assertEqual((result['source_width'], result['source_height']), (640, 360))
                self.assertTrue(result['vfr_input']['source_jpeg_bytes_preserved'])
                self.assertEqual(result['frame_timing'], 'recorded_camera_header_stamp_VFR')
                png = Path(root) / 'first.png'
                subprocess.run(['ffmpeg', '-v', 'error', '-i', str(Path(root) / 'generated/native.mp4'),
                                '-frames:v', '1', str(png)], check=True)
                first = cv2.imread(str(png))
                self.assertTrue(near(first[-10, -10], RED, 60))  # Sensor top-left shown bottom-right.
                self.assertFalse(near(first[10, 10], RED, 60))


if __name__ == '__main__':
    unittest.main()
