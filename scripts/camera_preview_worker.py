"""One pending immutable camera frame; rendering/serialization never queues capture."""
from __future__ import annotations

from contextlib import ExitStack
import threading
import time


class LatestCameraWorker:
    def __init__(self, process, *, clock=time.monotonic):
        self.process = process
        self.clock = clock
        self.condition = threading.Condition()
        self.gates = {name: threading.Lock() for name in ('preview', 'raw', 'native')}
        self.pending = None
        self.generation = 0
        self.closed = False
        self.busy = False
        self.submitted = self.overwritten = self.cancelled = self.processed = 0
        self.raw_due_carried = 0
        self.last_work_ms = self.last_queue_wait_ms = self.last_frame_age_ms = None
        self.error = None
        self.thread = threading.Thread(target=self.run, name='camera-preview-latest', daemon=True)
        self.thread.start()

    def submit(self, frame, stamp, raw_due, captured_at):
        with self.condition:
            if self.closed:
                return None
            if self.pending is not None:
                self.overwritten += 1
                if self.pending[3] and not raw_due:
                    self.raw_due_carried += 1
                raw_due = bool(raw_due or self.pending[3])
            generation = self.generation
            self.pending = (generation, frame, stamp, bool(raw_due), captured_at)
            self.submitted += 1
            self.condition.notify_all()
            return generation

    def publish_current(self, generation, callback, channel='preview'):
        # Publishing one stream cannot block submission or the other streams.
        # Reset obtains these gates as a barrier for already-started DDS calls.
        with self.gates[channel]:
            if self.closed or generation != self.generation:
                return False
            callback()
            return True

    def is_current(self, generation):
        return not self.closed and generation == self.generation

    def reset(self, timeout=.5):
        with self.condition:
            if self.closed:
                return True
            self.generation += 1
            if self.pending is not None:
                self.cancelled += 1
            self.pending = None
            self.condition.notify_all()
        deadline = self.clock() + timeout
        with ExitStack() as stack:
            for gate in self.gates.values():
                if not gate.acquire(timeout=max(0., deadline - self.clock())):
                    self.error = 'camera_preview_reset_publish_barrier_timeout'
                    return False
                stack.callback(gate.release)
        return True

    def run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                generation, frame, stamp, raw_due, captured_at = self.pending
                self.pending = None
                self.busy = True
            started = self.clock()
            try:
                self.process(frame, stamp, raw_due, generation)
            except Exception as error:
                self.error = str(error)[:160]
            finally:
                finished = self.clock()
                with self.condition:
                    self.processed += 1
                    self.last_work_ms = (finished - started) * 1000
                    self.last_queue_wait_ms = max(0., (started - captured_at) * 1000)
                    self.last_frame_age_ms = max(0., (finished - captured_at) * 1000)
                    self.busy = False
                    self.condition.notify_all()

    def wait_idle(self, timeout=3):
        with self.condition:
            return self.condition.wait_for(lambda: not self.busy and self.pending is None, timeout=timeout)

    def close(self, timeout=3):
        # Python cannot cancel an arbitrary blocked codec/DDS call. Invalidate
        # all not-yet-published work immediately and report a bounded join miss.
        with self.condition:
            self.closed = True
            self.generation += 1
            if self.pending is not None:
                self.cancelled += 1
            self.pending = None
            self.condition.notify_all()
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            self.error = 'camera_preview_worker_shutdown_timeout'
            return False
        return True

    def status(self):
        with self.condition:
            return dict(policy='latest_frame_pending_1_raw_due_carried', generation=self.generation,
                        submitted=self.submitted, overwritten=self.overwritten, cancelled=self.cancelled,
                        processed=self.processed, pending=int(self.pending is not None), busy=self.busy,
                        raw_due_carried=self.raw_due_carried, work_ms=self.last_work_ms,
                        queue_wait_ms=self.last_queue_wait_ms, frame_age_ms=self.last_frame_age_ms,
                        error=self.error, closed=self.closed, thread_alive=self.thread.is_alive())
