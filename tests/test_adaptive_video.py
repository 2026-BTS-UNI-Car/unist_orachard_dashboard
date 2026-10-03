import hashlib,json,tempfile,unittest
from pathlib import Path
from dashboard_server.adaptive_video import manifest,asset_path,public_info


class AdaptiveAssetTest(unittest.TestCase):
    def fixture(self,root):
        a=Path(root)/'recording';p=a/'adaptive_video';p.mkdir(parents=True)
        assets={}
        for name in ('master.m3u8','native.mp4','720/index.m3u8','720/init.mp4','720/segment-00000.m4s'):
            path=p/name;path.parent.mkdir(exist_ok=True);data=name.encode();path.write_bytes(data)
            assets[name]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        value=dict(schema='orchard.adaptive_video.v1',complete=True,archive_id=a.name,
            master='master.m3u8',fallback='native.mp4',assets=assets)
        (p/'adaptive_video.json').write_text(json.dumps(value));return a,value
    def test_allowlist_and_no_private_paths_in_public_info(self):
        with tempfile.TemporaryDirectory() as root:
            a,v=self.fixture(root);self.assertEqual(manifest(a),v)
            self.assertIsNotNone(asset_path(a,v,'720/init.mp4'))
            self.assertIsNone(asset_path(a,v,'../metadata.json'))
            self.assertIsNone(asset_path(a,v,'720/../../metadata.json'))
            info=public_info(a,v);self.assertTrue(info['manifest_url'].startswith('/api/recordings/recording/'))
            self.assertNotIn(root,json.dumps(info))
    def test_incomplete_wrong_source_and_size_change_not_published(self):
        with tempfile.TemporaryDirectory() as root:
            a,v=self.fixture(root);p=a/'adaptive_video/adaptive_video.json'
            for change in ({'complete':False},{'archive_id':'other'},{'master':'../secret.mp4'}):
                p.write_text(json.dumps(dict(v,**change)));self.assertIsNone(manifest(a))
            p.write_text(json.dumps(v));(a/'adaptive_video/native.mp4').write_bytes(b'changed')
            self.assertIsNone(manifest(a))


if __name__=='__main__':unittest.main()
