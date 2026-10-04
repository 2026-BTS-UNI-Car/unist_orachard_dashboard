import gzip,hashlib,json,tempfile,time,unittest
from contextlib import ExitStack
from pathlib import Path
from dashboard_server.archive_mapping import ArchiveMapping
from dashboard_server.integrated_archive import IndexedTileHistory
from dashboard_server.occupancy_map import read_json
from test_integrated_mapping import scan


class PlaybackTests(unittest.TestCase):
    def fixture(self,root):
        archive=Path(root)/'record';source=archive/'mapping_source';source.mkdir(parents=True)
        (archive/'metadata.json').write_text(json.dumps(dict(started_at='1970-01-01T00:16:40Z',duration_s=.8)))
        with gzip.open(source/'sensors.jsonl.gz','wt') as out:
            for i in range(9):
                s=scan(1000+i*.1,shift=i*.01);stamp=s['header']['stamp']
                imu=dict(header=dict(stamp=stamp),orientation=dict(x=0,y=0,z=0,w=1),angular_velocity=dict(x=0,y=0,z=0),linear_acceleration=dict(x=0,y=0,z=9.80665))
                for topic,message in (('/imu/data_raw',imu),('/scan',s)):
                    out.write(json.dumps(dict(topic=topic,message=message,bag_time_ns=round((1000+i*.1)*1e9)))+'\n')
        digest=hashlib.sha256((source/'sensors.jsonl.gz').read_bytes()).hexdigest()
        (source/'manifest.json').write_text(json.dumps(dict(complete=True,output_sha256={'sensors.jsonl.gz':digest})))
        return archive,digest

    def wait(self,archive,target,timeout=8):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            value=read_json(archive/'integrated_map_history.json')
            if value.get('mapping_error'):self.fail(value['mapping_error'])
            if value and value.get('processed_t_s',-1)>=target-1e-5:return value
            time.sleep(.02)
        self.fail('mapping did not reach requested prefix')

    def test_new_map_play_pause_seek_restart_and_raw_preservation(self):
        with tempfile.TemporaryDirectory() as root, ExitStack() as cleanup:
            archive,digest=self.fixture(root);manager=ArchiveMapping(root)
            cleanup.callback(manager.close)
            first=manager.new_map('record');reader=IndexedTileHistory()
            self.assertEqual(reader.status(archive,0)['scan_count'],0)
            manager.playback('record',.6,False)
            self.assertEqual(read_json(archive/'integrated_map_history.json')['events'],[])
            manager.playback('record',.25,True);history=self.wait(archive,.2)
            manager.playback('record',.25,False)
            time.sleep(.08)
            history=read_json(archive/'integrated_map_history.json')
            self.assertLessEqual(history['processed_t_s'],.25)
            self.assertTrue(all(e['t_s']<=.25 for e in history['events']))
            before=reader.status(archive,.25)
            self.assertGreater(before['scan_count'],0)
            manager.playback('record',0,False)
            self.assertEqual(reader.status(archive,0)['scan_count'],1)
            mapper=manager.playback_mappers['record'];mapper.close();mapper.thread.join(timeout=2)
            restored=ArchiveMapping(root);cleanup.callback(restored.close)
            restored.playback('record',.55,True);history=self.wait(archive,.5)
            self.assertEqual(history['epoch'],first['map_epoch'])
            self.assertEqual(history['segments'],1)
            self.assertEqual([e['version'] for e in history['events']],list(range(1,len(history['events'])+1)))
            self.assertLessEqual(reader.status(archive,.2)['frame_t_s'],.2)
            self.assertGreater(reader.status(archive,.55)['scan_count'],before['scan_count'])
            restored.playback('record',.8,True);history=self.wait(archive,.8)
            end=time.monotonic()+2
            while not history['complete'] and time.monotonic()<end:
                time.sleep(.02);history=read_json(archive/'integrated_map_history.json')
            self.assertTrue(history['complete'])
            second=restored.new_map('record')
            self.assertNotEqual(second['map_epoch'],first['map_epoch'])
            self.assertEqual(reader.status(archive,.8)['scan_count'],0)
            self.assertTrue(list(archive.glob('integrated_map_history-previous-*.json')))
            self.assertEqual(hashlib.sha256((archive/'mapping_source/sensors.jsonl.gz').read_bytes()).hexdigest(),digest)
            for worker in restored.playback_mappers.values():worker.close()

    def test_corrupt_source_fails_without_replacing_raw_or_display_data(self):
        with tempfile.TemporaryDirectory() as root, ExitStack() as cleanup:
            archive,_=self.fixture(root);manager=ArchiveMapping(root);manager.new_map('record')
            cleanup.callback(manager.close)
            file=archive/'mapping_source/sensors.jsonl.gz';file.write_bytes(b'corrupt')
            manager.playback('record',.3,True)
            worker=manager.playback_mappers['record'];worker.thread.join(timeout=2)
            self.assertIn('unverified',worker.error)
            self.assertEqual(file.read_bytes(),b'corrupt')
            self.assertEqual(read_json(archive/'integrated_map_history.json')['events'],[])


if __name__=='__main__':unittest.main()
