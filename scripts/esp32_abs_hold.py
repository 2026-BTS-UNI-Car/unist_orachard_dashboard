"""Host side of the ESP32-S3 firmware 'ABS-HOLD-8-REBOUND-LIMITS' (BTS7960_Serial_Control.ino, 2026-11-16).

The vehicle has one ESP32-S3 (native USB, USB Serial/JTAG 303a:1001) and three BTS7960 drivers:
1 = steering (DC motor, potentiometer on GPIO6, absolute targets 'ANGLE -20..+20' in command degrees, + = RIGHT),
2 = left drive, 3 = right drive ('2 F 100', '3 R 80', '2 S', 'STOP'). Text lines, 115200 baud, newline terminated.

Firmware facts the host has to respect (read from the source, not measured on the vehicle):
  * The drive motors 2 and 3 have NO timeout. '2 F 100' runs until STOP, '2 S', '2 F 0', a new command or a reset.
  * Manual steering ('1 F n') stops after 1000 ms. An ANGLE hold runs until STOP, manual steering or a hold fault;
    one target has a 1000 ms correction-drive budget. A new target first waits a 300 ms quiet settle, so targets that
    change faster than about every 0.4 s never move the wheels (see ANGLE_MIN_INTERVAL_S).
  * Opening the USB port reset the board once (2026-11-16, one observation); the setup banner (FW, CAL, STATUS,
    READY and three help lines) follows a reset and a reset clears every output.

This module: the line parser, the command encoder, the unit conversions, the USB transport (by-id path, udev identity,
flock, TIOCEXCL) and Esp32SteerMotor, the motor adapter of SteerControl with the host safety layer:
  * the drive runs only inside its own TTL (drive_steer() sets it, at most RoverControl.INPUT_TTL); when it runs out,
    '2 S' and '3 S' go at once, whatever the control core does;
  * every stop is sent again until the firmware acknowledges it ('OK: MOTOR n STOP' / 'OK: ALL STOP'); without an
    acknowledgement within STOP_CONFIRM_S the stop_unconfirmed latch is set, STOP is written once more and the link
    is closed and opened again later (whether that resets the board is not verified);
  * while no drive is wanted, '2 S' and '3 S' are sent again every ZERO_REFRESH_S (a zero keep-alive);
  * a drive value is written when it changes, never re-asserted, so a board that reset by itself is never restarted
    by the host (a reset is detected from the banner and latches mcu_reset_detected);
  * ANGLE targets are quantised and written at most every ANGLE_MIN_INTERVAL_S (the newest target wins); a target that
    the firmware cancelled is not sent again until the requested target changes (no force retry, as the firmware).
The residual risk: if this process dies (SIGKILL, power loss of the host, a hang) while a drive is running and the
board stays powered over USB, the firmware keeps the last drive PWM. Only a firmware drive watchdog closes that gap.
No ROS, socket or thread code here; the clock is injected.
"""
import math
import os
import re
import stat
import time
from pathlib import Path

ESP_KIND = 'esp32_abs_hold'
FIRMWARE_NAME = 'ABS-HOLD-8-REBOUND-LIMITS'
DEFAULT_BAUD = 115200
COMMAND_LIMIT_DEG = 20.0          # MAX_COMMAND of the firmware (a command scale; physical degrees unverified)
# The equivalent soft limit of the steering API (int mrad, left +): 20 command degrees, rounded down.
LIMIT_MRAD = int(math.floor(COMMAND_LIMIT_DEG * math.pi / 180.0 * 1000.0))   # 349

# Host timing defaults [proposal values, not measured on the vehicle].
STATUS_PERIOD_S = 0.1             # STATUS request period (10 Hz); each answer is three lines (FW, CAL, STATUS)
STATUS_STALE_S = 0.6              # no STATUS line for this long: not ready (stops an armed control)
AGE_LIMIT_MS = 300                # the steering block's age limit for the screen (3 status periods)
BOOT_WAIT_S = 3.0                 # after opening: wait this long for READY before probing with STOP + STATUS
CONFIRM_WAIT_S = 1.5              # then: FW, a STATUS line and the STOP acknowledgement within this time
ACK_TIMEOUT_S = 0.15              # a drive command without its 'OK: MOTOR n ...' line is written again after this
STOP_CONFIRM_S = 0.6              # a stop without acknowledgement for this long: stop_unconfirmed latch, link reopened
ZERO_REFRESH_S = 1.0              # '2 S' / '3 S' again while no drive is wanted
RECONNECT_S = 2.0                 # retry period of a closed link
ANGLE_MIN_INTERVAL_S = 0.5        # at most one new ANGLE target per this time (firmware settle 300 ms + one pulse)
ANGLE_QUANTUM_DEG = 1.0           # ANGLE targets are rounded to this step (firmware tolerance ~0.7-0.9 deg)
ANGLE_SKIP_DEG = 0.5              # first target after arm/stop equal to the measured angle: not sent (no needless hold)
ANGLE_MAX_JUMP_DEG = 20.0         # one target at most this far from the measured angle (1000 ms budget per target)
STAGE_REACHED_DEG = 1.0           # an intermediate target counts as reached within this (firmware tolerance 10 counts)
DRIVE_TTL_S = 0.75                # = RoverControl.INPUT_TTL; never longer
EVENT_KEEP_S = 30.0
WRITE_DEADLINE_S = 0.1
MAX_LINE_BYTES = 8192

# ---------------------------------------------------------------------------------------------------------------------
# Lines from the firmware

_FLOAT = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
_RE = {
    'fw': re.compile(r'^FW: (\S+)(.*)$'),
    'cal': re.compile(r'^CAL: RIGHT=(-?\d+) CENTER=(-?\d+) LEFT=(-?\d+) MAX_COMMAND=(\d+)'),
    'status': re.compile(r'^STATUS: POT=(-?\d+) COMMAND_EQUIV=(%s) HOLD=(ON|OFF) TARGET_POT=(-?\d+) '
                         r'TARGET_COMMAND=(%s)\s*$' % (_FLOAT, _FLOAT)),
    'status_invalid': re.compile(r'^STATUS: INVALID POT=(-?\d+)'),
    'ok_motor': re.compile(r'^OK: MOTOR ([123]) (?:([FR]) PWM (\d+)|STOP)\s*$'),
    'quiet': re.compile(r'^QUIET: POT=(-?\d+) TARGET=(-?\d+) PROGRESS=(-?\d+) REBOUND=(-?\d+) DRIVE_MS=(\d+)'),
    'abs_target': re.compile(r'^ABS TARGET: (LEFT|RIGHT) COMMAND=(%s) POT=(-?\d+) CURRENT=(-?\d+)' % _FLOAT),
    'hold_target': re.compile(r'^QUIET HOLD TARGET: (LEFT|RIGHT) (%s) POT=(-?\d+)' % _FLOAT),
    'pot': re.compile(r'^POT: (-?\d+) CENTER_OFFSET: ([+-]?\d+)'),
    'drive_watchdog': re.compile(r'^DRIVE STOP: WATCHDOG (\d+) ?ms'),
    'drive_wd': re.compile(r'(?:^|\s)DRIVE_WD=(\d+)(?:\s|$)'),
}
_BOOT_ROM = ('ESP-ROM:', 'rst:', 'Build:', 'SPIWP:', 'mode:', 'load:', 'entry ', 'ets ')


def parse_line(text):
    """One firmware line (without the newline) -> (kind, fields dict). Unknown lines -> ('other', {'text': ...})."""
    line = text.strip()
    if not line:
        return 'empty', {}
    m = _RE['status'].match(line)
    if m:
        return 'status', dict(pot=int(m[1]), command_equiv=float(m[2]), hold=m[3] == 'ON', target_pot=int(m[4]),
                              target_command=float(m[5]))
    m = _RE['status_invalid'].match(line)
    if m:
        return 'status_invalid', dict(pot=int(m[1]))
    m = _RE['ok_motor'].match(line)
    if m:
        motor = int(m[1])
        if m[2] is None:
            return 'ok_motor', dict(motor=motor, direction=None, pwm=0)
        return 'ok_motor', dict(motor=motor, direction=m[2], pwm=int(m[3]))
    if line == 'OK: ALL STOP':
        return 'ok_all_stop', {}
    if line.startswith('OK: SAME ABSOLUTE TARGET'):
        return 'ok_same_target', {}
    m = _RE['fw'].match(line)
    if m:
        wd = _RE['drive_wd'].search(m[2])
        return 'fw', dict(name=m[1], detail=m[2].strip(), drive_wd_ms=int(wd[1]) if wd else None)
    m = _RE['drive_watchdog'].match(line)
    if m:
        return 'drive_watchdog', dict(ms=int(m[1]))
    m = _RE['cal'].match(line)
    if m:
        return 'cal', dict(right=int(m[1]), center=int(m[2]), left=int(m[3]), max_command=int(m[4]))
    if line.startswith('READY:'):
        return 'ready', dict(text=line)
    m = _RE['quiet'].match(line)
    if m:
        return 'quiet', dict(pot=int(m[1]), target=int(m[2]), progress=int(m[3]), rebound=int(m[4]),
                             drive_ms=int(m[5]))
    m = _RE['abs_target'].match(line)
    if m:
        return 'abs_target', dict(side=m[1], command=float(m[2]), pot=int(m[3]), current=int(m[4]))
    m = _RE['hold_target'].match(line)
    if m:
        return 'hold_target', dict(side=m[1], command=float(m[2]), pot=int(m[3]))
    if line.startswith('STEER HOLD CANCELLED:'):
        return 'hold_cancelled', dict(text=line[len('STEER HOLD CANCELLED:'):].strip())
    if line.startswith('STEER STOP:'):
        return 'steer_stop', dict(text=line[len('STEER STOP:'):].strip())
    m = _RE['pot'].match(line)
    if m:
        return 'pot', dict(pot=int(m[1]), offset=int(m[2]))
    if line.startswith('POT: INVALID'):
        return 'pot_invalid', {}
    if line.startswith('ERROR:'):
        return 'error', dict(text=line[len('ERROR:'):].strip())
    if line.startswith(('Commands:', 'Angles:', 'Absolute:')):
        return 'help', dict(text=line)
    if line.startswith(_BOOT_ROM):
        return 'boot_rom', dict(text=line)
    return 'other', dict(text=line)


# ---------------------------------------------------------------------------------------------------------------------
# Commands to the firmware

STOP_LINE = b'STOP\n'
STATUS_LINE = b'STATUS\n'
HEARTBEAT_LINE = b'HB\n'          # only for a firmware whose FW line says DRIVE_WD=<ms> (the watchdog proposal)


def angle_line(command_deg):
    """ANGLE line for an absolute target in command degrees (+ = RIGHT, the firmware's sign), clamped to +-20."""
    if not isinstance(command_deg, (int, float)) or not math.isfinite(command_deg):
        raise ValueError('angle must be a finite number')
    value = round(max(-COMMAND_LIMIT_DEG, min(COMMAND_LIMIT_DEG, float(command_deg))), 1)
    if value == 0:
        value = 0.0                   # never '-0.0'
    return ('ANGLE %+.1f\n' % value).encode('ascii')


def drive_line(motor, direction, pwm):
    """'2 F 120' / '3 R 80', or '2 S' for pwm 0. motor 2 or 3 only (never the steering motor 1)."""
    if motor not in (2, 3):
        raise ValueError('drive motor must be 2 or 3')
    if type(pwm) is not int or not 0 <= pwm <= 255:
        raise ValueError('pwm must be an int 0..255')
    if pwm == 0:
        return ('%d S\n' % motor).encode('ascii')
    if direction not in ('F', 'R'):
        raise ValueError('direction must be F or R')
    return ('%d %s %d\n' % (motor, direction, pwm)).encode('ascii')


# ---------------------------------------------------------------------------------------------------------------------
# Units. The steering API (dashboard, executor) uses steer_mrad with LEFT positive; the firmware's ANGLE and
# COMMAND_EQUIV use command degrees with RIGHT positive. Both scales are the firmware's command scale (+-20 = the
# calibration marks RIGHT=1681 / LEFT=2180), not measured wheel angles.

def mrad_to_command_deg(steer_mrad):
    return -float(steer_mrad) * 180.0 / math.pi / 1000.0


def command_deg_to_mrad(command_deg):
    return -float(command_deg) * math.pi / 180.0 * 1000.0


def quantize_command(command_deg, quantum=ANGLE_QUANTUM_DEG):
    value = max(-COMMAND_LIMIT_DEG, min(COMMAND_LIMIT_DEG, float(command_deg)))
    if quantum and quantum > 0:
        value = math.floor(abs(value) / quantum + 0.5) * quantum * (1 if value >= 0 else -1)
    value = round(max(-COMMAND_LIMIT_DEG, min(COMMAND_LIMIT_DEG, value)), 3)
    return 0.0 if value == 0 else value


def throttle_to_pwm(throttle, cap=255):
    """Drive duty of the V3 design ((|p| * 255 + 50) // 100, both sides the same) with a host cap."""
    return min(int(cap), (abs(int(throttle)) * 255 + 50) // 100)


def round_half_away(value):
    return int(math.floor(abs(value) + 0.5)) * (1 if value >= 0 else -1)


# ---------------------------------------------------------------------------------------------------------------------
# Transport

class UsbSerialTransport:
    """The ESP32 USB device: an explicit by-id path whose live udev ID_SERIAL must equal identity (never a scan of
    ttyACM*), a flock per device number, TIOCEXCL, raw 115200 8N1, non-blocking. open() may reset the board."""

    def __init__(self, port, identity, udev_root='/run/udev/data', lock_dir='/run/lock', baud=DEFAULT_BAUD):
        self.port, self.identity = port, identity
        self.udev_root, self.lock_dir = udev_root, lock_dir
        self.baud = baud
        self.fd = None
        self.lock = None
        self.device = None

    @property
    def is_open(self):
        return self.fd is not None

    def open(self):
        import fcntl
        import termios
        if not self.identity or not self.port or self.port == 'auto':
            raise RuntimeError('explicit ESP identity and device path required')
        info = os.stat(self.port)                     # follows the by-id symlink
        if not stat.S_ISCHR(info.st_mode):
            raise RuntimeError('not a character device: %s' % self.port)
        major, minor = os.major(info.st_rdev), os.minor(info.st_rdev)
        props = Path(self.udev_root, 'c%d:%d' % (major, minor)).read_text(encoding='utf-8', errors='replace')
        if 'E:ID_SERIAL=%s' % self.identity not in props.splitlines():
            raise RuntimeError('live udev USB identity mismatch')
        self.device = '%d:%d' % (major, minor)
        self.lock = open(os.path.join(self.lock_dir, 'ubts-esp-%d-%d.lock' % (major, minor)), 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            attrs = termios.tcgetattr(self.fd)
            attrs[0] = attrs[1] = attrs[3] = 0
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            speed = getattr(termios, 'B%d' % self.baud)
            attrs[4] = attrs[5] = speed
            attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        except Exception:
            self.close()
            raise

    def write(self, data):
        import select
        if self.fd is None:
            raise OSError('ESP disconnected')
        deadline = time.monotonic() + WRITE_DEADLINE_S
        view = memoryview(bytes(data))
        while view:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [self.fd], [], remaining)[1]:
                raise OSError('ESP write deadline')
            try:
                size = os.write(self.fd, view)
            except BlockingIOError:
                continue
            if size <= 0:
                raise OSError('ESP write failed')
            view = view[size:]

    def read(self):
        import select
        if self.fd is None:
            raise OSError('ESP disconnected')
        if not select.select([self.fd], [], [], 0)[0]:
            return b''
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return b''
        if not data:
            raise OSError('ESP disconnected')
        return data

    def close(self):
        if self.fd is not None:
            try:
                import fcntl
                import termios
                fcntl.ioctl(self.fd, termios.TIOCNXCL)
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if self.lock is not None:
            self.lock.close()
            self.lock = None


# ---------------------------------------------------------------------------------------------------------------------
# Motor adapter

DRIVE_MOTORS = (2, 3)


class Esp32SteerMotor:
    """SteerControl motor for the ABS-HOLD firmware (interface of scripts/steer_control_core.py: ready, stopped,
    firmware, outputs, error, hard_stop(), enable(), release(), arm_blocker(), clear_latches(), drive_steer()),
    plus poll(), take_stop_reason(), status_extra() and close(). Only the bridge loop thread calls it."""

    def __init__(self, transport, clock=time.monotonic, *, left_forward='F', right_forward='F', max_drive_pwm=255,
                 drive_ttl_s=DRIVE_TTL_S, status_period_s=STATUS_PERIOD_S, angle_min_interval_s=ANGLE_MIN_INTERVAL_S,
                 angle_quantum_deg=ANGLE_QUANTUM_DEG, angle_max_jump_deg=ANGLE_MAX_JUMP_DEG, boot_wait_s=BOOT_WAIT_S,
                 direction_verified=False):
        for name, value in (('left_forward', left_forward), ('right_forward', right_forward)):
            if value not in ('F', 'R'):
                raise ValueError('%s must be F or R' % name)
        if type(max_drive_pwm) is not int or not 0 <= max_drive_pwm <= 255:
            raise ValueError('max_drive_pwm must be an int 0..255')
        if not 0.1 <= float(drive_ttl_s) <= DRIVE_TTL_S:
            raise ValueError('drive_ttl_s must be 0.1..%.2f s' % DRIVE_TTL_S)
        if not 0.05 <= float(status_period_s) <= 0.5:
            raise ValueError('status_period_s must be 0.05..0.5 s')
        if not 0.3 <= float(angle_min_interval_s) <= 3.0:
            raise ValueError('angle_min_interval_s must be 0.3..3.0 s')
        if not 0.1 <= float(angle_quantum_deg) <= 5.0:
            raise ValueError('angle_quantum_deg must be 0.1..5.0')
        if not 5.0 <= float(angle_max_jump_deg) <= 40.0:
            raise ValueError('angle_max_jump_deg must be 5..40')
        self.transport = transport
        self.clock = clock
        self.forward = {2: left_forward, 3: right_forward}
        self.max_drive_pwm = max_drive_pwm
        self.drive_ttl_s = float(drive_ttl_s)
        self.status_period_s = float(status_period_s)
        self.angle_min_interval_s = float(angle_min_interval_s)
        self.angle_quantum_deg = float(angle_quantum_deg)
        self.angle_max_jump_deg = float(angle_max_jump_deg)
        self.boot_wait_s = float(boot_wait_s)
        self.direction_verified = bool(direction_verified)
        self.port = getattr(transport, 'port', None)
        self.identity = getattr(transport, 'identity', None)
        # link
        self.link = 'closed'            # closed, booting, confirming, ready, firmware_mismatch
        self.link_since = clock()
        self.reconnect_at = clock()
        self.opened = 0
        self.buffer = b''
        self.reset_seen = 0
        self.boot_banner = False
        self.ready_once = False
        self.last_rx = None
        self.firmware = None
        self.firmware_detail = None
        self.drive_wd_ms = None         # firmware drive watchdog (FW line DRIVE_WD=<ms>); None = no firmware timeout
        self.heartbeat_at = None
        self.cal = None
        # steering state from the firmware
        self.status = None
        self.status_at = None
        self.status_seq = 0
        self.status_sent_at = None
        self.pot_invalid = False
        self.events = []                # (time, kind, text) recent firmware events
        self.steer_event = None         # hold cancel / steering stop since the last ANGLE written
        # drive
        self.desired = {2: ('F', 0), 3: ('F', 0)}
        self.acked = {2: None, 3: None}
        self.sent = {2: (None, None), 3: (None, None)}
        self.drive_until = 0.0
        self.drive_ttl_stops = 0
        self.zero_refresh_at = 0.0
        self.unacked_since = {2: None, 3: None}
        # stop
        self.stop_state = 'none'        # none, pending, confirmed, unconfirmed
        self.stop_requested_at = None
        self.stop_written_at = None
        self.stop_writes = 0
        # steering target
        self.angle_req = None
        self.angle_sent = None
        self.angle_sent_at = None
        self.angle_writes = 0
        self.angle_skipped = 0
        self.angle_stages = 0
        self.angle_stage = None         # intermediate target on the way to a far request
        self.angle_blocked = None       # a request the firmware cancelled (sent again only after it changes)
        self.angle_requests = 0
        # latches and requests to the control core
        self.latches = {}
        self.stop_reason = None
        self.error = None
        self.counts = dict(lines=0, status=0, writes=0, write_errors=0, read_errors=0, opens=0, open_errors=0,
                           ack_resends=0, zero_refresh=0, unknown_lines=0, errors=0)
        self.enabled = False
        self.last_open_error = None

    # ---- SteerControl motor interface --------------------------------------------------------------------------
    @property
    def ready(self):
        return self._not_ready() is None

    def _not_ready(self, now=None):
        now = self.clock() if now is None else now
        if not getattr(self.transport, 'is_open', False) or self.link == 'closed':
            return 'device_unavailable'
        if self.link == 'firmware_mismatch':
            return 'firmware_mismatch'
        if self.link != 'ready':
            return 'esp32_not_ready'
        if self.status_at is None or now - self.status_at > STATUS_STALE_S:
            return 'steering_status_stale'
        if self.pot_invalid:
            return 'pot_invalid'
        if self.stop_state == 'unconfirmed':
            return 'stop_unconfirmed'
        return None

    @property
    def stopped(self):
        return all(self.desired[m][1] == 0 and self.acked[m] is not None and self.acked[m][1] == 0
                   for m in DRIVE_MOTORS)

    @property
    def outputs(self):
        """Drive duty the firmware acknowledged, in the pin keys the steering screen and the executor read (V2 pin-key
        convention of steer_bridge_core: D8/D5 left forward/reverse, D3/D0 right forward/reverse, D1 drive active,
        D2 steering hold active). Unknown after a reopen until the startup STOP is acknowledged: 0."""
        left, right = self.signed_acked(2), self.signed_acked(3)
        return {'D8': max(left or 0, 0), 'D5': max(-(left or 0), 0), 'D3': max(right or 0, 0),
                'D0': max(-(right or 0), 0), 'D1': 255 if (left or right) else 0,
                'D2': 255 if self.status and self.status.get('hold') else 0}

    def signed_acked(self, motor):
        value = self.acked[motor]
        if value is None:
            return None
        direction, pwm = value
        return pwm if direction == self.forward[motor] else -pwm

    def arm_blocker(self):
        return self._not_ready()

    def clear_latches(self):
        """Operator re-arm (all arm conditions passed): the informative latches go."""
        self.latches.clear()
        self.steer_event = None
        self.error = None

    def enable(self):
        self.enabled = True

    def hard_stop(self, for_arm=False):
        """STOP of all three motors (drive and the steering hold). Written now when the link is open, then confirmed
        or written again by poll()."""
        now = self.clock()
        self.enabled = False if not for_arm else self.enabled
        self.desired = {2: (self.forward[2], 0), 3: (self.forward[3], 0)}
        self.drive_until = 0.0
        self.angle_req = None
        self.angle_sent = self.angle_sent_at = self.angle_stage = self.angle_blocked = None
        self.stop_state = 'pending'
        self.stop_requested_at = now
        self.stop_written_at = None
        self._guarded(now, self._write_stop, now)

    def release(self):
        """Drive to zero, the steering target stays."""
        now = self.clock()
        self.desired = {2: (self.forward[2], 0), 3: (self.forward[3], 0)}
        self.drive_until = 0.0
        self._guarded(now, self._service_drive, now)

    def _guarded(self, now, fn, *args):
        """Writes from a control request: a transport error latches hardware_fault instead of escaping."""
        try:
            fn(*args)
        except OSError as exc:
            self._link_fault(now, exc)

    def drive_steer(self, throttle, steer_mrad):
        now = self.clock()
        pwm = throttle_to_pwm(throttle, self.max_drive_pwm)
        for motor in DRIVE_MOTORS:
            fwd = self.forward[motor]
            direction = fwd if throttle >= 0 else ('R' if fwd == 'F' else 'F')
            self.desired[motor] = (direction, pwm if throttle else 0)
        self.drive_until = now + self.drive_ttl_s if throttle else 0.0
        self.angle_req = quantize_command(mrad_to_command_deg(steer_mrad), self.angle_quantum_deg)
        self.angle_requests += 1
        self._guarded(now, self._service_drive, now)
        self._guarded(now, self._service_angle, now)

    # ---- bridge loop --------------------------------------------------------------------------------------------
    def take_stop_reason(self):
        reason, self.stop_reason = self.stop_reason, None
        return reason

    def _request_stop(self, reason):
        if self.stop_reason is None:
            self.stop_reason = reason

    def _latch(self, name, detail=None):
        self.latches[name] = detail or True
        self.error = name if detail is None else '%s: %s' % (name, detail)
        self._request_stop(name)

    def poll(self, now=None):
        """One loop step: open/reconnect, read lines, the safety layer, scheduled writes. Transport errors never
        escape: they latch hardware_fault, close the link and retry later."""
        now = self.clock() if now is None else now
        if self.link == 'closed':
            if now >= self.reconnect_at:
                self._open(now)
            return
        try:
            self._read(now)
            if self.link == 'closed':
                return
            self._service_link(now)
            if self.link in ('ready', 'confirming'):
                # (a board with another firmware gets nothing more than the STOP written at open)
                self._service_stop(now)
                self._service_drive(now)
                if self.link == 'ready':
                    self._service_angle(now)
                    self._service_status(now)
            if self.link == 'ready' and self.status_at is not None and now - self.status_at > STATUS_STALE_S:
                self._request_stop('steering_status_stale')
        except OSError as exc:
            self._link_fault(now, exc)

    def close(self, reason='shutdown'):
        """Best-effort STOP, then close (bridge shutdown)."""
        try:
            if getattr(self.transport, 'is_open', False):
                self.transport.write(STOP_LINE)
                self.counts['writes'] += 1
        except OSError:
            pass
        finally:
            self._close_transport(reason)

    # ---- internals ----------------------------------------------------------------------------------------------
    def _open(self, now):
        self.counts['opens'] += 1
        try:
            self.transport.open()
        except Exception as exc:          # missing device, identity mismatch, lock held, permission
            self.counts['open_errors'] += 1
            self.last_open_error = '%s: %s' % (type(exc).__name__, exc)
            self.error = 'device_unavailable: %s' % self.last_open_error
            self.reconnect_at = now + RECONNECT_S
            return
        self.opened += 1
        self.last_open_error = None
        self.buffer = b''
        self.link = 'booting'
        self.link_since = now
        self.boot_banner = False
        self.ready_once = False
        self.firmware = None
        self.status = None
        self.status_at = None
        self.pot_invalid = False
        self.acked = {2: None, 3: None}
        self.sent = {2: (None, None), 3: (None, None)}
        self.angle_sent = self.angle_sent_at = self.angle_stage = self.angle_blocked = None
        if self.error and self.error.startswith('device_unavailable'):
            self.error = None
        # A board that did not reset on open may still run a drive from before (no firmware timeout): STOP now.
        self.stop_state = 'pending'
        self.stop_requested_at = now
        try:
            self._write_stop(now)
        except OSError as exc:
            self._link_fault(now, exc)

    def _close_transport(self, why):
        try:
            self.transport.close()
        finally:
            self.link = 'closed'
            self.link_since = self.clock()
            self.enabled = False
            self.acked = {2: None, 3: None}
            self.status_at = None

    def _link_fault(self, now, exc):
        self.counts['write_errors' if 'write' in str(exc) else 'read_errors'] += 1
        self._latch('hardware_fault', '%s' % exc)
        self.desired = {2: (self.forward[2], 0), 3: (self.forward[3], 0)}
        self.drive_until = 0.0
        self.angle_req = None
        try:
            if getattr(self.transport, 'is_open', False):
                self.transport.write(STOP_LINE)       # best effort on a link that just failed
        except OSError:
            pass
        self._close_transport('hardware_fault')
        self.stop_state = 'pending'
        self.stop_requested_at = now
        self.reconnect_at = now + RECONNECT_S

    def _write(self, data):
        self.transport.write(data)
        self.counts['writes'] += 1

    def _write_stop(self, now):
        if not getattr(self.transport, 'is_open', False) or self.link == 'closed':
            return
        self._write(STOP_LINE)
        self.stop_written_at = now
        self.stop_writes += 1
        for motor in DRIVE_MOTORS:
            self.sent[motor] = ((self.forward[motor], 0), now)

    def _read(self, now):
        data = self.transport.read()
        while data:
            self.buffer += data
            if len(self.buffer) > MAX_LINE_BYTES and b'\n' not in self.buffer:
                self.buffer = b''
                self.counts['errors'] += 1
            data = self.transport.read()
        while b'\n' in self.buffer:
            raw, self.buffer = self.buffer.split(b'\n', 1)
            self._line(now, raw.rstrip(b'\r').decode('utf-8', 'replace'))
            if self.link == 'closed':
                return

    def _event(self, now, kind, text):
        self.events.append((now, kind, text))
        while self.events and now - self.events[0][0] > EVENT_KEEP_S:
            self.events.pop(0)

    def _line(self, now, text):
        self.counts['lines'] += 1
        self.last_rx = now
        kind, f = parse_line(text)
        if kind in ('ready', 'boot_rom'):
            if self.ready_once and (kind == 'ready' or self.link == 'ready'):
                # A reset after the link was confirmed: every output went off with it; the host must not continue.
                self.reset_seen += 1
                self._event(now, 'reset', text)
                self._latch('mcu_reset_detected')
                self._reconfirm(now)
            elif kind == 'ready':
                self.boot_banner = True
                if self.link == 'confirming':
                    self._reconfirm(now)       # a late setup banner: ask again after it
            return
        if kind == 'fw':
            self.firmware, self.firmware_detail = f['name'], f['detail']
            self.drive_wd_ms = f['drive_wd_ms']
            if f['name'] != FIRMWARE_NAME:
                if self.link != 'firmware_mismatch':
                    self._event(now, 'firmware_mismatch', text)
                    self._latch('firmware_mismatch', f['name'])
                self.link = 'firmware_mismatch'
            return
        if kind == 'cal':
            self.cal = f
            return
        if kind == 'status':
            self.status = dict(f, at=now)
            self.status_at = now
            self.status_seq += 1
            self.pot_invalid = False
            self.counts['status'] += 1
            return
        if kind == 'status_invalid':
            self.status = dict(pot=f['pot'], invalid=True, hold=False, at=now)
            self.status_at = now
            self.status_seq += 1
            if not self.pot_invalid:
                self._event(now, 'pot_invalid', text)
            self.pot_invalid = True
            self._request_stop('pot_invalid')
            return
        if kind == 'ok_motor':
            motor = f['motor']
            if motor in DRIVE_MOTORS:
                self.acked[motor] = (f['direction'] or self.forward[motor], f['pwm'])
                self._check_stop_confirmed(now)
            return
        if kind == 'drive_watchdog':
            # The firmware watchdog stopped the drive: the host loop was too late. Zero, and stop the control.
            for motor in DRIVE_MOTORS:
                self.acked[motor] = (self.forward[motor], 0)
            self.desired = {2: (self.forward[2], 0), 3: (self.forward[3], 0)}
            self.drive_until = 0.0
            self._event(now, 'drive_watchdog', text)
            self._request_stop('mcu_drive_watchdog')
            return
        if kind == 'ok_all_stop':
            for motor in DRIVE_MOTORS:
                self.acked[motor] = (self.forward[motor], 0)
            self._check_stop_confirmed(now, all_stop=True)
            return
        if kind in ('hold_cancelled', 'steer_stop'):
            self.steer_event = (now, kind, f['text'])
            self._event(now, kind, f['text'])
            if kind == 'hold_cancelled' and self.angle_sent_at is not None:
                self.angle_blocked = self.angle_req
                self.angle_stage = None
            if 'INVALID POT' in f['text'] or 'ADC STALE' in f['text']:
                self.pot_invalid = True
                self._request_stop('pot_invalid')
            return
        if kind == 'error':
            self.counts['errors'] += 1
            self._event(now, 'error', f['text'])
            return
        if kind == 'other':
            self.counts['unknown_lines'] += 1

    def _reconfirm(self, now):
        self.link = 'confirming'
        self.link_since = now
        self.acked = {2: None, 3: None}
        self.angle_sent = self.angle_sent_at = self.angle_stage = self.angle_blocked = None
        self.enabled = False
        self.stop_state = 'pending'
        self.stop_requested_at = now
        self._write_stop(now)
        self._write(STATUS_LINE)
        self.status_sent_at = now

    def _check_stop_confirmed(self, now, all_stop=False):
        if self.stop_state != 'pending':
            return
        if all_stop or all(self.acked[m] is not None and self.acked[m][1] == 0 for m in DRIVE_MOTORS):
            if self.desired[2][1] == 0 and self.desired[3][1] == 0:
                self.stop_state = 'confirmed'

    def _service_link(self, now):
        if self.link == 'booting':
            if self.boot_banner or now - self.link_since >= self.boot_wait_s:
                # Banner seen (the board just reset) or none (it did not): STOP + STATUS, then wait for the answers.
                self.link = 'confirming'
                self.link_since = now
                self.stop_state = 'pending'
                self.stop_requested_at = now
                self._write_stop(now)
                self._write(STATUS_LINE)
                self.status_sent_at = now
            return
        if self.link == 'confirming':
            if (self.firmware == FIRMWARE_NAME and self.status_at is not None and self.status_at >= self.link_since
                    and self.stop_state == 'confirmed'):
                self.link = 'ready'
                self.link_since = now
                self.ready_once = True
                return
            if now - self.link_since >= CONFIRM_WAIT_S:
                # No answer to STOP / STATUS: try again on a fresh open.
                self._event(now, 'confirm_timeout', 'no FW/STATUS/STOP answer')
                self.error = 'esp32_not_ready: no answer after open'
                self._close_transport('confirm_timeout')
                self.reconnect_at = now + RECONNECT_S
                return
            if self.status_sent_at is None or now - self.status_sent_at >= 0.3:
                self._write(STATUS_LINE)
                self.status_sent_at = now

    def _service_stop(self, now):
        if self.stop_state != 'pending':
            return
        if self.stop_written_at is None or now - self.stop_written_at >= ACK_TIMEOUT_S:
            self._write_stop(now)
        if self.stop_requested_at is not None and now - self.stop_requested_at >= STOP_CONFIRM_S:
            # The firmware does not acknowledge the STOP: latch, STOP once more, close and reopen later.
            self.stop_state = 'unconfirmed'
            self._event(now, 'stop_unconfirmed', 'no OK after %.2f s' % (now - self.stop_requested_at))
            self._latch('stop_unconfirmed')
            try:
                self._write(STOP_LINE)
            except OSError:
                pass
            self._close_transport('stop_unconfirmed')
            self.reconnect_at = now + RECONNECT_S

    def _service_drive(self, now):
        if self.link not in ('ready', 'confirming'):
            return
        if self.link != 'ready':
            # Only stops before the link is confirmed (and on a wrong firmware).
            self.desired = {2: (self.forward[2], 0), 3: (self.forward[3], 0)}
        if any(self.desired[m][1] for m in DRIVE_MOTORS) and now >= self.drive_until:
            # The drive TTL ran out without a new drive request: zero now (the firmware would keep running).
            self.desired = {2: (self.forward[2], 0), 3: (self.forward[3], 0)}
            self.drive_ttl_stops += 1
            self._event(now, 'drive_ttl_expired', 'drive TTL %.2f s' % self.drive_ttl_s)
        out = b''
        for motor in DRIVE_MOTORS:
            want = self.desired[motor]
            have = self.acked[motor]
            last, at = self.sent[motor]
            if have is not None and (have == want or (have[1] == 0 and want[1] == 0)):
                self.unacked_since[motor] = None
                continue
            if self.unacked_since[motor] is None:
                self.unacked_since[motor] = now
            if last != want or at is None or now - at >= ACK_TIMEOUT_S:
                if last == want and at is not None:
                    self.counts['ack_resends'] += 1
                out += drive_line(motor, want[0], want[1])
                self.sent[motor] = (want, now)
            if want[1] == 0 and self.stop_state != 'pending' and now - self.unacked_since[motor] >= STOP_CONFIRM_S:
                # a '2 S' / '3 S' that is never acknowledged is handled like an unconfirmed STOP
                self.stop_state = 'pending'
                self.stop_requested_at = now - STOP_CONFIRM_S
        if not out and all(self.desired[m][1] == 0 for m in DRIVE_MOTORS) and self.link == 'ready':
            if now >= self.zero_refresh_at:
                out = drive_line(2, self.forward[2], 0) + drive_line(3, self.forward[3], 0)
                self.counts['zero_refresh'] += 1
        driving = any(self.desired[m][1] for m in DRIVE_MOTORS)
        if driving and self.drive_wd_ms and self.link == 'ready':
            # Patched firmware only: keep its drive watchdog fed while the host still wants the drive (inside the TTL).
            period = min(0.1, self.drive_wd_ms / 3000.0)
            if self.heartbeat_at is None or now - self.heartbeat_at >= period:
                out += HEARTBEAT_LINE
                self.heartbeat_at = now
        if out:
            self._write(out)
            if not driving:
                self.zero_refresh_at = now + ZERO_REFRESH_S

    def _service_angle(self, now):
        if self.link != 'ready' or self.angle_req is None or not self.enabled:
            return
        req = self.angle_req
        if self.angle_blocked is not None:
            if req == self.angle_blocked:
                return                            # the firmware cancelled this target: no force retry
            self.angle_blocked = None
        status = self.status or {}
        measured = None if status.get('invalid') else status.get('command_equiv')
        if self.angle_stage is not None:
            # On the way to an intermediate target: wait until it is reached unless the request no longer needs it.
            travelling = bool(status.get('hold')) and measured is not None and \
                abs(measured - self.angle_stage) > STAGE_REACHED_DEG
            same_way = measured is not None and abs(req - measured) > self.angle_max_jump_deg and \
                (req - measured) * (self.angle_stage - measured) > 0
            if travelling and same_way:
                return
            self.angle_stage = None
        goal = req
        if measured is not None and abs(req - measured) > self.angle_max_jump_deg:
            # One target moves at most angle_max_jump_deg from the measured angle: a full left-right swing would
            # outrun the firmware's 1000 ms correction budget of one target and be cancelled half way.
            goal = quantize_command(measured + math.copysign(self.angle_max_jump_deg, req - measured),
                                    self.angle_quantum_deg)
        if self.angle_sent is not None and goal == self.angle_sent:
            return
        if self.angle_sent_at is not None and now - self.angle_sent_at < self.angle_min_interval_s:
            return                                # the newest target goes when the interval is over
        if self.angle_sent is None and self.angle_sent_at is None and not status.get('hold') \
                and measured is not None and abs(goal - measured) <= ANGLE_SKIP_DEG:
            # First target after arm/stop is where the wheels already are: no hold needed.
            self.angle_sent = goal
            self.angle_skipped += 1
            return
        self._write(angle_line(goal))
        self.angle_sent = goal
        self.angle_sent_at = now
        self.angle_stage = goal if goal != req else None
        if self.angle_stage is not None:
            self.angle_stages += 1
        self.angle_writes += 1
        self.steer_event = None

    def _service_status(self, now):
        if self.status_sent_at is None or now - self.status_sent_at >= self.status_period_s:
            self._write(STATUS_LINE)
            self.status_sent_at = now

    # ---- status for the screen, the executor and the status file ------------------------------------------------
    def steering_block(self, now=None):
        now = self.clock() if now is None else now
        s = self.status or {}
        valid = bool(s) and not s.get('invalid')
        cmd = s.get('command_equiv') if valid else None
        measured = round_half_away(command_deg_to_mrad(cmd)) if cmd is not None else None
        hold = bool(s.get('hold')) if valid else False
        target_cmd = s.get('target_command') if valid and hold else None
        faults = []
        if self.pot_invalid:
            faults.append('pot_invalid')
        if self.link == 'ready' and (self.status_at is None or now - self.status_at > STATUS_STALE_S):
            faults.append('steering_status_stale')
        if self.steer_event is not None:
            faults.append('steer_hold_cancelled' if self.steer_event[1] == 'hold_cancelled' else 'steer_stop')
        for name in self.latches:
            if name not in faults:
                faults.append(name)
        return {
            'measured_mrad': measured,
            'measured_permille': round_half_away(-1000.0 * cmd / COMMAND_LIMIT_DEG) if cmd is not None else None,
            'command_equiv_deg': cmd,
            'raw': s.get('pot'),
            'target_permille': round_half_away(-1000.0 * target_cmd / COMMAND_LIMIT_DEG) if target_cmd is not None
            else None,
            'hold': hold,
            'hold_target_command_deg': target_cmd,
            'target_pot': s.get('target_pot') if valid and hold else None,
            'age_ms': int(round((now - self.status_at) * 1000)) if self.status_at is not None else None,
            'age_limit_ms': AGE_LIMIT_MS,
            'mcu_ms': None,
            'seq': self.status_seq,
            'faults': faults,
            'saturated': False,
            'calibrated': self.cal is not None,
            'servo_state': 'hold' if hold else 'idle',
            'steer_en': hold,
            'drive_en': bool(self.signed_acked(2) or self.signed_acked(3)),
            'pwm': None, 'cmd_permille': None, 'applied_q': None, 'ttl_remaining_ms': None,
            'hold_remaining_ms': None, 'stop_mode': None,
            'cal': dict(self.cal) if self.cal else None,
            'last_event': list(self.steer_event[1:]) if self.steer_event else None,
            'scale': 'firmware command degrees (+-20 = CAL marks); physical wheel angle unverified',
        }

    def status_extra(self, now=None):
        now = self.clock() if now is None else now
        not_ready = self._not_ready(now)
        left, right = self.signed_acked(2), self.signed_acked(3)

        def signed_desired(motor):
            direction, pwm = self.desired[motor]
            return pwm if direction == self.forward[motor] else -pwm
        return {
            'esp_kind': ESP_KIND,
            'transport': 'esp32_usb',
            'serial_port': self.port,
            'serial_open': bool(getattr(self.transport, 'is_open', False)),
            'device_identity': self.identity,
            'firmware_detail': self.firmware_detail,
            'steer_control': {
                'kind': ESP_KIND, 'keys': 'full_limit', 'ramp': False, 'release': 'centre',
                'command_limit_deg': COMMAND_LIMIT_DEG, 'angle_quantum_deg': self.angle_quantum_deg,
                'angle_min_interval_ms': int(round(self.angle_min_interval_s * 1000)),
                'angle_max_jump_deg': self.angle_max_jump_deg,
                'sign': 'steer_mrad left +; firmware ANGLE right +',
            },
            'steering': self.steering_block(now),
            'steering_host': {
                'link': self.link, 'not_ready': not_ready, 'stale': not_ready == 'steering_status_stale',
                'stop_state': self.stop_state, 'arm_state': 'armed' if self.enabled else 'disarmed',
                'latches': sorted(self.latches), 'resets_seen': self.reset_seen, 'opens': self.opened,
                'target_command_deg': self.angle_req, 'sent_command_deg': self.angle_sent,
                'target_mrad': round_half_away(command_deg_to_mrad(self.angle_req)) if self.angle_req is not None
                else None,
                'angle_writes': self.angle_writes, 'angle_skipped': self.angle_skipped,
                'angle_stage_deg': self.angle_stage, 'angle_stages': self.angle_stages,
                'angle_blocked_deg': self.angle_blocked,
                'last_open_error': self.last_open_error, 'counts': dict(self.counts),
                'events': [[round(t - now, 3), k, x] for t, k, x in self.events[-8:]],
            },
            'drive': {
                'left': {'motor': 2, 'forward': self.forward[2], 'desired': signed_desired(2), 'acked': left},
                'right': {'motor': 3, 'forward': self.forward[3], 'desired': signed_desired(3), 'acked': right},
                'ttl_s': self.drive_ttl_s,
                'ttl_remaining_ms': max(0, int(round((self.drive_until - now) * 1000))) if self.drive_until else 0,
                'ttl_stops': self.drive_ttl_stops, 'max_pwm': self.max_drive_pwm,
                'direction_verified': self.direction_verified,
                'firmware_watchdog_ms': self.drive_wd_ms,
            },
        }
