import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dashboard_server.mapping_operations import ManagedArchiveMapping,MappingOperations,MappingConflict
from dashboard_server.source_live_mapping import SourceLiveMapping
from dashboard_server.continuous_mapping import ContinuousMapping
from dashboard_server.occupancy_map import read_json
from test_integrated_mapping import scan
from test_continuous_localization import imu


def fixture(root,name='record',seconds=3.):
    archive=Path(root)/name;source=archive/'mapping_source';source.mkdir(parents=True)
    (archive/'metadata.json').write_text(json.dumps(dict(started_at='1970-01-01T00:16:40Z',duration_s=seconds)))
    with gzip.open(source/'sensors.jsonl.gz','wt') as out:
        for i in range(round(seconds*10)+1):
            t=1000+i*.1
            for topic,msg in (('/imu/data_raw',imu(t)),('/scan',scan(t,i*.01))):
                out.write(json.dumps(dict(topic=topic,message=msg,bag_time_ns=round(t*1e9)))+'\n')
    digest=hashlib.sha256((source/'sensors.jsonl.gz').read_bytes()).hexdigest()
    (source/'manifest.json').write_text(json.dumps(dict(complete=True,output_sha256={'sensors.jsonl.gz':digest})))
    return archive,digest


def wait_for(predicate,seconds=7):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.02)
    raise AssertionError('expected mapper state not reached')


class OperationTests(unittest.TestCase):
    def test_live_start_preempts_an_already_running_recorded_prefix(self):
        with tempfile.TemporaryDirectory() as root,patch.dict('os.environ',{'UBTS_MAP_PIPELINE':'0'}):
            fixture(root,seconds=8.);live=SimpleNamespace(tracking=False)
            manager=ManagedArchiveMapping(root,live,start_monitor=False)
            try:
                manager.new_map('record');manager.playback('record',8.,True,{'client':'A','sequence':1})
                worker=manager.playback_mappers['record'];wait_for(lambda:worker.history['processed_t_s']>=.5)
                live.tracking=True;wait_for(lambda:worker.worker_stage=='waiting_live_priority')
                before=worker.history['processed_t_s'];time.sleep(.15)
                self.assertEqual(worker.history['processed_t_s'],before)
                live.tracking=False;wait_for(lambda:worker.history['processed_t_s']>before)
            finally:manager.close()

    def test_live_first_gate_prevents_even_startup_then_releases_worker(self):
        with tempfile.TemporaryDirectory() as root,patch.dict('os.environ',{'UBTS_MAP_PIPELINE':'0'}):
            archive,digest=fixture(root);live=SimpleNamespace(tracking=True)
            manager=ManagedArchiveMapping(root,live,start_monitor=False)
            try:
                manager.new_map('record');manager.playback('record',1.,True,{'client':'A','sequence':1})
                worker=manager.playback_mappers['record']
                wait_for(lambda:worker.worker_stage=='waiting_live_priority')
                time.sleep(.15);self.assertEqual(worker.history['input_scans'],0)
                self.assertEqual(manager.jobs()[0]['state'],'waiting_live_priority')
                live.tracking=False
                wait_for(lambda:worker.history['processed_t_s']>=1.)
                self.assertEqual(hashlib.sha256((archive/'mapping_source/sensors.jsonl.gz').read_bytes()).hexdigest(),digest)
            finally:manager.close()

    def test_multiple_devices_cannot_pause_or_reset_active_writer(self):
        with tempfile.TemporaryDirectory() as root,patch.dict('os.environ',{'UBTS_MAP_PIPELINE':'0'}):
            fixture(root);manager=ManagedArchiveMapping(root,SimpleNamespace(tracking=True),start_monitor=False)
            try:
                first=manager.new_map('record');manager.playback('record',1.,True,{'client':'A','sequence':1})
                with self.assertRaisesRegex(MappingConflict,'another_device'):
                    manager.playback('record',0.,False,{'client':'B','sequence':1})
                with self.assertRaisesRegex(MappingConflict,'another_device'):
                    manager.new_map('record',first['map_epoch'],'B')
                with self.assertRaisesRegex(MappingConflict,'another_device'):
                    manager.regenerate('record')
                job=manager.jobs('A')[0];self.assertTrue(job['controller_present']);self.assertTrue(job['control_owned'])
                self.assertTrue(manager.playback_mappers['record'].playing)
                result=manager.playback('record',0.,False,{'client':'A','sequence':0})
                self.assertTrue(result['stale_control_ignored'])
                self.assertTrue(manager.playback_mappers['record'].playing)
            finally:manager.close()

    def test_stop_revokes_late_request_then_explicit_play_resumes_prefix(self):
        with tempfile.TemporaryDirectory() as root,patch.dict('os.environ',{'UBTS_MAP_PIPELINE':'0'}):
            archive,_=fixture(root);manager=ManagedArchiveMapping(root,start_monitor=False)
            try:
                first=manager.new_map('record');manager.playback('record',1.,True,{'client':'A','sequence':1})
                worker=manager.playback_mappers['record'];wait_for(lambda:worker.history['processed_t_s']>=1.)
                result=manager.stop_job('record',first['map_epoch']);self.assertTrue(result['partial_map_preserved'])
                before=read_json(archive/'integrated_map_history.json');time.sleep(.1)
                self.assertEqual(read_json(archive/'integrated_map_history.json')['events'],before['events'])
                with self.assertRaisesRegex(MappingConflict,'press_play'):
                    manager.playback('record',2.,True,{'client':'A','sequence':2})
                with self.assertRaisesRegex(MappingConflict,'revoked'):
                    manager.playback('record',2.,True,{'client':'A','sequence':3},resume=True,generation=first['mapping_generation'])
                manager.playback('record',2.,True,{'client':'A','sequence':4},resume=True,generation=result['mapping_generation'])
                wait_for(lambda:manager.playback_mappers['record'].history['processed_t_s']>=2.)
                self.assertEqual(manager.playback_mappers['record'].history['epoch'],first['map_epoch'])
            finally:manager.close()

    def test_lease_expiry_stops_orphan_and_global_worker_limit_preserves_other(self):
        with tempfile.TemporaryDirectory() as root,patch.dict('os.environ',{'UBTS_MAP_PIPELINE':'0'}):
            fixture(root);fixture(root,'other');live=SimpleNamespace(tracking=True)
            manager=ManagedArchiveMapping(root,live,lease_seconds=.5,start_monitor=False)
            try:
                manager.new_map('record');manager.playback('record',1.,True,{'client':'A','sequence':1})
                manager.new_map('other')
                with self.assertRaisesRegex(MappingConflict,'another_recording'):
                    manager.playback('other',1.,True,{'client':'B','sequence':1})
                self.assertFalse(manager.playback_mappers['record'].closed)
                manager.expire(time.monotonic()+1)
                wait_for(lambda:not manager.playback_mappers['record'].running)
                self.assertFalse(manager.leases)
                self.assertEqual(manager.jobs()[0]['reason'],'replay_client_disconnected')
            finally:manager.close()

    def test_new_map_epoch_compare_and_swap_with_concurrent_clients(self):
        with tempfile.TemporaryDirectory() as root:
            fixture(root);manager=ManagedArchiveMapping(root,start_monitor=False)
            try:
                old=manager.new_map('record')['map_epoch'];results=[]
                def reset():
                    try:results.append(manager.new_map('record',old,'A')['map_epoch'])
                    except MappingConflict:results.append('conflict')
                threads=[threading.Thread(target=reset) for _ in range(2)]
                for t in threads:t.start()
                for t in threads:t.join()
                self.assertEqual(results.count('conflict'),1)
                self.assertNotEqual(read_json(Path(root)/'record/integrated_map_history.json')['epoch'],old)
            finally:manager.close()

    def test_live_reads_are_not_blocked_by_registration_stop_freezes_and_epochs_guard(self):
        with tempfile.TemporaryDirectory() as root:
            live=SourceLiveMapping(root,start_worker=False);live.action('start');now=time.monotonic()
            raw=imu(1000);packet=dict(raw=dict(raw,stamp=raw['header']['stamp']))
            live.ingest((dict(raw_scan=scan(),stamp=1000.),now),(packet,now),now=now)
            before=live.status(transport='tiles');started=threading.Event();finish=threading.Event()
            original=live.engine.add
            def slow(*a,**kw):started.set();finish.wait(3);return original(*a,**kw)
            with patch.object(live.engine,'add',side_effect=slow):
                m=imu(1000.1);p=dict(raw=dict(m,stamp=m['header']['stamp']))
                thread=threading.Thread(target=lambda:live.ingest((dict(raw_scan=scan(1000.1,.02),stamp=1000.1),now),(p,now),now=now));thread.start()
                self.assertTrue(started.wait(1));t=time.monotonic();self.assertEqual(live.status(transport='tiles')['scan_count'],before['scan_count'])
                self.assertLess(time.monotonic()-t,.1)
                stopper=threading.Thread(target=lambda:live.action('stop'));stopper.start()
                finish.set();thread.join(3);stopper.join(3)
            cells=dict(live.map.cells);self.assertFalse(live.tracking)
            live.ingest((dict(raw_scan=scan(1000.2,.04)),now),(packet,now),now=now)
            self.assertEqual(cells,live.map.cells)
            epoch=live.map_epoch;live.action('reset',epoch)
            with self.assertRaisesRegex(ValueError,'epoch_changed'):live.action('reset',epoch)
            live.close()

    def test_bounded_live_pose_diagnostics_never_stop_a_long_run(self):
        live=ContinuousMapping(max_records=3)
        for i in range(12):
            result=live.add(scan(1000+i*.1,i*.01),imu(1000+i*.1))
            self.assertTrue(result['accepted'])
        self.assertEqual(len(live.records),3);self.assertEqual(live.map.scan_count,12)
        self.assertEqual(live.localizer.accepted,12)


if __name__=='__main__':unittest.main()
