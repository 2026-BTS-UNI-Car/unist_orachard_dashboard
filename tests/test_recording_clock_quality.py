"""Pure clock evidence and synthetic recorder lifecycle; no ROS/device I/O."""
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
import copy
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from dashboard_server.gps import GPS_SCHEMA, GnssDecoder, ubx_packet
from dashboard_server.recording_clock import (
    CLOCK_SAMPLE_KEYS, clock_sample, display_clock, sort_key, start_clock_snapshot,
    timestamp,
)

HOST_TEXT = '2026-10-07T00:00:00+00:00'
HOST_S = timestamp(HOST_TEXT)


def sample(**changes):
    result = dict(schema=GPS_SCHEMA, protocol='UBX_NAV_PVT', received_at=HOST_S-.25,
        gps_time='2026-10-07T09:00:00+00:00', gps_time_valid_date=True,
        gps_time_valid_time=True, gps_time_fully_resolved=True,
        gps_time_accuracy_s=.025, gps_time_fraction_ns=250_000_000,
        valid=False, latitude=None, longitude=None, fix_dimension=5)
    result.update(changes)
    return result


def snapshot(value=None, *, receipt=99.75, now=100., host=HOST_S, started=HOST_TEXT):
    return start_clock_snapshot(started, sample() if value is None else value,
        receipt, now_monotonic=now, host_now_s=host)


class StartClockEvidenceTests(unittest.TestCase):
    def test_no_position_fix_still_qualifies_utc_and_only_display_is_shifted(self):
        data = sample(latitude=35.57, longitude=129.19, driver={'secret':'not stored'})
        before = copy.deepcopy(data)
        value = snapshot(data)
        self.assertEqual(value['clock_quality'], 'gnss_receiver_estimated')
        self.assertEqual(value['display_started_at'], '2026-10-07T09:00:00.500000+00:00')
        self.assertEqual(value['original_started_at'], HOST_TEXT)
        self.assertAlmostEqual(value['offset_s'], 32400.5, places=6)
        self.assertFalse(value['evidence']['transport_latency_verified'])
        self.assertFalse(value['evidence']['host_clock_sync_verified'])
        self.assertNotIn('latitude', value['evidence'])
        self.assertNotIn('longitude', value['evidence'])
        self.assertNotIn('driver', value['evidence'])
        self.assertEqual(data, before)
        self.assertEqual(set(clock_sample(data)), set(CLOCK_SAMPLE_KEYS))
        with tempfile.TemporaryDirectory() as temp:
            metadata = dict(started_at=HOST_TEXT, clock_quality_snapshot=value,
                sensor_clock={'sec':123, 'nanosec':456}, playback_t_s=.75)
            original = copy.deepcopy(metadata)
            result = display_clock(temp, metadata)
            self.assertEqual(result['clock_quality'], 'gnss_receiver_estimated')
            self.assertEqual(result['display_started_at'], value['display_started_at'])
            self.assertEqual(metadata, original)

    def test_negative_signed_fraction_and_exact_time_accuracy_limit(self):
        value = snapshot(sample(gps_time_fraction_ns=-500_000_000,
            gps_time_accuracy_s=.05, received_at=HOST_S))
        self.assertEqual(value['display_started_at'], '2026-10-07T08:59:59.500000+00:00')
        self.assertEqual(value['clock_quality'], 'gnss_receiver_estimated')

    def test_invalid_flags_accuracy_fraction_and_calendar_are_unverified(self):
        cases = [dict(protocol='NMEA_RMC'), dict(protocol='UBX_NAV_HPPOSLLH'),
            dict(gps_time_valid_date=False), dict(gps_time_valid_time=1),
            dict(gps_time_fully_resolved=False), dict(gps_time_accuracy_s=.050001),
            dict(gps_time_accuracy_s=-1), dict(gps_time_accuracy_s=float('nan')),
            dict(gps_time_accuracy_s=float('inf')), dict(gps_time_accuracy_s=True),
            dict(gps_time_fraction_ns=float('nan')), dict(gps_time_fraction_ns=True),
            dict(gps_time_fraction_ns=1.5), dict(gps_time_fraction_ns=1_000_000_001),
            dict(gps_time_fraction_ns=-1_000_000_001), dict(gps_time='2026-10-07T09:00:00'),
            dict(gps_time='2026-10-07T09:00:00+09:00'),
            dict(gps_time='2026-10-07T09:00:00.100000Z'),
            dict(gps_time='2026-02-30T09:00:00Z'), dict(gps_time='bad'),
            dict(received_at=HOST_S+.001), dict(received_at=HOST_S-2.001),
            dict(received_at=float('nan')), dict(gps_time='2026-10-09T09:00:00Z')]
        for change in cases:
            with self.subTest(change=change):
                value = snapshot(sample(**change))
                self.assertEqual(value['clock_quality'], 'host_unverified')
                self.assertEqual(value['display_started_at'], HOST_TEXT)
                self.assertEqual(value['offset_s'], 0)
                json.dumps(value, allow_nan=False)

    def test_monotonic_age_future_nan_and_capture_clock_discontinuity(self):
        for changes in (dict(receipt=None), dict(receipt=100.001),
                dict(receipt=97.999), dict(receipt=float('nan')),
                dict(now=float('nan')), dict(host=float('inf')),
                dict(host=HOST_S+1.001)):
            with self.subTest(changes=changes):
                value = snapshot(**changes)
                self.assertEqual(value['clock_quality'], 'host_unverified')
                json.dumps(value, allow_nan=False)
        self.assertEqual(snapshot(receipt=98.)['clock_quality'], 'gnss_receiver_estimated')

    def test_snapshot_cannot_be_relabelled_verified_after_evidence_tampering(self):
        for key, bad in (('gps_time_accuracy_s', .2), ('gps_time_fully_resolved', False),
                         ('receiver_receipt_monotonic_s', 101.), ('gps_time_fraction_ns', float('nan'))):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp:
                value = snapshot(); value['evidence'][key] = bad
                metadata = dict(started_at=HOST_TEXT, clock_quality_snapshot=value)
                self.assertEqual(display_clock(temp, metadata)['clock_quality'], 'host_unverified')
        with tempfile.TemporaryDirectory() as temp:
            value = snapshot(); value['display_started_at'] = HOST_TEXT
            self.assertEqual(display_clock(temp, dict(started_at=HOST_TEXT,
                clock_quality_snapshot=value))['clock_quality'], 'host_unverified')

    def test_existing_sidecar_remains_first_and_old_archives_are_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = dict(started_at=HOST_TEXT, clock_quality_snapshot=snapshot())
            sidecar = dict(schema='ubts.recording_clock.v1', original_started_at=HOST_TEXT,
                display_started_at='2026-10-07T10:00:00Z', offset_s=36000,
                evidence={'source':'historical audit'})
            file = root/'recording_clock.json'
            file.write_text(json.dumps(sidecar), encoding='utf-8')
            before = file.read_bytes()
            result = display_clock(root, metadata)
            self.assertEqual(result['clock_quality'], 'gnss_estimated')
            self.assertEqual(result['display_started_at'], sidecar['display_started_at'])
            self.assertEqual(file.read_bytes(), before)
            sidecar['offset_s'] = 1
            file.write_text(json.dumps(sidecar), encoding='utf-8')
            self.assertEqual(display_clock(root, metadata)['clock_quality'], 'gnss_receiver_estimated')
            self.assertEqual(display_clock(root, dict(started_at=HOST_TEXT))['clock_quality'], 'host_clock')
            file.write_text('[]', encoding='utf-8')
            self.assertEqual(display_clock(root, metadata)['clock_quality'], 'gnss_receiver_estimated')

    def test_sort_uses_display_time_without_changing_original_and_handles_epoch_zero(self):
        rows = [dict(id='host', started_at='2026-10-07T08:00:00Z'),
                dict(id='gnss', started_at=HOST_TEXT,
                     display_started_at='2026-10-07T09:00:00Z')]
        before = copy.deepcopy(rows)
        self.assertEqual([r['id'] for r in sorted(rows, key=sort_key, reverse=True)], ['gnss','host'])
        self.assertEqual(rows, before)
        self.assertEqual(sort_key(dict(id='zero', display_started_at='1970-01-01T00:00:00Z',
            started_at=HOST_TEXT))[0], 0.)

    def test_real_nav_pvt_decoder_distinguishes_time_only_from_position_fix(self):
        body = bytearray(92)
        struct.pack_into('<H', body, 4, 2026)
        body[6:12] = bytes((10,7,9,0,0,7))
        struct.pack_into('<I', body, 12, 25_000_000)
        struct.pack_into('<i', body, 16, 250_000_000)
        body[20] = 5  # time-only fix; gnssFixOK is not a position qualification
        frame, = GnssDecoder(clock=lambda:HOST_S-.25).feed(ubx_packet(1,7,body))
        self.assertFalse(frame['valid'])
        self.assertIsNone(frame['latitude'])
        self.assertEqual(snapshot(frame)['clock_quality'], 'gnss_receiver_estimated')


def load_recorder():
    """Load production RecordingState with ROS import types only, no ROS calls."""
    source = Path(__file__).resolve().parents[1]/'scripts/data_recorder_service.py'
    spec = importlib.util.spec_from_file_location('_clock_synthetic_recorder', source)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as error:
        if error.name not in {'rclpy', 'sensor_msgs', 'std_msgs'}:
            raise
        names = ('rclpy', 'rclpy.node', 'rclpy.qos', 'sensor_msgs',
                 'sensor_msgs.msg', 'std_msgs', 'std_msgs.msg')
        stubs = {name:types.ModuleType(name) for name in names}
        stubs['rclpy.node'].Node = object
        for name in ('DurabilityPolicy','HistoryPolicy','QoSProfile','ReliabilityPolicy'):
            setattr(stubs['rclpy.qos'], name, object)
        for name in ('Image','Imu','LaserScan','MagneticField'):
            setattr(stubs['sensor_msgs.msg'], name, object)
        stubs['std_msgs.msg'].String = object
        with patch.dict(sys.modules, stubs):
            spec.loader.exec_module(module)
    return module


class SyntheticRecorderStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recorder = load_recorder()

    def make_state(self, temp):
        values = dict(archive_dir=str(Path(temp)/'records'), status_file=str(Path(temp)/'status.json'),
            camera_preview_width=1280, camera_jpeg_quality=70, camera_archive_flush_timeout_s=.1,
            timeline_hz=10., timeline_max_frames=100, imu_timeline_hz=30., imu_timeline_max_frames=100)
        for name in ('cmd_topic','rover_status_topic','odom_topic','tf_topic','tf_static_topic',
                'imu_raw_topic','imu_relay_topic','imu_mag_topic','imu_driver_status_topic','scan_topic',
                'camera_topic','camera_compressed_topic','camera_info_topic','camera_preview_info_topic'):
            values[name] = '/'+name
        return self.recorder.RecordingState(types.SimpleNamespace(**values))

    def clock_context(self, clock):
        module = self.recorder
        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromtimestamp(clock['host'], tz)
        stack = ExitStack()
        stack.enter_context(patch.object(module, 'datetime', FrozenDatetime))
        stack.enter_context(patch.object(module.time, 'time', lambda:clock['host']))
        stack.enter_context(patch.object(module.time, 'monotonic', lambda:clock['steady']))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return stack

    def receive(self, state, value, *, status=False):
        state.observe_gps(types.SimpleNamespace(data=json.dumps(value)), status=status)

    def test_start_stop_snapshot_frozen_and_sensor_playback_clock_unchanged(self):
        clock = dict(host=HOST_S-.25, steady=99.75)
        with tempfile.TemporaryDirectory() as temp, self.clock_context(clock):
            state = self.make_state(temp)
            historical = state.archive_dir/'historical'; historical.mkdir(parents=True)
            original_files = {'metadata.json':b'{"started_at":"old","legacy":true}',
                              'recording_clock.json':b'{"legacy":true}'}
            for name, data in original_files.items(): (historical/name).write_bytes(data)
            self.receive(state, sample(latitude=35.57, longitude=129.19))
            clock.update(host=HOST_S, steady=100.)
            with patch.object(state, 'start_rosbag', return_value=None) as rosbag:
                self.assertTrue(state.start('lidar')['ok'])
            rosbag.assert_called_once()
            root = Path(state.active['path'])
            metadata = json.loads((root/'metadata.json').read_text(encoding='utf-8'))
            frozen = copy.deepcopy(metadata['clock_quality_snapshot'])
            self.assertEqual(metadata['id'], '20261007T000000Z_lidar')
            self.assertEqual(metadata['started_at'], HOST_TEXT)
            self.assertEqual(frozen['display_started_at'], '2026-10-07T09:00:00.500000+00:00')
            self.assertEqual(state.active['started_monotonic'], HOST_S)
            self.assertEqual(state.active['started_steady'], 100.)
            clock.update(host=HOST_S+.5, steady=100.5)
            later = sample(gps_time='2026-10-07T09:00:01Z', received_at=HOST_S+.5,
                gps_time_fraction_ns=0, latitude=35.6, longitude=129.2)
            self.receive(state, later)
            self.assertEqual(state.gps_frames[0]['t_s'], .5)
            self.assertEqual(state.gps_frames[0]['sample'], later)
            self.assertEqual(state.active['clock_quality_snapshot'], frozen)
            self.assertTrue(state.stop()['ok'])
            final = json.loads((root/'metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(final['started_at'], HOST_TEXT)
            self.assertEqual(final['clock_quality_snapshot'], frozen)
            self.assertFalse((root/'recording_clock.json').exists())
            self.assertEqual({p.name:p.read_bytes() for p in historical.iterdir()}, original_files)
            self.assertEqual(state.detail(metadata['id'])['recording']['clock_quality'], 'gnss_receiver_estimated')

    def test_hppos_and_duplicate_epoch_do_not_renew_time_cache_age(self):
        clock = dict(host=HOST_S-.25, steady=99.75)
        with tempfile.TemporaryDirectory() as temp, self.clock_context(clock):
            state = self.make_state(temp)
            self.receive(state, sample())
            original = copy.deepcopy(state.gps_clock_latest)
            clock.update(host=HOST_S+2., steady=102.)
            self.receive(state, sample(protocol='UBX_NAV_HPPOSLLH', received_at=clock['host']))
            self.receive(state, sample(received_at=clock['host']))  # same UTC epoch
            self.assertEqual(state.gps_clock_last_at, 99.75)
            self.assertEqual(state.gps_clock_latest, original)
            with patch.object(state, 'start_rosbag', return_value=None): state.start('lidar')
            self.assertEqual(state.active['clock_quality_snapshot']['reason'], 'receiver_stale')
            self.assertEqual(state.status()['clock_quality'], 'host_unverified')
            state.stop()

    def test_dropout_and_unresolved_nav_time_do_not_use_last_good_sample(self):
        for invalid_status in (False, True):
            clock = dict(host=HOST_S-.25, steady=99.75)
            with self.subTest(status=invalid_status), tempfile.TemporaryDirectory() as temp, self.clock_context(clock):
                state = self.make_state(temp)
                self.receive(state, sample())
                clock.update(host=HOST_S, steady=100.)
                bad = (dict(schema=GPS_SCHEMA, state='stale', valid=False, received_at=HOST_S)
                    if invalid_status else sample(gps_time_fully_resolved=False, received_at=HOST_S))
                self.receive(state, bad, status=invalid_status)
                with patch.object(state, 'start_rosbag', return_value=None): state.start('lidar')
                self.assertEqual(state.active['clock_quality_snapshot']['clock_quality'], 'host_unverified')
                state.stop()

    def test_reordered_epoch_cannot_be_readmitted_after_cache_invalidation(self):
        clock = dict(host=HOST_S-.25, steady=99.75)
        with tempfile.TemporaryDirectory() as temp, self.clock_context(clock):
            state = self.make_state(temp)
            self.receive(state, sample())
            watermark = state.gps_clock_epoch_watermark
            for step in (0., .25):
                clock.update(host=HOST_S+step, steady=100.+step)
                self.receive(state, sample(gps_time='2026-10-07T08:59:59Z',
                    received_at=clock['host']))
                self.assertEqual(state.gps_clock_latest, {})
                self.assertIsNone(state.gps_clock_last_at)
                self.assertEqual(state.gps_clock_epoch_watermark, watermark)
            clock.update(host=HOST_S+.5, steady=100.5)
            self.receive(state, sample(gps_time='2026-10-07T09:00:01Z',
                received_at=clock['host']))
            self.assertEqual(state.gps_clock_last_at, 100.5)
            self.assertGreater(state.gps_clock_epoch_watermark, watermark)

    def test_synthetic_start_rejects_future_nan_and_invalid_time_inputs(self):
        for change in (dict(received_at=HOST_S+.001), dict(gps_time_accuracy_s=float('nan')),
                dict(gps_time_fraction_ns=float('nan')), dict(gps_time='2026-10-07T09:00:00'),
                dict(gps_time_valid_date=False), dict(gps_time_fully_resolved=False)):
            clock = dict(host=HOST_S-.25, steady=99.75)
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp, self.clock_context(clock):
                state = self.make_state(temp)
                self.receive(state, sample(**change))
                clock.update(host=HOST_S, steady=100.)
                with patch.object(state, 'start_rosbag', return_value=None): state.start('lidar')
                metadata = json.loads((Path(state.active['path'])/'metadata.json').read_text(encoding='utf-8'))
                self.assertEqual(metadata['clock_quality_snapshot']['clock_quality'], 'host_unverified')
                self.assertEqual(metadata['started_at'], HOST_TEXT)
                self.assertEqual(metadata['clock_quality_snapshot']['display_started_at'], HOST_TEXT)
                state.stop()

    def test_recorder_list_display_sort_matches_sidecar_and_snapshot_with_original_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            state = self.make_state(temp)
            for name, started, extra in (
                ('late_host', '2026-10-07T08:00:00Z', {}),
                ('earlier_id', HOST_TEXT, {'clock_quality_snapshot':snapshot()})):
                root=state.archive_dir/name; root.mkdir(parents=True)
                (root/'metadata.json').write_text(json.dumps(dict(id=name, started_at=started, **extra)))
            rows = state.list_recordings()['recordings']
            self.assertEqual([r['id'] for r in rows], ['earlier_id','late_host'])
            self.assertEqual(rows[0]['started_at'], HOST_TEXT)
            root = state.archive_dir/'late_host'
            (root/'recording_clock.json').write_text(json.dumps(dict(schema='ubts.recording_clock.v1',
                original_started_at='2026-10-07T08:00:00Z', display_started_at='2026-10-07T10:00:00Z', offset_s=7200)))
            rows = state.list_recordings()['recordings']
            self.assertEqual([r['id'] for r in rows], ['late_host','earlier_id'])
            self.assertEqual(rows[0]['clock_quality'], 'gnss_estimated')


if __name__ == '__main__':
    unittest.main()
