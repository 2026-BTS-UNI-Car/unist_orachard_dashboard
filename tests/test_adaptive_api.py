import hashlib,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen,Request
from urllib.error import HTTPError
from dashboard_server.app import create_server


class AdaptiveApiTest(unittest.TestCase):
    def test_native_detail_polar_timeline_range_and_asset_guard(self):
        with tempfile.TemporaryDirectory() as root:
            archive=Path(root)/'adaptive_unit';package=archive/'adaptive_video';package.mkdir(parents=True)
            (archive/'metadata.json').write_text(json.dumps(dict(id=archive.name,kind='both',duration_s=2)))
            original=dict(lidar=dict(frames=[dict(t_s=0,points=[dict(angle_deg=90,distance_m=2,quality=0,x_m=2,y_m=0)])]),
                camera=dict(frames=[dict(t_s=0,data='unused_preview')]),imu=dict(frames=[]),duration_s=2)
            (archive/'timeline.json').write_text(json.dumps(original))
            contents={'master.m3u8':b'#EXTM3U\n','native.mp4':b'nativevideo','360/segment-00000.m4s':b'0123456789'}
            for name,data in contents.items():
                p=package/name;p.parent.mkdir(exist_ok=True);p.write_bytes(data)
            m=dict(schema='orchard.adaptive_video.v1',archive_id=archive.name,complete=True,master='master.m3u8',fallback='native.mp4',
                fps=14.9,source_width=1280,source_height=720,timeline_offset_s=.7,
                assets={n:dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest()) for n,data in contents.items()})
            (package/'adaptive_video.json').write_text(json.dumps(m))
            server=create_server('127.0.0.1',0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_address[1]}/api/recordings/{archive.name}'
            try:
                with patch.object(server.RequestHandlerClass.provider,'recording_archive_dir',Path(root)):
                    detail=json.load(urlopen(base))['recording']
                    self.assertEqual(detail['source_video']['height'],720);self.assertEqual(detail['adaptive_video']['timeline_offset_s'],.7)
                    compact=json.load(urlopen(base+'/timeline?profile=dashboard-polar-v2'))['timeline']
                    self.assertEqual(compact['camera']['frames'],[])
                    self.assertEqual(compact['playback']['lidar_point_fields'],['angle_deg','distance_m','quality'])
                    self.assertEqual(compact['lidar']['frames'][0]['points'],[[90,2,0]])
                    unchanged=json.load(urlopen(base+'/timeline'))['timeline'];self.assertEqual(unchanged['camera']['frames'],original['camera']['frames'])
                    with urlopen(Request(base+'/adaptive-video/360/segment-00000.m4s',headers={'Range':'bytes=2-5'})) as r:
                        self.assertEqual(r.status,206);self.assertEqual(r.read(),b'2345');self.assertEqual(r.headers['Content-Type'],'video/iso.segment')
                    with self.assertRaises(HTTPError) as e:urlopen(base+'/adaptive-video/../metadata.json')
                    self.assertEqual(e.exception.code,404)
            finally:server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
