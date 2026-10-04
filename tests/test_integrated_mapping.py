import gzip
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from dashboard_server.recording_clock import display_clock, sort_key
from dashboard_server.integrated_archive import build, IndexedTileHistory
from dashboard_server.tile_transport import StreamingOccupancyMap
from dashboard_server.tiled_occupancy import TiledOccupancyMap
from dashboard_server.integrated_mapping import integrate
from dashboard_server.source_live_mapping import SourceLiveMapping


def scan(stamp=1000.,shift=0.):
    ranges=[]
    for i in range(720):
        a=-math.pi+i*2*math.pi/720;c,s=math.cos(a),math.sin(a)
        candidates=[]
        if abs(c)>.00001:candidates.extend([(5-shift)/c,(-5-shift)/c])
        if abs(s)>.00001:candidates.extend([4/s,-4/s])
        ranges.append(min(d for d in candidates if d>0))
    return dict(header=dict(stamp=dict(sec=int(stamp),nanosec=round((stamp%1)*1e9)),frame_id='laser'),
                ranges=ranges,angle_min=-math.pi,angle_increment=2*math.pi/720,range_min=.05,range_max=30.)


class IntegratedTests(unittest.TestCase):
    def test_clock_preserves_original_and_ignores_tampered_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);metadata=dict(started_at='2026-10-03T07:22:38+00:00')
            value=dict(schema='ubts.recording_clock.v1',original_started_at=metadata['started_at'],offset_s=7880,
                       display_started_at='2026-10-03T09:33:58+00:00')
            (p/'recording_clock.json').write_text(json.dumps(value))
            result=display_clock(p,metadata)
            self.assertEqual(result['clock_quality'],'gnss_estimated')
            self.assertEqual(metadata['started_at'],'2026-10-03T07:22:38+00:00')
            self.assertGreater(sort_key(dict(id='a',**result)),sort_key(dict(id='z',started_at='2026-10-03T08:19:34Z')))
            value['offset_s']=10;(p/'recording_clock.json').write_text(json.dumps(value))
            self.assertEqual(display_clock(p,metadata)['clock_quality'],'host_clock')

    def test_incremental_tiles_keep_exact_odds_and_negative_coordinates(self):
        first=StreamingOccupancyMap(laser_x=0,max_beams=450);second=TiledOccupancyMap(laser_x=0,max_beams=450)
        for i in range(4):
            for grid in (first,second):integrate(grid,scan(1000+i*.1),[-.25+i*.08,-.3,0])
        self.assertEqual(first.cells,second.cells)
        all_tiles=first.tile_packet();self.assertTrue(all_tiles['tiles_reset'])
        self.assertTrue(any(t['x']<0 for t in all_tiles['tiles']))
        self.assertFalse(first.tile_packet(first.scan_count)['tiles'])
        self.assertEqual(first.grid(),second.grid())

    def test_source_live_uses_geometry_and_preserves_stop_map(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            live=SourceLiveMapping(d,start_worker=False);live.action('start')
            raw=dict(stamp=dict(sec=1000,nanosec=0),orientation=dict(x=0,y=0,z=1.,w=0),
                linear_acceleration=dict(x=0,y=0,z=9.80665),covariance=dict(orientation=[0]*9))
            now=time.monotonic();s=scan()
            self.assertTrue(live.ingest((dict(stamp=1000.,raw_scan=s),now),(dict(raw=raw),now),now=now))
            value=live.status(transport='tiles');self.assertEqual(value['scan_count'],1)
            self.assertFalse(value['PWM_used_as_odometry']);self.assertAlmostEqual(value['pose']['yaw'],0.)
            before=dict(live.map.cells);live.action('stop')
            self.assertEqual(before,live.map.cells)
            restored=SourceLiveMapping(d,start_worker=False)
            self.assertEqual(restored.state,'needs_reset')
            self.assertEqual(restored.status()['scan_count'],1)
            snapshot=restored.status(transport='tiles')
            self.assertNotIn('image_base64',str(snapshot));self.assertEqual(snapshot['grid']['encoding'],'tiles-cells-v1')
            restored.action('stop')
            self.assertEqual(restored.status(transport='tiles')['tiles'],snapshot['tiles'])
            self.assertIn('grid',restored.status())
            live.close();restored.close()

    def test_archive_forward_backward_seek_never_leaks_future_cells(self):
        with tempfile.TemporaryDirectory() as d:
            archive=Path(d)/'example';source=archive/'mapping_source';source.mkdir(parents=True)
            (archive/'metadata.json').write_text(json.dumps(dict(started_at='1970-01-01T00:16:40Z',duration_s=1)))
            corrected={}
            with gzip.open(source/'sensors.jsonl.gz','wt') as stream:
                for i in range(8):
                    m=scan(1000+i*.1)
                    corrected[str(1000+i*.1)]=dict(segment=int(i>=5),frame='isolated',pose=[i*.3,0,0])
                    stream.write(json.dumps(dict(topic='/scan',message=m,bag_time_ns=round((1000+i*.1)*1e9)))+'\n')
            sha=hashlib.sha256((source/'sensors.jsonl.gz').read_bytes()).hexdigest()
            (source/'manifest.json').write_text(json.dumps(dict(complete=True,archive_id='example',output_sha256={'sensors.jsonl.gz':sha})))
            (source/'trajectory.json').write_text(json.dumps(dict(corrected=corrected,source_sensor_sha256=sha)))
            history=build(archive,{},lambda *args:None);reader=IndexedTileHistory()
            early=reader.status(archive,.25);later=reader.status(archive,.45,early['tile_version'],early['tile_epoch'])
            self.assertEqual(early['grid']['encoding'],'tiles-cells-v1')
            self.assertNotIn('image_base64',str(early))
            window=reader.window(archive,.25,1)
            self.assertNotIn('image_base64',str(window))
            self.assertLessEqual(early['frame_t_s'],.25)
            self.assertLessEqual(later['frame_t_s'],.45)
            self.assertTrue(later['path_append']);self.assertFalse(later['tiles_reset'])
            back=reader.status(archive,.1,later['tile_version'],later['tile_epoch'])
            self.assertTrue(back['tiles_reset']);self.assertLessEqual(back['frame_t_s'],.1+1e-6)
            other=reader.status(archive,.8,later['tile_version'],later['tile_epoch'])
            self.assertTrue(other['tiles_reset']);self.assertNotEqual(other['segment'],early['segment'])
            self.assertEqual(history['segments'],2)


if __name__=='__main__':unittest.main()
