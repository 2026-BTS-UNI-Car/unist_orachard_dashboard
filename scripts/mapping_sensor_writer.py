"""Raw-rate mapping sidecar; bounded disk worker, original ROS bags retained."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import queue
import threading
import time
from scripts.recording_durability import atomic_json, flush_sync, sync_completed_file, sync_directory, INTERVAL_SECONDS


def scan_payload(msg):
    import math
    return dict(header=dict(stamp=dict(sec=msg.header.stamp.sec,nanosec=msg.header.stamp.nanosec),frame_id=msg.header.frame_id),
        ranges=[float(r) if math.isfinite(r) else None for r in msg.ranges],
        angle_min=float(msg.angle_min),angle_increment=float(msg.angle_increment),
        range_min=float(msg.range_min),range_max=float(msg.range_max),
        scan_time=float(msg.scan_time),time_increment=float(msg.time_increment))


class MappingSensorWriter:
    def __init__(self, archive):
        self.archive=Path(archive);self.folder=self.archive/'mapping_source';self.folder.mkdir()
        sync_directory(self.archive)
        self.queue=queue.Queue(maxsize=512);self.stop=threading.Event()
        self.counts=Counter();self.dropped=0;self.error=None;self.fsync_count=0;self.last_fsync_monotonic=None
        self.thread=threading.Thread(target=self.run,name='mapping-raw-sidecar',daemon=True);self.thread.start()

    def add(self,topic,message,receipt=None):
        if self.stop.is_set():return
        row=dict(topic=topic,message=message,bag_time_ns=int((receipt or time.time())*1e9))
        try:self.queue.put_nowait(row)
        except queue.Full:self.dropped+=1

    def run(self):
        try:
            with gzip.open(self.folder/'sensors.jsonl.gz','wt',encoding='utf-8',compresslevel=1) as stream:
                sync_directory(self.folder)
                next_sync=time.monotonic()+INTERVAL_SECONDS
                dirty=False
                while not self.stop.is_set() or not self.queue.empty():
                    try:row=self.queue.get(timeout=.1)
                    except queue.Empty:
                        if dirty and time.monotonic()>=next_sync:
                            flush_sync(stream);self.fsync_count+=1;self.last_fsync_monotonic=time.monotonic()
                            next_sync=self.last_fsync_monotonic+INTERVAL_SECONDS;dirty=False
                        continue
                    stream.write(json.dumps(row,allow_nan=False,separators=(',',':'))+'\n')
                    self.counts[row['topic']]+=1;self.queue.task_done();dirty=True
                    if time.monotonic()>=next_sync:
                        flush_sync(stream);self.fsync_count+=1;self.last_fsync_monotonic=time.monotonic()
                        next_sync=self.last_fsync_monotonic+INTERVAL_SECONDS;dirty=False
            sync_completed_file(self.folder/'sensors.jsonl.gz')
        except Exception as error:self.error=str(error)[:200]

    def close(self):
        self.stop.set();self.thread.join(timeout=15)
        if self.thread.is_alive():self.error='mapping_writer_flush_timeout'
        path=self.folder/'sensors.jsonl.gz';sha=hashlib.sha256()
        # A timed-out writer still owns its stream. Never hash a changing file
        # or present that hash as a finalized immutable output.
        closed=not self.thread.is_alive()
        if closed and path.is_file():
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(4*1024*1024),b''):sha.update(block)
        value=dict(schema='ubts.field_extraction.v1',archive_id=self.archive.name,
            complete=not self.error and not self.dropped and not self.thread.is_alive(),
            topic_counts=dict(self.counts),output_sha256={'sensors.jsonl.gz':sha.hexdigest()} if closed else {},
            source='recording_ROS_callback_native_data',time_base='host_receipt_nanoseconds',
            dropped=self.dropped,error=self.error,original_rosbag_preserved=True,
            durability=dict(periodic_fsync_count=self.fsync_count,fsync_interval_s=INTERVAL_SECONDS,
                            worker_closed=closed,sudden_power_loss_qualified=False,rosbag_readproof=False))
        atomic_json(self.folder/'manifest.json',value)
        return value
