#!/usr/bin/env python3
"""Verify all generated assets and actual native/HLS packet timing and dimensions."""
import argparse,json,subprocess
from pathlib import Path
from add_low_bandwidth_variant import sha

def verify(package,ffprobe='ffprobe'):
    package=package.resolve(strict=True);m=json.loads((package/'adaptive_video.json').read_text())
    for name,v in m['assets'].items():
        if (package/name).stat().st_size!=v['bytes'] or sha(package/name)!=v['sha256']:raise ValueError('asset_hash_mismatch')
    expected=m['source_frames']+1;rows=[]
    targets=[(m['fallback'],m['source_width'],m['source_height'])]+[(v['playlist'],v['width'],v['height']) for v in m['variants']]
    for name,width,height in targets:
        output=subprocess.check_output([ffprobe,'-v','error','-count_packets','-select_streams','v:0',
            '-show_entries','stream=codec_name,width,height,nb_read_packets,duration,avg_frame_rate,profile,level','-of','json',str(package/name)])
        stream=json.loads(output)['streams'][0];count=int(stream['nb_read_packets'])
        if count!=expected or stream['codec_name']!='h264' or stream['width']!=width or stream['height']!=height:
            raise ValueError('generated_video_packet_or_dimension_mismatch')
        duration=stream.get('duration')
        if duration is not None and abs(float(duration)-m['duration_s'])>.10:raise ValueError('generated_video_clock_mismatch')
        rows.append(dict(asset=name,width=width,height=height,packets=count,source_frames=m['source_frames'],last_hold_frames=1,
                         codec=stream['codec_name'],profile=stream.get('profile'),level=stream.get('level'),duration_s=duration))
    return dict(ok=True,archive_id=m['archive_id'],manifest_sha256=sha(package/'adaptive_video.json'),asset_sha256_verified=len(m['assets']),
                streams=rows,time_base=m.get('time_base'),timeline_offset_s=m['timeline_offset_s'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('package',type=Path);p.add_argument('--ffprobe',default='ffprobe')
    a=p.parse_args();print(json.dumps(verify(a.package,a.ffprobe)),flush=True)
