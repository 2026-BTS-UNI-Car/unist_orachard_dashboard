"""On-demand original JPEG preview; no capture, archive or actuator operations."""
from dataclasses import dataclass, field
import hashlib
import re
import threading
import time

MAX_ORIGINAL_BYTES = 5 * 1024 * 1024
ORIGINAL_STALE_S = 2.0


def jpeg_header_dimensions(data):
    """Bounded SOF/SOS marker walk; no image decoding or EXIF orientation guess."""
    if data[:2] != b'\xff\xd8':
        raise ValueError('original_camera_JPEG_SOI')
    position, dimensions = 2, None
    for _ in range(128):
        if position + 4 > min(len(data), 65536) or data[position] != 255:
            raise ValueError('original_camera_JPEG_header_bound')
        marker = data[position + 1]
        if marker == 255:
            position += 1; continue
        length = int.from_bytes(data[position + 2:position + 4], 'big')
        end = position + 2 + length
        if length < 2 or end > min(len(data) - 2, 65536):
            raise ValueError('original_camera_JPEG_segment_bound')
        if marker in (0xC0, 0xC1, 0xC2, 0xC9, 0xCA):
            if dimensions is not None or length < 8:
                raise ValueError('original_camera_JPEG_SOF')
            body = position + 4
            components = data[body + 5]
            if not 1 <= components <= 4 or length != 8 + 3 * components:
                raise ValueError('original_camera_JPEG_components')
            dimensions = int.from_bytes(data[body + 3:body + 5], 'big'), int.from_bytes(data[body + 1:body + 3], 'big')
        elif marker == 0xDA:
            if dimensions is None or length < 6:
                raise ValueError('original_camera_JPEG_SOS')
            components = data[position + 4]
            if not 1 <= components <= 4 or length != 6 + 2 * components:
                raise ValueError('original_camera_JPEG_scan_components')
            return dimensions
        elif marker not in (0xC4, 0xCC, 0xDB, 0xDC, 0xDD, 0xFE, *range(0xE0, 0xF0)):
            raise ValueError('original_camera_JPEG_marker')
        position = end
    raise ValueError('original_camera_JPEG_header_segments')


def original_jpeg_metadata(jpeg, compressed_format):
    """Validate producer declarations against JPEG SOF without decoding pixels."""
    if not isinstance(jpeg, bytes) or not 4 <= len(jpeg) <= MAX_ORIGINAL_BYTES:
        raise ValueError('original_camera_JPEG_byte_bound')
    if jpeg[-2:] != b'\xff\xd9' or not isinstance(compressed_format, str) or len(compressed_format) > 512:
        raise ValueError('original_camera_JPEG_container')
    width, height = jpeg_header_dimensions(jpeg)
    tokens = [v.strip() for v in compressed_format.split(';')]
    if len(tokens) != 5 or tokens[0] != 'jpeg' or not re.fullmatch(r'original_camera_mjpeg|reencoded_bgr_q(?:[1-9][0-9]?|100)', tokens[1]):
        raise ValueError('original_camera_JPEG_provenance')
    params = {}
    for token in tokens[2:]:
        key, sep, value = token.partition('=')
        if not sep or key in params or key not in ('width', 'height', 'rotation') or not value.isdecimal():
            raise ValueError('original_camera_JPEG_declarations')
        params[key] = int(value)
    if set(params) != {'width', 'height', 'rotation'} or params['rotation'] not in (0, 180):
        raise ValueError('original_camera_JPEG_rotation')
    if (params['width'], params['height']) != (width, height) or not 1 <= width <= 4096 or not 1 <= height <= 2160:
        raise ValueError('original_camera_JPEG_SOF_mismatch')
    return dict(source_width=width, source_height=height,
                display_rotation_deg=params['rotation'], jpeg_provenance=tokens[1],
                source_jpeg_sha256=hashlib.sha256(jpeg).hexdigest())


@dataclass
class OriginalPreviewState:
    """One latest frame, released on last viewer; callbacks cannot refill idle state."""
    clients: int = 0
    count: int = 0
    frame: dict = field(default_factory=dict)
    jpeg: bytes | None = None
    last_at: float | None = None
    window_at: float | None = None
    window_count: int = 0
    source_fps: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def adjust_clients(self, delta):
        with self.lock:
            self.clients = max(0, self.clients + delta)
            if self.clients == 0:
                self.frame.clear(); self.jpeg = None; self.last_at = None
                self.window_at = None; self.window_count = 0; self.source_fps = None

    def wanted(self):
        with self.lock:
            return self.clients > 0

    def update(self, frame_id, sec, nsec, jpeg, compressed_format):
        metadata = original_jpeg_metadata(jpeg, compressed_format)
        now = time.monotonic()
        with self.lock:
            if self.clients == 0:
                return False
            self.count += 1
            self.jpeg = jpeg
            self.last_at = now
            if self.window_at is None:
                self.window_at = now; self.window_count = 0
            self.window_count += 1
            if now - self.window_at >= 1.0:
                self.source_fps = round(self.window_count / (now - self.window_at), 1)
                self.window_at = now; self.window_count = 0
            self.frame = dict(metadata, frame_id=str(frame_id or '').strip(),
                              stamp_s=int(sec) + int(nsec) / 1e9, frame_count=self.count,
                              original_format=compressed_format)
            return True

    def snapshot(self, include_jpeg=True):
        with self.lock:
            frame = dict(frame_id='camera_link_optical', stamp_s=0.0, source_width=None, source_height=None)
            frame.update(self.frame)
            return dict(frame, jpeg=self.jpeg if include_jpeg else None,
                        last_frame_age_sec=None if self.last_at is None else round(time.monotonic() - self.last_at, 3),
                        source_fps=self.source_fps, frame_count=self.count, error=None)


def original_subscription_policy(client_count, original_count, original_alive):
    """Keep legacy subscriptions for legacy viewers or while original is stale."""
    wanted = original_count > 0
    legacy = not (wanted and original_count == client_count and original_alive)
    return wanted, legacy
