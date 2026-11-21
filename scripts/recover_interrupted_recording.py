"""Finish a recording that the recorder could not finish (power loss or a Jetson restart while recording).

Such a recording keeps its rosbag2 bag (not closed, without an index), its sidecars (GNSS, motor, executor lines) and a
cut mapping source, but it has no timeline.json and no preview_scan.json, its metadata still says "recording", and the
dashboard can neither list it as playable nor prepare its video. This tool does what the recorder's Stop would have done,
with the recorder's own code: it reads the bag in order and passes every message to the recorder state's observers
(data_recorder_service.RecordingState) with the recorder's clock set to the time the bag received that message, then
writes metadata.json, preview_scan.json and timeline.json the way RecordingState.stop() writes them, rebuilds the
mapping source with the recorder's mapping writer, and marks the recording for video preparation.

Run inside the recorder container (ROS 2, the recorder's code under /app, the recordings folder mounted), while the
recorder is idle:
    source /opt/ros/iron/setup.bash
    python3 /tmp/recover_interrupted_recording.py <recording id>            (reads only, prints what it would write)
    python3 /tmp/recover_interrupted_recording.py <recording id> --apply

What it keeps: the bag file is never changed (ros2 bag reindex only adds metadata.yaml next to it; an empty one is kept
as metadata.yaml.empty-before-reindex), metadata.json is kept as metadata.before-recovery.json, the cut mapping source as
mapping_source.cut-before-recovery/, and the sidecars gps.jsonl, motor.jsonl and executor_state.jsonl are not rewritten.
Differences from a recording that ended normally: the live recorder took its camera preview frames from the raw camera
topic, which the bag holds only now and then; the tool takes them from the bag's compressed camera topic (the same
camera, decoded, then the recorder's own sampling and preview encoding) when the raw topic is sparse. The recording
ends where the bag can be read, and the stopping of the vehicle after that is missing. metadata.json gets a
"recovered" block that says so.
"""
from __future__ import annotations

import argparse
import inspect
import json
import math
import shutil
import subprocess
import sys
import time as real_time
from datetime import datetime, timezone
from pathlib import Path

APP = Path('/app')
RECORDINGS = APP / 'runtime' / 'recordings'
SCHEMA_MEDIA = 'orchard.media_queue.v2'


class Clock:
    """The recorder module's view of time: the bag's receive time of the message being replayed."""
    def __init__(self):
        self.now = 0.0

    def install(self, module):
        clock = self

        class TimeShim:
            def __getattr__(self, name):
                return getattr(real_time, name)

            def time(self):
                return clock.now

            def monotonic(self):
                return clock.now
        module.time = TimeShim()


def recorder_args(module):
    """The live recorder's arguments: its own parser, given the command line of the running recorder (PID 1)."""
    source = inspect.getsource(module.main)
    head, _, _ = source.partition('args = parser.parse_args(')
    if not head or 'parser = argparse.ArgumentParser' not in head:
        raise SystemExit('the recorder main() has no parser to reuse')
    body = '\n'.join(line[4:] if line.startswith('    ') else line for line in head.split('\n')[1:])
    namespace = dict(vars(module))
    exec(body, namespace)
    argv = Path('/proc/1/cmdline').read_bytes().split(b'\0')
    argv = [a.decode() for a in argv if a]
    script = next(i for i, a in enumerate(argv) if a.endswith('data_recorder_service.py'))
    return namespace['parser'].parse_args(argv[script + 1:])


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def reindex(bag_dir, apply):
    yaml = bag_dir / 'metadata.yaml'
    if yaml.is_file() and yaml.stat().st_size > 0:
        return 'index present'
    if not apply:
        return 'index missing: would run ros2 bag reindex'
    if yaml.exists():
        yaml.rename(bag_dir / 'metadata.yaml.empty-before-reindex')
    run = subprocess.run(['ros2', 'bag', 'reindex', str(bag_dir), '-s', 'mcap'], capture_output=True, text=True, timeout=1800)
    if run.returncode != 0 or not yaml.is_file() or yaml.stat().st_size == 0:
        raise SystemExit('ros2 bag reindex failed: ' + (run.stderr or run.stdout)[-400:])
    return 'index rebuilt'


def read_bag(bag_dir):
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from rosidl_runtime_py.utilities import get_message
    reader = rosbag2_py.SequentialReader()
    yaml = bag_dir / 'metadata.yaml'
    # Without an index (the read-only run, before reindex) the bag file itself is opened.
    uri = bag_dir if yaml.is_file() and yaml.stat().st_size > 0 else sorted(bag_dir.glob('*.mcap'))[0]
    reader.open(rosbag2_py.StorageOptions(uri=str(uri), storage_id='mcap'),
                rosbag2_py.ConverterOptions(input_serialization_format='cdr', output_serialization_format='cdr'))
    types = {t.name: get_message(t.type) for t in reader.get_all_topics_and_types()}
    while True:
        try:
            if not reader.has_next():
                return
            topic, data, stamp_ns = reader.read_next()
        except Exception as error:  # an unclosed bag ends with a broken chunk
            print(json.dumps(dict(bag_read_stopped=str(error)[:200])), flush=True)
            return
        if topic in types:
            yield topic, deserialize_message(data, types[topic]), stamp_ns


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('recording_id')
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    root = RECORDINGS / options.recording_id
    if root.resolve().parent != RECORDINGS.resolve() or not root.is_dir():
        raise SystemExit('no such recording')
    metadata = json.loads((root / 'metadata.json').read_text(encoding='utf-8'))
    if metadata.get('state') != 'recording' or metadata.get('id') != options.recording_id:
        raise SystemExit('only a recording left in state "recording" is finished here (state is %r)' % metadata.get('state'))
    status = json.loads((APP / 'runtime' / 'data_recorder_status.json').read_text(encoding='utf-8'))
    if status.get('state') != 'idle':
        raise SystemExit('the recorder is not idle (%r); try again when no recording runs' % status.get('state'))

    sys.path.insert(0, str(APP))
    import scripts.data_recorder_service as recorder
    import scripts.mapping_sensor_writer as mapping
    args = recorder_args(recorder)
    args.status_file = '/tmp/recovery_recorder_status.json'   # never the live recorder's status file
    clock = Clock()
    clock.install(recorder)
    clock.install(mapping)

    print(json.dumps(dict(step='bag index', result=reindex(root / 'rosbag2', options.apply))), flush=True)
    if not options.apply:
        counts = {}
        for topic, _, stamp_ns in read_bag(root / 'rosbag2'):
            counts[topic] = counts.get(topic, 0) + 1
        print(json.dumps(dict(would_replay=counts), ensure_ascii=False))
        return 0

    started = datetime.fromisoformat(metadata['started_at']).timestamp()
    backup = root / 'metadata.before-recovery.json'
    if not backup.exists():
        shutil.copy2(root / 'metadata.json', backup)
    cut = root / 'mapping_source.cut-before-recovery'
    if (root / 'mapping_source').exists() and not cut.exists():
        (root / 'mapping_source').rename(cut)

    clock.now = started
    state = recorder.RecordingState(args)
    state.active = {**metadata, 'path': str(root), 'started_monotonic': started, 'started_steady': started}
    state.mapping_writer = mapping.MappingSensorWriter(root)
    writer = state.mapping_writer

    def add_blocking(topic, message, receipt=None):   # replay is faster than live: wait instead of dropping
        writer.queue.put(dict(topic=topic, message=message, bag_time_ns=int((receipt or clock.now) * 1e9)))
    writer.add = add_blocking
    state.motor_timeline.started_steady = started     # preview frames only; motor.jsonl is kept as recorded
    state.motor_timeline.started_at = started

    handlers = {
        args.scan_topic: state.observe_scan,
        args.imu_raw_topic: state.observe_imu,
        args.imu_mag_topic: state.observe_magnetic,
        '/gps/data': state.observe_gps,
        '/gps/driver_status': lambda msg: state.observe_gps(msg, status=True),
        args.rover_status_topic: state.observe_motor,
    }
    first_ns = last_ns = None
    raw_frames = 0
    for topic, _, stamp_ns in read_bag(root / 'rosbag2'):
        first_ns = stamp_ns if first_ns is None else first_ns
        last_ns = stamp_ns
        raw_frames += topic == args.camera_topic
    span_s = (last_ns - first_ns) / 1e9 if first_ns is not None else 0.
    camera_topic = args.camera_topic
    if raw_frames < 0.5 * span_s * float(args.timeline_hz):
        camera_topic = args.camera_compressed_topic   # raw camera too sparse in the bag: decode the compressed one

    def as_image(message):
        import cv2
        import numpy as np
        from types import SimpleNamespace
        frame = cv2.imdecode(np.frombuffer(bytes(message.data), dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return None
        return SimpleNamespace(header=message.header, height=frame.shape[0], width=frame.shape[1], encoding='bgr8',
                               data=frame.tobytes())

    counts, last = {}, started
    for topic, message, stamp_ns in read_bag(root / 'rosbag2'):
        clock.now = stamp_ns / 1e9
        if clock.now < started:
            continue
        last = max(last, clock.now)
        if topic == camera_topic:
            if camera_topic != args.camera_topic:
                if clock.now < state.next_camera_sample_at:   # the recorder's own sampling, checked before decoding
                    continue
                message = as_image(message)
                if message is None:
                    continue
            state.observe_camera(message)
            sample, state.camera_pending_sample = state.camera_pending_sample, None
            if sample is not None:
                state.process_camera_sample(sample)
        elif topic in handlers:
            handlers[topic](message)
        else:
            continue
        counts[topic] = counts.get(topic, 0) + 1

    clock.now = last
    mapping_result = writer.close()
    duration_s = round(last - started, 2)
    stop_source = inspect.getsource(recorder.RecordingState.stop)
    assert '"timeline": {' in stop_source   # the block below mirrors stop(); a changed stop() needs this tool checked
    metadata.update({
        'state': 'archived',
        'durability': {**recorder.durability_policy(), 'sidecar_worker': None, 'sidecar_worker_closed': None,
                       'mapping_sidecar_worker_closed': mapping_result['durability']['worker_closed'],
                       'mapping_sidecar_complete': mapping_result['complete'],
                       'mapping_sidecar_error': mapping_result['error']},
        'finished_at': iso(last),
        'duration_s': duration_s,
        'timeline': {
            'lidar_frames': len(state.lidar_frames), 'camera_frames': len(state.camera_frames),
            'imu_frames': len(state.imu_frames), 'gps_frames': state.gps_frame_count,
            'gps_valid_frames': state.gps_valid_count, 'gps_enabled': bool(metadata.get('include_gps', True)),
            'gps_error': state.gps_write_error, 'motor_enabled': True, 'motor_schema': recorder.MOTOR_TIMELINE_SCHEMA,
            'motor_frames': sum(1 for _ in (root / 'motor.jsonl').open(encoding='utf-8')) if (root / 'motor.jsonl').is_file() else 0,
            'motor_frames_file': 'motor.jsonl', 'motor_error': state.motor_timeline.error,
            'sample_hz': args.timeline_hz, 'camera_preview_width': args.camera_preview_width,
            'camera_preview_decoupled': True, 'camera_latest_only': True,
            'imu_enabled': bool(metadata.get('include_imu', True)), 'imu_schema': recorder.IMU_TIMELINE_SCHEMA,
            'imu_processing': recorder.imu_processing_status(), 'imu_timeline_hz': args.imu_timeline_hz,
        },
        'recovered': {
            'at': iso(real_time.time()),
            'by': 'scripts/recover_interrupted_recording.py',
            'reason': 'the recording was not finished by the recorder (power loss or Jetson restart while recording)',
            'source': 'rosbag2 replayed in order through the recorder state with the bag receive times',
            'replayed_messages': counts,
            'bag_readable_until_s': duration_s,
            'camera_preview_source': camera_topic + ' as held by the bag' + ('' if camera_topic == args.camera_topic else
                                     ' (decoded; the raw topic %s has %d frames in the bag)' % (args.camera_topic, raw_frames)),
            'kept': ['rosbag2 bag file unchanged', 'metadata.before-recovery.json', 'mapping_source.cut-before-recovery/',
                     'gps.jsonl, motor.jsonl, executor_state.jsonl as recorded'],
            'not_in_recording': 'the vehicle stopping after the last readable message',
        },
    })
    recorder.write_json_atomic(root / 'metadata.json', metadata)
    recorder.write_json_atomic(root / 'preview_scan.json', state.preview_scan or {'ok': False, 'points': [], 'message': 'no lidar scan captured'})
    recorder.write_json_atomic(root / 'timeline.json', state.timeline_payload(state.active, metadata, duration_s))
    media = root / 'video_preparation.json'
    if metadata.get('kind') in ('camera', 'both') and not media.exists():
        # The live recorder's media queue loads queue-marked recordings when it starts (MediaQueue._load).
        recorder.write_json_atomic(media, dict(schema=SCHEMA_MEDIA, recording_id=root.name, state='queued', stage='queued',
                                               queued_at=real_time.time(), attempt=None, reason='recovered_recording',
                                               error=None, raw_source_preserved=True))
    print(json.dumps(dict(done=root.name, duration_s=duration_s, replayed=counts, lidar_frames=len(state.lidar_frames),
                          camera_frames=len(state.camera_frames), imu_frames=len(state.imu_frames), gps_frames=state.gps_frame_count,
                          mapping_source=dict(complete=mapping_result['complete'], counts=mapping_result['topic_counts']),
                          video_marked=media.exists()), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
