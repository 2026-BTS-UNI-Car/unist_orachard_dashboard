"""Bounded independent full-resolution JPEG encoder for recording subscribers."""
import queue
import threading
import time
import cv2


class NativeCameraEncoder:
    def __init__(self, publish, quality=90):
        self.publish = publish
        self.quality = max(80, min(95, quality))
        self.queue = queue.Queue(maxsize=4)
        self.dropped = self.published = 0
        self.cancelled = 0
        self.last_encode_ms = 0.
        self.error = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name='native-camera-jpeg', daemon=True)
        self.thread.start()

    def submit(self, frame, stamp):
        try:
            self.queue.put_nowait((frame, stamp))
        except queue.Full:
            self.dropped += 1  # Explicit overload, never invented frames/FPS.

    def run(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                frame, stamp = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            started = time.perf_counter()
            try:
                ok, data = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
                if not ok:
                    raise ValueError('native_jpeg_encode_failed')
                accepted = self.publish(data.tobytes(), stamp, frame.shape[1], frame.shape[0])
                if accepted is False:
                    self.cancelled += 1
                else:
                    self.published += 1
                self.last_encode_ms = (time.perf_counter()-started)*1000
            except Exception as error:
                self.error = str(error)[:160]
            finally:
                self.queue.task_done()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)

    def status(self):
        return dict(published=self.published, dropped=self.dropped, cancelled=self.cancelled, pending=self.queue.qsize(),
                    encode_ms=round(self.last_encode_ms, 2), error=self.error,
                    width_policy='capture_native_without_resize', quality=self.quality)
