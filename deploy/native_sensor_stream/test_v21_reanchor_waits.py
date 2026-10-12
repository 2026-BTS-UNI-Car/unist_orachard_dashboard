"""V21 typed WAITs through the real ingress, factory and stream rows; no ROS.

Original CDR rows are synthesized before ingestion and flow through the real
PhysicalOriginalIngress / PhysicalNativeShadowFactory / StreamObserverRows.
Only the numeric registration solver (or, for one outcome, surfaces) is
patched to select an exact kernel outcome. This does not qualify geometry,
installed callback provenance or any physical autonomy.
"""
import hashlib
import importlib
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PACKAGE = HERE / 'optimized-package'
for _name in [n for n in sys.modules if n in ('physical_ingress', 'physical_factory', 'inputs',
              'observer_rows', 'stream_rows', 'stream_rows_map', 'common', 'kernel') or
              n.startswith(('common.', 'kernel.'))]:
    _module = sys.modules[_name]
    _file = getattr(_module, '__file__', None) or ''
    if not (_file and Path(_file).resolve().is_relative_to(HERE)) or 'native_sensor_shadow' in _file:
        del sys.modules[_name]  # another package's same-named module: isolate this file
sys.path.insert(0, str(PACKAGE))
sys.path.insert(1, str(HERE))

import numpy as np  # noqa: E402
from common.original_cdr import LASER_TYPE, IMU_TYPE, decode  # noqa: E402
from common.native_ownership import RawReceipt, RAW_SCHEMA  # noqa: E402
import physical_ingress  # noqa: E402
import physical_factory  # noqa: E402
from physical_ingress import PhysicalOriginalIngress  # noqa: E402
from physical_factory import PhysicalNativeShadowFactory, PhysicalShadowContract, revision  # noqa: E402
import stream_rows_map  # noqa: E402
import stream_rows  # noqa: E402

assert Path(physical_ingress.__file__).resolve().parent == PACKAGE.resolve()

DOMAIN = '1' * 64
STREAM_SHA = '2' * 64
SCAN_GID, IMU_GID = bytes.fromhex('3' * 32), bytes.fromhex('4' * 32)
PID, NS = 123, 'time:[123]'
START = 1_000_000_000_000
MS = 1_000_000
ACCEPT = dict(accepted=True, xy=(.01, 0.), yaw=.002, coverage=.9, rmse_m=.02)
REJECT = dict(accepted=False, reason='unsupported_or_inconsistent_registration',
              fit_rejection_counts={'final_insufficient_overlap': 2, 'IMU_relative_rotation_disagreement': 1},
              current_supported_points=118, sampled_point_denominator=120)
AMBIGUOUS = dict(accepted=False, reason='ambiguous_registration')
REG_WAIT = 'waiting_for_next_scan_after_rejected_native_registration'
GAP_WAIT = 'waiting_for_fresh_scan_after_positive_age_original_IMU_bracket_gap'
SPAN_WAIT = 'waiting_for_fresh_scan_after_positive_age_original_IMU_span_loss'
MISSING_WAIT = 'waiting_for_next_scan_after_missing_original_scan'
REPLACED_WAIT = 'waiting_for_fresh_scan_after_pending_pair_replaced_continuity_loss'


class SyntheticCDR:
    def __init__(self):
        self.data = bytearray(b'\x00\x01\x00\x00')

    def raw(self, value, alignment=1):
        self.data.extend(b'\0' * ((-(len(self.data) - 4)) % alignment))
        self.data.extend(value)

    def scalar(self, kind, value):
        self.raw(struct.pack('<' + kind, value), struct.calcsize(kind))

    def header(self, ns, frame):
        self.scalar('i', ns // 1_000_000_000)
        self.scalar('I', ns % 1_000_000_000)
        value = frame.encode('utf-8') + b'\0'
        self.scalar('I', len(value))
        self.raw(value)


def imu_cdr(ns, quaternion=(0., 0., 0., 1.)):
    c = SyntheticCDR()
    c.header(ns, 'imu_link')
    for values in (quaternion, (0.,) * 9, (0., 0., .07), (0.,) * 9, (0., 0., 9.80665), (0.,) * 9):
        c.raw(struct.pack('<' + 'd' * len(values), *values), 8)
    return bytes(c.data)


def scan_cdr(ns, rng=2., count=120):
    c = SyntheticCDR()
    c.header(ns, 'laser')
    c.raw(struct.pack('<7f', -.6, -.6 + (count - 1) * .01, .01, 0., .1, .05, 50.), 4)
    c.scalar('I', count)
    c.raw(struct.pack('<' + 'f' * count, *((rng,) * count)), 4)
    c.scalar('I', count)
    c.raw(struct.pack('<' + 'f' * count, *((1.,) * count)), 4)
    return bytes(c.data)


class Clocks:
    def __init__(self):
        self.ns = START

    @property
    def raw_ns(self):
        return 10_000_000_000 + self.ns - START

    def sample(self):
        return RawReceipt(self.raw_ns, DOMAIN)


class FakeMap:
    def __init__(self, factory):
        self.factory, self.calls = factory, []

    def invalidate_gap(self, reason):
        self.calls.append(reason)

    def commit(self, owned):
        return self.factory.commit(owned)

    def stop(self):
        self.calls.append('stop')


class FakeControl:
    def __init__(self, factory):
        self.factory, self.calls = factory, []

    def invalidate_gap(self, reason):
        self.calls.append(reason)

    def commit(self, owned, map_adapter=None):
        return self.factory.commit(owned) if map_adapter is None else map_adapter.commit(owned)

    def stop(self):
        self.calls.append('stop')

    def status(self):
        return {}

    def bind_epoch(self, _epoch):
        pass


class Harness:
    def __init__(self, *, rows=stream_rows_map, preparation=True, gap=True, registration=True,
                 positive_gap=True, continuity=True, fakes=True):
        self.clock = Clocks()
        contract = PhysicalShadowContract('laser', 'imu_link')
        self.ingress = PhysicalOriginalIngress(raw_clock=self.clock, source_now_ns=lambda: self.clock.ns,
            source_clock_kind=lambda: 'ROS_SYSTEM_TIME', scan_frame='laser', imu_frame='imu_link',
            profile_revision=revision(contract), scan_publisher_gid=SCAN_GID, imu_publisher_gid=IMU_GID,
            observer_contract_sha256=STREAM_SHA, imu_gap_reanchor_enabled=gap,
            startup_expiry_discard_enabled=True, current_expiry_reanchor_enabled=True,
            positive_age_imu_gap_reanchor_enabled=positive_gap, scan_continuity_reanchor_enabled=continuity)
        self.ingress.reset()
        self.factory = PhysicalNativeShadowFactory(self.ingress, contract, exact_surface_cache_enabled=True,
            postconsumed_causal_raw_expiry_reanchor_enabled=True,
            receiver_commit_causal_raw_expiry_reanchor_enabled=True,
            final_scalar_causal_raw_expiry_reanchor_enabled=True,
            preparation_causal_raw_expiry_reanchor_enabled=preparation,
            registration_rejection_reanchor_enabled=registration)
        self.adapter = rows.StreamObserverRows(self.ingress, self.factory, observer_contract_sha256=STREAM_SHA)
        self.map = self.control = None
        if fakes:
            # Recording stand-ins: only invalidate_gap/commit/stop are exercised.
            self.control = self.adapter._control_adapter = FakeControl(self.factory)
            if rows is stream_rows_map:
                self.map = self.adapter._map_adapter = FakeMap(self.factory)
        self.adapter.bind_process(PID, NS)
        self.seq = 0
        self.results = [ACCEPT]
        self.emit(dict(kind='begin', schema=rows.SCHEMA, streaming=True, pid=PID, uid=0, gid=0,
            time_namespace=NS, observer_process_session='5' * 32, RAW_domain=DOMAIN,
            GID_storage_bytes=16, expected_scan_GID=SCAN_GID.hex(), expected_IMU_GID=IMU_GID.hex(),
            header_clock_origin='original_publisher_unmodified', observer_clock_type='RCL_SYSTEM_TIME',
            use_sim_time=False, clock_topic_created=False, publishers_created=False, mission_admission=False))

    def emit(self, row):
        row = dict(row, seq=self.seq)
        raw = (json.dumps(row, separators=(',', ':'), allow_nan=False) + '\n').encode()
        try:
            return self.adapter.receive_line(raw)
        finally:
            self.seq += 1

    def registration(self, *_args, **_kwargs):
        value = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        return value(self) if callable(value) else dict(value)

    def deliver(self, kind, header_ms, deliver_ms, cdr=None):
        self.clock.ns = START + int(deliver_ms * MS)
        header = START + int(header_ms * MS)
        cdr = cdr or (scan_cdr(header) if kind == LASER_TYPE else imu_cdr(header))
        wire = decode(cdr, kind)
        with patch('physical_factory.registration', side_effect=self.registration):
            return self.emit(dict(kind='original_cdr', ros_type=kind,
                topic='/scan' if kind == LASER_TYPE else '/imu/data_raw',
                original_CDR_hex=cdr.hex(), original_CDR_bytes=len(cdr),
                original_CDR_sha256=hashlib.sha256(cdr).hexdigest(),
                original_header_stamp_sec=wire.header.sec, original_header_stamp_nanosec=wire.header.nanosec,
                original_header_stamp_ns=wire.header.stamp_ns, frame_id_bytes_hex=wire.header.frame_id.encode().hex(),
                ranges_count=wire.count if kind == LASER_TYPE else 0,
                intensities_count=len(wire.intensity_f32_bytes) // 4 if kind == LASER_TYPE else 0,
                RAW_schema=RAW_SCHEMA, RAW_domain=DOMAIN, callback_monotonic_raw_ns=self.clock.raw_ns,
                observer_system_ros_now_ns=self.clock.ns,
                publisher_gid_hex=(SCAN_GID if kind == LASER_TYPE else IMU_GID).hex(),
                proposed_linear_mps=0., proposed_yaw_rate_rad_s=0., mission_admission=False))

    def imu(self, header_ms, cdr=None):
        return self.deliver(IMU_TYPE, header_ms, header_ms + 2, cdr)

    def scan(self, header_ms, deliver_ms=None, cdr=None):
        return self.deliver(LASER_TYPE, header_ms, header_ms + 100 if deliver_ms is None else deliver_ms, cdr)

    def prefix(self):
        """IMU 20 Hz (+2 ms delivery), scans +10 ms headers, +100 ms delivery: one diagnostic."""
        self.imu(0); self.imu(50); self.imu(100)
        self.scan(10)
        self.imu(150); self.imu(200)
        records = self.scan(110)
        assert len(records) == 1 and self.status()['diagnostic_count'] == 1, self.status()
        return records

    def cycle(self, scan_header_ms, cdr=None):
        """IMUs at S+40/S+90 (delivered first), then scan S delivered at S+100."""
        self.imu(scan_header_ms + 40); self.imu(scan_header_ms + 90)
        return self.scan(scan_header_ms, cdr=cdr)

    def status(self):
        return self.adapter.status()

    def header(self, envelope):
        return (envelope.wire.header.stamp_ns - START) // MS


class Base(unittest.TestCase):
    rows = stream_rows_map

    def make(self, **kwargs):
        return Harness(rows=self.rows, **kwargs)

    def assertNoPublication(self, h, before):
        status = h.status()
        self.assertEqual(status['diagnostic_count'], before['diagnostic_count'])
        self.assertEqual(h.factory.snapshot().version, before['diagnostic_count'])
        self.assertIsNone(h.adapter.proposal_input())
        self.assertIsNone(h.factory._pending)
        self.assertIsNone(h.factory._pending_digest)
        self.assertIsNone(h.factory._final_pending_seal)
        self.assertIsNone(h.ingress.prepared)
        self.assertIsNone(h.ingress._prepared_digest)
        self.assertIsNone(h.ingress._active)
        self.assertIsNone(h.ingress.pending)
        self.assertIsNone(h.ingress.fault)
        self.assertIsNone(status['fault'])
        self.assertIs(h.ingress._first_pair, True)

    def assertInvalidated(self, h, count=1):
        self.assertEqual(h.control.calls.count('native_original_pair_wait'), count)
        if h.map is not None:
            self.assertEqual(h.map.calls.count('native_original_pair_wait'), count)

    def assertFullReanchor(self, h):
        self.assertIsNone(h.ingress.previous)
        self.assertFalse(h.ingress.imus)
        self.assertFalse(h.ingress._owners)
        self.assertFalse(h.ingress.callback_provenance)


class RegistrationRejectionTests(Base):
    def test_rejected_registration_waits_keeps_newest_scan_and_next_scan_resumes(self):
        h = self.make(); h.prefix(); before = h.status()
        h.results = [REJECT]
        self.assertEqual(h.cycle(210), ())
        status = h.status()
        self.assertEqual(status['waiting_reason'], REG_WAIT)
        self.assertEqual(status['registration_rejection_wait_count'], 1)
        self.assertEqual(status['registration_rejection_wait_streak'], 1)
        self.assertEqual(status['registration_rejection_reason_counts']['unsupported_or_inconsistent_registration'], 1)
        timing = status['registration_rejection_wait_timing']
        self.assertEqual(timing['kernel_detail']['fit_rejection_counts'],
                         {'IMU_relative_rotation_disagreement': 1, 'final_insufficient_overlap': 2})
        self.assertIs(timing['published'], False)
        self.assertNoPublication(h, before)
        self.assertInvalidated(h)
        # Keep-newest anchor: the rejected pair's validated current scan is the next previous.
        self.assertEqual(h.header(h.ingress.previous), 210)
        self.assertIs(h.ingress.previous, h.factory._registration_rejection_anchor)
        h.ingress.validate_owned(h.ingress.previous)
        h.results = [ACCEPT]
        self.assertEqual(len(h.cycle(310)), 1)
        status = h.status()
        self.assertEqual(status['diagnostic_count'], 2)
        self.assertIsNone(status['waiting_reason'])
        self.assertEqual(status['registration_rejection_wait_streak'], 0)
        self.assertEqual(status['registration_rejection_wait_max_streak'], 1)
        self.assertIsNone(status['fault'])

    def test_each_allowlisted_kernel_outcome_is_an_owned_wait(self):
        surfaces = physical_factory.surfaces
        def no_surfaces(*args, **kwargs):
            raise ValueError('insufficient_observed_surfaces')
        cases = (('unsupported_or_inconsistent_registration', [REJECT], None),
                 ('ambiguous_registration', [AMBIGUOUS], None),
                 ('bounded_sensor_frame_fit_required', [dict(ACCEPT, xy=(.7, 0.))], None),
                 ('bounded_sensor_frame_fit_required', [dict(ACCEPT, yaw=3.2)], None),
                 ('insufficient_observed_surfaces', [ACCEPT], no_surfaces))
        for reason, results, surface in cases:
            with self.subTest(reason=reason, results=results):
                h = self.make(); h.prefix(); before = h.status(); h.results = results
                with patch('physical_factory.surfaces', side_effect=surface or surfaces):
                    self.assertEqual(h.cycle(210), ())
                status = h.status()
                self.assertEqual(status['registration_rejection_reason_counts'][reason], 1)
                self.assertEqual(status['waiting_reason'], REG_WAIT)
                self.assertNoPublication(h, before)

    def test_real_kernel_insufficient_returns_waits_twice_then_recovers(self):
        h = self.make(); h.prefix()
        empty = scan_cdr(START + 210 * MS, rng=30.)  # beyond the kernel's 20 m clip: zero returns
        self.assertEqual(h.cycle(210, cdr=empty), ())
        self.assertEqual(h.status()['registration_rejection_reason_counts']['insufficient_returns'], 1)
        # The kept anchor is the empty scan itself, so the next pair is rejected once more.
        self.assertEqual(h.cycle(310), ())
        status = h.status()
        self.assertEqual((status['registration_rejection_wait_count'], status['registration_rejection_wait_streak']), (2, 2))
        self.assertEqual(len(h.cycle(410)), 1)
        status = h.status()
        self.assertEqual((status['diagnostic_count'], status['registration_rejection_wait_streak'],
                          status['registration_rejection_wait_max_streak']), (2, 0, 2))

    def test_real_unmocked_kernel_rejection_matches_the_exact_result_schema(self):
        from kernel import raw_localization as kernel
        x = np.linspace(-2., 2., 400)
        corner = np.vstack((np.column_stack((x, np.full_like(x, 1.))), np.column_stack((np.full_like(x, 1.), x))))
        far = corner + np.array([7., -6.])  # no overlap inside the search bound
        result = kernel.registration(kernel.surfaces(corner), kernel.surfaces(far), 0., .1, None)
        self.assertIs(result['accepted'], False)
        self.assertEqual(result['reason'], 'unsupported_or_inconsistent_registration')
        h = self.make()
        reason, detail = h.factory._registration_rejection_detail(result=result)
        self.assertEqual(reason, 'unsupported_or_inconsistent_registration')
        self.assertTrue(detail['fit_rejection_counts'])
        with self.assertRaisesRegex(ValueError, '^insufficient_returns$'):
            kernel.scan_points(dict(ranges=[30.] * 120, angle_min=-.6, angle_increment=.01,
                                    range_min=.05, range_max=50.))

    def test_unlisted_or_malformed_outcomes_stay_fatal(self):
        def raises(error):
            def side_effect(*_args, **_kwargs):
                raise error
            return side_effect
        bad_results = (dict(REJECT, reason='unreviewed_rejection'), dict(REJECT, xy=(0., 0.)),
                       dict(REJECT, accepted=None), dict(REJECT, accepted=0),
                       dict(REJECT, fit_rejection_counts={'invented_failure': 1}),
                       dict(REJECT, fit_rejection_counts={'final_insufficient_overlap': True}),
                       dict(REJECT, current_supported_points=-1),
                       dict(ACCEPT, xy=(float('nan'), 0.)), dict(ACCEPT, yaw=float('inf')),
                       dict(ACCEPT, xy=(.1, .1, .1)))
        for result in bad_results:
            with self.subTest(result=result):
                h = self.make(); h.prefix(); h.results = [result]
                with self.assertRaisesRegex(ValueError, 'physical_internal_fit_rejected'):
                    h.cycle(210)
                self.assertEqual(h.status()['registration_rejection_wait_count'], 0)
                self.assertIsNotNone(h.factory.fault)
        for error in (np.linalg.LinAlgError('insufficient_observed_surfaces'),
                      ValueError('invalid_scan_angles'), ValueError('bounded_scan_size_exceeded'),
                      ValueError('insufficient_returns', 'extra'), RuntimeError('insufficient_returns')):
            with self.subTest(error=repr(error)):
                h = self.make(); h.prefix()
                with patch('physical_factory.surfaces', side_effect=raises(error)):
                    with self.assertRaises(Exception):
                        h.cycle(210)
                self.assertTrue(h.status()['fault'].startswith('physical_internal_fit_rejected'))
                self.assertEqual(h.status()['registration_rejection_wait_count'], 0)

    def test_feature_off_keeps_the_v20_fatal_fit_rejection(self):
        h = self.make(registration=False); h.prefix(); h.results = [REJECT]
        with self.assertRaisesRegex(ValueError, 'physical_internal_fit_rejected:.*diagnostic_native_fit_rejected:'
                                                'unsupported_or_inconsistent_registration'):
            h.cycle(210)
        self.assertIs(h.status()['registration_rejection_reanchor_enabled'], False)
        self.assertEqual(h.status()['registration_rejection_wait_count'], 0)
        self.assertTrue(h.factory.stop_intent.is_set())

    def test_integrity_mutation_during_fit_is_fatal_not_a_wait(self):
        def digest(h):
            h.ingress._prepared_digest = '0' * 64
            return REJECT
        def envelope(h):
            current = h.ingress.prepared.current
            owned = h.ingress._owners[id(current)]
            h.ingress._owners[id(current)] = (owned[0], ('0' * 64, owned[1][1]))
            return REJECT
        def gid(h):
            h.ingress.expected_gids = (bytes.fromhex('6' * 32), IMU_GID)
            return REJECT
        def published(h):
            h.factory._state = physical_factory.DiagnosticState(h.factory._state.version, None)
            return REJECT
        for mutate, reason in ((digest, 'original_prepared_ownership_changed'),
                               (envelope, 'unowned_or_changed_original_envelope'),
                               (gid, 'exact_rejected_pair_anchor_required'),
                               (published, 'unpublished_registration_rejection_required')):
            with self.subTest(reason=reason):
                h = self.make(); h.prefix(); h.results = [mutate]
                with self.assertRaisesRegex(ValueError, 'physical_internal_fit_rejected:.*' + reason):
                    h.cycle(210)
                self.assertEqual(h.status()['registration_rejection_wait_count'], 0)

    def test_stop_during_fit_never_waits_or_publishes(self):
        h = self.make(); h.prefix(); before = h.status()
        def stop(h):
            h.adapter.stop()
            return REJECT
        h.results = [stop]
        self.assertEqual(h.cycle(210), ())
        status = h.status()
        self.assertIs(status['stopped'], True)
        self.assertEqual(status['registration_rejection_wait_count'], 0)
        self.assertEqual(status['diagnostic_count'], before['diagnostic_count'])
        self.assertEqual(h.factory.fault, 'physical_shadow_STOP_latched')
        self.assertIsNone(h.factory._registration_rejection_wait)

    def test_stop_race_at_acceptance_is_revoked_fail_closed(self):
        h = self.make(); h.prefix(); h.results = [REJECT]
        real = h.factory.is_owned_registration_rejection_wait
        calls = []
        def validator(value):
            calls.append(value)
            if len(calls) == 2:
                h.adapter._stopping.set()
            return real(value)
        h.factory.is_owned_registration_rejection_wait = validator
        with self.assertRaisesRegex(ValueError, 'native_stream_registration_rejection_wait_revoked'):
            h.cycle(210)
        self.assertIsNone(h.adapter.proposal_input())

    def test_forged_or_stale_wait_is_not_owned(self):
        h = self.make(); h.prefix(); h.results = [REJECT]; h.cycle(210)
        wait = h.factory._registration_rejection_wait
        self.assertTrue(h.factory.is_owned_registration_rejection_wait(wait))
        forged = physical_factory._RegistrationRejectionWait(wait.reason, wait.generation, h.factory,
                                                             wait.diagnostic_version)
        self.assertFalse(h.factory.is_owned_registration_rejection_wait(forged))
        self.assertFalse(h.ingress.is_owned_gap_wait(wait))
        h.ingress.stop()
        self.assertFalse(h.factory.is_owned_registration_rejection_wait(wait))

    def test_repeated_waits_are_bounded_counted_and_counter_exhaustion_is_fatal(self):
        h = self.make(); h.prefix(); h.results = [REJECT]
        for index, header in enumerate((210, 310, 410, 510), 1):
            self.assertEqual(h.cycle(header), ())
            status = h.status()
            self.assertEqual((status['registration_rejection_wait_count'],
                              status['registration_rejection_wait_streak']), (index, index))
            self.assertEqual(status['diagnostic_count'], 1)
        h.results = [ACCEPT]
        self.assertEqual(len(h.cycle(610)), 1)
        self.assertEqual(h.status()['registration_rejection_wait_max_streak'], 4)
        h.factory.registration_rejection_wait_count = (1 << 63) - 1
        h.results = [REJECT]
        with self.assertRaisesRegex(ValueError, 'registration_rejection_counter_exhausted'):
            h.cycle(710)


class PositiveAgeImuGapTests(Base):
    def stall(self, h, imu_deliver_ms=451.6, imu_header_ms=401.5):
        # 035952-like host stall: scan delivered late (fresh, 226 ms), next IMU after a 201.5 ms gap.
        self.assertEqual(h.scan(210, 436), ())
        return h.deliver(IMU_TYPE, imu_header_ms, imu_deliver_ms)

    def resume(self, h, expected_diagnostics):
        h.deliver(IMU_TYPE, 451.5, 453.5); h.deliver(IMU_TYPE, 501.5, 503.5)
        self.assertEqual(h.scan(410, 510), ())  # priming: no start IMU before its Header
        h.deliver(IMU_TYPE, 551.5, 553.5); h.deliver(IMU_TYPE, 601.5, 603.5)
        self.assertEqual(h.scan(510, 610), ())
        h.deliver(IMU_TYPE, 651.5, 653.5); h.deliver(IMU_TYPE, 701.5, 703.5)
        self.assertEqual(len(h.scan(610, 710)), 1)
        self.assertEqual(h.status()['diagnostic_count'], expected_diagnostics)
        self.assertIsNone(h.status()['fault'])

    def test_field_stall_is_an_owned_full_reanchor_wait_and_stream_resumes(self):
        for preparation in (True, False):
            with self.subTest(preparation=preparation):
                h = self.make(preparation=preparation); h.prefix(); before = h.status()
                self.assertEqual(self.stall(h), ())
                status = h.status()
                self.assertEqual(status['waiting_reason'], GAP_WAIT)
                self.assertEqual(status['positive_age_imu_gap_wait_count'], 1)
                self.assertEqual(status['imu_gap_reanchor_count'], 0)
                timing = status['positive_age_imu_gap_wait_timing']
                self.assertEqual(timing['pairing_reason'], 'imu_bracket_gap_exceeded')
                self.assertEqual(timing['boundaries'][1], dict(kind='bracket', gap_ns=201_500_000))
                self.assertGreaterEqual(timing['selected_lower_IMU']['RAW_age_ns'], 200_000_000)
                self.assertNoPublication(h, before)
                self.assertFullReanchor(h)
                self.assertInvalidated(h)
                self.resume(h, 2)

    def test_feature_off_keeps_the_v20_fatal_reasons(self):
        for preparation, reason in ((True, 'physical_original_pair_rejected:imu_bracket_gap_exceeded'),
                                    (False, 'physical_original_pair_rejected:stale_original_causal_imu')):
            with self.subTest(preparation=preparation):
                h = self.make(preparation=preparation, positive_gap=False); h.prefix()
                with self.assertRaisesRegex(ValueError, '^' + reason + '$'):
                    self.stall(h)
                self.assertEqual(h.status()['positive_age_imu_gap_wait_count'], 0)

    def test_fresh_gap_remains_the_v9_wait(self):
        h = self.make(); h.prefix()
        h.imu(280)  # IMU 250 missing: 80 ms fresh bracket around scan 210
        self.assertEqual(h.scan(210), ())
        status = h.status()
        self.assertEqual(status['waiting_reason'], 'waiting_for_fresh_scan_after_original_IMU_gap')
        self.assertEqual((status['imu_gap_reanchor_count'], status['positive_age_imu_gap_wait_count']), (1, 0))

    def test_horizon_span_loss_is_its_own_exact_wait(self):
        h = self.make(); h.prefix()
        self.assertEqual(self.stall(h, imu_deliver_ms=620), ())  # start brackets pruned by the 500 ms horizon
        status = h.status()
        self.assertEqual(status['waiting_reason'], SPAN_WAIT)
        self.assertEqual(status['positive_age_imu_gap_wait_timing']['boundaries'][0]['kind'], 'below_retained_span')
        self.assertFullReanchor(h)

    def test_end_IMU_never_received_is_a_sequencing_fault_not_a_wait(self):
        h = self.make(); h.prefix()
        self.assertEqual(h.scan(210, 436), ())
        h.clock.ns = START + 700 * MS
        outcome = h.ingress.prepare(positive_age_factory=h.factory)
        self.assertEqual(outcome.reason, 'end_original_IMU_not_received_cannot_be_positive_age_wait')
        self.assertEqual(h.ingress.fault, outcome.reason)
        self.assertEqual(h.ingress.positive_age_imu_gap_wait_count, 0)

    def test_malformed_unused_IMU_group_in_window_stays_fatal(self):
        h = self.make(); h.prefix()
        # Malformed lower endpoint of the gapped bracket: pair_imu returns the gap
        # before inspecting its groups; the WAIT guards validate every group.
        h.deliver(IMU_TYPE, 205, 211, imu_cdr(START + 205 * MS, quaternion=(0., 0., 0., 2.)))
        with self.assertRaisesRegex(ValueError, 'physical_original_pair_rejected:imu_orientation_nonunit_quaternion'):
            self.stall(h)
        self.assertEqual(h.status()['positive_age_imu_gap_wait_count'], 0)

    def test_tampered_retained_ownership_stays_fatal(self):
        h = self.make(); h.prefix()
        imu = h.ingress.imus[0]
        owned = h.ingress._owners[id(imu)]
        h.ingress._owners[id(imu)] = (owned[0], ('0' * 64, owned[1][1]))
        with self.assertRaisesRegex(ValueError, 'unowned_or_changed_original_envelope'):
            self.stall(h)
        self.assertEqual(h.status()['positive_age_imu_gap_wait_count'], 0)

    def test_stop_between_signal_and_quarantine_never_waits(self):
        h = self.make(); h.prefix()
        real = h.ingress._positive_age_imu_gap_guards
        calls = []
        def guards(*args):
            calls.append(args)
            if len(calls) == 2:
                h.adapter.stop()
            return real(*args)
        h.ingress._positive_age_imu_gap_guards = guards
        self.assertEqual(self.stall(h), ())
        self.assertEqual(len(calls), 2)
        status = h.status()
        self.assertIs(status['stopped'], True)
        self.assertEqual(status['positive_age_imu_gap_wait_count'], 0)
        self.assertIsNone(h.ingress._positive_age_gap_wait)

    def test_forged_wait_and_counter_exhaustion(self):
        h = self.make(); h.prefix()
        forged = physical_ingress._PositiveAgeImuGapWait(GAP_WAIT, h.ingress.generation, h.ingress)
        self.assertFalse(h.ingress.is_owned_positive_age_imu_gap_wait(forged))
        h.ingress.positive_age_imu_gap_wait_count = (1 << 63) - 1
        with self.assertRaisesRegex(ValueError, 'positive_age_IMU_gap_wait_counter_exhausted'):
            self.stall(h)


class ScanContinuityTests(Base):
    def test_missing_scan_keeps_newest_scan_and_next_scan_resumes(self):
        h = self.make(); h.prefix(); before = h.status()
        h.imu(250); h.imu(300); h.imu(350); h.imu(400)
        self.assertEqual(h.scan(320), ())  # 210 ms Header interval: a dropped revolution
        status = h.status()
        self.assertEqual(status['waiting_reason'], MISSING_WAIT)
        self.assertEqual(status['scan_continuity_wait_counts'],
                         dict(missing_original_scan=1, pending_pair_replaced=0))
        self.assertEqual(status['scan_continuity_wait_timing']['header_interval_ns'], 210 * MS)
        self.assertNoPublication(h, before)
        self.assertInvalidated(h)
        self.assertEqual(h.header(h.ingress.previous), 320)
        self.assertTrue(h.ingress.imus)
        h.imu(450); h.imu(500)
        self.assertEqual(len(h.scan(420)), 1)
        self.assertEqual(h.status()['diagnostic_count'], 2)

    def test_exact_200ms_interval_is_still_an_ordinary_pair(self):
        h = self.make(); h.prefix()
        h.imu(250); h.imu(300); h.imu(350)
        self.assertEqual(len(h.scan(310, 400)), 1)  # exactly PAIR_INTERVAL_NS: no WAIT
        self.assertEqual(h.status()['scan_continuity_wait_count'], 0)

    def test_pending_pair_replaced_is_a_full_reanchor_and_stream_resumes(self):
        h = self.make(); h.prefix(); before = h.status()
        self.assertEqual(h.scan(210), ())  # end IMU stalled: pair waits
        self.assertEqual(h.scan(310), ())
        status = h.status()
        self.assertEqual(status['waiting_reason'], REPLACED_WAIT)
        self.assertEqual(status['scan_continuity_wait_counts'],
                         dict(missing_original_scan=0, pending_pair_replaced=1))
        self.assertNoPublication(h, before)
        self.assertFullReanchor(h)
        h.imu(420); h.imu(470)
        self.assertEqual(h.scan(410), ())  # priming, unadmitted
        h.imu(520); h.imu(570); h.scan(510)
        h.imu(620); h.imu(670)
        self.assertEqual(len(h.scan(610)), 1)
        self.assertEqual(h.status()['diagnostic_count'], 2)

    def test_feature_off_keeps_the_v20_fatal_reasons(self):
        h = self.make(continuity=False); h.prefix(); h.imu(250); h.imu(300)
        with self.assertRaisesRegex(ValueError, '^native_original_ingress_rejected:missing_or_reordered_adjacent_native_pair$'):
            h.scan(320)
        h = self.make(continuity=False); h.prefix(); h.scan(210)
        with self.assertRaisesRegex(ValueError, '^native_original_ingress_rejected:pending_pair_replaced_continuity_lost$'):
            h.scan(310)

    def test_rewind_publisher_change_and_tampered_ownership_stay_fatal(self):
        h = self.make(); h.prefix()
        with self.assertRaisesRegex(ValueError, 'observed_original_Header_rewind'):
            h.scan(100, 400)
        h = self.make(); h.prefix(); h.imu(250); h.imu(300)
        imu = h.ingress.imus[-1]
        owned = h.ingress._owners[id(imu)]
        h.ingress._owners[id(imu)] = (owned[0], ('0' * 64, owned[1][1]))
        with self.assertRaisesRegex(ValueError, 'native_original_ingress_rejected:unowned_or_changed_original_envelope'):
            h.scan(320)
        self.assertEqual(h.status()['scan_continuity_wait_count'], 0)
        h = self.make(); h.prefix(); h.imu(250); h.imu(300)
        h.ingress.expected_gids = (bytes.fromhex('6' * 32), IMU_GID)
        with self.assertRaises(ValueError):
            h.scan(320)
        self.assertEqual(h.status()['scan_continuity_wait_count'], 0)

    def test_unprovable_wait_is_fatal_and_admitted_accounting_is_exact(self):
        h = self.make(); h.prefix(); h.imu(250); h.imu(300); h.imu(350); h.imu(400)
        admitted = h.status()['admitted_samples']
        self.assertEqual(h.scan(320), ())  # keep-newest anchor is admitted into the receiver
        self.assertEqual(h.status()['admitted_samples'][LASER_TYPE], admitted[LASER_TYPE] + 1)
        h = self.make(); h.prefix(); h.scan(210)
        admitted = h.status()['admitted_samples']
        self.assertEqual(h.scan(310), ())  # full re-anchor drops the triggering scan
        self.assertEqual(h.status()['admitted_samples'][LASER_TYPE], admitted[LASER_TYPE])
        h = self.make(); h.prefix(); h.imu(250); h.imu(300)
        h.ingress.is_owned_scan_continuity_wait = lambda value: False
        with self.assertRaisesRegex(ValueError, '^unowned_native_scan_continuity_wait$'):
            h.scan(320)

    def test_stop_between_signal_and_quarantine_never_waits(self):
        h = self.make(); h.prefix(); h.imu(250); h.imu(300)
        real = h.ingress._scan_continuity_guards
        calls = []
        def guards(*args):
            calls.append(args)
            if len(calls) == 2:
                h.adapter.stop()
            return real(*args)
        h.ingress._scan_continuity_guards = guards
        self.assertEqual(h.scan(320), ())
        self.assertEqual(len(calls), 2)
        self.assertIs(h.status()['stopped'], True)
        self.assertEqual(h.status()['scan_continuity_wait_count'], 0)


class NonMapRowsParityTests(Base):
    """stream_rows.py shares the WAIT logic; repeat one WAIT of each kind there."""
    rows = stream_rows

    def test_each_v21_wait_is_owned_in_non_map_rows(self):
        h = self.make(); h.prefix(); h.results = [REJECT]
        self.assertEqual(h.cycle(210), ())
        self.assertEqual(h.status()['waiting_reason'], REG_WAIT)
        h.results = [ACCEPT]
        self.assertEqual(len(h.cycle(310)), 1)
        h = self.make(); h.prefix()
        self.assertEqual(PositiveAgeImuGapTests.stall(self, h), ())
        self.assertEqual(h.status()['waiting_reason'], GAP_WAIT)
        h = self.make(); h.prefix(); h.imu(250); h.imu(300)
        self.assertEqual(h.scan(320), ())
        self.assertEqual(h.status()['waiting_reason'], MISSING_WAIT)
        for h in (self.make(), self.make(registration=False, positive_gap=False, continuity=False)):
            self.assertIsNone(h.status()['fault'])
        self.assertInvalidated(h, 0)


class DefaultOffConstructorTests(unittest.TestCase):
    def test_constructor_defaults_are_off_and_exact_bool(self):
        contract = PhysicalShadowContract('laser', 'imu_link')
        kwargs = dict(raw_clock=Clocks(), source_now_ns=lambda: START, source_clock_kind=lambda: 'ROS_SYSTEM_TIME',
                      scan_frame='laser', imu_frame='imu_link', profile_revision=revision(contract),
                      scan_publisher_gid=SCAN_GID, imu_publisher_gid=IMU_GID, observer_contract_sha256=STREAM_SHA)
        ingress = PhysicalOriginalIngress(**kwargs)
        self.assertIs(ingress.positive_age_imu_gap_reanchor_enabled, False)
        self.assertIs(ingress.scan_continuity_reanchor_enabled, False)
        for key in ('positive_age_imu_gap_reanchor_enabled', 'scan_continuity_reanchor_enabled'):
            for value in (1, 'true', None):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'typed_v21'):
                    PhysicalOriginalIngress(**kwargs, **{key: value})
        ingress.reset()
        factory = PhysicalNativeShadowFactory(ingress, contract)
        self.assertIs(factory.registration_rejection_reanchor_enabled, False)
        for value in (1, 'true', None):
            with self.assertRaisesRegex(ValueError, 'typed_registration_rejection'):
                PhysicalNativeShadowFactory(ingress, contract, registration_rejection_reanchor_enabled=value)


if __name__ == '__main__':
    unittest.main()
