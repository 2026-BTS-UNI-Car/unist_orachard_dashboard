"""Original synthetic CDR and real factory/map commits. No ROS/live claims."""
import base64
from dataclasses import replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent
D=HERE.parents[1]
sys.path[:0]=[str(D/'optimized-package'),str(D),str(HERE),str(D.parents[1])]
from common.native_ownership import RawReceipt
from common.original_cdr import decode,LASER_TYPE,IMU_TYPE
from physical_ingress import PhysicalOriginalIngress
from physical_factory import PhysicalNativeShadowFactory,PhysicalShadowContract,PreparedPhysicalDiagnostic,revision
from committed_native_map import CommittedNativeMapAdapter
from stream_rows_map import StreamObserverRows
from native_pose_ledger import CommittedPoseLedger,canonical
from native_gnss_dispatch import NativeGNSSDispatch
from export_native_pose_history import export
from raster_native_gnss_posterior import rerasterize
from native_map_owner import NativeAcceptedMapOwner
import stream_runner
import native_gnss_fusion as n
from test_native_gnss_fusion import fixture
from fixtures import room_scan,imu_cdr

DOMAIN=hashlib.sha256(b'only-synthetic-clock').hexdigest()
SYSTEM=1790000000000000000
GIDS=(bytes.fromhex('01'*16),bytes.fromhex('02'*16))
GPS={'/gps/data':'03'*16,'/gps/fix':'04'*16}
CONTRACT='a'*64

class Raw:
    def __init__(self):self.now=5000000000
    def sample(self):return RawReceipt(self.now,DOMAIN)

class Rig:
    def __init__(self,ledger=True):
        self.raw=Raw();self.now=SYSTEM;self.contract=PhysicalShadowContract('laser','imu_link')
        self.i=PhysicalOriginalIngress(raw_clock=self.raw,source_now_ns=lambda:self.now,
            source_clock_kind=lambda:'ROS_SYSTEM_TIME',scan_frame='laser',imu_frame='imu_link',
            profile_revision=revision(self.contract),scan_publisher_gid=GIDS[0],imu_publisher_gid=GIDS[1],
            observer_contract_sha256=CONTRACT)
        self.i.reset();self.f=PhysicalNativeShadowFactory(self.i,self.contract,exact_surface_cache_enabled=True)
        self.map=CommittedNativeMapAdapter(self.f,enabled=True,map_epoch='synthetic-map',
            accepted_native_chain_enabled=True,pose_ledger_enabled=ledger)
        self.map.start(explicit=True)
    def advance(self,ns):self.now+=ns;self.raw.now+=ns
    def deliver(self,kind,cdr):
        answer=self.i.consume_original(kind,cdr,callback_receipt=RawReceipt(self.raw.now,DOMAIN),
            callback_ros_system_now_ns=self.now,publisher_gid=GIDS[0 if kind==LASER_TYPE else 1])
        assert self.i.fault is None,answer
    def pair(self):
        self.advance(10_000_000);self.deliver(IMU_TYPE,imu_cdr(SYSTEM,frame='imu_link'));self.deliver(LASER_TYPE,room_scan(SYSTEM))
        self.advance(35_000_000);self.deliver(IMU_TYPE,imu_cdr(SYSTEM+25_000_000,frame='imu_link'))
        self.deliver(IMU_TYPE,imu_cdr(SYSTEM+45_000_000,frame='imu_link',yaw=.01))
        self.advance(105_000_000);self.deliver(IMU_TYPE,imu_cdr(SYSTEM+60_000_000,frame='imu_link',yaw=.01))
        self.deliver(LASER_TYPE,room_scan(SYSTEM+50_000_000,(.02,.005,.01)))
        self.original=self.i.prepare().prepared;assert self.original is not None,self.i.fault
        self.prepared=self.f.prepare(self.original);assert type(self.prepared) is PreparedPhysicalDiagnostic,self.prepared
        return self
    def idle(self):
        # The current owner has a separate retained chain-head slot. The old
        # fixture predates it; pending=None alone was not a worker-idle proof.
        with self.map._cv:assert self.map._cv.wait_for(lambda:self.map._pending is None and self.map._head is None and self.map._working is None,4)
        assert self.map._fault is None,self.map._fault
    def close(self):assert self.map.close(3)['joined']
    def dispatcher(self):
        self.gps=NativeGNSSDispatch(self.map,GPS)
        self.rows=StreamObserverRows(self.i,self.f,observer_contract_sha256=CONTRACT,map_adapter=self.map,gnss_dispatcher=self.gps)
        self.rows.bind_process(123,'time:[123]');self.seq=0
        self.begin=dict(kind='begin',schema='ubts.private.actual_native_cdr_observer.v1',streaming=True,pid=123,uid=0,gid=0,
            time_namespace='time:[123]',observer_process_session='5'*32,RAW_domain=DOMAIN,rmw_implementation='rmw_fastrtps_cpp',
            GID_storage_bytes=16,expected_scan_GID=GIDS[0].hex(),expected_IMU_GID=GIDS[1].hex(),
            header_clock_origin='original_publisher_unmodified',observer_clock_type='RCL_SYSTEM_TIME',use_sim_time=False,
            clock_topic_created=False,publishers_created=False,mission_admission=False,optional_GNSS_enabled=True,
            expected_GPS_data_GID=GPS['/gps/data'],expected_GPS_fix_GID=GPS['/gps/fix'])
        self.emit(self.begin);return self
    def emit(self,row):
        row=dict(row,seq=self.seq);answer=self.rows.receive_line(canonical(row)+b'\n');self.seq+=1;return answer
    def gps_row(self,topic,raw):
        fix=topic=='/gps/fix'
        return dict(kind='original_gnss_cdr',topic=topic,ros_type='sensor_msgs/msg/NavSatFix' if fix else 'std_msgs/msg/String',
            observer_pid=123,observer_process_session='5'*32,time_namespace='time:[123]',rmw_implementation='rmw_fastrtps_cpp',
            callback_monotonic_raw_ns=self.raw.now,RAW_schema='linux.clock_monotonic_raw.integer_ns.v1',RAW_domain=DOMAIN,
            receipt_clock_realtime_ns=self.now,observer_system_ros_now_ns=self.now,original_header_present=fix,
            original_header_stamp_ns=n.decode_navsat(raw)['stamp_ns'] if fix else None,publisher_gid_hex=GPS[topic],
            original_CDR_bytes=len(raw),original_CDR_sha256=hashlib.sha256(raw).hexdigest(),original_CDR_hex=raw.hex(),
            mission_admission=False,proposed_linear_mps=0,proposed_yaw_rate_rad_s=0)


class GNSSIntegrationTests(unittest.TestCase):
    def test_real_numeric_commit_ledger_is_exact_published_pose_and_original(self):
        r=Rig().pair()
        try:
            before=(r.now,r.raw.now);answer=r.map.commit(r.prepared);r.idle()
            self.assertIs(answer['diagnostic'],r.f.snapshot().latest);snapshot=r.map.display_snapshot()
            self.assertEqual((r.now,r.raw.now),before);self.assertEqual(r.map.pose_ledger.status()['rows'],1)
            with self.assertRaisesRegex(ValueError,'joined'):r.map.pose_ledger.document()
            r.close();doc=json.loads(r.map.pose_ledger.document(include_scans=True));row=doc['rows'][0]
            self.assertEqual(tuple(row['xy'])+(row['yaw_rad'],),snapshot['historical_laser_pose'])
            self.assertEqual(row['source_ns'],r.original.current.wire.header.stamp_ns)
            self.assertEqual(row['callback_RAW_ns'],r.original.current.receipt.ns)
            self.assertEqual(bytes.fromhex(row['original_scan_CDR_hex']),r.original.current.original_cdr)
            self.assertFalse(doc['runtime_authority'])
        finally:r.close()

    def test_optional_GPS_global_sequence_not_physical_samples_and_original_clocks(self):
        r=Rig().dispatcher();p=fixture(source=SYSTEM+50_000_000)
        try:
            r.emit(r.gps_row('/gps/data',p.data_cdr));r.emit(r.gps_row('/gps/fix',p.fix_cdr))
            self.assertEqual(r.rows._next_seq,3);self.assertEqual(r.rows.rows,0);self.assertEqual(r.rows.samples,{LASER_TYPE:0,IMU_TYPE:0})
            self.assertFalse(r.rows.ready);self.assertIsNone(r.rows.fault);self.assertEqual(r.gps.status()['quality_pairs'],1)
            original=r.gps.originals[0];self.assertEqual(original.data_cdr,p.data_cdr)
            self.assertEqual(original.data_callback_RAW_ns,r.raw.now);self.assertEqual(original.context,r.map._context)
            r.rows.stop();r.close();doc=json.loads(r.map.pose_ledger.document())
            self.assertEqual(doc['original_GNSS_pairs'][0]['data_callback_RAW_ns'],r.raw.now)
        finally:r.close()

    def test_optional_NoFix_does_not_fault_or_mark_READY(self):
        r=Rig().dispatcher();p=fixture(source=SYSTEM,changes=dict(valid=False,latitude=None,longitude=None),status=-1)
        try:
            for topic,raw in (('/gps/data',p.data_cdr),('/gps/fix',p.fix_cdr)):r.emit(r.gps_row(topic,raw))
            self.assertIsNone(r.rows.fault);self.assertIsNone(r.i.fault);self.assertFalse(r.rows.ready)
            self.assertEqual(r.gps.status()['reason'],'NoFix');self.assertEqual(r.gps.status()['quality_pairs'],0)
        finally:r.close()

    def test_bad_covariance_then_fresh_quality_pair_recovers_optional_GPS_only(self):
        r=Rig().dispatcher()
        try:
            bad=fixture(source=SYSTEM,cov_type=0)
            for topic,raw in (('/gps/data',bad.data_cdr),('/gps/fix',bad.fix_cdr)):r.emit(r.gps_row(topic,raw))
            self.assertIsNone(r.rows.fault);self.assertEqual(r.gps.status()['quality_pairs'],0)
            good=fixture(source=SYSTEM+1)
            for topic,raw in (('/gps/data',good.data_cdr),('/gps/fix',good.fix_cdr)):r.emit(r.gps_row(topic,raw))
            self.assertIsNone(r.rows.fault);self.assertEqual(r.gps.status()['quality_pairs'],1)
        finally:r.close()

    def test_explicit_invalid_partial_receiver_time_not_hidden_by_missing_fields(self):
        p=fixture(quality=False,changes=dict(gps_time_valid_time=False))
        with self.assertRaisesRegex(ValueError,'receiver_time_invalid'):n.solution(p)

    def test_GID_process_SHA_clock_exclusions_preserve_physical_ingress(self):
        for changes in (dict(publisher_gid_hex='ff'*16),dict(observer_pid=9),dict(original_CDR_sha256='0'*64),dict(RAW_domain='0'*64)):
            r=Rig().dispatcher();p=fixture(source=SYSTEM)
            try:
                r.emit(dict(r.gps_row('/gps/data',p.data_cdr),**changes))
                self.assertIsNone(r.rows.fault);self.assertIsNone(r.i.fault);self.assertEqual(r.gps.status()['quality_pairs'],0)
                self.assertTrue(r.gps.status()['reason'])
            finally:r.close()

    def test_large_String_original_bypasses_only_metadata_cap_not_CDR_cap(self):
        r=Rig().dispatcher();p=fixture(source=SYSTEM,changes=dict(extra='x'*10000))
        try:
            self.assertGreater(len(p.data_cdr),8192);r.emit(r.gps_row('/gps/data',p.data_cdr));r.emit(r.gps_row('/gps/fix',p.fix_cdr))
            self.assertEqual(r.gps.status()['quality_pairs'],1);self.assertEqual(r.rows.rows,0)
        finally:r.close()

    def test_Stop_inhibits_new_GPS_pairs_and_does_not_clear_history(self):
        r=Rig().dispatcher();p=fixture(source=SYSTEM)
        try:
            r.emit(r.gps_row('/gps/data',p.data_cdr));r.emit(r.gps_row('/gps/fix',p.fix_cdr));r.rows.stop()
            r.emit(r.gps_row('/gps/data',fixture(source=SYSTEM+1).data_cdr))
            self.assertEqual(r.gps.status()['quality_pairs'],1);self.assertTrue(r.gps.status()['stopped'])
            self.assertFalse(r.rows.proposal_input());r.close()
        finally:r.close()

    def test_unknown_worker_cannot_export_or_publish_after_Stop(self):
        r=Rig().pair();entered=threading.Event();release=threading.Event();old=r.map._map._rays
        def slow(*args):entered.set();release.wait(3);return old(*args)
        try:
            with patch.object(r.map._map,'_rays',side_effect=slow):
                r.map.commit(r.prepared);self.assertTrue(entered.wait(2));self.assertFalse(r.map.close(.001)['joined'])
                with self.assertRaisesRegex(ValueError,'joined'):r.map.pose_ledger.chunk()
                release.set();r.close()
            self.assertEqual(r.map.pose_ledger.status()['rows'],0)
        finally:release.set();r.close()

    def test_default_and_typed_ledger_option(self):
        r=Rig(False)
        try:
            self.assertFalse(r.map.pose_ledger.status()['enabled'])
            self.assertIs(inspect.signature(CommittedNativeMapAdapter).parameters['pose_ledger_enabled'].default,False)
            for bad in (1,0,None,'true'):
                with self.assertRaisesRegex(ValueError,'typed_pose'):CommittedNativeMapAdapter(r.f,pose_ledger_enabled=bad)
        finally:r.close()

    def test_immutable_chunk_export_full_SHA_and_mutation_detection(self):
        r=Rig().pair()
        try:
            r.map.commit(r.prepared);r.idle();r.close()
            raw,sha=export(lambda request:r.map.pose_ledger.chunk(request.get('offset',0),include_scans=request['include_scans'],
                expected_sha256=request.get('sha256')),include_scans=True)
            self.assertEqual(hashlib.sha256(raw).hexdigest(),sha);self.assertGreater(len(raw),10000)
            with self.assertRaisesRegex(ValueError,'SHA'):r.map.pose_ledger.chunk(1,expected_sha256='0'*64)
            entry=r.map.pose_ledger.entries[0];object.__setattr__(entry,'metadata',entry.metadata+b' ')
            r.map.pose_ledger._exports.clear()
            with self.assertRaisesRegex(ValueError,'changed'):r.map.pose_ledger.document()
        finally:r.close()

    def test_capacity_never_evicts_originals_and_flags_incomplete_for_raster(self):
        r=Rig().pair()
        try:
            r.map.pose_ledger.capacity=1;r.map.commit(r.prepared);r.idle()
            frame=r.map._map._state.frame
            newer=replace(frame,scan_stamps_ns=(frame.scan_stamps_ns[0],frame.scan_stamps_ns[1]+1))
            self.assertFalse(r.map.pose_ledger.append_committed(newer,(0.,0.,0.),2,'next',r.original.current.original_cdr,validate=lambda:True))
            self.assertEqual(r.map.pose_ledger.status()['rows'],1);self.assertEqual(r.map.status()['map_version'],1)
            r.close();self.assertFalse(json.loads(r.map.pose_ledger.document())['complete_retained_history'])
        finally:r.close()

    def test_full_original_reraster_matches_original_all_3200_ray_oracle(self):
        r=Rig().pair()
        try:
            r.map.commit(r.prepared);r.idle();snapshot=r.map.display_snapshot();r.close()
            history=json.loads(r.map.pose_ledger.document(include_scans=True));row=history['rows'][0]
            proposal=dict(accepted=True,runtime_authority=False,requires_complete_retained_scan_rerasterization=True,
                pose_versions=[row['version']],source_ns=[row['source_ns']],chain=row['chain'],
                corrected_ENU_xy=[row['xy']],alignment_yaw_rad=0.)
            oracle=NativeAcceptedMapOwner(historical_display_only=True)
            result=rerasterize(history,proposal,decode_scan=lambda b:decode(b,LASER_TYPE),rays=oracle._rays)
            self.assertEqual(result['cells'],list(snapshot['cells']));self.assertEqual(result['original_scans_rerasterized'],1)
            self.assertFalse(result['fused_occupancy_qualified']);self.assertTrue(result['original_source_and_RAW_times_unchanged'])
            proposal['pose_versions']=[]
            with self.assertRaisesRegex(ValueError,'every_original'):rerasterize(history,proposal,decode_scan=lambda b:decode(b,LASER_TYPE),rays=oracle._rays)
        finally:r.close()

    def test_source_manifests_strict_parent_and_default_CLIs(self):
        sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        self.assertTrue(stream_runner.load_native_map_sources(D,sha(D/'NATIVE_MAP_SOURCE.json')))
        self.assertTrue(stream_runner.load_native_gnss_sources(D,sha(D/'NATIVE_GNSS_SOURCE.json')))
        for script in ('native_observation_service.py','stream_runner.py','export_native_pose_history.py','raster_native_gnss_posterior.py'):
            run=subprocess.run([sys.executable,'-B',str(D/script)],capture_output=True,text=True,timeout=5,
                env=dict(os.environ,PYTHONPATH=str(D.parents[1])))
            self.assertEqual(run.returncode,0,run.stderr);v=json.loads(run.stdout)
            self.assertFalse(v['motor_command_sent'])

    def test_CPP_source_optional_subscriptions_no_GPS_readiness_or_reserialize(self):
        cpp=(D/'observer-src/actual_cdr_stream.cpp').read_text()
        self.assertIn('argc==6||argc==10',cpp);self.assertIn('original_gnss_cdr',cpp)
        self.assertIn('GPS_counts' if False else 'gps_counts',cpp)
        self.assertIn('node->counts[0]>=2&&node->counts[1]>=2',cpp)
        self.assertEqual(cpp.count('SingleThreadedExecutor>()'),1)
        self.assertNotIn('serialize_message(',cpp)
        self.assertIn('original_header_present',cpp);self.assertIn('(fix?std::to_string(h.ns()):"null")',cpp)


if __name__=='__main__':unittest.main()
