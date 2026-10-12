"""Lossless JPEG packet mux for the public PyAV 8 / FFmpeg 4.4 API.

Only generated files are written. JPEG bytes are verified and remuxed; no
decoded frame is re-encoded by this helper. PyAV 8 Packet.duration is read-only:
packet presentation timestamps use a millisecond time base, and a repeated
last JPEG at the terminal PTS explicitly preserves the existing last-frame hold.
"""
from fractions import Fraction
import hashlib
import io
import math
from pathlib import Path
import re


TIME_BASE=Fraction(1,1000)
HEADER_STAMP_MIN_SPACING_NS=1_000_000  # Distinct at the 1-ms PTS quantum.
HEADER_RECEIPT_MAX_DIVERGENCE_NS=1_000_000_000


def header_stamp_rejection(frames,receipts):
    """None when the header stamps can be the presentation clock, else why not.

    Header stamps are the capture times the driver measured (V4L2 buffer
    timestamps for passthrough). They are used only when every frame has one,
    they increase by at least 1 ms across the whole recording, and relative to
    the first frame they stay within 1 s of the bag receipt clock.
    """
    stamps=[row.get('stamp_ns') for row in frames]
    if any(type(s) is not int or s<=0 for s in stamps):return 'header_stamp_missing'
    if any(b-a<HEADER_STAMP_MIN_SPACING_NS for a,b in zip(stamps,stamps[1:])):
        return 'header_stamp_not_increasing_by_1ms'
    if any(abs((s-stamps[0])-(r-receipts[0]))>HEADER_RECEIPT_MAX_DIVERGENCE_NS for s,r in zip(stamps,receipts)):
        return 'header_stamp_diverges_from_receipt'
    return None


def frame_timing(frames):
    """Millisecond PTS for every recorded frame; never adds or drops a frame.

    The presentation clock is the header (capture) stamp when it qualifies,
    else the bag receipt. Receipts written in a burst can fall inside one
    millisecond; such a frame moves to the next free millisecond and the
    adjustment is counted instead of failing the whole recording. Bag receipt
    times (t_s) remain the archive alignment clock.
    """
    if not frames:raise ValueError('camera_frames_required')
    times=[];native=[]
    for row in frames:
        value=row.get('t_s')
        if type(value) not in (float,int) or not math.isfinite(value) or value<0:
            raise ValueError('invalid_camera_frame_time')
        if times and value<=times[-1]:raise ValueError('camera_frame_time_nonmonotonic')
        times.append(float(value))
        ns=row.get('bag_time_ns')
        if ns is not None:
            if type(ns) is not int or ns<0 or (native and ns<=native[-1]):
                raise ValueError('invalid_native_camera_time')
            native.append(ns)
    if native and len(native)!=len(frames):raise ValueError('mixed_native_camera_time')
    if native:
        receipt=[ns-native[0] for ns in native]
        if any(abs(ns*1e-9-(t-times[0]))>2e-6 for ns,t in zip(receipt,times)):
            raise ValueError('camera_native_time_mismatch')
    rejection=header_stamp_rejection(frames,native or [round(t*1e9) for t in times])
    if rejection is None or native:
        if rejection is None:
            stamps=[row['stamp_ns'] for row in frames]
            relative=[s-stamps[0] for s in stamps];source='header_stamp'
        else:
            relative=receipt;source='bag_receipt'
        intervals=[relative[i+1]-relative[i] for i in range(len(relative)-1)]
        last_ns=sorted(intervals)[len(intervals)//2] if intervals else 66_666_667
        pts=[(ns+500_000)//1_000_000 for ns in relative]
        terminal=(relative[-1]+last_ns+500_000)//1_000_000
        last_dt=last_ns*1e-9;exact=[ns*1e-9 for ns in relative]
    else:
        exact=[t-times[0] for t in times];source='t_s'
        intervals=[exact[i+1]-exact[i] for i in range(len(exact)-1)]
        last_dt=sorted(intervals)[len(intervals)//2] if intervals else 1/15
        pts=[math.floor(t*1000+.5) for t in exact]
        terminal=math.floor((exact[-1]+last_dt)*1000+.5)
    # Two receipts inside one millisecond are distinct frames: keep both and
    # their order, moving the later one to the next millisecond (counted).
    adjusted=0;max_shift=0
    for i in range(1,len(pts)):
        if pts[i]<=pts[i-1]:
            max_shift=max(max_shift,pts[i-1]+1-pts[i]);pts[i]=pts[i-1]+1;adjusted+=1
    span=exact[-1];duration=span+last_dt
    if terminal<=pts[-1]:terminal=pts[-1]+1;duration=terminal*.001
    return dict(pts_ms=pts,terminal_pts_ms=terminal,last_dt_s=last_dt,span_s=span,duration_s=duration,
                pts_source=source,header_stamp_rejected_reason=rejection,
                pts_adjusted_frames=adjusted,pts_max_adjustment_s=max_shift*.001,
                max_rounding_error_s=max(abs(p*.001-t) for p,t in zip(pts,exact)))


def _verified_frame(root,row):
    name=row.get('file')
    if not isinstance(name,str) or not re.fullmatch(r'frames/[0-9]{7}\.jpg',name):
        raise ValueError('invalid_camera_frame_path')
    path=root/name
    if path.is_symlink() or root not in path.resolve(strict=True).parents:
        raise ValueError('camera_frame_path_escape')
    count=row.get('bytes')
    if type(count) is not int or not 0<count<=64*1024*1024 or path.stat().st_size!=count:
        raise ValueError('camera_frame_size_mismatch')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=row.get('sha256'):
        raise ValueError('camera_frame_hash_mismatch')
    return data


def mux_native_jpeg(frames,root,output,width,height):
    import av
    root=Path(root).resolve(strict=True);output=Path(output)
    plan=frame_timing(frames)
    def input_container(data):
        # Demux a SINGLE JPEG, not a filename sequence. Its duration/time-base
        # and keyframe flag come from the demuxer, using APIs available in 8.x.
        return av.open(io.BytesIO(data),'r',format='image2pipe',
                       options={'framerate':'1000','threads':'1'})
    first=_verified_frame(root,frames[0])
    with input_container(first) as template,output.open('xb') as stream_file:
        input_stream=template.streams.video[0]
        if (input_stream.width,input_stream.height)!=(width,height):
            raise ValueError('camera_frame_dimensions_mismatch')
        with av.open(stream_file,'w',format='matroska') as container:
            stream=container.add_stream(template=input_stream)
            stream.time_base=TIME_BASE
            for index,row in enumerate([*frames,frames[-1]]):
                data=first if index==0 else _verified_frame(root,row)
                with input_container(data) as source:
                    source_stream=source.streams.video[0]
                    if (source_stream.width,source_stream.height)!=(width,height):
                        raise ValueError('camera_frame_dimensions_mismatch')
                    packets=[packet for packet in source.demux(source_stream) if packet.size]
                    if len(packets)!=1:raise ValueError('one_native_jpeg_packet_required')
                    packet=packets[0]
                    if bytes(packet)!=data:raise ValueError('native_jpeg_packet_bytes_changed')
                    # FFmpeg 4.4 image2pipe may leave duration unknown (0).
                    # Presentation intervals are still explicit in adjacent
                    # PTS; do not try an unavailable PyAV 8 duration setter.
                    if (packet.time_base!=TIME_BASE or packet.duration not in (None,0,1)
                            or source_stream.average_rate!=1000 or not packet.is_keyframe):
                        raise ValueError('native_jpeg_demux_timing_contract')
                    packet.stream=stream
                    packet.pts=packet.dts=plan['pts_ms'][index] if index<len(frames) else plan['terminal_pts_ms']
                    packet.time_base=TIME_BASE
                    container.mux(packet)
    return dict(kind='pyav_mjpeg_matroska',pyav_version=av.__version__,
                source_jpeg_bytes_preserved=True,time_base=[1,1000],
                timestamp_quantum_s=.001,max_rounding_error_s=plan['max_rounding_error_s'],
                pts_source=plan['pts_source'],header_stamp_rejected_reason=plan['header_stamp_rejected_reason'],
                pts_adjusted_frames=plan['pts_adjusted_frames'],pts_max_adjustment_s=plan['pts_max_adjustment_s'],
                last_frame_policy='repeat_last_at_terminal_pts',last_frame_hold_s=plan['last_dt_s'],
                terminal_pts_s=plan['terminal_pts_ms']*.001,terminal_packet_duration_s=None,
                source_packets=len(frames),muxed_packets=len(frames)+1)
