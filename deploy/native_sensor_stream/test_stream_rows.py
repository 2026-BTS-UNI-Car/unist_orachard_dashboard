"""Portable synthetic original CDR tests; no ROS, process or recorded data.

All receive paths use the unchanged real PhysicalOriginalIngress/CDR decoder.
Only the numeric registration solver is mocked in pair-drain tests; this does
not qualify physical geometry or installed native callback provenance.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1] / 'native_sensor_shadow' / 'physical-package'
sys.path.insert(0, str(PACKAGE))

from common.original_cdr import LASER_TYPE, IMU_TYPE, decode, original_atom_kinds
from common.native_ownership import RawReceipt, RAW_SCHEMA
from physical_ingress import PhysicalOriginalIngress
from physical_factory import PhysicalNativeShadowFactory, PhysicalShadowContract, revision
from stream_rows import StreamObserverRows, MAX_COUNTER, MAX_LINE_BYTES, SCHEMA

DOMAIN = '1' * 64
STREAM_SHA = '2' * 64  # explicit separate synthetic contract, not finite inputs.OBSERVER_SHA
SCAN_GID, IMU_GID = bytes.fromhex('3' * 32), bytes.fromhex('4' * 32)
PID, NS = 123, 'time:[123]'
START = 1_000_000_000_000


class SyntheticCDR:
    """Test-only CDR1 serializer, used to create originals before ingestion."""
    def __init__(self, endian='<'):
        self.endian = endian
        self.data = bytearray(b'\x00\x01\x00\x00' if endian == '<' else b'\x00\x00\x00\x00')

    def raw(self, value, alignment=1):
        self.data.extend(b'\0' * ((-(len(self.data)-4)) % alignment))
        self.data.extend(value)

    def scalar(self, kind, value):
        self.raw(struct.pack(self.endian+kind, value), struct.calcsize(kind))

    def header(self, ns, frame):
        self.scalar('i', ns//1_000_000_000)
        self.scalar('I', ns % 1_000_000_000)
        value = frame.encode('utf-8') + b'\0'
        self.scalar('I', len(value))
        self.raw(value)


def imu_cdr(ns, *, frame='imu_link', endian='<'):
    c = SyntheticCDR(endian)
    c.header(ns, frame)
    for values in ((0., 0., 0., 1.), (0.,)*9, (0., 0., .07), (0.,)*9,
                   (0., 0., 9.80665), (0.,)*9):
        c.raw(struct.pack(endian+'d'*len(values), *values), 8)
    return bytes(c.data)


def scan_cdr(ns, *, count=120, endian='<', raw_atoms=None):
    c = SyntheticCDR(endian)
    c.header(ns, 'laser')
    c.raw(struct.pack(endian+'7f', -.6, -.6+(count-1)*.01, .01, 0., .1, .05, 50.), 4)
    c.scalar('I', count)
    c.raw(raw_atoms if raw_atoms is not None else struct.pack(endian+'f'*count, *((2.,)*count)), 4)
    c.scalar('I', count)
    c.raw(struct.pack(endian+'f'*count, *((1.,)*count)), 4)
    return bytes(c.data)


class Clocks:
    def __init__(self):
        self.ns = START

    @property
    def raw_ns(self):
        return 10_000_000_000 + self.ns - START

    def sample(self):
        return RawReceipt(self.raw_ns, DOMAIN)

    def at(self, source_ns):
        self.ns = source_ns + 1_000_000


def line(row):
    return (json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n').encode('utf-8')


class StreamRowsTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clocks()
        contract = PhysicalShadowContract('laser', 'imu_link')
        self.ingress = PhysicalOriginalIngress(raw_clock=self.clock,
            source_now_ns=lambda: self.clock.ns, source_clock_kind=lambda: 'ROS_SYSTEM_TIME',
            scan_frame='laser', imu_frame='imu_link', profile_revision=revision(contract),
            scan_publisher_gid=SCAN_GID, imu_publisher_gid=IMU_GID,
            observer_contract_sha256=STREAM_SHA)
        self.ingress.reset()
        self.factory = PhysicalNativeShadowFactory(self.ingress, contract)
        self.adapter = StreamObserverRows(self.ingress, self.factory,
                                          observer_contract_sha256=STREAM_SHA)
        self.adapter.bind_process(PID, NS)
        self.seq = 0

    def begin(self):
        return dict(seq=0, kind='begin', schema=SCHEMA, streaming=True, pid=PID, uid=0, gid=0,
            time_namespace=NS, observer_process_session='5'*32, RAW_domain=DOMAIN,
            GID_storage_bytes=16, expected_scan_GID=SCAN_GID.hex(), expected_IMU_GID=IMU_GID.hex(),
            header_clock_origin='original_publisher_unmodified', observer_clock_type='RCL_SYSTEM_TIME',
            use_sim_time=False, clock_topic_created=False, publishers_created=False, mission_admission=False)

    def emit(self, row):
        row = dict(row, seq=self.seq)
        answer = self.adapter.receive_line(line(row))
        self.seq += 1
        return answer

    def start(self):
        self.emit(self.begin())

    def original(self, ros_type, cdr):
        wire = decode(cdr, ros_type)
        return dict(kind='original_cdr', ros_type=ros_type,
            topic='/scan' if ros_type == LASER_TYPE else '/imu/data_raw',
            original_CDR_hex=cdr.hex(), original_CDR_bytes=len(cdr),
            original_CDR_sha256=hashlib.sha256(cdr).hexdigest(),
            original_header_stamp_sec=wire.header.sec, original_header_stamp_nanosec=wire.header.nanosec,
            original_header_stamp_ns=wire.header.stamp_ns,
            frame_id_bytes_hex=wire.header.frame_id.encode('utf-8').hex(),
            ranges_count=wire.count if ros_type == LASER_TYPE else 0,
            intensities_count=len(wire.intensity_f32_bytes)//4 if ros_type == LASER_TYPE else 0,
            RAW_schema=RAW_SCHEMA, RAW_domain=DOMAIN, callback_monotonic_raw_ns=self.clock.raw_ns,
            observer_system_ros_now_ns=self.clock.ns,
            publisher_gid_hex=(SCAN_GID if ros_type == LASER_TYPE else IMU_GID).hex(),
            proposed_linear_mps=0., proposed_yaw_rate_rad_s=0., mission_admission=False)

    def sample(self, kind, at, **kwargs):
        self.clock.at(at)
        cdr = scan_cdr(at, **kwargs) if kind == LASER_TYPE else imu_cdr(at, **kwargs)
        return self.emit(self.original(kind, cdr))

    def ready(self):
        return dict(kind='ready', actual_original_CDR_and_per_message_GIDs=True,
                    stable_graph_min_seconds=.05, mission_admission=False)

    def done(self, **changes):
        value = dict(kind='done', schema=SCHEMA, streaming=True, termination_reason='operator_stop',
            ready_observed=self.adapter.ready, ok=True, error='', scan_samples=self.adapter.samples[LASER_TYPE],
            IMU_samples=self.adapter.samples[IMU_TYPE], ROS_node_destroyed=True,
            ROS_executor_destroyed=True, ROS_subscriptions_destroyed=True,
            ROS_shutdown_completed=True, physical_autonomy_qualified=False,
            observed_elapsed_ns=123_000_000, mission_admission=False)
        value.update(changes)
        return value

    def test_separate_contract_and_exact_types_are_required_before_streaming(self):
        with self.assertRaisesRegex(ValueError, 'streaming_observer_contract'):
            StreamObserverRows(self.ingress, self.factory, observer_contract_sha256='0'*64)
        with self.assertRaisesRegex(ValueError, 'physical_original_ingress'):
            StreamObserverRows(object(), self.factory, observer_contract_sha256=STREAM_SHA)
        with self.assertRaisesRegex(ValueError, 'physical_original_factory'):
            StreamObserverRows(self.ingress, object(), observer_contract_sha256=STREAM_SHA)

    def test_original_LE_and_BE_decoder_keeps_header_atoms_and_exact_bytes(self):
        for endian in ('<', '>'):
            with self.subTest(endian=endian):
                atoms = struct.pack(endian+'5I', 0x7f800001, 0x7fc00001, 0x80000000,
                                    0x7f800000, 0xff800000) + struct.pack(endian+'115f', *((2.,)*115))
                original = scan_cdr(START, endian=endian, raw_atoms=atoms)
                wire = decode(original, LASER_TYPE)
                self.assertEqual(original_atom_kinds(wire.range_f32_bytes, endian)[:5],
                                 ('sNaN', 'qNaN', '-0', '+Inf', '-Inf'))
                self.assertEqual(wire.header.stamp_ns, START)
                self.assertEqual(wire.range_f32_bytes, atoms)
                imu = decode(imu_cdr(START, endian=endian), IMU_TYPE)
                self.assertEqual(tuple(imu.groups[0]), (0., 0., 0., 1.))
        self.start()
        self.sample(IMU_TYPE, START)
        self.sample(LASER_TYPE, START+1_000_000)
        self.assertEqual(self.ingress.previous.original_cdr, scan_cdr(START+1_000_000))

    def test_every_pair_drains_through_real_receiver_and_factory_with_solver_only_mocked(self):
        self.start()
        fit = dict(accepted=True, xy=(.01, 0.), yaw=.002, coverage=.9, rmse_m=.02)
        with patch('physical_factory.registration', return_value=fit) as solver:
            self.sample(IMU_TYPE, START)
            self.sample(LASER_TYPE, START)
            self.sample(IMU_TYPE, START+50_000_000)
            self.sample(IMU_TYPE, START+100_000_000)
            first = self.sample(LASER_TYPE, START+100_000_000)
            self.emit(self.ready())
            self.sample(IMU_TYPE, START+150_000_000)
            self.sample(IMU_TYPE, START+200_000_000)
            second = self.sample(LASER_TYPE, START+200_000_000)
        self.assertEqual(solver.call_count, 2)
        self.assertEqual(self.factory.snapshot().version, 2)
        self.assertIsNone(self.ingress.pending)
        self.assertIsNone(self.ingress.prepared)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        diagnostic = second[0]['diagnostic']
        self.assertIsNone(diagnostic['body_velocity_mps'])
        for key in ('runtime_authority', 'motor_command_sent', 'autonomy_output_enabled',
                    'physical_autonomy_qualified', 'execution_eligible'):
            self.assertIs(diagnostic[key], False)
        self.assertEqual(diagnostic['diagnostic']['original_scan_stamps_ns'],
                         [START+100_000_000, START+200_000_000])
        self.assertIn('stages_ns', second[0]['evidence']['diagnostic_stage_timing'])
        second[0]['evidence']['samples'] = -1
        self.assertEqual(self.adapter.rows, 8)  # caller-owned response cannot mutate adapter
        self.adapter.stop()
        self.emit(self.done())
        result = self.adapter.finish(0, True)
        self.assertTrue(result['owner_cleanup'])
        self.assertTrue(result['data_ready_before_stop'])

    def test_over4096_real_ingress_samples_preserve_finite_receiver_ownership(self):
        self.start()
        for i in range(4100):
            self.sample(IMU_TYPE, START+i*1_000_000)
        self.assertEqual(self.adapter.rows, 4100)
        self.assertEqual(self.adapter.admitted_samples[IMU_TYPE], 4100)
        self.assertEqual(self.ingress.sequence, 4100)
        self.assertLessEqual(len(self.ingress.imus), 64)
        self.assertLessEqual(len(self.ingress._owners), 136)
        self.assertFalse(self.adapter.ready)

    def test_startup_scans_before_IMU_prime_without_fabricating_old_brackets(self):
        self.start()
        self.sample(LASER_TYPE, START)
        self.sample(LASER_TYPE, START+50_000_000)
        self.assertIsNone(self.ingress.previous)
        self.assertEqual(self.adapter.bootstrap_scans_unadmitted, 2)
        self.sample(IMU_TYPE, START+50_000_000)
        self.sample(IMU_TYPE, START+100_000_000)
        self.emit(self.ready())
        self.assertTrue(self.adapter.ready)
        self.assertFalse(self.adapter._ready_before_stop)
        with patch('physical_factory.registration', return_value=dict(
                accepted=True, xy=(.01, 0.), yaw=.002, coverage=.9, rmse_m=.02)) as solver:
            self.sample(LASER_TYPE, START+100_000_000)
            self.sample(IMU_TYPE, START+150_000_000)
            self.sample(IMU_TYPE, START+200_000_000)
            records = self.sample(LASER_TYPE, START+200_000_000)
        self.assertEqual(solver.call_count, 1)
        self.assertEqual(len(records), 1)
        self.assertTrue(self.adapter._ready_before_stop)
        self.assertEqual(records[0]['diagnostic']['diagnostic']['original_scan_stamps_ns'],
                         [START+100_000_000, START+200_000_000])
        self.assertEqual(self.adapter.samples[LASER_TYPE], 4)
        self.assertEqual(self.adapter.admitted_samples[LASER_TYPE], 2)
        self.adapter.stop()
        self.emit(self.done())
        self.assertTrue(self.adapter.finish(0, True)['owner_cleanup'])

    def test_pending_pair_waits_for_late_original_IMU_without_retimestamp(self):
        self.start()
        self.sample(IMU_TYPE, START)
        self.sample(LASER_TYPE, START)
        self.assertEqual(self.sample(LASER_TYPE, START+100_000_000), ())
        self.assertIsNotNone(self.ingress.pending)
        self.assertEqual(self.adapter.waiting_reason, 'waiting_for_original_end_IMU')
        with patch('physical_factory.registration', return_value=dict(
                accepted=True, xy=(.01, 0.), yaw=.002, coverage=.9, rmse_m=.02)) as solver:
            self.emit(self.original(IMU_TYPE, imu_cdr(START+50_000_000)))
            self.assertIsNotNone(self.ingress.pending)
            records = self.emit(self.original(IMU_TYPE, imu_cdr(START+100_000_000)))
        self.assertEqual(solver.call_count, 1)
        self.assertEqual(len(records), 1)
        self.assertIsNone(self.ingress.pending)
        self.assertEqual(records[0]['diagnostic']['diagnostic']['original_scan_stamps_ns'],
                         [START, START+100_000_000])

    def test_native_numpy_diagnostic_is_publishable_and_latest_input_keeps_original_clocks(self):
        import numpy as np
        from dashboard_server.native_owner_lifecycle import NativeOwnerLifecycle, ResourceHooks
        self.start()
        with patch('physical_factory.registration', return_value=dict(accepted=True,
                xy=(np.float64(.01), np.float64(0.)), yaw=np.float64(.002),
                coverage=np.float64(.9), rmse_m=np.float64(.02))):
            self.sample(IMU_TYPE, START)
            self.sample(LASER_TYPE, START)
            self.sample(IMU_TYPE, START+50_000_000)
            self.sample(IMU_TYPE, START+100_000_000)
            records = self.sample(LASER_TYPE, START+100_000_000)
        contexts = []
        def factory(ctx):
            contexts.append(ctx)
            return ResourceHooks(lambda: None, lambda timeout: True, lambda: True)
        owner = NativeOwnerLifecycle(factory)
        owner.start('synthetic-native-numpy-diagnostic')
        self.assertTrue(contexts[0].publish(records[0]['diagnostic'], records[0]['evidence']))
        self.assertIsNotNone(owner.status().latest)
        owner.stop()
        first = self.adapter.proposal_input()
        self.clock.ns += 20_000_000
        second = self.adapter.proposal_input()
        self.assertEqual(first['scan'], second['scan'])
        self.assertEqual(first['imu'], second['imu'])
        self.assertGreater(second['source_now_ns'], first['source_now_ns'])
        self.assertGreater(second['receipt_now_ns'], first['receipt_now_ns'])
        self.assertTrue(first['scan']['original_native'])
        self.assertFalse(first['runtime_authority'])
        self.assertFalse(first['motion']['body_mount_verified'])
        first['scan']['ranges'][0] = -123
        self.assertNotEqual(self.adapter.proposal_input()['scan']['ranges'][0], -123)
        self.clock.ns += 200_000_000
        self.assertIsNone(self.adapter.proposal_input())
        self.assertTrue(second['scan']['original_native'])
        self.adapter.stop()
        self.assertIsNone(self.adapter.proposal_input())

    def test_stop_drain_can_exceed16MiB_without_live_admission_or_history(self):
        self.start()
        self.adapter.stop()
        original = scan_cdr(START, count=4096)
        for _ in range(520):
            self.assertEqual(self.emit(self.original(LASER_TYPE, original)), ())
        self.assertGreater(self.adapter.total_original_bytes, 16*1024*1024)
        self.assertEqual(self.adapter.admitted_samples[LASER_TYPE], 0)
        self.assertEqual(self.factory.snapshot().version, 0)
        self.assertIsNone(self.ingress.previous)
        self.emit(self.done())
        self.assertTrue(self.adapter.finish(0, True)['owner_cleanup'])

    def test_operator_stop_before_ready_separates_cleanup_and_data_readiness(self):
        self.adapter.stop()
        self.start()
        self.sample(IMU_TYPE, START)
        self.emit(self.done())
        result = self.adapter.finish(0, True)
        self.assertTrue(result['ok'])
        self.assertTrue(result['owner_cleanup'])
        self.assertFalse(result['ready_observed'])
        self.assertFalse(result['data_ready_before_stop'])
        self.assertEqual(self.adapter.admitted_samples[IMU_TYPE], 0)

    def test_prebegin_fault_preserves_original_error_and_never_admits_cleanup(self):
        prefix = 'native_subscription_constructor_failed:'
        error = prefix + 'x'*(512-len(prefix))
        self.assertEqual(len(error), 512)
        row = self.done(ok=False, termination_reason='fault', error=error,
                        ROS_executor_destroyed=False, ROS_subscriptions_destroyed=False,
                        ROS_node_destroyed=False, ROS_shutdown_completed=False)
        expected = 'native_observer_startup_fault:' + error
        with self.assertRaises(ValueError) as raised:
            self.emit(row)
        self.assertEqual(str(raised.exception), expected)
        status = self.adapter.status()
        self.assertEqual(status['fault'], expected)
        self.assertTrue(status['ended'])
        self.assertTrue(status['stopped'])
        self.assertFalse(status['ready_observed'])
        self.assertFalse(status['data_ready_before_stop'])
        self.assertIsNone(self.adapter.begin)
        self.assertEqual(status['sample_rows'], 0)
        self.assertEqual(status['diagnostic_count'], 0)
        self.assertEqual(self.ingress.fault, 'STOP_latched')
        for code in (0, 1):
            result = self.adapter.finish(code, True)
            self.assertFalse(result['ok'])
            self.assertFalse(result['owner_cleanup'])
            self.assertEqual(result['error'], expected)
        with self.assertRaises(ValueError):
            self.adapter.receive_line(line(self.begin()))
        self.assertEqual(self.adapter.fault, expected)

    def test_prebegin_fault_spoof_nonzero_or_bad_metadata_cannot_pass_error_through(self):
        changes = ({'schema': 'spoof'}, {'streaming': 1}, {'ok': True},
            {'termination_reason': 'operator_stop'}, {'ready_observed': True},
            {'error': None}, {'error': ''}, {'error': 'x'*513},
            {'scan_samples': 1}, {'IMU_samples': 1}, {'scan_samples': False},
            {'IMU_samples': None}, {'physical_autonomy_qualified': True},
            {'mission_admission': True}, {'seq': 1})
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                row = self.done(ok=False, termination_reason='fault', error='actual_error')
                row = dict(row, seq=0)
                row.update(change)
                with self.assertRaises(ValueError):
                    self.adapter.receive_line(line(row))
                self.assertFalse(self.adapter.fault.startswith('native_observer_startup_fault:'))
                self.assertTrue(self.adapter.ended)
                self.assertFalse(self.adapter.finish(1, True)['owner_cleanup'])

    def test_prebegin_fault_still_requires_bound_process_context_and_metadata_cap(self):
        for case in ('unbound', 'changed_context', 'oversized'):
            with self.subTest(case=case):
                self.setUp()
                row = dict(self.done(ok=False, termination_reason='fault', error='actual_error'), seq=0)
                if case == 'unbound':
                    self.adapter.expected_pid = None
                elif case == 'changed_context':
                    self.ingress.observer_contract_sha256 = '8'*64
                else:
                    row['extra'] = 'x'*8192
                with self.assertRaises(ValueError):
                    self.adapter.receive_line(line(row))
                self.assertFalse(self.adapter.fault.startswith('native_observer_startup_fault:'))
                self.assertTrue(self.adapter.ended)

    def test_stop_during_numeric_prepare_never_publishes_or_consumes_next_input(self):
        self.start()
        self.sample(IMU_TYPE, START)
        self.sample(LASER_TYPE, START)
        self.sample(IMU_TYPE, START+100_000_000)
        def stop_in_fit(*_args):
            self.adapter.stop()
            return dict(accepted=True, xy=(.01, 0.), yaw=0., coverage=.9, rmse_m=.02)
        with patch('physical_factory.registration', side_effect=stop_in_fit):
            self.assertEqual(self.sample(LASER_TYPE, START+100_000_000), ())
        self.assertEqual(self.factory.snapshot().version, 0)
        self.assertIsNone(self.adapter.fault)
        self.emit(self.done())
        self.assertTrue(self.adapter.finish(0, True)['owner_cleanup'])

    def test_sequence_gaps_rewinds_bools_and_signed63_overflow_latch_stop(self):
        for seq in (1, -1, True, 2**63):
            with self.subTest(seq=seq):
                self.setUp()
                with self.assertRaisesRegex(ValueError, 'sequence'):
                    self.adapter.receive_line(line(dict(self.begin(), seq=seq)))
                self.assertTrue(self.adapter.ended)
                self.assertEqual(self.ingress.fault, 'STOP_latched')
        self.setUp();self.start()
        with self.assertRaisesRegex(ValueError, 'sequence'):
            self.adapter.receive_line(line(self.begin()))

    def test_header_SHA_array_GID_RAW_and_nonzero_proposal_fail_closed(self):
        for key, value in (('original_header_stamp_ns', START+1), ('original_CDR_sha256', '0'*64),
                           ('ranges_count', 1), ('publisher_gid_hex', '6'*32),
                           ('RAW_domain', '7'*64), ('proposed_linear_mps', .01)):
            with self.subTest(key=key):
                self.setUp();self.start();self.clock.at(START)
                row = self.original(IMU_TYPE, imu_cdr(START))
                row[key] = value
                with self.assertRaises(ValueError):self.emit(row)
                self.assertTrue(self.adapter.ended)
                self.assertIsNotNone(self.adapter.fault)
                self.assertEqual(self.ingress.fault, 'STOP_latched')

    def test_physical_source_and_receipt_TTL_and_clock_jump_are_unchanged(self):
        for failure in ('source_age', 'receipt_age', 'clock_jump'):
            with self.subTest(failure=failure):
                self.setUp();self.start();self.clock.at(START)
                row = self.original(IMU_TYPE, imu_cdr(START))
                if failure == 'source_age':
                    self.clock.at(START+300_000_000)
                    row['callback_monotonic_raw_ns'] = self.clock.raw_ns
                elif failure == 'receipt_age':
                    self.clock.at(START+300_000_000)
                    row = self.original(IMU_TYPE, imu_cdr(START+300_000_000))
                    row['callback_monotonic_raw_ns'] -= 200_000_001
                else:self.clock.ns += 60_000_000  # source advances independently of RAW
                if failure == 'clock_jump':
                    fixed_raw = 10_001_000_000
                    with patch.object(self.clock, 'sample', return_value=RawReceipt(fixed_raw, DOMAIN)):
                        with self.assertRaises(ValueError):self.emit(row)
                else:
                    with self.assertRaisesRegex(ValueError, 'callback_system_time' if failure == 'source_age'
                                                else 'callback_RAW'):self.emit(row)
                self.assertTrue(self.adapter.ended)

    def test_ready_requires_two_originals_of_each_type_and_done_exact_ready_counts(self):
        self.start()
        with self.assertRaisesRegex(ValueError, 'readiness'):
            self.emit(self.ready())
        for changes in ({'ready_observed': True}, {'scan_samples': True}, {'IMU_samples': 2**63},
                        {'ROS_shutdown_completed': False}, {'termination_reason': 'fault'}, {'ok': False}):
            with self.subTest(changes=changes):
                self.setUp();self.start();self.adapter.stop()
                with self.assertRaises(ValueError):self.emit(self.done(**changes))
                self.assertFalse(self.adapter.finish(0, True)['owner_cleanup'])

    def test_trailing_duplicate_nonfinite_and_oversized_lines_latch_fault(self):
        for raw in (b'{"seq":0,"seq":0}\n', b'{"seq":NaN}\n', b'{"seq":1e999}\n', line(self.begin())+b'\n',
                    b'x'*(MAX_LINE_BYTES+1), line(self.begin())[:-1], b'\xff\n'):
            with self.subTest(raw=raw[:30]):
                self.setUp()
                with self.assertRaises((ValueError, UnicodeError)):self.adapter.receive_line(raw)
                self.assertTrue(self.adapter.ended)
        self.setUp()
        with self.assertRaisesRegex(ValueError, 'metadata'):
            self.emit(dict(self.begin(), extra='x'*8192))

    def test_begin_process_namespace_actor_contract_and_shutdown_must_match(self):
        for change in ({'pid': PID+1}, {'time_namespace': 'time:[other]'}, {'uid': 1000},
                       {'gid': True}, {'streaming': False}, {'mission_admission': True},
                       {'expected_scan_GID': IMU_GID.hex()}):
            with self.subTest(change=change):
                self.setUp()
                with self.assertRaises(ValueError):self.emit(dict(self.begin(), **change))
        self.setUp();self.start()
        with self.assertRaises(ValueError):self.emit(self.done())  # unrequested native stop
        self.assertFalse(self.adapter.finish(1, True)['owner_cleanup'])

    def test_post_stop_malformed_CDR_or_GID_is_not_silently_accepted(self):
        for change in ({'original_CDR_hex': '00010000'}, {'publisher_gid_hex': '6'*32}):
            with self.subTest(change=change):
                self.setUp();self.start();self.adapter.stop();self.clock.at(START)
                row = self.original(IMU_TYPE, imu_cdr(START));row.update(change)
                with self.assertRaises(ValueError):self.emit(row)
                self.assertIsNotNone(self.adapter.fault)

    def test_same_original_context_cannot_be_changed_or_rebound(self):
        with self.assertRaisesRegex(ValueError, 'single_use'):
            self.adapter.bind_process(PID, NS)
        self.start()
        self.ingress.observer_contract_sha256 = '8'*64
        self.clock.at(START)
        with self.assertRaisesRegex(ValueError, 'context_changed'):
            self.emit(self.original(IMU_TYPE, imu_cdr(START)))


if __name__ == '__main__':
    unittest.main()
