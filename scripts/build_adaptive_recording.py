#!/usr/bin/env python3
"""Build private VOD proxies from a verified native-camera export ZIP.

No archive/metadata replacement. The package is installed as a generated
sidecar only after source hashes and playback have been verified. Never raises
source capture FPS or upscales its resolution. FFmpeg is an offline dependency.
"""
import argparse,hashlib,json,math,re,subprocess,time,zipfile
from pathlib import Path
from datetime import datetime


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()


def run(args,log,cwd=None):
    with log.open('ab') as f:subprocess.run(args,check=True,stdout=f,stderr=subprocess.STDOUT,cwd=cwd)


def build(source,output,ffmpeg='ffmpeg',archive_metadata=None):
    source=source.resolve(strict=True);output=output.resolve()
    if output.exists():raise ValueError('new_output_directory_required')
    output.mkdir(parents=True);framesdir=output/'frames';framesdir.mkdir()
    started=time.monotonic()
    with zipfile.ZipFile(source) as bundle:
        m=json.loads(bundle.read('manifest.json'));frames=m['frames']
        if m.get('schema')!='ubts.native_camera_export.v1' or not m.get('complete') or not frames:
            raise ValueError('incomplete_camera_export')
        if archive_metadata is None or digest(archive_metadata)!=m['source_sha256']['metadata.json']:
            raise ValueError('verified_archive_metadata_required_for_sensor_sync')
        metadata=json.loads(archive_metadata.read_text())
        origin=datetime.fromisoformat(metadata['started_at'])
        if origin.tzinfo is None:raise ValueError('archive_origin_timezone_required')
        bag_offset=m['bag_start_ns']/1e9-origin.timestamp()
        if not math.isfinite(bag_offset) or not 0<=bag_offset<=60:raise ValueError('archive_camera_clock_mismatch')
        previous=-math.inf
        for row in frames:
            name=row['file'];stamp=float(row['t_s'])
            if not re.fullmatch(r'frames/[0-9]{7}\.jpg',name) or not math.isfinite(stamp) or stamp<=previous:
                raise ValueError('invalid_camera_frame_index')
            previous=stamp;data=bundle.read(name)
            if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('frame_hash_mismatch')
            (output/name).write_bytes(data)
    # FFconcat paths are fixed numeric relative names, with real per-frame
    # receipt intervals. The last duplicate provides the final frame duration.
    intervals=[frames[i+1]['t_s']-frames[i]['t_s'] for i in range(len(frames)-1)]
    ordered=sorted(intervals);last_dt=ordered[len(ordered)//2] if ordered else 1/15
    concat=['ffconcat version 1.0']
    for i,row in enumerate(frames):
        concat.extend([f"file '{row['file']}'",'option framerate 1000',
            f"duration {(intervals[i] if i<len(intervals) else last_dt):.9f}"])
    concat.append(f"file '{frames[-1]['file']}'");concat.append('option framerate 1000')
    playlist=output/'frames.ffconcat';playlist.write_text('\n'.join(concat)+'\n')
    import cv2
    image=cv2.imread(str(output/frames[0]['file']));height,width=image.shape[:2]
    prefix=[ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-n','-threads','2',
        '-safe','0','-f','concat','-i',str(playlist),'-an','-c:v','libx264','-preset','fast',
        '-threads','2','-pix_fmt','yuv420p','-fps_mode','vfr','-enc_time_base','1:1000',
        '-level:v','4.1','-force_key_frames','expr:gte(t,n_forced*2)']
    # Full source-sized high-quality MP4 is the fallback and top ABR variant.
    native=output/'native.mp4'
    run(prefix+['-crf','20','-maxrate','3000k','-bufsize','6000k','-movflags','+faststart',str(native)],output/'encoder.log')
    rates=[(height,3000000,None),(480,1100000,24),(360,450000,25)]
    variants=[];assetnames=['native.mp4']
    for h,rate,crf in rates:
        if h>height or any(v['height']==h for v in variants):continue
        w=(int(width*h/height)//2)*2;folder=output/str(h);folder.mkdir()
        args=[ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-n','-i',str(native),'-an']
        if crf is None:args+=['-c:v','copy']
        else:args+=['-vf',f'scale={w}:{h}','-c:v','libx264','-preset','fast','-threads','2','-crf',str(crf),
                   '-maxrate',str(rate),'-bufsize',str(rate*2),'-pix_fmt','yuv420p','-fps_mode','vfr',
                   '-enc_time_base','1:1000','-level:v','4.1','-force_key_frames','expr:gte(t,n_forced*2)']
        args+=['-f','hls','-hls_time','4','-hls_playlist_type','vod','-hls_segment_type','fmp4',
               '-hls_flags','independent_segments','-hls_fmp4_init_filename','init.mp4',
               '-hls_segment_filename','segment-%05d.m4s','index.m3u8']
        run(args,output/'encoder.log',cwd=folder)
        names=[p.relative_to(output).as_posix() for p in folder.iterdir() if p.is_file()];assetnames+=names
        actual_rate=max(int((folder/s.split('/')[-1]).stat().st_size*8/4) for s in names if s.endswith('.m4s'))
        variants.append(dict(height=h,width=w,bandwidth=max(rate,int(actual_rate*1.15)),playlist=f'{h}/index.m3u8'))
    master=['#EXTM3U','#EXT-X-VERSION:7','#EXT-X-INDEPENDENT-SEGMENTS']
    for v in sorted(variants,key=lambda v:v['height']):
        # The decoder reads the actual AVC configuration in init.mp4. Do not
        # fabricate a profile/level CODECS string for arbitrary source sizes.
        master.extend([f"#EXT-X-STREAM-INF:BANDWIDTH={v['bandwidth']},RESOLUTION={v['width']}x{v['height']}",v['playlist']])
    (output/'master.m3u8').write_text('\n'.join(master)+'\n');assetnames.append('master.m3u8')
    duration=frames[-1]['t_s']-frames[0]['t_s']+last_dt
    public=dict(schema='orchard.adaptive_video.v1',complete=True,archive_id=m['archive_id'],
        source_topic=m['source_topic'],source_sha256=m['source_sha256'],source_signatures=m['source_signatures'],camera_export_sha256=digest(source),
        source_frames=len(frames),source_width=width,source_height=height,fps=(len(frames)-1)/(duration-last_dt) if len(frames)>1 else 1/last_dt,
        timeline_offset_s=bag_offset+frames[0]['t_s'],duration_s=duration,frame_timing='recorded_camera_receipt_VFR',
        time_base='archive_metadata.started_at',
        master='master.m3u8',fallback='native.mp4',variants=variants,
        assets={name:dict(bytes=(output/name).stat().st_size,sha256=digest(output/name)) for name in sorted(assetnames)},
        build_elapsed_s=time.monotonic()-started)
    (output/'adaptive_video.json').write_text(json.dumps(public,indent=2,allow_nan=False))
    print(json.dumps({k:v for k,v in public.items() if k not in ('assets','source_sha256')}),flush=True)
    return public


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--ffmpeg',default='ffmpeg');p.add_argument('--archive-metadata',type=Path,required=True)
    a=p.parse_args();build(a.source,a.output,a.ffmpeg,a.archive_metadata)
