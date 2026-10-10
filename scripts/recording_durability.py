"""Recorder-owned durable writes, without bag repair or recording admission.

fsync completion is an OS/filesystem acknowledgement, not a power-loss test.
The rosbag backend and its buffering remain unchanged.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time

INTERVAL_SECONDS = 1.0


def sync_directory(path):
    """Linux target requires directory fsync; Windows tests report unsupported."""
    if os.name != 'posix':
        return False
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('durable_metadata_symlink')
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as output:
            fd = None
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_synced = sync_directory(path.parent)
        return {'file_fsync_completed': True, 'directory_fsync_completed': directory_synced}
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def flush_sync(stream):
    """Only called by the thread that owns the stream's Python buffers."""
    stream.flush()
    os.fsync(stream.fileno())


def sync_completed_file(path):
    # This is the newly owned sidecar, never an original bag. r+b does not
    # truncate or rewrite it and permits FlushFileBuffers on Windows too.
    with Path(path).open('r+b') as stream:
        os.fsync(stream.fileno())
    return sync_directory(Path(path).parent)


def policy():
    return dict(schema='ubts.recorder.durability.v1', sidecar_fsync_interval_s=INTERVAL_SECONDS,
                atomic_metadata_file_and_directory_fsync_requested=True,
                rosbag_backend_policy='unchanged', rosbag_readproof=False,
                sudden_power_loss_qualified=False, filesystem_storage_failure_qualified=False,
                automatic_interrupted_recording_recovery=False)


class PeriodicFsync:
    """At most two duplicated FDs; fsync never holds the recorder state lock.

    GPS/motor writers already flush each row under the recorder lock. This worker
    touches only duplicate OS descriptors, not their Python stream buffers.
    A blocked fsync cannot be preempted: close(False) retains ownership.
    """
    def __init__(self, *, interval=INTERVAL_SECONDS, fsync=os.fsync):
        if type(interval) not in (int, float) or not .1 <= interval <= 10:
            raise ValueError('bounded_durability_interval')
        self.interval = float(interval)
        self.fsync = fsync
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.descriptors = {}
        self.errors = {}
        self.sync_count = 0
        self.last_sync_monotonic = None

    def register(self, name, stream):
        if name not in ('gps', 'motor'):
            raise ValueError('bounded_recorder_sidecar_name')
        with self.lock:
            if self.thread is not None or self.stop.is_set() or name in self.descriptors:
                raise ValueError('durability_registration_closed')
            fd = os.dup(stream.fileno())
            try:
                os.set_inheritable(fd, False)
            except BaseException:
                os.close(fd)
                raise
            self.descriptors[name] = fd

    def start(self):
        with self.lock:
            if self.thread is not None or self.stop.is_set():
                raise ValueError('one_durability_worker')
            worker = threading.Thread(target=self._run, name='recorder-sidecar-fsync', daemon=True)
            worker.start()
            self.thread = worker

    def _sync(self):
        with self.lock:
            rows = tuple(self.descriptors.items())
        for name, fd in rows:
            try:
                self.fsync(fd)
                with self.lock:
                    self.sync_count += 1
                    self.last_sync_monotonic = time.monotonic()
            except OSError as error:
                with self.lock:
                    self.errors[name] = str(error)[:160]

    def _run(self):
        try:
            while not self.stop.wait(self.interval):
                self._sync()
            self._sync()
        finally:
            with self.lock:
                rows = tuple(self.descriptors.items())
            for name, fd in rows:
                try:
                    os.close(fd)
                except OSError as error:
                    with self.lock:
                        self.errors[name] = 'descriptor_close:' + str(error)[:128]
                else:
                    with self.lock:
                        self.descriptors.pop(name, None)

    def close(self, timeout=2.0):
        self.stop.set()
        with self.lock:
            worker = self.thread
        if worker is None:
            self._sync()
            with self.lock:
                rows = tuple(self.descriptors.items())
            for name, fd in rows:
                os.close(fd)
                with self.lock:
                    self.descriptors.pop(name)
        else:
            worker.join(timeout=max(0., min(2., float(timeout))))
        value = self.status()
        return not value['worker_alive'] and value['owned_descriptors'] == 0

    def status(self):
        with self.lock:
            return dict(interval_s=self.interval, worker_alive=self.thread is not None and self.thread.is_alive(),
                        owned_descriptors=len(self.descriptors), fsync_calls_completed=self.sync_count,
                        last_fsync_monotonic=self.last_sync_monotonic, errors=dict(self.errors),
                        sudden_power_loss_qualified=False, rosbag_readproof=False)
