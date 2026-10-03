import json,tempfile,unittest
from pathlib import Path
from scripts.normalize_adaptive_bandwidth import normalize,sha
class Tests(unittest.TestCase):
    def fixture(self,root):
        p=root/'source';(p/'720').mkdir(parents=True)
        files={'native.mp4':b'native video','master.m3u8':b'original master',
            '720/init.mp4':b'init','720/segment-00000.m4s':b'x'*4000,
            '720/index.m3u8':b'#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nsegment-00000.m4s\n#EXT-X-ENDLIST\n'}
        for n,b in files.items():(p/n).write_bytes(b)
        m=dict(schema='orchard.adaptive_video.v1',archive_id='fixture',complete=True,source_frames=150,timeline_offset_s=.7,
            fallback='native.mp4',master='master.m3u8',generated_upgrade_source_manifest_sha256='a'*64,
            variants=[dict(height=720,width=1280,bandwidth=3000000,playlist='720/index.m3u8')],
            assets={n:dict(bytes=len(b),sha256=sha(p/n)) for n,b in files.items()})
        (p/'adaptive_video.json').write_text(json.dumps(m));return p,m
    def test_only_master_metadata_changes_and_upgrade_chain_preserves_media(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,m=self.fixture(root);before=sha(source/'adaptive_video.json')
            result=normalize(source,root/'output')
            self.assertEqual(result['variants'][0]['bandwidth'],8000)
            self.assertEqual(result['variants'][0]['average_bandwidth'],8000)
            self.assertEqual(result['source_frames'],150);self.assertEqual(result['timeline_offset_s'],.7)
            self.assertEqual(result['generated_upgrade_manifest_ancestry_sha256'],['a'*64,before])
            self.assertEqual(sha(source/'adaptive_video.json'),before)
            for n,sig in m['assets'].items():
                if n!='master.m3u8':self.assertEqual(sha(root/'output'/n),sig['sha256'])
    def test_corrupt_media_and_escaping_master_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,m=self.fixture(root);(source/'native.mp4').write_bytes(b'corrupt')
            with self.assertRaises(ValueError):normalize(source,root/'output')
            self.assertFalse((root/'output').exists())
            m['master']='../escape.m3u8';(source/'adaptive_video.json').write_text(json.dumps(m))
            with self.assertRaises(ValueError):normalize(source,root/'output')
