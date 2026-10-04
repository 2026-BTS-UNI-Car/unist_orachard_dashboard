import math,tempfile,time,unittest
import numpy as np
from unittest.mock import patch
from dashboard_server.continuous_mapping import ContinuousMapping
from dashboard_server.source_live_mapping import SourceLiveMapping
from dashboard_server.playback_mapping import SourceCursorLocalizer
from test_integrated_mapping import scan
from test_continuous_localization import imu


class ContinuousMappingTests(unittest.TestCase):
    def test_live_and_recorded_prefix_have_identical_pose_and_keep_one_frame(self):
        live=ContinuousMapping();replay=SourceCursorLocalizer()
        for i in range(50):
            stamp=1000+i*.1;m=imu(stamp)
            m['angular_velocity']['z']=math.radians(.2)
            live.imu(m);current=scan(stamp,i*.015)
            result=live.add(current,m)
            expected,_,_=replay.consume([dict(topic='/imu/data_raw',message=m),dict(topic='/scan',message=current)])
            self.assertEqual(result['accepted'],expected['accepted'])
            np.testing.assert_array_equal(result['pose'],expected['pose'])
            self.assertEqual(result['segment'],0)
        self.assertLess(abs(live.localizer.pose[2]),math.radians(.1))
        self.assertEqual(live.map.scan_count,50)
        self.assertTrue(live.map.conservative_free)

    def test_future_imu_waits_and_pause_keeps_cells_while_tracking_motion(self):
        live=ContinuousMapping();future=imu(1000.1);live.imu(future)
        self.assertFalse(live.add(scan(1000),None)['accepted'])
        self.assertIn(('/imu/data_raw',1000.1),live.pending)
        self.assertTrue(live.add(scan(1000.1),future)['accepted'])
        before=dict(live.map.cells)
        result=live.add(scan(1000.2,.05),imu(1000.2),draw=False)
        self.assertTrue(result['accepted']);self.assertEqual(before,live.map.cells)
        self.assertEqual(live.map.scan_count,1)
        bad=imu(1000.3);bad['linear_acceleration']['z']=30
        self.assertFalse(live.add(scan(1000.3,.1),bad)['accepted'])
        self.assertEqual(before,live.map.cells)
        self.assertEqual(live.info()['segment'],0)

    def test_live_adapter_feeds_every_native_gyro_and_preserves_unavailable_covariance(self):
        with tempfile.TemporaryDirectory() as d:
            live=SourceLiveMapping(d,start_worker=False);live.action('start');now=time.monotonic()
            for i in range(5):
                m=imu(1000+i*.05);raw={**m,'stamp':m['header']['stamp'],'covariance':{'orientation':[0]*9,'angular_velocity':[0]*9}}
                live.imu_history.append((dict(raw=raw),now))
            sample=scan(1000.2)
            self.assertTrue(live.ingest((dict(stamp=1000.2,raw_scan=sample),now),live.imu_history[-1],now=now))
            self.assertAlmostEqual(live.engine.localizer.gyro_valid_s,.2)
            raw['covariance']['angular_velocity']=[-1]+[0]*8
            self.assertEqual(live.native_imu(dict(raw=raw))['angular_velocity_covariance'][0],-1)
            live.close()

    def test_old_fit_cannot_restore_cells_after_new_map(self):
        with tempfile.TemporaryDirectory() as d:
            live=SourceLiveMapping(d,start_worker=False);live.action('start');now=time.monotonic()
            m=imu(1000);raw={**m,'stamp':m['header']['stamp']};old=live.engine;original=old.add
            def interrupted(*args,**kwargs):
                result=original(*args,**kwargs);live.action('reset');return result
            with patch.object(old,'add',side_effect=interrupted):
                accepted=live.ingest((dict(stamp=1000,raw_scan=scan()),now),(dict(raw=raw),now),now=now)
            self.assertFalse(accepted)
            self.assertEqual(live.map.scan_count,0)
            self.assertEqual(live.status(transport='tiles')['tiles'],[])
            self.assertFalse(live.active)
            live.close()


if __name__=='__main__':unittest.main()
