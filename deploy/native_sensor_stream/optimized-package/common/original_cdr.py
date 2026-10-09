"""Private bounded CDR1 sensor parser; original bytes outrank decoded floats.

Only plain CDR1 LE/BE with zero encapsulation options is supported. This is
not installed-rclpy/raw-subscription validation; the isolated ROS harness is
still required. No JSON, ROS runtime, command, recording or map access exists.
"""
from dataclasses import dataclass
import hashlib
import struct

import numpy as np

MAX_CDR_BYTES = 256 * 1024
MAX_BEAMS = 4096
MAX_FRAME_BYTES = 128
MAX_NS = 2**63 - 1
LASER_TYPE = 'sensor_msgs/msg/LaserScan'
IMU_TYPE = 'sensor_msgs/msg/Imu'
CLOCK_TYPE = 'rosgraph_msgs/msg/Clock'


@dataclass(frozen=True)
class Header:
    sec: int
    nanosec: int
    frame_id: str

    @property
    def stamp_ns(self):
        return self.sec * 1_000_000_000 + self.nanosec


@dataclass(frozen=True)
class WireScan:
    header: Header
    metadata_f32_bytes: bytes
    range_f32_bytes: bytes
    intensity_f32_bytes: bytes
    endianness: str

    @property
    def metadata(self):
        return tuple(np.frombuffer(self.metadata_f32_bytes, dtype=self.endianness+'f4'))

    @property
    def ranges(self):
        return tuple(np.frombuffer(self.range_f32_bytes, dtype=self.endianness+'f4'))

    @property
    def intensities(self):
        return tuple(np.frombuffer(self.intensity_f32_bytes, dtype=self.endianness+'f4'))

    @property
    def count(self):
        return len(self.range_f32_bytes) // 4


@dataclass(frozen=True)
class WireImu:
    header: Header
    groups_f64_bytes: tuple
    endianness: str

    @property
    def groups(self):
        # orientation, orientation covariance, angular velocity/covariance,
        # linear acceleration/covariance, all in their original message order.
        return tuple(tuple(np.frombuffer(group, dtype=self.endianness+'f8'))
                     for group in self.groups_f64_bytes)


class Reader:
    def __init__(self, cdr):
        if type(cdr) is not bytes or not 4 <= len(cdr) <= MAX_CDR_BYTES:
            raise ValueError('bounded_immutable_original_CDR_bytes_required')
        if cdr[:4] not in (b'\x00\x00\x00\x00', b'\x00\x01\x00\x00'):
            raise ValueError('unsupported_CDR_encapsulation_or_options')
        self.cdr, self.at = cdr, 4
        self.endian = '<' if cdr[1] == 1 else '>'

    def take(self, count, alignment=1):
        # Alignment origin resets after the four-byte encapsulation.
        padding = (-(self.at-4)) % alignment
        at = self.at + padding
        if type(count) is not int or count < 0 or at+count > len(self.cdr):
            raise ValueError('truncated_CDR')
        self.at = at + count
        return self.cdr[at:self.at]

    def scalar(self, kind):
        size = struct.calcsize(kind)
        return struct.unpack(self.endian+kind, self.take(size, size))[0]

    def stamp(self):
        sec, nanosec = self.scalar('i'), self.scalar('I')
        if sec < 0 or nanosec >= 1_000_000_000 or sec*1_000_000_000+nanosec > MAX_NS:
            raise ValueError('invalid_original_header_stamp')
        return sec, nanosec

    def header(self):
        sec, nanosec = self.stamp()
        length = self.scalar('I')
        if not 2 <= length <= MAX_FRAME_BYTES+1:
            raise ValueError('bounded_original_frame_required')
        raw = self.take(length)
        if raw[-1] != 0 or b'\0' in raw[:-1]:
            raise ValueError('malformed_CDR_frame_string')
        try:
            frame = raw[:-1].decode('utf-8', errors='strict')
        except UnicodeError as error:
            raise ValueError('invalid_UTF8_frame') from error
        if not frame.strip():
            raise ValueError('nonempty_original_frame_required')
        return Header(sec, nanosec, frame)

    def array_f32(self, *, allow_empty=False):
        count = self.scalar('I')
        if not (0 if allow_empty else 24) <= count <= MAX_BEAMS:
            raise ValueError('native_beam_count_out_of_bound')
        # Validate the count before allocating a list or decoding numeric atoms.
        return self.take(count*4, 4)

    def finish(self):
        if self.at != len(self.cdr):
            raise ValueError('unsupported_CDR_trailing_bytes')


def decode(cdr, ros_type):
    if ros_type not in (LASER_TYPE, IMU_TYPE, CLOCK_TYPE):
        raise ValueError('unsupported_original_ROS_type')
    reader = Reader(cdr)
    if ros_type == CLOCK_TYPE:
        sec, nanosec = reader.stamp()
        reader.finish()
        return sec*1_000_000_000+nanosec
    header = reader.header()
    if ros_type == LASER_TYPE:
        metadata = reader.take(7*4, 4)
        values = np.frombuffer(metadata, dtype=reader.endian+'f4')
        if not np.isfinite(values).all():
            raise ValueError('nonfinite_scan_metadata')
        ranges = reader.array_f32()
        intensities = reader.array_f32(allow_empty=True)
        if intensities and len(intensities) != len(ranges):
            raise ValueError('original_intensity_count_mismatch')
        result = WireScan(header, metadata, ranges, intensities, reader.endian)
    else:
        groups = tuple(reader.take(count*8, 8) for count in (4, 9, 3, 9, 3, 9))
        result = WireImu(header, groups, reader.endian)
    reader.finish()
    return result


def original_atom_kinds(raw_f32, endianness):
    """Classify original IEEE bits without quieting a signaling NaN."""
    if type(raw_f32) is not bytes or len(raw_f32) % 4 or endianness not in ('<', '>'):
        raise ValueError('original_float32_atoms_required')
    result = []
    for (bits,) in struct.iter_unpack(endianness+'I', raw_f32):
        exponent, fraction, sign = (bits >> 23) & 255, bits & 0x7fffff, bits >> 31
        if exponent == 255:
            kind = ('qNaN' if fraction & 0x400000 else 'sNaN') if fraction else ('-Inf' if sign else '+Inf')
        elif exponent == 0 and fraction == 0:
            kind = '-0' if sign else '+0'
        else:
            kind = 'finite'
        result.append(kind)
    return tuple(result)


def cdr_sha(cdr):
    if type(cdr) is not bytes or len(cdr) > MAX_CDR_BYTES:
        raise ValueError('bounded_original_CDR_required')
    return hashlib.sha256(cdr).hexdigest()
