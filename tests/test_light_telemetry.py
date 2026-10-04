import gzip,hashlib,json,tempfile,unittest
from pathlib import Path
from dashboard_server.telemetry_cache import light_segment,raw_telemetry
from scripts.imu_ws_bridge import display_snapshot

class LightTelemetryTest(unittest.TestCase):
    def test_cache_is_small_read_only_and_reusable_without_lidar(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=Path(tmp);(cache/'segments').mkdir()
            source=cache/'segments/segment-000000.json'
            value=dict(imu=dict(frames=[dict(t_s=.1,raw=dict(euler_deg=dict(yaw=12)))]),lidar=dict(frames=[dict(points=list(range(4096)))]),camera=dict(frames=[dict(data='original')]),duration_s=4)
            source.write_text(json.dumps(value));original=source.read_bytes()
            first=light_segment(cache,0)
            self.assertNotIn('lidar',first);self.assertNotIn('camera',first)
            self.assertEqual(first['imu'],value['imu']);self.assertEqual(source.read_bytes(),original)
            source.unlink();self.assertEqual(light_segment(cache,0),first)
    def test_latest_display_keeps_native_values_and_stamp_without_covariance(self):
        sample=dict(raw=dict(stamp=dict(sec=123,nanosec=45),euler_deg=dict(yaw=30),orientation_covariance=list(range(9)),linear_acceleration=dict(x=1)),magnetic=dict(magnetic_field_uT=dict(x=3)),imu_topic_alive=True)
        preview=display_snapshot(sample)
        self.assertEqual(preview['raw']['stamp'],sample['raw']['stamp'])
        self.assertEqual(preview['raw']['euler_deg'],sample['raw']['euler_deg'])
        self.assertNotIn('orientation_covariance',preview['raw']);self.assertIn('orientation_covariance',sample['raw'])
        self.assertFalse(preview['ack_required'])

    def test_empty_preview_recovers_native_imu_on_original_bag_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'fixture';source=archive/'mapping_source';source.mkdir(parents=True)
            (archive/'timeline.json').write_bytes(b'')
            (archive/'metadata.json').write_text(json.dumps(dict(started_at='1970-01-01T00:01:40+00:00',duration_s=4)))
            row=dict(topic='/imu/data_raw',bag_time_ns=100250000000,message=dict(header=dict(frame_id='imu',stamp=dict(sec=100,nanosec=200000000)),orientation=dict(x=0,y=0,z=0,w=1),orientation_covariance=[0]*9,angular_velocity=dict(x=.1,y=0,z=0),linear_acceleration=dict(x=0,y=0,z=9.8)))
            with gzip.open(source/'sensors.jsonl.gz','wt') as stream:stream.write(json.dumps(row)+'\n')
            signature=hashlib.sha256((source/'sensors.jsonl.gz').read_bytes()).hexdigest()
            (source/'manifest.json').write_text(json.dumps(dict(complete=True,output_sha256={'sensors.jsonl.gz':signature})))
            value=raw_telemetry(archive,root/'cache');frame=value['imu']['frames'][0]
            self.assertEqual(frame['t_s'],.25);self.assertEqual(frame['raw']['stamp']['nanosec'],200000000)
            self.assertEqual(frame['raw']['angular_velocity']['x'],.1)
            self.assertEqual((archive/'timeline.json').read_bytes(),b'')
            self.assertEqual(raw_telemetry(archive,root/'cache'),value)

if __name__=='__main__':unittest.main()
