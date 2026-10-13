"""Bounded history failure visibility; synthetic contracts and real factory/map commits."""
from dataclasses import replace
import base64
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from test_gnss_stream_integration import Rig, SYSTEM, LASER_TYPE, IMU_TYPE, room_scan, imu_cdr
from native_pose_ledger import CommittedPoseLedger, MAX_MISSING_COMMITS
from native_map_transport import NativeMapTransport


def frame(source=1, receipt=2, raw=b'original-synthetic-cdr'*256):
    return SimpleNamespace(context=('source',1,'SYSTEM','RAW','epoch','laser-local'),
        scan_cdr_sha256=('0'*64,hashlib.sha256(raw).hexdigest()),scan_stamps_ns=(0,source),
        scan_receipts_ns=(0,receipt),laser_frame='laser',scan_gid='1'*32,imu_gid='2'*32,
        imu_stamp_ns=source,imu_receipt_ns=receipt,imu_cdr_sha256='3'*64),raw


class CapacityNoticeTests(unittest.TestCase):
    def test_private_service_binds_exact_own_map_and_GNSS_source_manifests(self):
        source=Path(__file__).resolve().parents[2]
        service=(source/'native_observation_service.py').read_text(encoding='utf8')
        map_sha=re.search(r"bound.native_map_manifest_sha256 = '([0-9a-f]{64})'",service).group(1)
        gnss_sha=re.search(r"GNSS_SOURCE_SHA='([0-9a-f]{64})'",service).group(1)
        self.assertEqual(map_sha,hashlib.sha256((source/'NATIVE_MAP_SOURCE.json').read_bytes()).hexdigest())
        self.assertEqual(gnss_sha,hashlib.sha256((source/'NATIVE_GNSS_SOURCE.json').read_bytes()).hexdigest())

    def append(self, ledger, stamp, version, raw=None):
        f,r=frame(stamp,stamp,raw=raw or b'original-synthetic-cdr'*256)
        return ledger.append_committed(f,(0.,0.,0.),version,'chain',r,validate=lambda:True)

    def test_row_capacity_reports_all_missing_commits_no_eviction_or_CDR_growth(self):
        ledger=CommittedPoseLedger(enabled=True,capacity=1)
        self.assertTrue(self.append(ledger,1,1));original=ledger.entries[0];retained_bytes=ledger.original_bytes
        for value in range(2,102):self.assertFalse(self.append(ledger,value,value))
        state=ledger.status()
        self.assertEqual(state['missing_committed_rows'],100)
        self.assertEqual(state['rows'],1);self.assertIs(ledger.entries[0],original)
        self.assertEqual(ledger.original_bytes,retained_bytes);self.assertFalse(state['full_history_preserved'])
        self.assertTrue(state['retained_prefix_only'])
        self.assertEqual(state['first_missing_committed']['version'],2)
        self.assertNotIn('original_scan_CDR_hex',state['first_missing_committed'])
        self.assertLessEqual(len(ledger._first_missing),1024)

    def test_byte_capacity_reports_first_unretained_exact_SHA_and_clocks(self):
        ledger=CommittedPoseLedger(enabled=True,original_bytes=4096)
        raw=b'x'*4096
        self.assertTrue(self.append(ledger,1,1,raw));self.assertFalse(self.append(ledger,2,2,raw))
        missing=ledger.status()['first_missing_committed']
        self.assertEqual(missing['source_ns'],2);self.assertEqual(missing['callback_RAW_ns'],2)
        self.assertEqual(missing['scan_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(missing['original_CDR_bytes'],4096)
        self.assertEqual(ledger.original_bytes,4096)

    def test_invalid_duplicate_or_rewound_commit_does_not_inflate_notice(self):
        ledger=CommittedPoseLedger(enabled=True,capacity=1)
        self.assertTrue(self.append(ledger,1,1));self.assertFalse(self.append(ledger,2,2))
        for stamp,version in ((2,2),(1,3),(3,2)):
            with self.assertRaisesRegex(ValueError,'committed_pose_rewind'):self.append(ledger,stamp,version)
        f,raw=frame(3,3)
        with self.assertRaisesRegex(ValueError,'SHA_required'):
            ledger.append_committed(f,(0.,0.,0.),3,'chain',raw+b'!',validate=lambda:True)
        with self.assertRaisesRegex(ValueError,'map_commit_changed'):
            ledger.append_committed(f,(0.,0.,0.),3,'chain',raw,validate=lambda:False)
        self.assertEqual(ledger.status()['missing_committed_rows'],1)

    def test_Stop_and_default_OFF_never_create_fake_history_failure(self):
        for enabled in (False,True):
            ledger=CommittedPoseLedger(enabled=enabled,capacity=1)
            if enabled:self.assertTrue(self.append(ledger,1,1))
            ledger.inhibit();self.assertFalse(self.append(ledger,2,2))
            self.assertEqual(ledger.status()['missing_committed_rows'],0)
            self.assertIsNone(ledger.status()['first_missing_committed'])

    def test_live_prefix_pins_notice_at_open_without_retroactive_mutation(self):
        ledger=CommittedPoseLedger(enabled=True,capacity=1)
        self.assertTrue(self.append(ledger,1,1));self.assertFalse(self.append(ledger,2,2))
        opened=ledger.open_live_prefix();self.assertFalse(opened['complete_retained_history'])
        self.assertEqual(opened['missing_committed_rows'],1)
        self.assertFalse(self.append(ledger,3,3))
        chunk=ledger.live_prefix_chunk(opened['snapshot'],expected_sha256=opened['index_sha256'])
        index=json.loads(base64.b64decode(chunk['data_base64']))
        self.assertEqual(index['missing_committed_rows'],1);self.assertEqual(ledger.status()['missing_committed_rows'],2)
        ledger.release_live_prefix(opened['snapshot']);ledger.close_after_join()
        exported=json.loads(ledger.document(include_scans=True))
        self.assertEqual(exported['missing_committed_rows'],2);self.assertTrue(exported['retained_prefix_only'])
        self.assertEqual(len(exported['rows']),1)

    def test_counter_saturates_explicitly_and_notice_survives_Stop_Start(self):
        ledger=CommittedPoseLedger(enabled=True,capacity=1)
        self.assertTrue(self.append(ledger,1,1));ledger.missing_commits=MAX_MISSING_COMMITS
        self.assertFalse(self.append(ledger,2,2));self.assertTrue(ledger.status()['missing_count_saturated'])
        self.assertEqual(ledger.status()['missing_committed_rows'],MAX_MISSING_COMMITS)
        ledger.close_after_join();ledger.start_after_join(explicit=True)
        self.assertFalse(ledger.status()['full_history_preserved'])
        self.assertEqual(ledger.status()['first_missing_committed']['version'],2)

    def test_real_factory_second_map_commit_keeps_mapping_and_transport_exposes_failure(self):
        r=Rig().pair()
        try:
            r.map.pose_ledger.capacity=1
            r.map.commit(r.prepared);r.idle()
            first=r.map.pose_ledger.entries[0]
            r.advance(50_000_000)
            for delta,yaw in ((85_000_000,.02),(105_000_000,.02),(120_000_000,.02)):
                r.deliver(IMU_TYPE,imu_cdr(SYSTEM+delta,frame='imu_link',yaw=yaw))
            r.deliver(LASER_TYPE,room_scan(SYSTEM+100_000_000,(.04,.01,.02)))
            original=r.i.prepare().prepared;self.assertIsNotNone(original)
            prepared=r.f.prepare(original);self.assertIsNotNone(prepared)
            answer=r.map.commit(prepared);r.idle()
            self.assertIn('diagnostic',answer)
            state=r.map.status();self.assertEqual(state['map_version'],2)
            self.assertIsNone(state['adapter_fault']);self.assertIsNone(state['encoder_fault'])
            self.assertEqual(state['pose_history_warning'],'retained_pose_history_capacity')
            self.assertFalse(state['pose_history_complete'])
            self.assertEqual(state['pose_history_missing_commits'],1)
            self.assertEqual(state['counters']['pose_history_retention_failures'],1)
            self.assertIs(r.map.pose_ledger.entries[0],first)
            transport=NativeMapTransport(r.map,push_only=True)
            status=transport.status()
            self.assertEqual(status['map']['pose_history_missing_commits'],1)
            self.assertFalse(status['map']['pose_history_complete'])
        finally:r.close()


if __name__=='__main__':unittest.main()
