import copy,gzip,json,tempfile,time,unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import numpy as np
from dashboard_server.archive_mapping import ArchiveMapping
from dashboard_server.integrated_archive import IndexedTileHistory,digest
from dashboard_server.playback_mapping import SourceCursorLocalizer
from dashboard_server.replay_state import export_localizer,restore_localizer,load_state,plain
from dashboard_server.resumable_playback import source_rows
from dashboard_server.occupancy_map import read_json
from dashboard_server.tile_transport import StreamingOccupancyMap
import test_playback_mapping
from test_integrated_mapping import scan
from test_continuous_localization import imu


class StateTests(unittest.TestCase):
    def batch(self,i):
        t=1000+i*.1;m=imu(t);m['angular_velocity']['z']=.002
        # Includes independent receiver epochs and rejected duplicate epochs.
        gps=dict(valid=True,latitude=35.,longitude=129.+i*.0000001,h_acc_m=2.,
                 gps_time=f'2026-10-03T00:00:{i:02d}+00:00',stamp=m['header']['stamp'],speed_mps=.1)
        return [dict(topic='/imu/data_raw',message=m),
                dict(topic='/gps/data',message=dict(data=json.dumps(gps))),
                dict(topic='/scan',message=scan(t,i*.01))]

    def test_state_roundtrip_keeps_exact_next_pose_native_gyro_and_GNSS(self):
        source=SourceCursorLocalizer()
        self.assertEqual(export_localizer(restore_localizer(export_localizer(source))),export_localizer(source))
        for i in range(15):
            source.consume(self.batch(i))
            if i==0:
                seed=restore_localizer(export_localizer(source))
                self.assertIs(seed.engine.anchor,seed.engine.previous)
        self.assertIsNot(source.engine.anchor,source.engine.previous)
        # Restore an established ENU basis and a soft position prior, too.
        from dashboard_server.raw_mapping.raw_localization import rotate
        source.basis=dict(rotation=rotate(.2),translation=np.array([.1,.2]),heading_uncertainty_lower_bound_rad=.01,accepted=True)
        source.engine.gps_prior(1001.4,[.14,0.],2.)
        # Future IMUs in a recorded prefix must wait for their scan after restart.
        source.consume([dict(topic='/imu/data_raw',message=imu(1001.55))])
        restored=restore_localizer(json.loads(json.dumps(export_localizer(source))))
        for i in range(15,25):
            self.assertEqual(plain(source.consume(self.batch(i))),plain(restored.consume(self.batch(i))))
            self.assertEqual(export_localizer(source),export_localizer(restored))

    def test_bad_numeric_or_cross_version_state_is_rejected(self):
        source=SourceCursorLocalizer()
        for i in range(3):source.consume(self.batch(i))
        state=export_localizer(source)
        for key,value in [('gyro_rate','nan'),('gyro_integral',float('inf')),('accepted',True),('anchor_is_previous',1)]:
            bad=copy.deepcopy(state);bad['engine'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):restore_localizer(bad)
        bad=copy.deepcopy(state);bad['pairs'][0]['factor']=-1
        with self.assertRaises(ValueError):restore_localizer(bad)
        bad=copy.deepcopy(state);bad['libraries']['scipy']='other'
        with self.assertRaises(ValueError):restore_localizer(bad)
        with self.assertRaises(ValueError):restore_localizer([])

    def test_rejected_shock_retains_reference_and_recovers_after_state_restore(self):
        source=SourceCursorLocalizer()
        for i in range(3):source.consume(self.batch(i))
        bad=self.batch(3);bad[0]['message']['linear_acceleration']['z']=30.
        self.assertFalse(source.consume(bad)[0]['accepted'])
        restored=restore_localizer(export_localizer(source))
        for i in range(4,12):
            self.assertEqual(plain(source.consume(self.batch(i))),plain(restored.consume(self.batch(i))))
            self.assertEqual(export_localizer(source),export_localizer(restored))


class ResumeTests(unittest.TestCase):
    fixture=test_playback_mapping.PlaybackTests.fixture
    wait=test_playback_mapping.PlaybackTests.wait

    def finish(self,manager,archive):
        manager.playback('record',.8,True);self.wait(archive,.8)
        mapper=manager.playback_mappers['record'];mapper.thread.join(timeout=8)
        self.assertFalse(mapper.running);self.assertIsNone(mapper.error)
        h=read_json(archive/'integrated_map_history.json');self.assertTrue(h['complete'])
        final=StreamingOccupancyMap.load_checkpoint(archive/'integrated_map'/h['epoch']/'native-continuous.json')
        view=IndexedTileHistory().status(archive,.8)
        return final.cells,final.pose,final.path,{k:view[k] for k in ('pose','path','tiles','cell_count','scan_count','grid')}

    def run_record(self,pipeline,restart=False,corrupt=False,storage_fail=False,previous_fallback=False):
        with tempfile.TemporaryDirectory() as root,ExitStack() as cleanup,patch.dict('os.environ',{'UBTS_MAP_PIPELINE':str(pipeline),'UBTS_MAP_STATE_INTERVAL_S':'.1'}):
            archive,raw_hash=self.fixture(root);manager=ArchiveMapping(root);cleanup.callback(manager.close)
            initial=manager.new_map('record')
            if not restart:return self.finish(manager,archive)
            manager.playback('record',.45,True);self.wait(archive,.4)
            manager.playback('record',.45,False,dict(client='fixture',sequence=3))
            worker=manager.playback_mappers['record'];worker.close();worker.thread.join(timeout=8)
            history=read_json(archive/'integrated_map_history.json');folder=archive/'integrated_map'/history['epoch']
            meta=read_json(folder/'resume.json');self.assertGreater(meta['raw_offset'],0)
            self.assertLessEqual(len(list(folder.glob('resume-*-grid.json'))),2,'resume storage remains bounded while display history is retained')
            self.assertLessEqual(meta['t_s'],.45);self.assertGreaterEqual(meta['drawn_scans'],3)
            self.assertIsNotNone(load_state(folder,history))
            self.assertGreater(next(source_rows(archive/'mapping_source/sensors.jsonl.gz',meta['raw_offset']))[0]['bag_time_ns'],round((1000+meta['t_s'])*1e9))
            if corrupt:
                (folder/meta['state']).write_bytes(b'corrupt')
                if previous_fallback:
                    restored=load_state(folder,history);self.assertTrue(restored is not None)
                    meta=restored[0]
                else:
                    (folder/meta['previous']['state']).write_bytes(b'corrupt')
                    self.assertTrue(load_state(folder,history) is None)
            else:
                for field,value in [('epoch','other'),('source_sensor_sha256','0'*64),('localization_fingerprint','other'),('raster_fingerprint','other')]:
                    bad={**history,field:value}
                    self.assertTrue(load_state(folder,bad) is None,'reject incompatible '+field)
            resumed=ArchiveMapping(root);cleanup.callback(resumed.close)
            calls=[];original=SourceCursorLocalizer.consume
            def count(source,batch):
                calls.extend(r['message']['header']['stamp'] for r in batch if r['topic']=='/scan')
                return original(source,batch)
            with patch.object(SourceCursorLocalizer,'consume',count):
                resumed.playback('record',.65,True,dict(client='fixture',sequence=4));self.wait(archive,.6)
                resumed.playback('record',.65,False,dict(client='fixture',sequence=6))
                h=read_json(archive/'integrated_map_history.json');n=len(h['events'])
                self.assertEqual(h['restart_state']['restored'],not corrupt or previous_fallback)
                stale=resumed.playback('record',.8,True,dict(client='fixture',sequence=5))
                self.assertTrue(stale['stale_control_ignored']);time.sleep(.08)
                self.assertEqual(len(read_json(archive/'integrated_map_history.json')['events']),n)
                self.assertFalse(resumed.playback_mappers['record'].playing)
                if storage_fail:
                    with patch('dashboard_server.resumable_playback.save_state',side_effect=OSError('fixture_disk_failure')):
                        result=self.finish(resumed,archive)
                    self.assertIn('fixture_disk_failure',read_json(archive/'integrated_map_history.json')['restart_state_warning'])
                else:result=self.finish(resumed,archive)
            if not pipeline:
                self.assertEqual(len(calls),9 if corrupt and not previous_fallback else 9-meta['input_scans'])
            final=read_json(archive/'integrated_map_history.json')
            self.assertEqual(final['epoch'],initial['map_epoch'])
            self.assertEqual([e['version'] for e in final['events']],list(range(1,10)))
            self.assertEqual(digest(archive/'mapping_source/sensors.jsonl.gz'),raw_hash)
            return result

    def test_restart_uses_exact_prefix_in_serial_and_spawn_pipeline(self):
        reference=self.run_record(0)
        for pipeline in (0,1):
            with self.subTest(pipeline=pipeline):self.assertEqual(reference,self.run_record(pipeline,restart=True))

    def test_corrupt_checkpoint_falls_back_without_changing_cells_or_source(self):
        self.assertEqual(self.run_record(0),self.run_record(0,restart=True,corrupt=True))

    def test_last_good_checkpoint_survives_corrupt_latest_bundle(self):
        self.assertEqual(self.run_record(0),self.run_record(0,restart=True,corrupt=True,previous_fallback=True))

    def test_checkpoint_storage_failure_keeps_mapping_and_previous_pointer(self):
        self.assertEqual(self.run_record(0),self.run_record(0,restart=True,storage_fail=True))


if __name__=='__main__':unittest.main()
