import math
import tempfile
import unittest
from pathlib import Path
import numpy as np
from dashboard_server.live_mapping import LiveMapping, attitude, translation
from dashboard_server.autonomy_settings import AutonomySettings
from dashboard_server.archive_mapping import ArchiveMapping


def frame(stamp=10.,yaw=0.):
    # Unequal radial features yield two-dimensional registration support.
    points=[dict(angle_deg=i,distance_m=4+.5*math.sin(i*.11)+.3*math.cos(i*.17)) for i in range(360)]
    return (dict(stamp=stamp,points=points),stamp),(dict(imu_topic_alive=True,raw=dict(stamp=dict(sec=int(stamp),nanosec=int((stamp-int(stamp))*1e9)),orientation=dict(x=0,y=0,z=math.sin(yaw/2),w=math.cos(yaw/2)))),stamp)


class Tests(unittest.TestCase):
    def test_map_stop_preserves_reset_clears_and_repeated_frame_is_not_new_scan(self):
        with tempfile.TemporaryDirectory() as root:
            mapper=LiveMapping(root,start_worker=False);mapper.action('start')
            scan,imu=frame();self.assertTrue(mapper.ingest(scan,imu,10.))
            saved=mapper.status();self.assertFalse(mapper.ingest(scan,imu,10.))
            self.assertEqual(mapper.status()['scan_count'],1)
            mapper.action('stop');self.assertEqual(mapper.status()['grid'],saved['grid'])
            self.assertEqual(mapper.status()['path'],saved['path'])
            mapper.action('reset');self.assertEqual(mapper.status()['scan_count'],0)
            self.assertNotEqual(saved['map_epoch'],mapper.status()['map_epoch'])

    def test_stale_tilt_and_source_clock_gap_rejected_without_adding_cells(self):
        with tempfile.TemporaryDirectory() as root:
            mapper=LiveMapping(root,start_worker=False);mapper.action('start')
            scan,imu=frame();mapper.ingest(scan,imu,10.)
            saved=mapper.status()
            for now in (9.,12.):
                with self.assertRaises(ValueError):mapper.ingest(scan,imu,now)
            scan,imu=frame(14.)
            with self.assertRaisesRegex(ValueError,'scan_clock_gap'):mapper.ingest(scan,imu,14.)
            self.assertEqual(mapper.status(),saved)
            mapper.freeze('uncertain');self.assertFalse(mapper.status()['active'])
            with self.assertRaises(ValueError):mapper.action('start')
            self.assertFalse(mapper.status()['runtime_authority'])

    def test_known_translation_display_estimate_and_degenerate_support_rejection(self):
        rng=np.random.default_rng(7);previous=rng.uniform(-4,4,(400,2))
        shift,quality=translation(previous,previous-[.08,-.04],0)
        np.testing.assert_allclose(shift,[.08,-.04],atol=.01)
        line=np.column_stack((np.linspace(-4,4,200),np.zeros(200)))
        with self.assertRaises(ValueError):translation(line,line,0)

    def test_settings_are_persistent_and_invalid_values_do_not_write(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'settings.json';settings=AutonomySettings(path)
            settings.set_speed(45);before=path.read_bytes()
            for value in (True,-1,101,'50',math.nan,math.inf):
                with self.assertRaises(ValueError):settings.set_speed(value)
                self.assertEqual(path.read_bytes(),before)
            status=AutonomySettings(path).status()
            self.assertEqual(status['speed_percent'],45)
            self.assertFalse(status['enabled']);self.assertFalse(status['motor_command'])

    def test_archive_rejects_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as root:
            archives=ArchiveMapping(root)
            for name in ('..','../outside','/outside','missing'):
                with self.assertRaises(ValueError):archives.status(name)


if __name__=='__main__':unittest.main()
