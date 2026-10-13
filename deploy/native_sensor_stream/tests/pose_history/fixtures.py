"""Independent hand-written CDR1 test vectors, NOT installed ROS proof."""
import math
import struct


class Writer:
    def __init__(self, endian='<'):
        self.endian = endian
        self.data = bytearray(b'\0\1\0\0' if endian == '<' else b'\0\0\0\0')

    def raw(self, data, alignment=1):
        self.data.extend(b'\0' * ((-(len(self.data)-4)) % alignment))
        at = len(self.data)
        self.data.extend(data)
        return at

    def scalar(self, kind, value):
        return self.raw(struct.pack(self.endian+kind, value), struct.calcsize(kind))

    def header(self, stamp_ns, frame):
        self.scalar('i', stamp_ns//1_000_000_000)
        self.scalar('I', stamp_ns % 1_000_000_000)
        raw = frame.encode()+b'\0'
        self.scalar('I', len(raw))
        self.raw(raw)

    def finish(self):
        return bytes(self.data)


def clock_cdr(stamp_ns, endian='<'):
    w = Writer(endian)
    w.scalar('i', stamp_ns//1_000_000_000)
    w.scalar('I', stamp_ns % 1_000_000_000)
    return w.finish()


def scan_cdr(stamp_ns=1_000_000_000, *, frame='laser', count=32, endian='<',
             range_bits=None, intensity_bits=None, dt=0., scan_time=.1):
    w = Writer(endian)
    w.header(stamp_ns, frame)
    inc = math.pi/max(1, count-1)
    for value in (-math.pi/2, math.pi/2, inc, dt, scan_time, .05, 30.):
        w.scalar('f', value)
    offsets = {'range_count': w.scalar('I', count)}
    range_bits = range_bits if range_bits is not None else [0x40000000] * count
    offsets['ranges'] = w.raw(b''.join(struct.pack(endian+'I', v) for v in range_bits), 4)
    intensity_bits = intensity_bits if intensity_bits is not None else []
    offsets['intensity_count'] = w.scalar('I', len(intensity_bits))
    offsets['intensities'] = w.raw(b''.join(struct.pack(endian+'I', v) for v in intensity_bits), 4)
    return w.finish(), offsets


def imu_cdr(stamp_ns=1_000_000_000, *, frame='imu', endian='<', yaw=0., roll=0.,
            gyro_z=.4, unavailable=None, covariance_unknown=True, linear=(1., 2., 3.)):
    w = Writer(endian)
    w.header(stamp_ns, frame)
    # Quaternion Rz(yaw)Rx(roll), exact zero pitch fixture.
    q = (math.cos(yaw/2)*math.sin(roll/2), math.sin(yaw/2)*math.sin(roll/2),
         math.sin(yaw/2)*math.cos(roll/2), math.cos(yaw/2)*math.cos(roll/2))
    covariance = (0.,)*9 if covariance_unknown else (.001, 0., 0., 0., .001, 0., 0., 0., .001)
    groups = [q, covariance, (0., 0., gyro_z), covariance, linear, covariance]
    if unavailable is not None:
        groups[{'orientation': 1, 'angular': 3, 'linear': 5}[unavailable]] = (-1.,)+(0.,)*8
    for group in groups:
        w.raw(struct.pack(endian+str(len(group))+'d', *group), 8)
    return w.finish()


def room_scan(stamp, pose=(0., 0., 0.), *, count=3200, dt=0., special=False):
    """Ray distances to four analytic fixture walls, independent of fit code."""
    w = Writer(); w.header(stamp, 'laser')
    amin, inc = -math.pi, 2*math.pi/count
    for v in (amin, amin+(count-1)*inc, inc, dt, .1, .05, 30.):
        w.scalar('f', v)
    x, y, yaw = pose
    ranges = []
    for i in range(count):
        dx, dy = math.cos(amin+i*inc+yaw), math.sin(amin+i*inc+yaw)
        candidates = []
        for bx in (-4., 6.):
            if abs(dx) > 1e-12:
                t = (bx-x)/dx
                if t > 0 and -3. <= y+t*dy <= 5.: candidates.append(t)
        for by in (-3., 5.):
            if abs(dy) > 1e-12:
                t = (by-y)/dy
                if t > 0 and -4. <= x+t*dx <= 6.: candidates.append(t)
        ranges.append(min(candidates))
    raw = bytearray(struct.pack('<'+str(count)+'f', *ranges))
    if special:
        raw[:24] = struct.pack('<6I', 0x7fc01234, 0x7f801234, 0x7f800000,
            0xff800000, 0, 0x80000000)
    w.scalar('I', count); w.raw(bytes(raw), 4); w.scalar('I', 0)
    return w.finish()
