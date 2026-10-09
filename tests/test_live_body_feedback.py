"""Synthetic source-join tests; no hardware or actuator qualification."""
import math
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from dashboard_server.source_live_mapping import SourceLiveMapping
from test_integrated_mapping import scan


def stamp(ns):
    return dict(sec=ns//1_000_000_000,nanosec=ns%1_000_000_000)


def imu_packet(ns,receipt_ns,rate=0.):
    return dict(_mapping_ws_receipt_ns=receipt_ns,raw=dict(
        stamp=stamp(ns),frame_id='original_imu_frame',
        orientation=dict(x=0.,y=0.,z=0.,w=1.),
        angular_velocity=dict(x=0.,y=0.,z=rate),
        linear_acceleration=dict(x=0.,y=0.,z=9.80665),
        covariance=dict(orientation=[0.]*9,angular_velocity=[0.]*9)))


class LiveBodyFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.mapper=SourceLiveMapping(self.temp.name,start_worker=False)
        self.history=[]

    def tearDown(self):
        self.mapper.close()
        self.temp.cleanup()

    def observe(self,ns,receipt_ns,pose,**changes):
        packet=dict(stamp=ns/1e9,_mapping_ws_receipt_ns=receipt_ns,
            raw_scan=dict(header=dict(stamp=stamp(ns),frame_id='original_laser_frame')))
        result=dict(accepted=True,map_eligible=True,motion_prior=False,path_break=False,pose=pose)
        result.update(changes)
        self.mapper._observe_feedback(packet,receipt_ns/1e9,result,self.mapper.engine,self.history)
        return self.mapper.feedback_status()

    def test_sensor_plane_velocity_never_becomes_body_or_native_authority(self):
        self.observe(1_000_000_000_000,10_000_000_000,(0.,0.,0.))
        value=self.observe(1_000_100_000_000,10_100_000_000,(.02,0.,0.))
        self.assertAlmostEqual(value['laser']['forward_m_s'],.2)
        self.assertFalse(value['valid']);self.assertIsNone(value['body'])
        self.assertFalse(value['runtime_authority']);self.assertFalse(value['motor_command_sent'])
        self.assertFalse(value['adapter']['native_CDR_GID_RAW_context_available'])
        self.assertEqual(value['context']['receipt_clock'],'python_monotonic_web_ws')
        self.assertTrue(value['context']['source_session'].startswith('web_ws_subscriptions:'))

    def test_rejected_prior_and_relocalized_results_never_form_velocity_endpoints(self):
        for change in ({'accepted':False},{'map_eligible':False},
                       {'motion_prior':True},{'path_break':True}):
            with self.subTest(change=change):
                self.mapper._invalidate_feedback('test_reset',new_session=True)
                self.observe(1_000_000_000_000,10_000_000_000,(0.,0.,0.))
                old=self.observe(1_000_100_000_000,10_100_000_000,(.02,0.,0.))
                bad=self.observe(1_000_200_000_000,10_200_000_000,(4.,0.,0.),**change)
                self.assertEqual(bad['laser'],old['laser'])
                first=self.observe(1_000_300_000_000,10_300_000_000,(4.2,0.,0.))
                self.assertEqual(first['reason'],'first_geometric_pose_priming')
                fresh=self.observe(1_000_400_000_000,10_400_000_000,(4.22,0.,0.))
                self.assertAlmostEqual(fresh['laser']['forward_m_s'],.2)

    def test_actual_cursor_lower_imu_is_used_instead_of_future_nearest(self):
        lower=imu_packet(1_000_050_000_000,10_050_000_000,.7)
        future=imu_packet(1_000_200_000_000,10_090_000_000,8.)
        self.history=[(lower,10.05),(future,10.09)]
        self.mapper.engine.source.imus=[self.mapper.native_imu(p) for p,_ in self.history]
        self.mapper.engine.source.stamps=[1000.05,1000.2]
        self.observe(1_000_000_000_000,10_000_000_000,(0.,0.,0.))
        value=self.observe(1_000_100_000_000,10_100_000_000,(.02,0.,0.))
        self.assertEqual(value['adapter']['selected_imu_stamp_ns'],1_000_050_000_000)
        self.assertAlmostEqual(value['adapter']['candidate_vertical_gyro_rad_s'],.7)
        self.assertFalse(value['valid'])

    def test_float_timestamp_alias_cannot_pass_exact_future_imu_as_causal(self):
        ns=1_790_000_000_000_000_000
        future=imu_packet(ns+1,10_090_000_000,.7)
        self.history=[(future,10.09)]
        self.mapper.engine.source.imus=[self.mapper.native_imu(future)]
        self.mapper.engine.source.stamps=[ns*1e-9]
        self.observe(ns-100_000_000,10_000_000_000,(0.,0.,0.))
        value=self.observe(ns,10_100_000_000,(.02,0.,0.))
        self.assertIsNone(value['adapter']['selected_imu_stamp_ns'])

    def test_cursor_key_uses_original_sec_nsec_arithmetic_at_modern_epochs(self):
        ns=1_791_550_000_000_069_500
        lower=imu_packet(ns-500,10_050_000_000,.7)
        current=imu_packet(ns,10_090_000_000,8.)
        self.history=[(lower,10.05),(current,10.09)]
        self.mapper.engine.source.imus=[self.mapper.native_imu(p) for p,_ in self.history]
        self.mapper.engine.source.stamps=[
            p['raw']['stamp']['sec']+p['raw']['stamp']['nanosec']*1e-9 for p,_ in self.history]
        self.assertNotEqual(ns*1e-9,ns//1_000_000_000+(ns%1_000_000_000)*1e-9)
        self.observe(ns-100_000_000,10_000_000_000,(0.,0.,0.))
        value=self.observe(ns,10_100_000_000,(.02,0.,0.))
        self.assertEqual(value['adapter']['selected_imu_stamp_ns'],ns)
        self.assertEqual(value['adapter']['candidate_vertical_gyro_rad_s'],8.)

    def test_duplicate_header_conflict_or_receipt_ambiguity_cannot_replace_selected_imu(self):
        ns=1_000_050_000_000
        selected=imu_packet(ns,10_050_000_000,.7)
        for rate in (.7,8.):
            with self.subTest(rate=rate):
                self.mapper._invalidate_feedback('test_reset',new_session=True)
                duplicate=imu_packet(ns,10_060_000_000,rate)
                self.history=[(selected,10.05),(duplicate,10.06)]
                self.mapper.engine.source.imus=[self.mapper.native_imu(selected)]
                self.mapper.engine.source.stamps=[1000.05]
                self.observe(1_000_000_000_000,10_000_000_000,(0.,0.,0.))
                value=self.observe(1_000_100_000_000,10_100_000_000,(.02,0.,0.))
                self.assertIsNone(value['adapter']['selected_imu_stamp_ns'])
                self.assertIsNone(value['adapter']['candidate_vertical_gyro_rad_s'])
                self.assertFalse(value['valid'])

    def test_original_frames_are_required_and_display_fallbacks_are_not_evidence(self):
        self.observe(1_000_000_000_000,10_000_000_000,(0.,0.,0.))
        packet=dict(frame_id='laser',raw_scan=dict(header=dict(stamp=stamp(1_000_100_000_000))))
        result=dict(accepted=True,map_eligible=True,motion_prior=False,path_break=False,pose=(.02,0.,0.))
        self.mapper._observe_feedback(packet,10.1,result,self.mapper.engine,[])
        self.assertEqual(self.mapper.feedback_status()['reason'],'original_scan_clock_or_frame_missing')
        lower=imu_packet(1_000_250_000_000,10_250_000_000,.7)
        del lower['raw']['frame_id']
        self.history=[(lower,10.25)]
        self.mapper.engine.source.imus=[self.mapper.native_imu(lower)]
        self.mapper.engine.source.stamps=[1000.25]
        self.observe(1_000_200_000_000,10_200_000_000,(0.,0.,0.))
        value=self.observe(1_000_300_000_000,10_300_000_000,(.02,0.,0.))
        self.assertIsNone(value['adapter']['selected_imu_frame'])

    def test_stop_start_and_source_session_changes_require_new_prime(self):
        self.mapper.action('start')
        self.observe(1_000_000_000_000,10_000_000_000,(0.,0.,0.))
        old=self.observe(1_000_100_000_000,10_100_000_000,(.02,0.,0.))
        self.mapper.action('stop');self.mapper.action('start')
        first=self.observe(1_000_200_000_000,10_200_000_000,(4.,0.,0.))
        self.assertEqual(first['reason'],'first_geometric_pose_priming')
        value=self.observe(1_000_300_000_000,10_300_000_000,(4.02,0.,0.))
        self.assertNotEqual(value['context']['source_session'],old['context']['source_session'])
        self.assertAlmostEqual(value['laser']['forward_m_s'],.2)

    def test_large_gap_or_full_turn_cannot_be_aliased_into_small_speed(self):
        for ns,pose in ((1_000_400_000_000,(.02,0.,0.)),
                        (1_000_100_000_000,(.02,0.,2*math.pi))):
            self.mapper._invalidate_feedback('test_reset',new_session=True)
            self.observe(1_000_000_000_000,10_000_000_000,(0.,0.,0.))
            value=self.observe(ns,10_100_000_000,pose)
            self.assertEqual(value['reason'],'sensor_pose_pair_discontinuous')
            self.assertIsNone(value['body'])

    def test_restored_qualified_metadata_is_not_current_feedback(self):
        self.mapper.restored_snapshot={}
        self.mapper.snapshot={**self.mapper.snapshot,
            'body_feedback':dict(valid=True,body=dict(forward_m_s=9.))}
        value=self.mapper.status()['body_feedback']
        self.assertFalse(value['valid']);self.assertIsNone(value['body'])

    def test_observer_error_cannot_interrupt_mapping_or_publish_old_feedback(self):
        self.mapper.action('start')
        for i in range(2):
            ns=1_000_000_000_000+i*100_000_000
            receipt_ns=time.monotonic_ns()
            imu=imu_packet(ns,receipt_ns)
            self.mapper.imu_history.append((imu,receipt_ns/1e9))
            packet=dict(stamp=ns/1e9,_mapping_ws_receipt_ns=receipt_ns,
                        raw_scan=scan(ns/1e9,i*.02))
            if i:
                with patch.object(self.mapper.body_feedback,'observe',side_effect=RuntimeError('synthetic failure')):
                    self.assertTrue(self.mapper.ingest((packet,receipt_ns/1e9),(imu,receipt_ns/1e9)))
            else:self.assertTrue(self.mapper.ingest((packet,receipt_ns/1e9),(imu,receipt_ns/1e9)))
        self.assertEqual(self.mapper.map.scan_count,2)
        self.assertEqual(self.mapper.feedback_status()['reason'],'feedback_observer_error')
        self.assertFalse(self.mapper.feedback_status()['valid'])
        self.assertEqual(self.mapper.state,'drawing')

    def test_input_or_localizer_failure_breaks_feedback_without_erasing_map(self):
        self.mapper.action('start')
        for kind in ('stale_input','localizer_error'):
            with self.subTest(kind=kind):
                self.mapper._invalidate_feedback('test_reset',new_session=True)
                self.observe(1_000_000_000_000,10_000_000_000,(0.,0.,0.))
                self.observe(1_000_100_000_000,10_100_000_000,(.02,0.,0.))
                before=dict(self.mapper.map.cells)
                receipt=time.monotonic_ns()
                packet=dict(stamp=1000.15,_mapping_ws_receipt_ns=receipt,raw_scan=scan(1000.15))
                sample=imu_packet(1_000_150_000_000,receipt)
                if kind=='stale_input':
                    with self.assertRaisesRegex(ValueError,'stale_original_LaserScan'):
                        self.mapper.ingest((packet,receipt/1e9),(sample,receipt/1e9),now=receipt/1e9+2.)
                else:
                    with patch.object(self.mapper.engine,'add',side_effect=RuntimeError('synthetic localizer failure')):
                        with self.assertRaisesRegex(RuntimeError,'synthetic localizer failure'):
                            self.mapper.ingest((packet,receipt/1e9),(sample,receipt/1e9),now=receipt/1e9)
                self.assertEqual(self.mapper.feedback_status()['reason'],'source_input_or_localizer_error')
                self.assertEqual(self.mapper.map.cells,before)
                first=self.observe(1_000_200_000_000,10_200_000_000,(.04,0.,0.))
                self.assertEqual(first['reason'],'first_geometric_pose_priming')

    def test_dequeued_old_source_cannot_enter_new_generation_or_erase_new_feedback(self):
        self.mapper.action('start')
        for operation in ('stop_resume','reset','reconnect'):
            with self.subTest(operation=operation):
                old_identity=(self.mapper.engine,self.mapper.work_generation,self.mapper._feedback_session)
                if operation=='reconnect':
                    with self.mapper.processing_lock,self.mapper.lock:
                        self.mapper._invalidate_feedback('sensor_subscription_disconnected',new_session=True)
                else:
                    self.mapper.action('stop' if operation=='stop_resume' else 'reset')
                    self.mapper.action('start')
                self.observe(1_000_200_000_000,10_200_000_000,(.04,0.,0.))
                before=self.mapper.feedback_status()
                receipt=time.monotonic_ns()
                packet=dict(stamp=1000.15,raw_scan=scan(1000.15))
                with patch.object(self.mapper.engine,'add') as add:
                    self.assertFalse(self.mapper.ingest((packet,receipt/1e9),None,
                        now=receipt/1e9,source_identity=old_identity))
                    add.assert_not_called()
                self.assertEqual(self.mapper.feedback_status(),before)

    def test_waiting_ingest_failure_invalidates_the_generation_it_actually_entered(self):
        original=self.mapper.processing_lock
        attempted=threading.Event();main=threading.current_thread();errors=[]

        class ObservedLock:
            def __enter__(this):
                if threading.current_thread() is not main:attempted.set()
                original.acquire();return this
            def __exit__(this,*args):original.release()

        self.mapper.processing_lock=ObservedLock()
        self.mapper.action('start')
        receipt=time.monotonic_ns()
        packet=dict(stamp=1000.15,raw_scan=scan(1000.15))

        def worker():
            try:self.mapper.ingest((packet,receipt/1e9),None,now=receipt/1e9)
            except RuntimeError as error:errors.append(str(error))

        with original:
            thread=threading.Thread(target=worker);thread.start()
            self.assertTrue(attempted.wait(1.))
            self.mapper.action('reset');self.mapper.action('start')
            self.observe(1_000_000_000_000,10_000_000_000,(0.,0.,0.))
            self.observe(1_000_100_000_000,10_100_000_000,(.02,0.,0.))
            with patch.object(self.mapper.engine,'add',side_effect=RuntimeError('new-generation failure')):
                original.release()
                try:thread.join(1.)
                finally:original.acquire()
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors,['new-generation failure'])
        self.assertEqual(self.mapper.feedback_status()['reason'],'source_input_or_localizer_error')


if __name__=='__main__':unittest.main()
