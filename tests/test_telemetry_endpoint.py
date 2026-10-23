import json,tempfile,unittest
from pathlib import Path
from dashboard_server.telemetry import TelemetryProvider

class TelemetryEndpointTest(unittest.TestCase):
    def test_small_profile_uses_cache_without_loading_big_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp);(archive/'timeline.json').write_text('original archive')
            cache=archive/'cache';(cache/'segments').mkdir(parents=True)
            for index,time in enumerate((3.95,4.1)):
                (cache/'segments'/f'segment-{index:06d}.json').write_text(json.dumps(dict(imu=dict(frames=[dict(t_s=time,raw=dict(euler_deg=dict(yaw=index)))]),lidar=dict(frames=[dict(points=list(range(4096)))]),duration_s=8)))
                (cache/'segments'/f'telemetry-{index:06d}.json').write_text(json.dumps(dict(imu=dict(frames=[dict(t_s=time,raw=dict(euler_deg=dict(yaw=index)))]),duration_s=8)))
            provider=TelemetryProvider.__new__(TelemetryProvider)
            provider.recording_archive_path=lambda _:archive
            provider._playback_cache_path=lambda *args:cache
            provider._valid_playback_manifest=lambda *args:dict(ok=True,segment_count=2)
            provider._load_recording_timeline=lambda *args:(_ for _ in ()).throw(AssertionError('large source timeline read'))
            response=provider.recording_timeline('fixture',4,4,'dashboard-telemetry-v1')
            self.assertTrue(response['ok'])
            timeline=response['timeline']
            self.assertEqual([f['t_s'] for f in timeline['imu']['frames']],[3.95,4.1])
            self.assertEqual(timeline['lidar']['frames'],[])
            self.assertEqual(timeline['camera']['frames'],[])
            self.assertEqual((archive/'timeline.json').read_text(),'original archive')
            self.assertFalse(provider.recording_timeline('fixture','nan',4,'dashboard-telemetry-v1')['ok'])

if __name__=='__main__':unittest.main()
