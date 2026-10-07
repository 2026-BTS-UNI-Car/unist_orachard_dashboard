import tempfile,unittest
from pathlib import Path
from scripts.hls_bandwidth import hls_bandwidth_metrics,normalize_generated_target_duration
class Tests(unittest.TestCase):
    def playlist(self,root,durations,sizes,target=4):
        lines=['#EXTM3U',f'#EXT-X-TARGETDURATION:{target}']
        for i,(duration,size) in enumerate(zip(durations,sizes)):
            name=f'segment-{i:05d}.m4s';(root/name).write_bytes(b'x'*size)
            lines.extend([f'#EXTINF:{duration},',name])
        (root/'index.m3u8').write_text('\n'.join(lines+['#EXT-X-ENDLIST'])+'\n')
    def test_true_durations_and_contiguous_windows_replace_encoder_ceiling(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.playlist(root,[4,4,1],[4000,8000,4000])
            v=hls_bandwidth_metrics(root)
            # The short final second cannot stand alone: previous4s+1s=5s.
            self.assertEqual(v['bandwidth'],19200);self.assertEqual(v['average_bandwidth'],14223)
            self.assertEqual(v['measured_segments'],3)
    def test_incomplete_playlist_and_escaping_URI_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.playlist(root,[4],[4000]);p=root/'index.m3u8'
            p.write_text(p.read_text().replace('segment-00000.m4s','../outside.m4s'))
            with self.assertRaises(ValueError):hls_bandwidth_metrics(root)
            self.playlist(root,[4],[4000]);p.write_text(p.read_text().replace('#EXT-X-ENDLIST',''))
            with self.assertRaisesRegex(ValueError,'completed_VOD_required'):hls_bandwidth_metrics(root)
    def test_invalid_duration_and_short_clip_do_not_invent_bitrate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.playlist(root,[.1],[100],target=1)
            self.assertEqual(hls_bandwidth_metrics(root)['bandwidth'],8000)
            for duration in (0,'nan','inf'):
                self.playlist(root,[duration],[100])
                with self.assertRaises(ValueError):hls_bandwidth_metrics(root)
    def test_generated_zero_target_is_fixed_without_changing_extinf_or_segments(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.playlist(root,[.151],[100],target=0)
            before=(root/'index.m3u8').read_text();segment=(root/'segment-00000.m4s').read_bytes()
            with self.assertRaisesRegex(ValueError,'invalid_target_duration'):hls_bandwidth_metrics(root)
            info=normalize_generated_target_duration(root)
            self.assertEqual(info,dict(original_target_duration_s=0,target_duration_s=1))
            self.assertEqual((root/'index.m3u8').read_text(),before.replace('TARGETDURATION:0','TARGETDURATION:1'))
            self.assertEqual((root/'segment-00000.m4s').read_bytes(),segment)
            self.assertAlmostEqual(hls_bandwidth_metrics(root)['measured_duration_s'],.151)
            self.playlist(root,[4.001,.2],[100,100],target=4)
            self.assertEqual(normalize_generated_target_duration(root)['target_duration_s'],5)
    def test_generated_normalization_rejects_bad_vod_before_any_write(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for change in ('EXTINF:nan,','../outside.m4s','EXT-X-TARGETDURATION:-1'):
                self.playlist(root,[.151],[100],target=0);p=root/'index.m3u8'
                original=p.read_text()
                if change.startswith('EXTINF'):bad=original.replace('EXTINF:0.151,',change)
                elif change.startswith('EXT-X'):bad=original.replace('EXT-X-TARGETDURATION:0',change)
                else:bad=original.replace('segment-00000.m4s',change)
                p.write_text(bad)
                with self.assertRaises(ValueError):normalize_generated_target_duration(root)
                self.assertEqual(p.read_text(),bad)
