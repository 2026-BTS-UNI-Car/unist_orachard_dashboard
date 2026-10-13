"""Real synthetic native commit + immutable live-prefix export, never live proof."""
from dataclasses import replace
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

from test_gnss_stream_integration import Rig, fixture, SYSTEM, NativeAcceptedMapOwner, rerasterize, decode, LASER_TYPE
from native_pose_ledger import CommittedPoseLedger
from export_native_pose_history import export_live_prefix, dispatch_live_history
import native_pose_ledger as ledger_module


def mapped():
    r=Rig().pair();r.map.commit(r.prepared);r.idle();return r


class LivePoseLedgerTests(unittest.TestCase):
    def test_actual_numeric_commit_live_export_keeps_exact_pose_CDR_and_quality_GPS(self):
        r=mapped().dispatcher()
        try:
            p=fixture(source=SYSTEM+50_000_000)
            r.emit(r.gps_row('/gps/data',p.data_cdr));r.emit(r.gps_row('/gps/fix',p.fix_cdr))
            ledger=r.map.pose_ledger
            before=r.map.display_snapshot();clock=(r.now,r.raw.now)
            raw,sha=export_live_prefix(lambda request:dispatch_live_history(ledger,request))
            doc=json.loads(raw);row=doc['rows'][0]
            self.assertEqual(sha,hashlib.sha256(raw).hexdigest())
            self.assertFalse(ledger.joined);self.assertFalse(ledger.cancel.is_set())
            self.assertEqual(tuple(row['xy'])+(row['yaw_rad'],),before['historical_laser_pose'])
            self.assertEqual(bytes.fromhex(row['original_scan_CDR_hex']),r.original.current.original_cdr)
            self.assertEqual(row['source_ns'],r.original.current.wire.header.stamp_ns)
            self.assertEqual(row['callback_RAW_ns'],r.original.current.receipt.ns)
            self.assertEqual(bytes.fromhex(doc['original_GNSS_pairs'][0]['data_CDR_hex']),p.data_cdr)
            self.assertEqual(bytes.fromhex(doc['original_GNSS_pairs'][0]['fix_CDR_hex']),p.fix_cdr)
            self.assertEqual(r.map.display_snapshot(),before);self.assertEqual((r.now,r.raw.now),clock)
            self.assertFalse(doc['current_run_complete']);self.assertFalse(doc['GNSS_correction_applied_to_cells'])
            self.assertFalse(doc['fused_occupancy_qualified']);self.assertIsNone(ledger._live_prefix)
        finally:r.close()

    def test_live_export_all_original_beams_can_reproduce_current_prefix_cells(self):
        r=mapped()
        try:
            before=r.map.display_snapshot()
            raw,_=export_live_prefix(lambda request:dispatch_live_history(r.map.pose_ledger,request))
            doc=json.loads(raw);row=doc['rows'][0]
            proposal=dict(accepted=True,runtime_authority=False,requires_complete_retained_scan_rerasterization=True,
                pose_versions=[row['version']],source_ns=[row['source_ns']],chain=row['chain'],
                corrected_ENU_xy=[row['xy']],alignment_yaw_rad=0.)
            oracle=NativeAcceptedMapOwner(historical_display_only=True)
            result=rerasterize(doc,proposal,decode_scan=lambda b:decode(b,LASER_TYPE),rays=oracle._rays)
            self.assertEqual(result['cells'],list(before['cells']))
            self.assertEqual(result['original_scans_rerasterized'],1)
            self.assertEqual(r.map.display_snapshot(),before)
        finally:r.close()

    def test_snapshot_is_prefix_and_later_append_does_not_renew_it(self):
        r=mapped()
        try:
            ledger=r.map.pose_ledger;opened=ledger.open_live_prefix()
            entry=ledger.entries[0]
            # Synthetic append after pin: snapshot must not silently grow.
            with ledger.lock:ledger.entries.append(entry)
            index=ledger.live_prefix_chunk(opened['snapshot'],expected_sha256=opened['index_sha256'])
            import base64
            value=json.loads(base64.b64decode(index['data_base64']))
            self.assertEqual(len(value['pose_entry_sha256']),1)
            self.assertEqual(opened['pose_rows'],1)
            with self.assertRaisesRegex(ValueError,'out_of_range'):
                ledger.live_prefix_chunk(opened['snapshot'],kind='pose',entry=1,expected_sha256=entry.seal.hex())
            ledger.release_live_prefix(opened['snapshot'])
            with ledger.lock:ledger.entries.pop()
        finally:r.close()

    def test_ONE_pin_bound_does_not_fault_or_reset_map_and_release_allows_new(self):
        r=mapped()
        try:
            ledger=r.map.pose_ledger;state=r.map.display_snapshot()
            first=ledger.open_live_prefix()
            with self.assertRaisesRegex(ValueError,'one_pinned'):ledger.open_live_prefix()
            self.assertIsNone(r.map._fault);self.assertEqual(r.map.display_snapshot(),state)
            ledger.release_live_prefix(first['snapshot'])
            second=ledger.open_live_prefix();self.assertNotEqual(first['snapshot'],second['snapshot'])
            ledger.release_live_prefix(second['snapshot'])
        finally:r.close()

    def test_capacity_exports_incomplete_without_blocking_native_mapping(self):
        r=mapped()
        try:
            ledger=r.map.pose_ledger
            ledger.reason='retained_pose_history_capacity';ledger.gnss_complete=False
            state=r.map.display_snapshot()
            raw,_=export_live_prefix(lambda request:dispatch_live_history(ledger,request));doc=json.loads(raw)
            self.assertFalse(doc['complete_retained_history'])
            self.assertFalse(doc['complete_retained_quality_GNSS_history'])
            self.assertEqual(doc['ledger_reason'],'retained_pose_history_capacity')
            self.assertEqual(r.map.display_snapshot(),state);self.assertIsNone(r.map._fault)
        finally:r.close()

    def test_retained_entry_mutation_rejected_and_client_pin_released(self):
        r=mapped()
        try:
            ledger=r.map.pose_ledger;entry=ledger.entries[0];saved=entry.metadata
            opened=ledger.open_live_prefix()
            object.__setattr__(entry,'metadata',saved+b' ')
            with self.assertRaisesRegex(ValueError,'retained_committed_pose_changed'):
                ledger.live_prefix_chunk(opened['snapshot'],kind='pose',expected_sha256=entry.seal.hex())
            ledger.release_live_prefix(opened['snapshot']);object.__setattr__(entry,'metadata',saved)
            def corrupt(request):
                if request['op']=='map_live_history_chunk' and request['kind']=='pose':
                    object.__setattr__(entry,'metadata',saved+b' ')
                return dispatch_live_history(ledger,request)
            with self.assertRaisesRegex(ValueError,'retained_committed_pose_changed'):export_live_prefix(corrupt)
            self.assertIsNone(ledger._live_prefix);object.__setattr__(entry,'metadata',saved)
        finally:r.close()

    def test_lease_expiry_wrong_SHA_and_wrong_token_never_reads_new_originals(self):
        r=mapped()
        try:
            ledger=r.map.pose_ledger
            with patch.object(ledger_module.time,'monotonic',return_value=10.):opened=ledger.open_live_prefix()
            with patch.object(ledger_module.time,'monotonic',return_value=11.):
                with self.assertRaisesRegex(ValueError,'SHA_required'):
                    ledger.live_prefix_chunk(opened['snapshot'],expected_sha256='0'*64)
                with self.assertRaisesRegex(ValueError,'owned_unexpired'):
                    ledger.live_prefix_chunk('0'*32,expected_sha256=opened['index_sha256'])
            with patch.object(ledger_module.time,'monotonic',return_value=70.):
                with self.assertRaisesRegex(ValueError,'owned_unexpired'):
                    ledger.live_prefix_chunk(opened['snapshot'],expected_sha256=opened['index_sha256'])
            ledger.release_live_prefix(opened['snapshot'])
        finally:r.close()

    def test_Stop_retains_pinned_originals_but_new_generation_revokes_pin(self):
        r=mapped()
        try:
            ledger=r.map.pose_ledger;opened=ledger.open_live_prefix();r.close()
            self.assertTrue(ledger.joined)
            self.assertTrue(ledger.live_prefix_chunk(opened['snapshot'],expected_sha256=opened['index_sha256'])['ok'])
            ledger.start_after_join(explicit=True)
            with self.assertRaisesRegex(ValueError,'owned_unexpired'):
                ledger.live_prefix_chunk(opened['snapshot'],expected_sha256=opened['index_sha256'])
            self.assertEqual(len(ledger.entries),1)
        finally:r.close()

    def test_index_encoding_never_holds_append_lock_and_pending_pin_bounds_concurrency(self):
        r=mapped()
        try:
            ledger=r.map.pose_ledger;entered=threading.Event();release=threading.Event();answer=[]
            original=ledger_module.canonical
            def slow(value):
                if value.get('schema')=='ubts.native.live_pose_history.index.v1':
                    entered.set();self.assertTrue(release.wait(2))
                return original(value)
            with patch.object(ledger_module,'canonical',side_effect=slow):
                worker=threading.Thread(target=lambda:answer.append(ledger.open_live_prefix()));worker.start()
                self.assertTrue(entered.wait(2))
                self.assertTrue(ledger.lock.acquire(timeout=.1));ledger.lock.release()
                with self.assertRaisesRegex(ValueError,'one_pinned'):ledger.open_live_prefix()
                release.set();worker.join(2);self.assertFalse(worker.is_alive())
            ledger.release_live_prefix(answer[0]['snapshot'])
        finally:r.close()

    def test_defaultOFF_and_exact_read_only_operations(self):
        ledger=CommittedPoseLedger()
        with self.assertRaisesRegex(ValueError,'default_OFF'):ledger.open_live_prefix()
        for value in ({'op':'start'},{'op':'map_live_history_open','motor':1},
                      {'op':'map_live_history_release','snapshot':'x','reset':True}):
            with self.assertRaises(ValueError):dispatch_live_history(ledger,value)

    def test_chunk_bool_negative_offsets_and_indices_rejected(self):
        r=mapped()
        try:
            ledger=r.map.pose_ledger;opened=ledger.open_live_prefix()
            for kwargs in ({'offset':True},{'offset':-1},{'entry':True},{'entry':-1}):
                with self.assertRaisesRegex(ValueError,'typed_live_history'):
                    ledger.live_prefix_chunk(opened['snapshot'],expected_sha256=opened['index_sha256'],**kwargs)
            ledger.release_live_prefix(opened['snapshot'])
        finally:r.close()


if __name__=='__main__':unittest.main()
