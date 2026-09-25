import json
import math
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dashboard_server.gps import GPS_SCHEMA
from dashboard_server.telemetry import TelemetryProvider
import test_data_recorder_service as fixtures


class GpsArchiveTests(unittest.TestCase):
    def test_sidecar_preserves_early_history_and_segment_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frames = [{'t_s':i/5, 'sample':{'valid':True,'latitude':35.57,'longitude':129.19}} for i in range(18010)]
            (root/'gps.jsonl').write_text(''.join(json.dumps(f)+'\n' for f in frames))
            timeline = {'gps':{'enabled':True,'frames':frames[-10:]}}
            # The sidecar, not the bounded in-memory tail, is authoritative.
            result = TelemetryProvider._with_recorded_gps(None,root,timeline,1,1)
            self.assertEqual(result['gps']['frames'][0]['t_s'], .8)
            self.assertEqual(result['gps']['frames'][-1]['t_s'], 2.2)
            self.assertEqual(timeline['gps']['frames'][0]['t_s'],3600)
            old = TelemetryProvider._with_recorded_gps(None,root/'old',timeline,1,1)
            self.assertIs(old,timeline)

    def test_api_defaults_gps_on_and_allows_explicit_off(self):
        provider = TelemetryProvider()
        with patch.object(provider,'_recorder_post',return_value={'ok':True}) as post:
            provider.start_recording({'kind':'both'})
            self.assertTrue(post.call_args.args[1]['include_gps'])
            provider.start_recording({'kind':'both','include_gps':False})
            self.assertFalse(post.call_args.args[1]['include_gps'])


@unittest.skipUnless(fixtures.RecordingState is not None, 'ROS2 runtime required')
class GpsRosTests(unittest.TestCase):
    def make_state(self,temp):
        return fixtures.DataRecorderCameraPipelineTests().make_state(temp)

    def test_navsat_contract_no_fix_and_covariance(self):
        from sensor_msgs.msg import NavSatFix
        from scripts.gps_ros2_driver import make_navsat
        sample = dict(valid=True,quality=4,latitude=35.57,longitude=129.19,altitude_ellipsoid_m=55.,h_acc_m=.02,v_acc_m=.04,stamp={'sec':42,'nanosec':123})
        msg=make_navsat(sample,NavSatFix)
        self.assertEqual(msg.header.frame_id,'gps_link')
        self.assertEqual(msg.header.stamp.sec,42)
        self.assertEqual(msg.status.status,2)
        self.assertEqual(msg.status.service,0)
        self.assertAlmostEqual(msg.position_covariance[0],.0004)
        sample['valid']=False
        msg=make_navsat(sample,NavSatFix)
        self.assertEqual(msg.status.status,-1)
        self.assertTrue(math.isnan(msg.latitude))
        self.assertEqual(msg.position_covariance_type,0)

    def test_gps_default_topics_jsonl_timestamps_no_fix_and_opt_out(self):
        with tempfile.TemporaryDirectory() as temp:
            state=self.make_state(temp)
            for topic in ('/scan','/camera/image_jpeg','/imu/data','/gps/fix','/gps/data','/gps/driver_status'):
                self.assertIn(topic,state.topics_for('both'))
            self.assertNotIn('/gps/data',state.topics_for('both',include_gps=False))
            at=time.time()
            sample=dict(schema=GPS_SCHEMA,valid=True,quality=4,latitude=35.57,longitude=129.19,received_at=at,gps_time='2026-09-26T00:00:00Z')
            path=Path(temp)/'gps.jsonl'
            with path.open('w') as state.gps_log:
                state.observe_gps(SimpleNamespace(data=json.dumps(sample)))
                state.next_gps_sample_at=0
                state.observe_gps(SimpleNamespace(data=json.dumps(dict(sample,valid=False,latitude=None,longitude=None))))
                state.next_gps_sample_at=0
                state.observe_gps(SimpleNamespace(data=json.dumps(dict(sample,received_at='bad'))))
                state.active['include_gps']=False
                state.observe_gps(SimpleNamespace(data=json.dumps(sample)))
            state.gps_log=None
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows),2)
            self.assertEqual(state.gps_valid_count,1)
            self.assertAlmostEqual(rows[0]['t_s'],at-state.active['started_monotonic'],places=5)
            self.assertEqual(rows[0]['sample']['gps_time'],sample['gps_time'])
            self.assertFalse(rows[1]['sample']['valid'])

    def test_recording_start_stop_persists_gps_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            state=self.make_state(temp); state.active=None
            with patch.object(state,'start_rosbag',return_value=None):
                result=state.start('both')
            self.assertTrue(result['ok'])
            active=dict(state.active)
            state.observe_gps(SimpleNamespace(data=json.dumps(dict(schema=GPS_SCHEMA,received_at=time.time(),valid=False,state='no_data'))),status=True)
            result=state.stop()
            self.assertTrue(result['ok'])
            root=state.archive_dir/active['id']
            metadata=json.loads((root/'metadata.json').read_text())
            self.assertTrue(metadata['include_gps'])
            self.assertEqual(metadata['timeline']['gps_frames'],1)
            self.assertEqual(metadata['timeline']['gps_valid_frames'],0)
            self.assertTrue((root/'gps.jsonl').is_file())


if __name__=='__main__': unittest.main()
