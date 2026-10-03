import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('lowvideo',Path(__file__).parents[1]/'scripts/add_low_bandwidth_variant.py')
low=importlib.util.module_from_spec(spec);spec.loader.exec_module(low)

class LowVariantTest(unittest.TestCase):
    def fixture(self,root):
        p=Path(root)/'old';p.mkdir()
        for n in ('native.mp4','master.m3u8'):(p/n).write_bytes(n.encode())
        m=dict(schema='orchard.adaptive_video.v1',archive_id='fixture',complete=True,source_width=1280,source_height=720,
               source_frames=150,frame_timing='recorded_camera_receipt_VFR',timeline_offset_s=.7,
               fallback='native.mp4',master='master.m3u8',variants=[dict(height=720,width=1280,bandwidth=3000000,playlist='720/index.m3u8')],
               assets={n:dict(bytes=(p/n).stat().st_size,sha256=low.sha(p/n)) for n in ('native.mp4','master.m3u8')})
        (p/'adaptive_video.json').write_text(json.dumps(m));return p
    def test_extension_preserves_native_clock_resolution_and_source_package(self):
        with tempfile.TemporaryDirectory() as root:
            p=self.fixture(root);before={n:low.sha(p/n) for n in ('native.mp4','master.m3u8','adaptive_video.json')}
            def ffmpeg(*a,**k):
                for n in ('init.mp4','index.m3u8','segment-00000.m4s'):(Path(k['cwd'])/n).write_bytes(n.encode())
            with patch.object(low.subprocess,'run',side_effect=ffmpeg):m=low.extend(p,Path(root)/'new')
            self.assertEqual(m['source_frames'],150);self.assertEqual(m['timeline_offset_s'],.7)
            self.assertEqual(m['source_width'],1280);self.assertEqual(m['generated_upgrade_source_manifest_sha256'],before['adaptive_video.json'])
            self.assertEqual({n:low.sha(p/n) for n in before},before)
            self.assertEqual(m['assets']['native.mp4']['sha256'],before['native.mp4'])
            self.assertEqual(m['variants'][-1]['height'],240)
    def test_source_tamper_refused_before_output(self):
        with tempfile.TemporaryDirectory() as root:
            p=self.fixture(root);(p/'native.mp4').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'source_asset_changed'):low.extend(p,Path(root)/'new')
            self.assertFalse((Path(root)/'new').exists())

if __name__=='__main__':unittest.main()
