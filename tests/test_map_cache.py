import gzip,hashlib,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from dashboard_server.integrated_archive import (prepare_trajectory,build,IndexedTileHistory,
    localization_fingerprint,raster_fingerprint)
from dashboard_server.integrated_mapping import integrate
from tests.test_integrated_mapping import scan


class MapCacheTests(unittest.TestCase):
    def fixture(self,root):
        archive=Path(root);source=archive/'mapping_source';source.mkdir()
        (archive/'metadata.json').write_text(json.dumps(dict(started_at='1970-01-01T00:16:40Z',duration_s=1.)))
        with gzip.open(source/'sensors.jsonl.gz','wt') as stream:
            for i in range(2):stream.write(json.dumps(dict(topic='/scan',message=scan(1000.+i*.1),bag_time_ns=round((1000.+i*.1)*1e9)))+'\n')
        sha=hashlib.sha256((source/'sensors.jsonl.gz').read_bytes()).hexdigest()
        (source/'manifest.json').write_text(json.dumps(dict(complete=True,archive_id='test',output_sha256={'sensors.jsonl.gz':sha},topic_counts={'/scan':2})))
        corrected={str(1000.+i*.1):dict(segment=0,frame='isolated',pose=[0,0,0]) for i in range(2)}
        (source/'trajectory.json').write_text(json.dumps(dict(source_sensor_sha256=sha,localization_fingerprint=localization_fingerprint(),corrected=corrected)))
        return archive,source

    def test_changed_algorithm_recomputes_and_preserves_old_pose_cache(self):
        class Engine:
            def __init__(self,**kwargs):
                self.old_segments=[];self.segment=0;self.records=[];self.input_scans=0
                self.gnss=SimpleNamespace(accepted=[])
            def add(self,message,imu,draw):
                stamp=message['header']['stamp'];self.input_scans+=1
                self.records.append(dict(stamp=stamp['sec']+stamp['nanosec']*1e-9,pose=np.zeros(3)))
        with tempfile.TemporaryDirectory() as d:
            archive,source=self.fixture(d);original=(source/'trajectory.json').read_bytes()
            with patch('dashboard_server.integrated_archive.IntegratedMapping',side_effect=AssertionError('cache must be reused')):
                prepare_trajectory(source,lambda *a:None)
            with patch('dashboard_server.integrated_archive.localization_fingerprint',return_value='changed'),patch('dashboard_server.integrated_archive.IntegratedMapping',Engine):
                value=prepare_trajectory(source,lambda *a:None)
            self.assertEqual(value['localization_fingerprint'],'changed')
            backups=list(source.glob('trajectory-previous-*.json'))
            self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),original)

    def test_changed_raster_marks_saved_history_outdated_without_hiding_map(self):
        with tempfile.TemporaryDirectory() as d:
            archive,_=self.fixture(d);build(archive,{},lambda *a:None)
            reader=IndexedTileHistory();value=reader.status(archive,.5)
            self.assertFalse(value['needs_regeneration']);self.assertTrue(value['tiles'])
            with patch('dashboard_server.integrated_archive.raster_fingerprint',return_value='changed'):
                value=reader.status(archive,.5)
            self.assertTrue(value['needs_regeneration']);self.assertEqual(value['state'],'outdated');self.assertTrue(value['tiles'])

    def test_failed_rebuild_restores_saved_history_pointer_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            archive,_=self.fixture(d);build(archive,{},lambda *a:None)
            original=(archive/'integrated_map_history.json').read_bytes();calls=[]
            def fail_after_first(*args):
                calls.append(True)
                if len(calls)>1:raise ValueError('failed test drawing')
                return integrate(*args)
            with patch('dashboard_server.integrated_archive.integrate',side_effect=fail_after_first):
                with self.assertRaisesRegex(ValueError,'failed test drawing'):build(archive,{},lambda *a:None)
            self.assertEqual((archive/'integrated_map_history.json').read_bytes(),original)
            self.assertEqual(IndexedTileHistory().status(archive,.5)['scan_count'],2)


if __name__=='__main__':unittest.main()
