"""Measured VOD bit rates per RFC8216 sections 4.1 and 4.3.4.2.

Encoder maxrate is a ceiling, not a completed playlist's measured BANDWIDTH.
All segment bytes include container overhead. HTTP overhead is not included.
"""
import math,re
from pathlib import Path

def hls_bandwidth_metrics(folder):
    folder=Path(folder).resolve(strict=True);playlist=folder/'index.m3u8'
    if playlist.stat().st_size>1024*1024:raise ValueError('playlist_too_large')
    lines=playlist.read_text(encoding='utf-8').splitlines();target=None;duration=None;rows=[]
    if '#EXT-X-ENDLIST' not in lines:raise ValueError('completed_VOD_required')
    for line in lines:
        if line.startswith('#EXT-X-TARGETDURATION:'):
            value=line.partition(':')[2]
            if not value.isdigit() or not 1<=int(value)<=3600 or target is not None:raise ValueError('invalid_target_duration')
            target=int(value)
        elif line.startswith('#EXTINF:'):
            if duration is not None:raise ValueError('missing_segment_URI')
            duration=float(line.partition(':')[2].split(',')[0])
            if not math.isfinite(duration) or not .001<=duration<=3600:raise ValueError('invalid_segment_duration')
        elif line and not line.startswith('#'):
            if duration is None or not re.fullmatch(r'[A-Za-z0-9_-]+\.m4s',line):raise ValueError('invalid_segment_URI')
            path=folder/line
            if path.is_symlink() or path.resolve(strict=True).parent!=folder:raise ValueError('unconfined_segment')
            size=path.stat().st_size
            if not 0<size<=1024**3:raise ValueError('invalid_segment_size')
            rows.append((duration,size));duration=None
    if target is None or duration is not None or not 1<=len(rows)<=20000:raise ValueError('invalid_completed_playlist')
    low,high=.5*target,1.5*target;peak=0.;total_duration=sum(d for d,s in rows)
    for start in range(len(rows)):
        elapsed=size=0
        for index in range(start,len(rows)):
            d,s=rows[index]
            elapsed+=d;size+=s
            if elapsed>high+1e-8:break
            if elapsed>=low-1e-8:peak=max(peak,8*size/elapsed)
    # A very short VOD may have no window meeting half its target duration.
    if not peak:peak=max(8*s/d for d,s in rows)
    return dict(bandwidth=math.ceil(peak),average_bandwidth=math.ceil(8*sum(s for d,s in rows)/total_duration),
        measured_duration_s=total_duration,measured_segments=len(rows),bandwidth_source='RFC8216_measured_VOD_segment_windows')
