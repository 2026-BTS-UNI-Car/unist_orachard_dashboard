"""Native JPEG bytes and millisecond PTS survive a real, offline remux."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from datetime import datetime,timezone

from scripts.native_jpeg_vfr import frame_timing,mux_native_jpeg


DEPENDENCIES=bool(importlib.util.find_spec('av') and importlib.util.find_spec('cv2')
                  and shutil.which('ffprobe') and shutil.which('ffmpeg'))
REQUIRED=os.environ.get('UBTS_REQUIRE_MEDIA_PIPELINE')=='1'


def assert_hls_sample_timeline(case,folder,expected):
    """Probe actual init+fragment bytes, including all segment boundaries."""
    folder=Path(folder);lines=(folder/'index.m3u8').read_text().splitlines()
    target=int(next(line.partition(':')[2] for line in lines if line.startswith('#EXT-X-TARGETDURATION:')))
    durations=[float(line.partition(':')[2].split(',')[0]) for line in lines if line.startswith('#EXTINF:')]
    segments=[folder/line for line in lines if line and not line.startswith('#')]
    case.assertEqual(len(segments),len(durations));case.assertGreaterEqual(target,1)
    case.assertGreaterEqual(target,max(durations));groups=[];whole=bytearray((folder/'init.mp4').read_bytes())
    for segment in segments:
        fragment=segment.read_bytes();whole.extend(fragment)
        with tempfile.NamedTemporaryFile(suffix='.mp4') as combined:
            combined.write((folder/'init.mp4').read_bytes()+fragment);combined.flush()
            result=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
                '-show_packets','-show_entries','packet=pts_time,dts_time','-of','json',combined.name],text=True))
        points=[float(p['pts_time']) for p in result['packets']]
        case.assertGreater(len(points),0);case.assertEqual(points,sorted(points));groups.append(points)
        for packet in result['packets']:case.assertEqual(packet['pts_time'],packet['dts_time'])
    points=[point for group in groups for point in group]
    case.assertEqual(len(points),len(expected))
    for got,wanted in zip(points,expected):case.assertAlmostEqual(got,wanted,places=3)
    endpoint=expected[-1]+.001
    for i,(duration,group) in enumerate(zip(durations,groups)):
        stop=groups[i+1][0] if i+1<len(groups) else endpoint
        case.assertAlmostEqual(duration,stop-group[0],places=3)
    with tempfile.NamedTemporaryFile(suffix='.mp4') as combined:
        combined.write(whole);combined.flush()
        result=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format',
            '-show_entries','format=duration','-of','json',combined.name],text=True))
    case.assertAlmostEqual(float(result['format']['duration']),endpoint,places=3)
    case.assertAlmostEqual(sum(durations),endpoint,places=3)


class FrameTimingTest(unittest.TestCase):
    def test_native_ns_are_relative_quantized_without_epoch_float_loss(self):
        origin=1_800_000_000_000_000_000
        offsets=[0,63_000_400,131_000_499,197_000_501]
        rows=[dict(t_s=.02+n*1e-9,bag_time_ns=origin+n) for n in offsets]
        plan=frame_timing(rows)
        self.assertEqual(plan['pts_ms'],[0,63,131,197])
        self.assertEqual(plan['terminal_pts_ms'],263)
        self.assertLessEqual(plan['max_rounding_error_s'],.0005)
        self.assertAlmostEqual(plan['last_dt_s'],.066000002,places=9)

    def test_collision_and_invalid_times_are_rejected_without_adjusting_pts(self):
        for values in ([0.,.0004],[0.,0.],[0.,-.01],[0.,float('nan')],[False,.1]):
            with self.subTest(values=values),self.assertRaises(ValueError):
                frame_timing([dict(t_s=t) for t in values])
        with self.assertRaisesRegex(ValueError,'mixed_native_camera_time'):
            frame_timing([dict(t_s=0.,bag_time_ns=10),dict(t_s=.1)])
        with self.assertRaisesRegex(ValueError,'camera_native_time_mismatch'):
            frame_timing([dict(t_s=0.,bag_time_ns=10),dict(t_s=.2,bag_time_ns=100_000_010)])

    def test_single_frame_has_explicit_hold_not_forced_capture_fps(self):
        plan=frame_timing([dict(t_s=.2,bag_time_ns=100)])
        self.assertEqual(plan['pts_ms'],[0])
        self.assertEqual(plan['terminal_pts_ms'],67)
        self.assertAlmostEqual(plan['duration_s'],1/15,places=8)


@unittest.skipUnless(DEPENDENCIES or REQUIRED,'PyAV/OpenCV/FFmpeg release dependency required')
class NativeJpegMuxTest(unittest.TestCase):
    def test_real_packets_keep_sha_pts_duration_resolution_and_transcode_vfr(self):
        for intervals in ([0.,.063,.131,.197,.275],[0.,.05,.1]):
            with self.subTest(intervals=intervals):self._real_case(intervals)

    def _real_case(self,intervals):
        self.assertTrue(DEPENDENCIES,'required native JPEG mux dependencies unavailable')
        import av
        import cv2
        import numpy as np
        from scripts.build_adaptive_recording import run
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'frames').mkdir();frames=[]
            origin=1_800_000_000_000_000_000
            for index,t in enumerate(intervals):
                image=np.zeros((48,64,3),dtype=np.uint8)
                image[:]=(20+index*20,60,140)
                image[3:14,4:20]=(230,30,70)
                ok,jpeg=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,92])
                self.assertTrue(ok);data=jpeg.tobytes();name=f'frames/{index:07d}.jpg'
                (root/name).write_bytes(data)
                frames.append(dict(file=name,t_s=t,bag_time_ns=origin+round(t*1e9),
                                   bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
            protected={row['file']:(root/row['file']).read_bytes() for row in frames}
            path=root/'native.mkv';info=mux_native_jpeg(frames,root,path,64,48)
            self.assertEqual(info['pyav_version'],av.__version__)
            self.assertTrue(info['source_jpeg_bytes_preserved'])
            self.assertEqual(info['source_packets'],len(frames))
            self.assertEqual(info['muxed_packets'],len(frames)+1)
            probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
                '-show_streams','-show_packets','-show_data_hash','sha256','-show_entries',
                'stream=width,height,codec_name,time_base:packet=pts_time,dts_time,duration_time,data_hash',
                '-of','json',str(path)],text=True))
            stream=probe['streams'][0]
            self.assertEqual((stream['width'],stream['height'],stream['codec_name']),(64,48,'mjpeg'))
            packets=probe['packets'];expected=intervals+[info['terminal_pts_s']]
            self.assertEqual(len(packets),len(expected))
            for i,(packet,t) in enumerate(zip(packets,expected)):
                self.assertAlmostEqual(float(packet['pts_time']),t,places=6)
                self.assertAlmostEqual(float(packet['dts_time']),t,places=6)
                # PyAV 8 cannot write Packet.duration. Matroska duration may
                # stay unknown; adjacent exact PTS define captured holds.
                self.assertEqual(float(packet.get('duration_time',0)),0.)
                self.assertEqual(packet['data_hash'].lower(),'sha256:'+frames[min(i,len(frames)-1)]['sha256'])
            target=root/'native.mp4'
            run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n','-threads','1',
                 '-i',str(path),'-an','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',
                 '-bf','0','-r','1000','-fps_mode','vfr','-enc_time_base','1:1000','-vf','hflip,vflip',str(target)],root/'encoder.log')
            encoded=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
                '-show_frames','-show_format','-show_entries',
                'frame=best_effort_timestamp_time:format=duration','-of','json',str(target)],text=True))
            stamps=[float(f['best_effort_timestamp_time']) for f in encoded['frames']]
            self.assertEqual(len(stamps),len(expected))
            for got,wanted in zip(stamps,expected):self.assertAlmostEqual(got,wanted,places=6)
            self.assertAlmostEqual(float(encoded['format']['duration']),expected[-1]+.001,places=6)
            with av.open(str(target)) as decoded:
                first=next(decoded.decode(video=0)).to_ndarray(format='bgr24')
            source=cv2.imdecode(np.frombuffer(protected[frames[0]['file']],np.uint8),cv2.IMREAD_COLOR)
            self.assertLess(float(cv2.absdiff(first,cv2.flip(source,-1)).mean()),12.)
            self.assertEqual({row['file']:(root/row['file']).read_bytes() for row in frames},protected)
            with self.assertRaises(FileExistsError):mux_native_jpeg(frames,root,path,64,48)
            tampered=root/frames[0]['file'];tampered.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'camera_frame_size_mismatch'):
                mux_native_jpeg(frames,root,root/'rejected.mkv',64,48)
            self.assertFalse((root/'rejected.mkv').exists())

    def test_uniform_30hz_two_segment_hls_keeps_capture_pts_and_real_extinf(self):
        self.assertTrue(DEPENDENCIES,'required native JPEG mux dependencies unavailable')
        import cv2
        import numpy as np
        from scripts.build_adaptive_recording import build,digest
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);origin=1_800_000_000_000_000_000
            metadata=root/'metadata.json'
            metadata.write_text(json.dumps(dict(started_at=datetime.fromtimestamp(origin/1e9,timezone.utc).isoformat())))
            ok,jpeg=cv2.imencode('.jpg',np.full((48,64,3),110,np.uint8));self.assertTrue(ok)
            data=jpeg.tobytes();frames=[]
            for index in range(128):
                delta=round(index/30*1e9)
                frames.append(dict(file=f'frames/{index:07d}.jpg',t_s=.02+delta*1e-9,
                    bag_time_ns=origin+20_000_000+delta,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
            manifest=dict(schema='ubts.native_camera_export.v1',complete=True,frames=frames,
                source_sha256={'metadata.json':digest(metadata)},source_signatures={},bag_start_ns=origin,
                archive_id='uniform_native',source_topic='/camera/recording/jpeg',rotation_deg=180)
            source=root/'source.zip'
            with zipfile.ZipFile(source,'w') as bundle:
                bundle.writestr('manifest.json',json.dumps(manifest))
                for row in frames:bundle.writestr(row['file'],data)
            protected=(digest(source),digest(metadata))
            result=build(source,root/'generated',archive_metadata=metadata,vfr_input='pyav')
            expected=[p*.001 for p in frame_timing(frames)['pts_ms']]+[result['vfr_input']['terminal_pts_s']]
            variant=root/'generated/48'
            self.assertGreaterEqual(len(list(variant.glob('segment*.m4s'))),2)
            assert_hls_sample_timeline(self,variant,expected)
            self.assertEqual(result['source_frames'],128);self.assertAlmostEqual(result['fps'],30.,places=6)
            self.assertEqual((digest(source),digest(metadata)),protected)


if __name__=='__main__':unittest.main()
