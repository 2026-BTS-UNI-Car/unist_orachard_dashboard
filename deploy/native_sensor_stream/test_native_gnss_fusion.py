import copy
import hashlib
import json
import math
import struct
import unittest
import gnss_constraints as backend
import native_gnss_fusion as n
import replay_native_gnss as replay

CTX = ('native-session', 1, 'SYSTEM-source-domain', 'RAW-domain', 'retained-map', 'laser-local')


class Writer:
    def __init__(self, endian='<'):
        self.raw = bytearray(b'\0\1\0\0' if endian == '<' else b'\0\0\0\0')
        self.endian = endian
    def put(self, fmt, align, *values):
        self.raw.extend(bytes((align - (len(self.raw) - 4) % align) % align))
        self.raw.extend(struct.pack(self.endian + fmt, *values))
    def string(self, text):
        self.put('I', 4, len(text) + 1)
        self.raw.extend(text + b'\0')
    def done(self, padding=False):
        if padding: self.raw.extend(bytes((-len(self.raw)) & 3))
        return bytes(self.raw)


def fixture(source=1_000_000_000, epoch=1, x_m=0., changes=None, covariance=None,
            cov_type=1, endian='<', status=0, quality=True):
    # Synthetic geometry only: no claim of original physical recording.
    lat, lon = 35., 129. + math.degrees(x_m / (6378137. * math.cos(math.radians(35.))))
    sample = dict(schema='orchard.gps.v1', valid=True, quality=1, fix_type='autonomous',
        latitude=lat, longitude=lon, h_acc_m=4., frame_id='gps_link',
        gps_time='2026-10-03T07:25:%02d+00:00' % epoch, i_tow_ms=epoch * 1000,
        stamp=dict(sec=source // 10**9, nanosec=source % 10**9))
    if quality:
        sample.update(gps_time_valid_date=True, gps_time_valid_time=True,
            gps_time_fully_resolved=True, gps_time_accuracy_s=.01, gps_time_fraction_ns=0)
    sample.update(changes or {})
    w = Writer(endian);w.string(json.dumps(sample, allow_nan=False).encode()); data = w.done(True)
    w = Writer(endian);w.put('iI', 4, source // 10**9, source % 10**9);w.string(b'gps_link')
    w.put('b', 1, status);w.put('H', 2, 0);w.put('3d', 8, lat, lon, math.nan)
    w.put('9d', 8, *(covariance if covariance is not None else [16.,0.,0.,0.,16.,0.,0.,0.,81.]))
    w.put('B', 1, cov_type)
    return n.OriginalGNSS(CTX, data, w.done(True))


def pose(source, version, x_m=0., chain='continuous'):
    return n.PoseWitness(CTX, chain, version, source, (x_m, 0.), ('%064x' % version))


class GNSSContractTests(unittest.TestCase):
    def test_both_endian_CDR_original_bytes_and_padding(self):
        for endian in '<>':
            value = fixture(endian=endian)
            s, reason = n.solution(value)
            self.assertEqual(reason, 'quality_position')
            self.assertEqual(s['source_ns'], 1_000_000_000)
            self.assertEqual(s['data_sha256'], hashlib.sha256(value.data_cdr).hexdigest())
            self.assertEqual(n.decode_navsat(value.fix_cdr)['covariance_type'], 1)
            self.assertTrue(math.isnan(n.decode_navsat(value.fix_cdr)['altitude']))

    def test_truncated_unsupported_and_bad_tail_CDR(self):
        p = fixture()
        for raw in (p.fix_cdr[:20], b'\0\3\0\0' + p.fix_cdr[4:], p.fix_cdr + b'x'):
            with self.subTest(raw=raw[:4]), self.assertRaises(ValueError): n.decode_navsat(raw)
        for raw in (p.data_cdr[:10], p.data_cdr + b'\0' * 5):
            with self.assertRaises(ValueError): n.decode_gps_data(raw)

    def test_duplicate_and_nonfinite_JSON_rejected(self):
        for text in (b'{"schema":"x","schema":"orchard.gps.v1"}', b'{"x":NaN}'):
            w = Writer();w.string(text)
            with self.assertRaises(ValueError): n.decode_gps_data(w.done())

    def test_NoFix_harmless_even_coordinates_missing(self):
        s, reason = n.solution(fixture(changes=dict(valid=False, latitude=None, longitude=None), status=-1))
        self.assertIsNone(s);self.assertEqual(reason, 'NoFix')

    def test_covariance_unknown_invalid_symmetric_negative_nan(self):
        cases = [(0, None), (1, [-1.,0.,0.,0.,16.,0.,0.,0.,1.]),
                 (3, [16.,1.,0.,0.,16.,0.,0.,0.,1.]),
                 (1, [16.,0.,0.,0.,math.nan,0.,0.,0.,1.]), (2, [0.] * 9)]
        for kind, cov in cases:
            with self.subTest(kind=kind, cov=cov), self.assertRaises(ValueError):
                n.solution(fixture(cov_type=kind, covariance=cov))

    def test_missing_time_quality_archival_only_not_invented(self):
        s, _ = n.solution(fixture(quality=False))
        self.assertFalse(s['receiver_time_quality_present'])
        self.assertEqual(s['source_ns'], 1_000_000_000)
        self.assertIn('2026-10-03', s['epoch'])
        self.assertGreater(s['scale_m'], 4.)
        with self.assertRaises(ValueError): n.solution(fixture(changes=dict(gps_time_valid_time=False)))

    def test_exact_stamp_frame_pair_and_virtual_exclusion(self):
        p = fixture(); q = fixture(source=1_000_000_001)
        with self.assertRaises(ValueError): n.solution(n.OriginalGNSS(CTX, p.data_cdr, q.fix_cdr))
        for change in (dict(frame_id='base_link'), dict(simulated=True), dict(quality=6), dict(h_acc_m=True)):
            with self.assertRaises(ValueError): n.solution(fixture(changes=change))

    def test_original_pair_collector_order_capacity_and_stop(self):
        c = n.OriginalGPSPairs(CTX, capacity=1);p = fixture()
        self.assertIsNone(c.receive('/gps/fix', p.fix_cdr, log_ns=11))
        pair = c.receive('/gps/data', p.data_cdr, log_ns=12)
        self.assertEqual(pair.data_cdr, p.data_cdr);self.assertEqual(pair.data_log_ns, 12)
        self.assertIsNone(pair.data_callback_RAW_ns)
        with self.assertRaises(ValueError): c.receive('/gps/data', p.data_cdr)
        c.receive('/gps/data', fixture(source=2*10**9).data_cdr)
        c.receive('/gps/data', fixture(source=3*10**9).data_cdr)
        self.assertEqual(c.counts['unmatched_evicted'], 1)
        c.stop();self.assertIsNone(c.receive('/gps/data', b'bad'))

    def test_live_original_GID_domain_SHA_and_String_no_Header(self):
        p = fixture();gids = {'/gps/data':'1'*32, '/gps/fix':'2'*32}
        c = n.LiveGPSPairs(CTX, gids, process_validator=lambda row: row.get('owned') is True)
        def row(topic, raw):
            return dict(topic=topic, ros_type='std_msgs/msg/String' if topic == '/gps/data' else 'sensor_msgs/msg/NavSatFix',
                publisher_gid_hex=gids[topic], RAW_domain=CTX[3], owned=True,
                original_CDR_hex=raw.hex(), original_CDR_bytes=len(raw),
                original_CDR_sha256=hashlib.sha256(raw).hexdigest(), original_header_present=topic=='/gps/fix',
                original_header_stamp_ns=10**9 if topic=='/gps/fix' else None,
                callback_monotonic_raw_ns=20, mission_admission=False, proposed_linear_mps=0.,proposed_yaw_rate_rad_s=0.)
        r = row('/gps/data', p.data_cdr)
        for change in (dict(publisher_gid_hex='3'*32), dict(RAW_domain='changed'),
                       dict(original_CDR_sha256='0'*64), dict(original_header_present=True), dict(owned=False)):
            bad = dict(r, **change)
            with self.assertRaises(ValueError): c.receive_row(bad)
        self.assertIsNone(c.receive_row(r))
        pair = c.receive_row(row('/gps/fix', p.fix_cdr))
        self.assertEqual(pair.data_gid, gids['/gps/data'])
        self.assertEqual(pair.data_callback_RAW_ns, 20)


class FusionTests(unittest.TestCase):
    def bridge(self, capacity=4096):
        self.valid_poses = {}
        def valid(p):
            return self.valid_poses.get(id(p)) == repr(p)
        b = n.WeakGNSSMapFactors(backend, enabled=True, validate_original=lambda p: True,
                               validate_pose=valid, capacity=capacity)
        b.start(CTX, explicit=True)
        return b

    def add(self, b, p):
        self.valid_poses[id(p)] = repr(p);b.add_pose(p)

    def test_default_inert_and_trusted_bindings_required(self):
        b = n.WeakGNSSMapFactors(backend)
        self.assertFalse(b.status()['active']);self.assertEqual(b.status()['retained_poses'], 0)
        with self.assertRaises(ValueError): b.start(CTX, explicit=True)

    def test_exact_integer_bracket_and_unique_receiver_epoch(self):
        b = self.bridge();base = 1_791_012_331_000_000_000
        self.add(b, pose(base, 1, 0.));self.add(b, pose(base+100_000_000, 2, 10.))
        p = fixture(source=base+25_000_001)
        self.assertTrue(b.add_original(p)['accepted'])
        a = b.associations[0]
        self.assertEqual(a['pose_source_ns'], (base, base+100_000_000))
        self.assertAlmostEqual(a['local_xy'][0], 2.5000001)
        self.assertEqual(b.add_original(p)['reason'], 'duplicate_receiver_epoch')
        self.assertEqual(len(b.factors), 1)

    def test_wait_and_gap_do_not_fabricate_interpolated_pose(self):
        b = self.bridge();self.add(b, pose(10**9, 1));p = fixture(source=10**9+50_000_000)
        self.assertEqual(b.add_original(p)['reason'], 'waiting_exact_native_pose_bracket')
        self.add(b, pose(10**9+100_000_000, 2, chain='reanchor'))
        self.assertEqual(b.add_original(p)['reason'], 'no_continuous_exact_header_bracket')
        self.assertEqual(b.status()['retained_GNSS_factors'], 0)

    def test_NoFix_and_badcovariance_preserve_entire_local_history(self):
        b = self.bridge();self.add(b, pose(10**9, 1));history = list(b.poses)
        self.assertEqual(b.add_original(fixture(status=-1))['reason'], 'NoFix')
        self.assertFalse(b.add_original(fixture(cov_type=0))['accepted'])
        self.assertEqual(b.poses, history);self.assertIsNone(b.origin)

    def test_retained_pose_mutation_and_context_change_never_fuse(self):
        b = self.bridge();p = pose(10**9, 1);self.add(b, p)
        object.__setattr__(p, 'xy', (99., 0.))
        with self.assertRaises(ValueError): b.add_original(fixture())
        with self.assertRaises(ValueError): b.add_original(n.OriginalGNSS(('other', *CTX[1:]), fixture().data_cdr, fixture().fix_cdr))

    def test_Stop_before_publication_rejects_and_retains(self):
        b = self.bridge();self.add(b, pose(10**9, 1))
        b.validate_pose = lambda p: (b.stop_event.set() or True)
        self.assertEqual(b.add_original(fixture())['reason'], 'stopped')
        self.assertEqual(len(b.factors), 0);self.assertEqual(len(b.poses), 1)

    def test_capacity_no_eviction_or_reset(self):
        b = self.bridge(capacity=8)
        for i in range(8): self.add(b, pose((i+1)*10**9, i+1))
        with self.assertRaises(ValueError): self.add(b, pose(9*10**9, 9))
        self.assertEqual(len(b.poses), 8)

    def test_stationary_cannot_claim_heading(self):
        b = self.bridge()
        for i in range(10):
            self.add(b, pose((i+1)*10**9, i+1))
            self.assertTrue(b.add_original(fixture(source=(i+1)*10**9, epoch=i+1, x_m=i*.2))['accepted'])
        self.assertFalse(b.global_proposal()['accepted'])

    def test_real_soft_graph_complete_history_unchanged(self):
        b = self.bridge()
        for i in range(21):
            self.add(b, pose((i+1)*10**9, i+1, x_m=i*5.))
            self.assertTrue(b.add_original(fixture(source=(i+1)*10**9, epoch=i+1, x_m=i*5.))['accepted'])
        old = tuple(b.poses)
        result = b.global_proposal()
        self.assertTrue(result['accepted']);self.assertEqual(len(result['corrected_ENU_xy']), 21)
        self.assertEqual(tuple(b.poses), old)
        self.assertTrue(result['requires_complete_retained_scan_rerasterization'])
        for k in ('runtime_authority', 'autonomy_output_enabled', 'native_GNSS_pipeline_qualified'):
            self.assertFalse(result[k])
        b.stop();self.assertIsNone(b.global_proposal());self.assertEqual(len(b.poses), 21)

    def test_original_bag_iterator_missing_timefields_still_weak_fusion(self):
        rows, poses = [], []
        for i in range(21):
            t = (i+1)*10**9;original = fixture(source=t, epoch=i+1, x_m=i*5., quality=False)
            poses.append(pose(t, i+1, i*5.))
            rows.extend([('/gps/fix', original.fix_cdr, t+1), ('/gps/data', original.data_cdr, t+2)])
        result = replay.build(iter(rows), poses, CTX)
        self.assertEqual(result['collector']['pairs'], 21)
        self.assertTrue(result['proposal']['accepted'])
        self.assertFalse(result['native_GNSS_pipeline_qualified'])
        self.assertEqual(result['factor_status']['retained_poses'], 21)

    def test_original_mutation_between_quality_and_final_binding_is_rejected(self):
        b = self.bridge();self.add(b, pose(10**9, 1));calls = []
        def validator(p):
            calls.append(1);return len(calls) == 1
        b.validate_original = validator
        with self.assertRaisesRegex(ValueError, 'changed_during_association'):
            b.add_original(fixture())
        self.assertIsNone(b.origin);self.assertEqual(len(b.factors), 0)


if __name__ == '__main__': unittest.main()
