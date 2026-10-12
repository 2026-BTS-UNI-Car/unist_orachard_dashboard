"""Original UVC MJPEG frames and per-stage camera rates; no pixel decode here.

With CAP_PROP_CONVERT_RGB=0 the OpenCV V4L2 backend returns the camera's own
JPEG as one uint8 row. It is checked from its marker headers (SOI, frame and
table segments up to an in-bounds SOS, EOI) and published unchanged; rotation
becomes declared metadata, never pixel work.
"""
from __future__ import annotations

from collections import deque
import threading
import time
from typing import NamedTuple

import numpy as np

# Frame types libjpeg-turbo 2.1 (Ubuntu 22.04, the export/preview decoder)
# decodes: baseline, extended, progressive and their arithmetic variants.
SUPPORTED_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC9, 0xCA})
# Every other SOFn (lossless, hierarchical, JPG) makes libjpeg stop decoding.
UNSUPPORTED_SOF = frozenset(range(0xC0, 0xD0)) - SUPPORTED_SOF - {0xC4, 0xCC}  # DHT/DAC are tables.
# Parameter segments libjpeg reads before a scan: DHT, DAC, DQT, DNL, DRI, APPn, COM.
TABLE_SEGMENTS = frozenset({0xC4, 0xCC, 0xDB, 0xDC, 0xDD, 0xFE, *range(0xE0, 0xF0)})
CAMERA_STAGES = ('device_reads', 'passthrough_frames', 'invalid_frames', 'recording_published',
                 'preview_decoded', 'preview_published', 'preview_overwritten', 'raw_published',
                 'jpeg_fill_trimmed')
PREVIEW_DECODE_REDUCTIONS = ('auto', '1', '2', '4')
SCALED_DECODE_FACTORS = (4, 2)  # libjpeg IMREAD_REDUCED_COLOR_{4,2}; 1 is a full decode.


class JpegFrame(NamedTuple):
    data: bytes  # Immutable copy of the device buffer; shared by recording/preview.
    width: int
    height: int
    trimmed_fill: int = 0  # 0x00/0xFF bytes removed after the final EOI.


class JpegHeader(NamedTuple):
    width: int
    height: int
    scan_start: int  # First entropy-coded byte after the first SOS header.


def one_row_uint8(frame):
    """The only buffer shape CONVERT_RGB=0 gives for an MJPEG palette."""
    return (isinstance(frame, np.ndarray) and frame.dtype == np.uint8
            and (frame.ndim == 1 or (frame.ndim == 2 and frame.shape[0] == 1)))


def jpeg_header(data):
    """Marker walk from SOI to the first SOS; ValueError names the defect.

    Header-only and bounded (microseconds), but mirrors what makes libjpeg
    refuse a frame: an SOI/EOI or a reserved/unknown marker before the scan,
    an unsupported or repeated frame header, a segment past the buffer end,
    quantisation tables the frame references but never defines, or a scan
    naming components the frame does not have. Huffman tables are optional
    (Motion-JPEG omits them and decoders use the standard tables).
    """
    end = len(data)
    if end < 4 or data[0] != 0xFF or data[1] != 0xD8:
        raise ValueError('jpeg_soi_missing')
    i, frame, quant_tables = 2, None, set()
    while True:
        if i + 2 > end:
            raise ValueError('jpeg_sos_missing')
        if data[i] != 0xFF:
            raise ValueError('jpeg_marker_expected')
        marker = data[i + 1]
        if marker == 0xFF:  # Fill byte before a marker.
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # TEM/RSTn carry no length.
            i += 2
            continue
        if marker in (0xD8, 0xD9):
            raise ValueError('jpeg_soi_or_eoi_before_sos')
        if marker != 0xDA and marker not in TABLE_SEGMENTS and marker not in SUPPORTED_SOF:
            raise ValueError('jpeg_sof_unsupported' if marker in UNSUPPORTED_SOF else 'jpeg_reserved_marker_before_sos')
        if i + 4 > end:
            raise ValueError('jpeg_segment_out_of_bounds')
        length = data[i + 2] << 8 | data[i + 3]
        body, after = i + 4, i + 2 + length
        if length < 2 or after > end:
            raise ValueError('jpeg_segment_out_of_bounds')
        if marker in SUPPORTED_SOF:
            if frame is not None:
                raise ValueError('jpeg_sof_duplicate')
            components = data[body + 5] if length >= 8 else 0
            if not 1 <= components <= 4 or length != 8 + 3 * components:
                raise ValueError('jpeg_sof_invalid')
            height, width = data[body + 1] << 8 | data[body + 2], data[body + 3] << 8 | data[body + 4]
            if not width or not height:
                raise ValueError('jpeg_sof_invalid')
            rows = range(body + 6, after, 3)
            frame = (width, height, {data[k]: data[k + 2] for k in rows})  # component id -> quant table.
        elif marker == 0xDB:
            k = body
            while k < after:
                precision, table = data[k] >> 4, data[k] & 0x0F
                k += 1 + 64 * (precision + 1)
                if precision > 1 or table > 3 or k > after:
                    raise ValueError('jpeg_dqt_invalid')
                quant_tables.add(table)
        elif marker == 0xDA:
            if frame is None:
                raise ValueError('jpeg_sof_missing')
            count = data[body] if length >= 3 else 0
            if not 1 <= count <= 4 or length != 6 + 2 * count or after > end - 2:
                raise ValueError('jpeg_sos_invalid')
            width, height, components = frame
            scan = [data[body + 1 + 2 * k] for k in range(count)]
            if any(component not in components for component in scan):
                raise ValueError('jpeg_sos_invalid')
            # libjpeg latches each scan component's table at the scan start.
            if any(components[component] not in quant_tables for component in scan):
                raise ValueError('jpeg_dqt_missing')
            return JpegHeader(width, height, after)
        i = after


def jpeg_dimensions(data):
    """(width, height) of a frame whose header reaches a valid SOS, else None."""
    try:
        header = jpeg_header(data)
    except ValueError:
        return None
    return header.width, header.height


def decoded_image(frame):
    """A decoded BGR image: HxWx3 uint8 only (raw YUYV planes are not)."""
    return (isinstance(frame, np.ndarray) and frame.dtype == np.uint8 and frame.ndim == 3
            and frame.shape[2] == 3 and frame.shape[0] > 0 and frame.shape[1] > 0)


def jpeg_frame(frame):
    """One passthrough buffer; ValueError names the rejected property.

    The buffer must be one JPEG. A buffer that does not end in EOI is accepted
    only when everything after the last EOI is 0x00/0xFF fill and that EOI lies
    after the SOS header: inside entropy-coded data byte stuffing means FF D9
    can only be the real EOI, so only fill is removed. Truncated buffers and
    other trailing bytes stay rejected.
    """
    if not one_row_uint8(frame):
        raise ValueError('passthrough_buffer_not_one_uint8_row')
    data = frame.tobytes()
    if len(data) < 4 or data[:2] != b'\xff\xd8':
        raise ValueError('jpeg_soi_missing')
    header = jpeg_header(data)
    trimmed = 0
    if data[-2:] != b'\xff\xd9':
        stop = len(data)
        while stop > header.scan_start and data[stop - 1] in (0x00, 0xFF):
            stop -= 1
        if stop == len(data) or stop - 2 < header.scan_start or data[stop - 2:stop] != b'\xff\xd9':
            raise ValueError('jpeg_eoi_missing')
        trimmed, data = len(data) - stop, data[:stop]
    return JpegFrame(data, header.width, header.height, trimmed)


def original_jpeg_format(jpeg, rotate_180):
    # Pixels are as the sensor delivered them; rotation is for consumers.
    return (f"jpeg; original_camera_mjpeg; rotation={180 if rotate_180 else 0}; "
            f"width={jpeg.width}; height={jpeg.height}")


def reencoded_jpeg_format(quality, rotate_180, width, height):
    """Decoded-capture fallback in a passthrough session: same orientation contract.

    The frame is encoded unrotated (sensor orientation) and declares the same
    rotation as passthrough frames, so a passthrough<->fallback switch inside
    one recording changes only payload provenance, never orientation.
    """
    return (f"jpeg; reencoded_bgr_q{int(quality)}; rotation={180 if rotate_180 else 0}; "
            f"width={int(width)}; height={int(height)}")


def preview_reduction(setting, source_width, requested_width=None):
    """libjpeg scaled-decode factor for the live preview.

    'auto' picks the largest factor whose decoded width still covers the
    requested preview width, so the configured preview size is never lowered
    (1920 source + 1280 request -> 1 = full decode then resize; 3840 + 1280 ->
    2; 1920 + 960 -> 2). Explicit 1/2/4 are honoured as given.
    """
    if str(setting) == 'auto':
        source, target = int(source_width), max(1, int(requested_width or source_width))
        for factor in SCALED_DECODE_FACTORS:
            if -(-source // factor) >= target:
                return factor
        return 1
    return int(setting)


class StageCounters:
    """Cumulative counts and a rolling window rate per stage, monotonic only.

    Rates are events inside the last min(window, elapsed) seconds; a stopped
    stage decays to zero instead of reporting its last observed interval.
    An optional reason is counted cumulatively per stage (e.g. invalid frames).
    """

    def __init__(self, names=CAMERA_STAGES, window_s=5.0, clock=time.monotonic):
        self.window_s = float(window_s)
        self.clock = clock
        self.lock = threading.Lock()
        self.started = clock()
        self.totals = {name: 0 for name in names}
        self.bytes = {}
        self.reasons = {}
        self.recent = {name: deque() for name in names}

    def add(self, name, nbytes=None, reason=None):
        now = self.clock()
        with self.lock:
            self.totals[name] = self.totals.get(name, 0) + 1
            if nbytes is not None:
                self.bytes[name] = self.bytes.get(name, 0) + int(nbytes)
            if reason is not None:
                reasons = self.reasons.setdefault(name, {})
                reasons[str(reason)] = reasons.get(str(reason), 0) + 1
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
                if name in self.reasons:
                    row['reasons'] = dict(self.reasons[name])
                rows[name] = row
            return dict(policy='cumulative_and_rolling_window_monotonic', window_s=round(max(0., window), 3),
                        stages=rows)
