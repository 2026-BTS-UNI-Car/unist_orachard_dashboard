#!/usr/bin/env python3
"""Read native camera frames from a finished ROS bag, never replay ROS topics."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

try:
    from scripts.native_jpeg_vfr import frame_timing
except ModuleNotFoundError:
    from native_jpeg_vfr import frame_timing

REENCODED_PAYLOAD=re.compile(r'reencoded_bgr_q[0-9]{1,3}')


def compressed_provenance(fmt):
    """(payload kind, rotation_deg) declared by a CompressedImage format string.

    Driver passthrough frames ('original_camera_mjpeg') and decoded-fallback
    frames ('reencoded_bgr_qNN') keep sensor orientation and declare
    'rotation=180'. Untagged legacy re-encodes are already upright (rotation 0).
    """
    tokens=[token.strip() for token in str(fmt).split(';')]
    rotation=0
    for token in tokens:
        if token.startswith('rotation='):
            if token not in ('rotation=0','rotation=180'):raise ValueError('unsupported_camera_rotation')
            rotation=int(token[len('rotation='):])
    if 'original_camera_mjpeg' in tokens:return 'original_camera_mjpeg',rotation
    return next((t for t in tokens if REENCODED_PAYLOAD.fullmatch(t)),'reencoded_jpeg'),rotation


def digest(path):
    sha=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):sha.update(block)
    return sha.hexdigest()


def export(archive,output,topic='auto'):
    import cv2
    import numpy as np
    import rosbag2_py
    import yaml
    from rclpy.serialization import deserialize_message
    from rosidl_runtime_py.utilities import get_message
    archive=Path(archive).resolve(strict=True);output=Path(output)
    metadata=json.loads((archive/'metadata.json').read_text())
    if metadata.get('state')!='archived':raise ValueError('archive_must_be_finished')
    info=yaml.safe_load((archive/'rosbag2/metadata.yaml').read_text())['rosbag2_bagfile_information']
    topics={r['topic_metadata']['name']:r['message_count'] for r in info['topics_with_message_count']}
    if topic=='auto':
        topic=next((name for name in ('/camera/recording/jpeg','/camera/image_jpeg','/camera/image_raw') if topics.get(name,0)>0),None)
    if topic not in topics or not topics[topic]:raise ValueError('camera_topic_not_recorded')
    if archive.joinpath('rosbag2').is_symlink():raise ValueError('source_bag_symlink')
    bag_files=sorted(p for p in (archive/'rosbag2').rglob('*') if p.is_file())
    if any(p.is_symlink() or archive not in p.resolve().parents for p in bag_files):raise ValueError('source_bag_symlink')
    signatures={str(p.relative_to(archive)):[p.stat().st_size,p.stat().st_mtime_ns] for p in
                [archive/'metadata.json',*bag_files,
                 *[archive/n for n in ('gps.jsonl','motor.jsonl') if (archive/n).is_file()]]}
    hashes={name:digest(archive/name) for name in signatures}
    reader=rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(archive/'rosbag2'),storage_id=info['storage_identifier']),rosbag2_py.ConverterOptions('',''))
    reader.set_filter(rosbag2_py.StorageFilter(topics=[topic]))
    typename=next(t.type for t in reader.get_all_topics_and_types() if t.name==topic)
    message_type=get_message(typename);start=info['starting_time']['nanoseconds_since_epoch']
    frames=[];previous=None;dimensions=None;rotation=None;payload_frames={};segments=[]
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise ValueError('export_target_already_exists')
    partial=output.with_suffix('.zip.partial')
    with zipfile.ZipFile(partial,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as bundle:
        while reader.has_next():
            _,raw,receipt=reader.read_next();message=deserialize_message(raw,message_type)
            if previous is not None and receipt<=previous:raise ValueError('camera_receipt_nonmonotonic')
            previous=receipt
            if typename=='sensor_msgs/msg/CompressedImage':
                data=bytes(message.data)
                image=cv2.imdecode(np.frombuffer(data,dtype=np.uint8),cv2.IMREAD_COLOR)
                payload,current_rotation=compressed_provenance(message.format)
            elif typename=='sensor_msgs/msg/Image':
                if message.encoding not in ('bgr8','rgb8'):raise ValueError('unsupported_native_camera_encoding')
                image=np.frombuffer(bytes(message.data),dtype=np.uint8).reshape(message.height,message.step)[:,:message.width*3].reshape(message.height,message.width,3)
                if message.encoding=='rgb8':image=cv2.cvtColor(image,cv2.COLOR_RGB2BGR)
                ok,jpeg=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,95])
                if not ok:raise ValueError('native_frame_export_failed')
                data=jpeg.tobytes();current_rotation=0;payload='raw_image_encoded_q95'
            else:raise ValueError('unsupported_camera_message')
            if image is None:raise ValueError('invalid_source_camera_frame')
            size=(image.shape[1],image.shape[0])
            # A payload switch (passthrough <-> decoded fallback) with the same
            # orientation and size is one recording; a size or rotation change
            # would make part of the video wrong, so it is still rejected.
            if dimensions is not None and (size!=dimensions or rotation!=current_rotation):raise ValueError('camera_mode_changed_inside_recording')
            dimensions=size;rotation=current_rotation
            payload_frames[payload]=payload_frames.get(payload,0)+1
            if segments and segments[-1]['payload']==payload:segments[-1]['frames']+=1
            else:segments.append(dict(first_frame=len(frames),frames=1,payload=payload))
            name=f'frames/{len(frames):07d}.jpg';bundle.writestr(name,data)
            frames.append(dict(file=name,t_s=(receipt-start)/1e9,bag_time_ns=receipt,
                stamp_ns=message.header.stamp.sec*1000000000+message.header.stamp.nanosec,
                bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
        if len(frames)!=topics[topic]:raise ValueError('camera_source_count_mismatch')
        for name,signature in signatures.items():
            p=archive/name
            if [p.stat().st_size,p.stat().st_mtime_ns]!=signature:raise ValueError('archive_changed_during_export')
        try:  # Summary only: the export itself never fails on presentation timing.
            timing=frame_timing(frames)
            timing={k:timing.get(k) for k in ('pts_source','header_stamp_rejected_reason',
                'pts_adjusted_frames','pts_max_adjustment_s','max_rounding_error_s')}
        except ValueError as error:
            timing=dict(error=str(error))
        payload=next(iter(payload_frames)) if len(payload_frames)==1 else 'mixed'
        value=dict(schema='ubts.native_camera_export.v1',complete=True,archive_id=archive.name,frames=frames,
            source_sha256=hashes,source_signatures=signatures,source_topic=topic,bag_start_ns=start,
            source_width=dimensions[0],source_height=dimensions[1],rotation_deg=rotation,all_frames_exported=True,
            # True only when every exported frame is the camera's own JPEG.
            original_camera_codec_preserved=payload=='original_camera_mjpeg',
            camera_payload=payload,camera_payload_frames=payload_frames,camera_payload_segments=segments,
            # PTS plan the adaptive build will use (frames keep receipt t_s/bag_time_ns for alignment).
            presentation_timing=timing)
        bundle.writestr('manifest.json',json.dumps(value,separators=(',',':')))
    partial.replace(output)
    return dict(archive_id=archive.name,source_topic=topic,frames=len(frames),width=dimensions[0],height=dimensions[1],bytes=output.stat().st_size,sha256=digest(output))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('archive',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--topic',default='auto');args=parser.parse_args()
    print(json.dumps(export(args.archive,args.output,args.topic)),flush=True)
