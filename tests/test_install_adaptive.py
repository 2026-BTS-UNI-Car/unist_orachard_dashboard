import hashlib,importlib.util,json,tempfile,unittest,shutil,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('installer',Path(__file__).parents[1]/'scripts/install_adaptive_recording.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class AtomicInstallTest(unittest.TestCase):
    def fixture(self,root):
        r=Path(root)/'records';a=r/'recording';a.mkdir(parents=True);package=Path(root)/'generated';package.mkdir()
        for n in ('metadata.json','gps.jsonl','motor.jsonl','bag.mcap'):(a/n).write_bytes(n.encode())
        (package/'master.m3u8').write_bytes(b'#EXTM3U\n');(package/'native.mp4').write_bytes(b'original-sized-proxy')
        m=dict(schema='orchard.adaptive_video.v1',complete=True,archive_id=a.name,source_frames=100,
            master='master.m3u8',fallback='native.mp4',
            source_sha256={n:installer.sha(a/n) for n in ('metadata.json','gps.jsonl','motor.jsonl','bag.mcap')},
            source_signatures={'bag.mcap':[(a/'bag.mcap').stat().st_size,(a/'bag.mcap').stat().st_mtime_ns]},
            assets={n:dict(bytes=(package/n).stat().st_size,sha256=installer.sha(package/n)) for n in ('master.m3u8','native.mp4')})
        (package/'adaptive_video.json').write_text(json.dumps(m));return r,a,package,m
    def test_atomic_sidecar_preserves_originals_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            r,a,p,m=self.fixture(root);before={n:installer.sha(a/n) for n in m['source_sha256']}
            self.assertEqual(installer.install(p,r)['state'],'installed')
            self.assertEqual(installer.install(p,r)['state'],'already_installed')
            self.assertEqual(before,{n:installer.sha(a/n) for n in before})
    def test_source_change_or_asset_tamper_never_installs(self):
        for change in ('source','asset'):
            with tempfile.TemporaryDirectory() as root:
                r,a,p,m=self.fixture(root)
                ((a/'metadata.json') if change=='source' else (p/'native.mp4')).write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'source_sidecar_changed|invalid_asset_hash'):installer.install(p,r)
                self.assertFalse((a/'adaptive_video').exists())
    def upgrade(self,root):
        r,a,p,m=self.fixture(root);installer.install(p,r);baseline=installer.sha(p/'adaptive_video.json')
        upgraded=Path(root)/'upgrade';shutil.copytree(p,upgraded)
        (upgraded/'master.m3u8').write_bytes(b'#EXTM3U\n#LOW-VARIANT\n');(upgraded/'240').mkdir()
        (upgraded/'240/init.mp4').write_bytes(b'new-low-bandwidth-init')
        m['generated_upgrade_source_manifest_sha256']=baseline
        for n in ('master.m3u8','240/init.mp4'):m['assets'][n]=dict(bytes=(upgraded/n).stat().st_size,sha256=installer.sha(upgraded/n))
        (upgraded/'adaptive_video.json').write_text(json.dumps(m));return r,a,p,upgraded,baseline
    def test_generated_upgrade_requires_exact_named_baseline(self):
        with tempfile.TemporaryDirectory() as root:
            r,a,p,u,baseline=self.upgrade(root)
            for token in (None,'0'*64):
                with self.assertRaisesRegex(ValueError,'requires_review'):installer.install(u,r,token)
                self.assertEqual(installer.sha(a/'adaptive_video/adaptive_video.json'),baseline)
    @unittest.skipUnless(sys.platform=='linux','Linux renameat2 production exchange')
    def test_atomic_upgrade_keeps_native_assets_backup_and_old_batch_resume(self):
        with tempfile.TemporaryDirectory() as root:
            r,a,p,u,baseline=self.upgrade(root);native=installer.sha(a/'adaptive_video/native.mp4')
            self.assertEqual(installer.install(u,r,baseline)['state'],'upgraded')
            self.assertEqual(installer.sha(a/'adaptive_video/native.mp4'),native)
            self.assertTrue((a/('.adaptive_video-backup-'+baseline[:16])/'adaptive_video.json').is_file())
            self.assertEqual(installer.install(p,r)['state'],'already_installed_upgraded')
            self.assertEqual(installer.install(u,r,baseline)['state'],'already_installed')


if __name__=='__main__':unittest.main()
