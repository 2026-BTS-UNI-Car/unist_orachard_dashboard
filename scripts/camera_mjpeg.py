"""Original UVC MJPEG frames and per-stage camera rates; no pixel decode here.

With CAP_PROP_CONVERT_RGB=0 the OpenCV V4L2 backend returns the camera's own
JPEG as one uint8 row. It is checked from its header (SOI, first SOF, EOI) and
published unchanged; rotation becomes declared metadata, never pixel work.
"""
from __future__ import annotations

from collections import deque
import threading
import time
from typing import NamedTuple

import numpy as np

SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}  # DHT/JPG/DAC are not frames.
CAMERA_STAGES = ('device_reads', 'passthrough_frames', 'invalid_frames', 'recording_published',
                 'preview_decoded', 'preview_published', 'preview_overwritten', 'raw_published')
PREVIEW_DECODE_REDUCTIONS = ('auto', '1', '2', '4')


class JpegFrame(NamedTuple):
    data: bytes  # Immutable copy of the device buffer; shared by recording/preview.
    width: int
    height: int


def jpeg_dimensions(data):
    """(width, height) of the first SOF segment, walking marker headers only."""
    i, end = 2, len(data)
    while i + 4 <= end:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # Fill byte.
            i += 1; continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            i += 2; continue
        if marker in (0xD8, 0xD9, 0xDA):  # A scan/EOI before any SOF is not a frame.
            return None
        length = data[i + 2] << 8 | data[i + 3]
        if length < 2:
            return None
        if marker in SOF_MARKERS:
            if length < 8 or i + 9 > end:
                return None
            height, width = data[i + 5] << 8 | data[i + 6], data[i + 7] << 8 | data[i + 8]
            return (width, height) if width and height else None
        i += 2 + length
    return None


def decoded_image(frame):
    """The backend ignored CONVERT_RGB=0 and returned decoded pixels."""
    return isinstance(frame, np.ndarray) and frame.ndim == 3


def jpeg_frame(frame):
    """One passthrough buffer; ValueError names the rejected property."""
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or not (frame.ndim == 1 or (frame.ndim == 2 and frame.shape[0] == 1))):
        raise ValueError('passthrough_buffer_not_one_uint8_row')
    data = frame.tobytes()
    if len(data) < 4 or data[:2] != b'\xff\xd8':
        raise ValueError('jpeg_soi_missing')
    # Strict end: a truncated or padded buffer is not proven to be one frame.
    if data[-2:] != b'\xff\xd9':
        raise ValueError('jpeg_eoi_missing')
    size = jpeg_dimensions(data)
    if size is None:
        raise ValueError('jpeg_sof_missing')
    return JpegFrame(data, *size)


def original_jpeg_format(jpeg, rotate_180):
    # Pixels are as the sensor delivered them; rotation is for consumers.
    return (f"jpeg; original_camera_mjpeg; rotation={180 if rotate_180 else 0}; "
            f"width={jpeg.width}; height={jpeg.height}")


def preview_reduction(setting, source_width):
    """libjpeg scaled-decode factor; auto halves 1080p+ sources (1920 -> 960)."""
    if str(setting) == 'auto':
        return 2 if int(source_width) >= 1920 else 1
    return int(setting)


class StageCounters:
    """Cumulative counts and a rolling window rate per stage, monotonic only.

    Rates are events inside the last min(window, elapsed) seconds; a stopped
    stage decays to zero instead of reporting its last observed interval.
    """

    def __init__(self, names=CAMERA_STAGES, window_s=5.0, clock=time.monotonic):
        self.window_s = float(window_s)
        self.clock = clock
        self.lock = threading.Lock()
        self.started = clock()
        self.totals = {name: 0 for name in names}
        self.bytes = {}
        self.recent = {name: deque() for name in names}

    def add(self, name, nbytes=None):
        now = self.clock()
        with self.lock:
            self.totals[name] = self.totals.get(name, 0) + 1
            if nbytes is not None:
                self.bytes[name] = self.bytes.get(name, 0) + int(nbytes)
            recent = self.recent.setdefault(name, deque())
            recent.append((now, int(nbytes or 0)))
            self.prune(recent, now)

    def prune(self, recent, now):
        horizon = now - self.window_s
        while recent and recent[0][0] <= horizon:
            recent.popleft()

    def total(self, name):
        with self.lock:
            return self.totals.get(name, 0)

    def status(self):
        now = self.clock()
        with self.lock:
            window = min(self.window_s, now - self.started)
            rated = window >= .5
            rows = {}
            for name, total in self.totals.items():
                recent = self.recent[name]
                self.prune(recent, now)
                row = dict(total=total, hz=round(len(recent) / window, 2) if rated else None)
                if name in self.bytes:
                    row.update(bytes=self.bytes[name],
                               bytes_per_s=round(sum(n for _, n in recent) / window, 1) if rated else None)
                rows[name] = row
            return dict(policy='cumulative_and_rolling_window_monotonic', window_s=round(max(0., window), 3),
                        stages=rows)
