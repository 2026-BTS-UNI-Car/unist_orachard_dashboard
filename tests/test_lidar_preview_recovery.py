import gzip,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dashboard_server.lidar_preview_recovery import build,window,folder
from dashboard_server.telemetry import TelemetryProvider

class RecoveryTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.archive=self.root/'archive';self.cache=self.root/'cache';self.archive.mkdir()
  (self.archive/'metadata.json').write_text(json.dumps(dict(state='archived',started_at='2026-10-03T00:00:00+00:00',duration_s=20)))
  source=self.archive/'mapping_source';source.mkdir();self.source=source/'sensors.jsonl.gz'
  self.rows=[];start=1790985600
  # Independent sparse analytic returns include invalid and beyond-range rays.
  for i in range(201):
   t=start+i*.1
   self.rows.append(dict(topic='/scan',bag_time_ns=round(t*1e9),message=dict(header=dict(frame_id='laser',stamp=dict(sec=int(t),nanosec=round((t%1)*1e9))),ranges=[1.,2.,None,-1.,32.,1.5,3.,4.],angle_min=-3.14,angle_increment=.785,range_min=.05,range_max=30.,scan_time=.1,time_increment=.00003)))
  with gzip.open(self.source,'wt') as f:
   for row in self.rows:
    f.write(json.dumps(row)+'\n')
    f.write(json.dumps(dict(topic='/imu/data_raw',bag_time_ns=row['bag_time_ns']+10_000_000,
     message=dict(header=dict(frame_id='imu',stamp=dict(sec=1,nanosec=2)),orientation=dict(x=0,y=0,z=0,w=1),
     orientation_covariance=[0]*9,angular_velocity=dict(x=.1,y=.2,z=.3),linear_acceleration=dict(x=0,y=0,z=9.8))))+'\n')
  self.sha=hashlib.sha256(self.source.read_bytes()).hexdigest()
  (source/'manifest.json').write_text(json.dumps(dict(complete=True,output_sha256={'sensors.jsonl.gz':self.sha})))
 def tearDown(self):self.temp.cleanup()
 def test_chunked_seek_preserves_raw_sources_and_uses_original_receipt_clock(self):
  before=self.source.read_bytes();v=build(self.archive,self.cache)
  self.assertEqual(v['frames'],201);self.assertEqual(len(v['files']),3)
  self.assertEqual(v['imu_frames'],201)
  r=window(self.archive,self.cache,9,1)
  self.assertGreater(len(r['frames']),8)
  self.assertTrue(all(f['t_s']<=10 for f in r['frames']))
  self.assertLess(r['frames'][0]['t_s'],9)
  self.assertTrue(all(f['t_s']<=10 for f in r['telemetry']['imu']['frames']))
  self.assertEqual(r['telemetry']['imu']['frames'][0]['raw']['angular_velocity']['z'],.3)
  self.assertEqual(self.source.read_bytes(),before)
  self.assertFalse((self.archive/'timeline.json').exists())
  self.assertEqual(build(self.archive,self.cache),v)
 def test_display_projection_and_invalid_ray_removal_do_not_change_mapping_inputs(self):
  build(self.archive,self.cache);r=window(self.archive,self.cache,0,.5)
  self.assertEqual(len(r['frames'][0]['points']),5)
  p=r['frames'][0]['points'][0]
  self.assertAlmostEqual(p[0],-.002,places=3);self.assertAlmostEqual(p[1],-1.,places=3)
  self.assertEqual(r['frames'][0]['metrics']['source_points'],8)
 def test_source_mismatch_and_active_recording_never_publish_complete_generation(self):
  with self.source.open('ab') as f:f.write(b'x')
  with self.assertRaisesRegex(ValueError,'checksum'):build(self.archive,self.cache)
  self.assertFalse(list(self.cache.glob('**/manifest.json')))
 def test_corrupt_chunk_is_rejected_and_reader_never_rebuilds(self):
  v=build(self.archive,self.cache);target=folder(self.archive,self.cache,self.sha,v['metadata_sha256'])
  (target/'chunk-000000.json.gz').write_bytes(b'bad')
  with self.assertRaisesRegex(ValueError,'checksum'):window(self.archive,self.cache,0,1)
 def test_missing_preview_api_uses_bounded_prepared_frames_with_normal_point_fields(self):
  build(self.archive,self.cache)
  provider=TelemetryProvider.__new__(TelemetryProvider);provider.recording_playback_cache_dir=self.cache
  provider.recording_archive_path=lambda _:self.archive
  with patch.object(provider,'_load_recording_timeline',return_value={}),patch.object(provider,'recording_adaptive_video',return_value=None):
   response=provider.recording_timeline('archive',9,1,'dashboard-polar-v2')
  self.assertTrue(response['ok']);t=response['timeline']
  self.assertGreater(len(t['lidar']['frames']),8)
  self.assertEqual(t['playback']['lidar_point_fields'],['x_m','y_m','distance_m','quality','angle_deg'])
  self.assertTrue(all(len(p)==5 for f in t['lidar']['frames'] for p in f['points']))

 def test_missing_original_light_api_uses_prepared_window_without_raw_archive_decode(self):
  build(self.archive,self.cache)
  provider=TelemetryProvider.__new__(TelemetryProvider);provider.recording_playback_cache_dir=self.cache
  with patch.object(provider,'_playback_cache_path',return_value=None),patch.object(provider,'_load_recording_light_timeline',return_value={}),patch('dashboard_server.telemetry_cache.raw_telemetry',side_effect=AssertionError('HTTP must not decode raw archive')):
   result=provider._recording_light_telemetry(self.archive,self.archive/'timeline.json',9,1)
  self.assertTrue(result['ok']);self.assertEqual(result['timeline']['lidar']['frames'],[])
  self.assertGreater(len(result['timeline']['imu']['frames']),8)
  self.assertTrue(all(f['t_s']<=10 for f in result['timeline']['imu']['frames']))

 def test_source_changed_or_active_recording_cannot_reuse_published_cache(self):
  build(self.archive,self.cache)
  with self.source.open('ab') as f:f.write(b'changed')
  with self.assertRaisesRegex(ValueError,'incompatible'):window(self.archive,self.cache,0,1)
  metadata=self.archive/'metadata.json';v=json.loads(metadata.read_text());v['state']='recording';metadata.write_text(json.dumps(v))
  with self.assertRaisesRegex(ValueError,'finished_recording'):build(self.archive,self.cache)

 def test_verified_motor_sidecar_is_retained_when_preview_descriptor_was_lost(self):
  metadata=self.archive/'metadata.json';v=json.loads(metadata.read_text());v['timeline']=dict(motor_schema='orchard.motor_pwm.timeline.v1');metadata.write_text(json.dumps(v))
  (self.archive/'motor.jsonl').write_text(json.dumps(dict(t_s=9.2,pins=dict(D8=dict(reported_pwm=40))))+'\n')
  build(self.archive,self.cache)
  provider=TelemetryProvider.__new__(TelemetryProvider);provider.recording_playback_cache_dir=self.cache
  provider.recording_archive_path=lambda _:self.archive
  with patch.object(provider,'_load_recording_timeline',return_value={}),patch.object(provider,'recording_adaptive_video',return_value=None):
   response=provider.recording_timeline('archive',9,1,'dashboard-polar-v2')
  self.assertEqual(len(response['timeline']['motor']['frames']),1)
  self.assertEqual(response['timeline']['motor']['frames'][0]['pins']['D8']['reported_pwm'],40)

if __name__=='__main__':unittest.main()
