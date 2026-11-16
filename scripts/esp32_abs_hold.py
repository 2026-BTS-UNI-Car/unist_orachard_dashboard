"""Host side of the ESP32-S3 firmware 'ABS-HOLD-8-REBOUND-LIMITS' (BTS7960_Serial_Control.ino, 2026-11-16).

The vehicle has one ESP32-S3 (native USB, USB Serial/JTAG 303a:1001) and three BTS7960 drivers:
1 = steering (DC motor, potentiometer on GPIO6, absolute targets 'ANGLE -20..+20' in command degrees, + = RIGHT),
2 = left drive, 3 = right drive ('2 F 100', '3 R 80', '2 S', 'STOP'). Text lines, 115200 baud, newline terminated.

Firmware facts the host has to respect (read from the source, not measured on the vehicle):
  * The drive motors 2 and 3 have NO timeout. '2 F 100' runs until STOP, '2 S', '2 F 0', a new command or a reset.
  * Manual steering ('1 F n') stops after 1000 ms. An ANGLE hold runs until STOP, manual steering or a hold fault;
    one target has a 1000 ms correction-drive budget. A new target first waits a 300 ms quiet settle, so targets that
    change faster than about every 0.4 s never move the wheels (see ANGLE_MIN_INTERVAL_S). This is the pulse hold;
    the SMOOTH=1 firmware (below) holds differently.
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

Runtime steering calibration (2026-11-16, firmware token CAL_RUNTIME=1 on the FW line; runs/steer_runtime_cal_20261116):
the firmware keeps its CAL marks in NVS and answers CAL / CAL CENTER / CAL SET RIGHT|LEFT|CENTER n / CAL SAVE /
CAL DEFAULT / CAL FIND LEFT|RIGHT (a gentle outward scan of the steering motor only, which any serial input aborts).
start_calibration() runs one action as a job: STOP first, then the CAL lines one by one, while STATUS, the zero refresh
and every motor 2/3 line pause (a STATUS line would abort a scan). The job, the samples and the found ends are in
status_extra()['calibration']. 'apply' sets the found ends moved CAL_MARGIN_DEFAULT counts inward, saves them and reloads
the CAL line; then ANGLE +-20 is the calibrated near-maximum position. A firmware without CAL_RUNTIME gets nothing
('calibration_needs_firmware_update'). ANGLE targets are staged by counts (STAGE_MAX_COUNTS) so that one target never
needs more than the firmware's 1000 ms correction budget when the calibration is wider than the defaults.

Smooth hold (2026-11-16 evening, FW token SMOOTH=1; runs/steer_smooth_20261116): the ANGLE hold drives the steering
motor continuously with a PWM ramp and coasts or brakes within STOP_TOL counts. There is no 1000 ms budget per target
and no 300 ms settle before a new target, and a new target in the running direction is taken over without stopping
(its own limits: STALL, RUN TIME LIMIT, NO CONVERGENCE, each 'no force retry'). With SMOOTH=1 the host writes a
target at most every ANGLE_MIN_INTERVAL_SMOOTH_S, rounded to ANGLE_QUANTUM_SMOOTH_DEG (two decimals), and never stages
it (one target may span the whole range): the count staging and the 0.5 s pacing above made the motion stop-and-go, and
an A/D key (the full-limit target) is one continuous run of the firmware. SMOOTH=0 (the firmware's
'SM OFF') or no token keeps the pulse-hold rules exactly. The SM line (mode, parameters, store) is parsed into
steering.smooth; the bridge never sends SM SET / SM ON|OFF / SM SAVE / TRACE (it asks 'SM' once to read the line when
the board did not print it). The right/left hard guards come from the CAL line (GUARD=lo..hi); CAL FIND LEFT|RIGHT
takes an optional pulse length (40..100 ms) on this firmware.
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
# One ANGLE target moves at most this many potentiometer counts [proposal]: the developer's verification moved 280
# counts (0 -> LEFT 20 at the default marks) in one target without a budget cancel; a wider calibration is staged.
STAGE_MAX_COUNTS = 280
# SMOOTH=1 firmware (FW token; 'SM OFF' prints SMOOTH=0 and brings the pulse hold back): no budget, no settle before a
# new target and a same-direction target taken over while running, so targets go faster, finer and unstaged [proposal].
ANGLE_MIN_INTERVAL_SMOOTH_S = 0.2
# 0.25 command deg = about 6-7 pot counts at the 2026-11-16 marks (1475/2029/2513); the smooth hold stops within
# STOP_TOL 6 counts and restarts a reached target only above RESTART_TOL 16. Written with two decimals.
ANGLE_QUANTUM_SMOOTH_DEG = 0.25

# Runtime calibration (firmware CAL_RUNTIME=1). The firmware checks its own hard guards and refuses a value outside them
# (CAL ERROR -> calibration_refused); the host checks the same before 'apply', so it never sends what would be refused.
CAL_DEFAULTS = {'right': 1681, 'center': 1900, 'left': 2180}
# Right/left hard guards: from the CAL line 'GUARD=lo..hi' when present (CAL_RUNTIME firmware 1300..2500, SMOOTH
# firmware 1300..2600); these are the fallback for a CAL line without it.
CAL_HARD_RIGHT_MIN, CAL_HARD_LEFT_MAX = 1300, 2600
# CENTER range (not on the CAL line): 1850..2150 of the SMOOTH firmware r2 (2026-11-16, straight ahead read about 2029
# on the lifted vehicle). The older CAL_RUNTIME firmware and SMOOTH r1 had 1800..2000; they refuse a centre above 2000
# themselves, and the host refuses 1800..1849 that they would accept.
CAL_CENTER_MIN, CAL_CENTER_MAX, CAL_MIN_SPAN = 1850, 2150, 150
CAL_MARGIN_DEFAULT = 30           # counts inward of a found end (battery voltage and load headroom) [proposal]
CAL_MARGIN_RANGE = (10, 120)
CAL_USABLE_REASONS = ('REBOUND', 'NO_PROGRESS')   # the scan stopped at a stop; other endings are not applied
CAL_MIN_TRAVEL = 20               # a found end must lie this far outward of where its scan started
CAL_ACTIONS = ('find_left', 'find_right', 'set_center', 'apply', 'default', 'read')
CAL_FIND_PULSE_RANGE = (40, 100)  # 'CAL FIND LEFT 60' (SMOOTH firmware); without pulse_ms the firmware's 100 ms
CAL_STOP_WAIT_S = 1.0             # the STOP before a job is acknowledged within this
CAL_REPLY_S = 2.5                 # a CAL command answers within this (CAL CENTER measures for 650 ms)
CAL_FIND_SILENCE_S = 3.0          # during a scan the firmware prints a line at least every ~0.75 s (first after ~1.4 s)
CAL_FIND_TOTAL_S = 60.0           # the firmware ends a scan within 40 s (CAL_RUNTIME) or 50 s (SMOOTH: 60 pulses)
CAL_SAMPLES_KEEP = 48

# ---------------------------------------------------------------------------------------------------------------------
# Lines from the firmware

_FLOAT = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
_RE = {
    'fw': re.compile(r'^FW: (\S+)(.*)$'),
    'cal': re.compile(r'^CAL: RIGHT=(-?\d+) CENTER=(-?\d+) LEFT=(-?\d+) MAX_COMMAND=(\d+)'
                      r'(?: STORE=([A-Z]+))?(?: GUARD=(\d+)\.\.(\d+))?'),
    'cal_runtime': re.compile(r'(?:^|\s)CAL_RUNTIME=1(?:\s|$)'),
    'smooth': re.compile(r'(?:^|\s)SMOOTH=([01])(?:\s|$)'),
    'sm': re.compile(r'^SM: MODE=([A-Z]+)((?:\s+[A-Z_]+=\S+)*)\s*$'),
    'sm_pair': re.compile(r'([A-Z_]+)=(\S+)'),
    'data': re.compile(r'^DATA,(\d+),(-?\d+),(-?\d+),(-?\d+),(\d+),(-?\d+),(-?\d+)\s*$'),
    'cal_find_start': re.compile(r'^CAL FIND START: SIDE=(LEFT|RIGHT) POT=(-?\d+)'),
    'cal_sample': re.compile(r'^CAL SAMPLE: SIDE=(LEFT|RIGHT) N=(\d+) BEFORE=(-?\d+) PEAK=(-?\d+) SETTLED=(-?\d+) '
                             r'PROGRESS=(-?\d+) REBOUND=(-?\d+) DRIVE_MS=(\d+)'),
    'cal_found': re.compile(r'^CAL FOUND (LEFT|RIGHT) POT=(-?\d+) REASON=([A-Z_]+) START=(-?\d+) PULSES=(\d+) '
                            r'DRIVE_MS=(\d+) TIME_MS=(\d+)'),
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
    if line.startswith('DATA,'):
        # TRACE output of the SMOOTH firmware (every 10 ms while TRACE ON; the bridge never sends TRACE)
        m = _RE['data'].match(line)
        if m:
            return 'data', dict(ms=int(m[1]), raw=int(m[2]), filtered=int(m[3]), steer_on=int(m[4]),
                                age_ms=int(m[5]), steer_pwm=int(m[6]), target=int(m[7]))
        return 'data', dict(text=line)
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
        smooth = _RE['smooth'].search(m[2])
        return 'fw', dict(name=m[1], detail=m[2].strip(), drive_wd_ms=int(wd[1]) if wd else None,
                          cal_runtime=bool(_RE['cal_runtime'].search(m[2])),
                          smooth=None if smooth is None else smooth[1] == '1')
    m = _RE['drive_watchdog'].match(line)
    if m:
        return 'drive_watchdog', dict(ms=int(m[1]))
    m = _RE['cal'].match(line)
    if m:
        cal = dict(right=int(m[1]), center=int(m[2]), left=int(m[3]), max_command=int(m[4]))
        if m[5]:
            cal['store'] = m[5]
        if m[6]:
            cal['guard_right'], cal['guard_left'] = int(m[6]), int(m[7])
        return 'cal', cal
    if line.startswith('CAL'):
        m = _RE['cal_sample'].match(line)
        if m:
            return 'cal_sample', dict(side=m[1].lower(), n=int(m[2]), before=int(m[3]), peak=int(m[4]),
                                      settled=int(m[5]), progress=int(m[6]), rebound=int(m[7]), drive_ms=int(m[8]))
        m = _RE['cal_found'].match(line)
        if m:
            return 'cal_found', dict(side=m[1].lower(), pot=int(m[2]), reason=m[3], start=int(m[4]),
                                     pulses=int(m[5]), drive_ms=int(m[6]), time_ms=int(m[7]))
        m = _RE['cal_find_start'].match(line)
        if m:
            return 'cal_find_start', dict(side=m[1].lower(), pot=int(m[2]), text=line)
        if line.startswith('CAL ERROR:'):
            return 'cal_error', dict(text=line[len('CAL ERROR:'):].strip())
        if line.startswith('CAL WARNING:'):
            return 'cal_warning', dict(text=line[len('CAL WARNING:'):].strip())
    if line.startswith('OK: CAL '):
        return 'ok_cal', dict(text=line[len('OK: CAL '):].strip())
    if line.startswith('SM'):
        m = _RE['sm'].match(line)
        if m:
            params, store = {}, None
            for name, value in _RE['sm_pair'].findall(m[2]):
                if name == 'STORE':
                    store = value
                elif re.fullmatch(r'-?\d+', value):
                    params[name] = int(value)
            return 'sm', dict(mode=m[1], params=params, store=store)
        if line.startswith('SM ERROR:'):
            return 'sm_error', dict(text=line[len('SM ERROR:'):].strip())
        if line.startswith('SM WARNING:'):
            return 'sm_warning', dict(text=line[len('SM WARNING:'):].strip())
    if line.startswith('OK: SM '):
        return 'ok_sm', dict(text=line[len('OK: SM '):].strip())
    if line.startswith('OK: TRACE'):
        return 'ok_trace', dict(text=line[len('OK: TRACE'):].strip())
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
    if line.startswith(('Commands:', 'Angles:', 'Absolute:', 'Calibration:', 'Drive watchdog:', 'Smooth:')):
        return 'help', dict(text=line)
    if line.startswith(_BOOT_ROM):
        return 'boot_rom', dict(text=line)
    return 'other', dict(text=line)


# ---------------------------------------------------------------------------------------------------------------------
# Commands to the firmware

STOP_LINE = b'STOP\n'
STATUS_LINE = b'STATUS\n'
SM_LINE = b'SM\n'                 # read only: the SMOOTH firmware prints its SM line
SM_QUERY_DELAY_S = 0.5            # after the link is confirmed (the banner's own SM line comes first)
HEARTBEAT_LINE = b'HB\n'          # only for a firmware whose FW line says DRIVE_WD=<ms> (the watchdog proposal)


def angle_line(command_deg, decimals=1):
    """ANGLE line for an absolute target in command degrees (+ = RIGHT, the firmware's sign), clamped to +-20.
    decimals: 1 ('ANGLE -12.0'), or 2 for the SMOOTH firmware's 0.25-degree targets ('ANGLE -12.25'; the firmware
    reads the number with sscanf %f)."""
    if not isinstance(command_deg, (int, float)) or not math.isfinite(command_deg):
        raise ValueError('angle must be a finite number')
    if decimals not in (1, 2):
        raise ValueError('decimals must be 1 or 2')
    value = round(max(-COMMAND_LIMIT_DEG, min(COMMAND_LIMIT_DEG, float(command_deg))), decimals)
    if value == 0:
        value = 0.0                   # never '-0.0'
    return (('ANGLE %+.' + str(decimals) + 'f\n') % value).encode('ascii')


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


def cal_problem(right, center, left, guard_right=CAL_HARD_RIGHT_MIN, guard_left=CAL_HARD_LEFT_MAX):
    """None when the three CAL marks pass the firmware's hard guards (calProblem), else the reason code. guard_right /
    guard_left: the CAL line's GUARD=lo..hi (Esp32SteerMotor.cal_guards())."""
    if not all(type(v) is int for v in (right, center, left)):
        return 'calibration_value_invalid'
    if right < guard_right:
        return 'calibration_right_below_guard'
    if left > guard_left:
        return 'calibration_left_above_guard'
    if not CAL_CENTER_MIN <= center <= CAL_CENTER_MAX:
        return 'calibration_center_outside'
    if center - right < CAL_MIN_SPAN:
        return 'calibration_right_span_small'
    if left - center < CAL_MIN_SPAN:
        return 'calibration_left_span_small'
    return None


def cal_set_line(side, value):
    """'CAL SET LEFT 2290' for one mark (side right, center or left; the value is checked by the caller)."""
    if side not in ('right', 'center', 'left') or type(value) is not int:
        raise ValueError('calibration mark')
    return ('CAL SET %s %d\n' % (side.upper(), value)).encode('ascii')


def cal_find_line(side, pulse_ms=None):
    """'CAL FIND LEFT' (the firmware's 100 ms pulses) or 'CAL FIND LEFT 60' (SMOOTH firmware, 40..100 ms)."""
    if side not in ('left', 'right'):
        raise ValueError('calibration side')
    if pulse_ms is None:
        return ('CAL FIND %s\n' % side.upper()).encode('ascii')
    lo, hi = CAL_FIND_PULSE_RANGE
    if type(pulse_ms) is not int or not lo <= pulse_ms <= hi:
        raise ValueError('calibration pulse')
    return ('CAL FIND %s %d\n' % (side.upper(), pulse_ms)).encode('ascii')


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
        # runtime calibration (FW line CAL_RUNTIME=1)
        self.cal_runtime = False
        self.cal_job = None             # the running job (start_calibration), else None
        self.cal_last = None            # summary of the last finished job
        self.cal_found = {'left': None, 'right': None}
        self.cal_samples = []           # CAL SAMPLE lines of the current / last scan
        self.cal_jobs = 0
        self.cal_ended_at = None        # end of the last job: STATUS restarts, its age counts from here
        # smooth hold (FW line SMOOTH=1 / SMOOTH=0; None: no token, an older firmware with the pulse hold only)
        self.smooth = None
        self.sm = None                  # last SM line {'mode', 'params', 'store', 'at'}
        self.sm_reply = None            # last 'OK: SM' / 'SM ERROR' / 'SM WARNING' (time, kind, text)
        self.sm_asked = False           # 'SM' written once on this link (only when no SM line came with the banner)
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
                           ack_resends=0, zero_refresh=0, unknown_lines=0, errors=0, data_lines=0)
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
        if self._status_stale(now):
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

    def _status_stale(self, now):
        """No fresh STATUS line. During a calibration job STATUS pauses on purpose: then the link counts as alive while
        the firmware keeps printing (a scan prints at least every ~0.75 s); after the job the age counts from its end."""
        if self.cal_job is not None:
            last = self.last_rx if self.last_rx is not None else self.cal_job['started']
            return now - max(last, self.cal_job['started']) > CAL_FIND_SILENCE_S
        if self.status_at is None:
            return True
        ref = self.status_at if self.cal_ended_at is None else max(self.status_at, self.cal_ended_at)
        return now - ref > STATUS_STALE_S

    def arm_blocker(self):
        if self.cal_job is not None:
            return 'calibrating'
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
        if self.cal_job is not None:
            # Any stop ends a calibration job; the STOP line itself aborts a running scan in the firmware.
            self._cal_finish(now, ok=False, error='calibration_stopped')
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
        self.angle_req = quantize_command(mrad_to_command_deg(steer_mrad), self.angle_quantum())
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
            if self.link != 'ready' and self.cal_job is not None:
                self._cal_finish(now, ok=False, error='esp32_not_ready')
            if self.link in ('ready', 'confirming'):
                # (a board with another firmware gets nothing more than the STOP written at open)
                self._service_stop(now)
                if self.cal_job is not None:
                    # A calibration job: only its own CAL lines (no STATUS, no zero refresh, no motor 2/3 line).
                    self._service_cal(now)
                else:
                    self._service_drive(now)
                    if self.link == 'ready':
                        self._service_angle(now)
                        self._service_status(now)
                        self._service_sm_query(now)
            if self.link == 'ready' and self.status_at is not None and self._status_stale(now):
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
        self.smooth = None              # until this link's FW line says otherwise: the pulse-hold rules
        self.sm = self.sm_reply = None
        self.sm_asked = False
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
        if self.cal_job is not None:
            self._cal_finish(self.clock(), ok=False, error='calibration_link_closed')
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
        if self.cal_job is not None and (kind in ('ready', 'boot_rom') or
                                         (kind == 'fw' and self.cal_job['state'] == 'waiting')):
            # No STATUS goes out during a job, and the answer of the last one came before the STOP acknowledgement
            # the job waited for: an FW line or a banner now means the board reset. The job ends; the reset itself
            # is handled below as always.
            self._cal_finish(now, ok=False, error='calibration_interrupted')
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
        if kind == 'data':
            self.counts['data_lines'] += 1      # TRACE output (not from this bridge): counted, otherwise ignored
            return
        if kind == 'fw':
            self.firmware, self.firmware_detail = f['name'], f['detail']
            self.drive_wd_ms = f['drive_wd_ms']
            self.cal_runtime = bool(f.get('cal_runtime'))
            smooth = f.get('smooth')
            if smooth != self.smooth:
                if self.firmware == FIRMWARE_NAME and self.link == 'ready':
                    self._event(now, 'smooth', 'SMOOTH=%s' % ('-' if smooth is None else int(smooth)))
                self.angle_stage = None           # the staging rule follows the hold method
                self.smooth = smooth
            if f['name'] != FIRMWARE_NAME:
                if self.link != 'firmware_mismatch':
                    self._event(now, 'firmware_mismatch', text)
                    self._latch('firmware_mismatch', f['name'])
                self.link = 'firmware_mismatch'
            return
        if kind == 'cal':
            self.cal = f
            self._cal_reply(now, kind, f)
            return
        if kind in ('ok_cal', 'cal_error', 'cal_warning', 'cal_find_start', 'cal_sample', 'cal_found'):
            self._cal_reply(now, kind, f)
            return
        if kind == 'sm':
            self.sm = dict(f, at=now)
            return
        if kind in ('ok_sm', 'sm_error', 'sm_warning', 'ok_trace'):
            self.sm_reply = (now, kind, f['text'])
            self._event(now, kind, f['text'])
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
            if self.cal_job is not None and self.cal_job['state'] == 'waiting':
                # the generic help answer: this firmware does not know the CAL command
                self._cal_finish(now, ok=False, error='calibration_needs_firmware_update', detail=f['text'])
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
        max_jump = self.max_jump_deg()
        staged = not self.smooth                  # SMOOTH=1: one target may span the whole range
        if self.angle_stage is not None:
            # On the way to an intermediate target: wait until it is reached unless the request no longer needs it.
            travelling = bool(status.get('hold')) and measured is not None and \
                abs(measured - self.angle_stage) > STAGE_REACHED_DEG
            same_way = measured is not None and abs(req - measured) > max_jump and \
                (req - measured) * (self.angle_stage - measured) > 0
            if staged and travelling and same_way:
                return
            self.angle_stage = None
        goal = req
        if staged and measured is not None and abs(req - measured) > max_jump:
            # One target moves at most max_jump from the measured angle: a full left-right swing (or one 20-degree
            # target over a wide calibration) would outrun the firmware's 1000 ms correction budget of one target and
            # be cancelled half way.
            goal = quantize_command(measured + math.copysign(max_jump, req - measured), self.angle_quantum())
        if self.angle_sent is not None and goal == self.angle_sent:
            return
        if self.angle_sent_at is not None and now - self.angle_sent_at < self.angle_interval_s():
            return                                # the newest target goes when the interval is over
        if self.angle_sent is None and self.angle_sent_at is None and not status.get('hold') \
                and measured is not None and abs(goal - measured) <= ANGLE_SKIP_DEG:
            # First target after arm/stop is where the wheels already are: no hold needed.
            self.angle_sent = goal
            self.angle_skipped += 1
            return
        self._write(angle_line(goal, 2 if self.smooth else 1))
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

    def _service_sm_query(self, now):
        """'SM' (read only: the firmware prints its SM line) once per link, on a SMOOTH firmware whose banner did not
        bring the line (a board that did not reset on open). Never SM SET / ON / OFF / SAVE / DEFAULT or TRACE."""
        if self.sm is None and self.smooth is not None and not self.sm_asked and \
                now - self.link_since >= SM_QUERY_DELAY_S:
            self.sm_asked = True
            self._write(SM_LINE)

    def angle_interval_s(self):
        """Shortest time between two ANGLE writes: ANGLE_MIN_INTERVAL_SMOOTH_S on the SMOOTH=1 firmware, else the
        configured angle_min_interval_s (the pulse hold's 300 ms settle per target)."""
        return ANGLE_MIN_INTERVAL_SMOOTH_S if self.smooth else self.angle_min_interval_s

    def angle_quantum(self):
        return ANGLE_QUANTUM_SMOOTH_DEG if self.smooth else self.angle_quantum_deg

    def max_jump_deg(self):
        """Largest move of one ANGLE target in command degrees. SMOOTH=1: the whole range (2 x 20, no staging). Else
        angle_max_jump_deg, narrowed only when STAGE_MAX_COUNTS counts are fewer than 20 command degrees (a calibration
        wider than the default marks, whose wider side is exactly 280 counts = 20 degrees, so the default marks keep the
        configured value)."""
        if self.smooth:
            return 2 * COMMAND_LIMIT_DEG
        cal = self.cal or CAL_DEFAULTS
        span = max(cal['center'] - cal['right'], cal['left'] - cal['center'])
        if span <= 0:
            return self.angle_max_jump_deg
        by_counts = COMMAND_LIMIT_DEG * STAGE_MAX_COUNTS / span
        if by_counts >= COMMAND_LIMIT_DEG:
            return self.angle_max_jump_deg
        return max(5.0, min(self.angle_max_jump_deg, by_counts))

    # ---- runtime calibration ------------------------------------------------------------------------------------
    @property
    def cal_supported(self):
        return self.firmware == FIRMWARE_NAME and self.cal_runtime

    def cal_blocker(self, now=None):
        """Why a calibration job cannot start now (the bridge adds armed / moving), or None."""
        now = self.clock() if now is None else now
        if self.cal_job is not None:
            return 'calibration_busy'
        not_ready = self._not_ready(now)
        if not_ready in ('device_unavailable', 'firmware_mismatch', 'esp32_not_ready'):
            return not_ready
        if not self.cal_supported:
            return 'calibration_needs_firmware_update'
        if not_ready:
            return not_ready
        if any(self.desired[m][1] for m in DRIVE_MOTORS) or not self.stopped:
            return 'calibration_needs_standstill'
        return None

    def cal_guards(self):
        """(right_min, left_max): the hard guards of the CAL line (GUARD=lo..hi), else the fallback constants."""
        cal = self.cal or {}
        right, left = cal.get('guard_right'), cal.get('guard_left')
        if type(right) is int and type(left) is int and right < left:
            return right, left
        return CAL_HARD_RIGHT_MIN, CAL_HARD_LEFT_MAX

    def cal_found_usable(self, found):
        if not found or found.get('reason') not in CAL_USABLE_REASONS:
            return False
        travel = found['pot'] - found['start'] if found['side'] == 'left' else found['start'] - found['pot']
        right_min, left_max = self.cal_guards()
        guard_ok = right_min <= found['pot'] <= left_max
        return travel >= CAL_MIN_TRAVEL and guard_ok

    def cal_proposal(self, left_margin=CAL_MARGIN_DEFAULT, right_margin=CAL_MARGIN_DEFAULT):
        """The marks 'apply' would set: each usable found end moved the margin inward; the other marks stay."""
        base = self.cal or CAL_DEFAULTS
        new = {'right': base['right'], 'center': base['center'], 'left': base['left']}
        sides = []
        for side, margin in (('left', left_margin), ('right', right_margin)):
            found = self.cal_found.get(side)
            if self.cal_found_usable(found):
                new[side] = found['pot'] - margin if side == 'left' else found['pot'] + margin
                sides.append(side)
        problem = None if sides else 'calibration_nothing_found'
        return new, sides, problem or cal_problem(new['right'], new['center'], new['left'], *self.cal_guards())

    def start_calibration(self, action, left_margin_counts=CAL_MARGIN_DEFAULT,
                          right_margin_counts=CAL_MARGIN_DEFAULT, pulse_ms=None):
        """Start one calibration action (CAL_ACTIONS); the bridge has checked armed / moving. STOP goes first.
        pulse_ms (find_left / find_right only, int 40..100, SMOOTH firmware): 'CAL FIND LEFT 60'; None: the
        firmware's 100 ms. Returns the answer for the control socket."""
        now = self.clock()
        if action not in CAL_ACTIONS:
            return {'ok': False, 'error': 'invalid_calibration_action'}
        if pulse_ms is not None:
            lo, hi = CAL_FIND_PULSE_RANGE
            if action not in ('find_left', 'find_right') or type(pulse_ms) is not int or not lo <= pulse_ms <= hi:
                return {'ok': False, 'error': 'calibration_pulse_invalid'}
        blocker = self.cal_blocker(now)
        if blocker:
            return {'ok': False, 'error': blocker}
        if pulse_ms is not None and self.smooth is None:
            # The CAL_RUNTIME firmware before SMOOTH knows only 'CAL FIND LEFT' (it would answer CAL ERROR).
            return {'ok': False, 'error': 'calibration_pulse_needs_firmware_update'}
        steps, want = [], None
        if action in ('find_left', 'find_right'):
            side = action.split('_')[1]
            steps = [(cal_find_line(side, pulse_ms), 'found')]
        elif action == 'set_center':
            steps = [(b'CAL CENTER\n', 'ok_cal'), (b'CAL SAVE\n', 'ok_cal')]
        elif action == 'default':
            steps = [(b'CAL DEFAULT\n', 'ok_cal')]
            want = dict(CAL_DEFAULTS)
        elif action == 'read':
            steps = [(b'CAL\n', 'cal')]
        else:   # apply
            lo, hi = CAL_MARGIN_RANGE
            for value in (left_margin_counts, right_margin_counts):
                if type(value) is not int or not lo <= value <= hi:
                    return {'ok': False, 'error': 'calibration_margin_invalid'}
            new, sides, problem = self.cal_proposal(left_margin_counts, right_margin_counts)
            if problem:
                return {'ok': False, 'error': problem, 'proposal': new}
            steps = [(cal_set_line(side, new[side]), 'ok_cal') for side in sides] + [(b'CAL SAVE\n', 'ok_cal')]
            want = new
        self.hard_stop()                 # STOP first: every motor, any hold (and no job is running)
        if self.cal_job is None and self.link == 'ready':
            self.cal_jobs += 1
            self.cal_job = {'action': action, 'id': self.cal_jobs, 'started': now, 'state': 'stopping',
                            'steps': steps, 'index': 0, 'sent_at': None, 'got_ok': False, 'want': want,
                            'margins': {'left': left_margin_counts, 'right': right_margin_counts},
                            'pulse_ms': pulse_ms,
                            'cal_before': dict(self.cal) if self.cal else None, 'detail': None}
            if action in ('find_left', 'find_right'):
                self.cal_samples = []
                self.cal_found[action.split('_')[1]] = None
            self._event(now, 'calibration', 'start %s' % action)
            return {'ok': True, 'state': 'calibration_started', 'action': action, 'job': self.cal_jobs}
        return {'ok': False, 'error': 'esp32_not_ready'}

    def _service_cal(self, now):
        job = self.cal_job
        if job['state'] == 'stopping':
            if self.stop_state == 'confirmed':
                job['state'] = 'next'
            elif now - job['started'] > CAL_STOP_WAIT_S:
                self._cal_finish(now, ok=False, error='calibration_stop_unconfirmed')
                return
            else:
                return
        if job['state'] == 'next':
            if job['index'] >= len(job['steps']):
                self._cal_complete(now)
                return
            line, expect = job['steps'][job['index']]
            self._write(line)
            job.update(state='waiting', sent_at=now, expect=expect, got_ok=False, line=line.decode('ascii').strip())
            return
        if job['state'] == 'waiting':
            if job['expect'] == 'found':
                last = max(self.last_rx or job['sent_at'], job['sent_at'])
                if now - last > CAL_FIND_SILENCE_S or now - job['sent_at'] > CAL_FIND_TOTAL_S:
                    # The scan went silent or overran: STOP (it aborts a scan) and end the job.
                    error = 'calibration_silent' if now - last > CAL_FIND_SILENCE_S else 'calibration_timeout'
                    self._cal_finish(now, ok=False, error=error)
                    self.stop_state = 'pending'
                    self.stop_requested_at = now
                    self.stop_written_at = None
                    self._write_stop(now)
            elif now - job['sent_at'] > CAL_REPLY_S:
                self._cal_finish(now, ok=False, error='calibration_no_answer', detail=job.get('line'))

    def _cal_reply(self, now, kind, f):
        """CAL lines from the firmware: progress of the running job, or a late line (e.g. after an aborted scan)."""
        job = self.cal_job
        if kind == 'cal_warning':
            self._event(now, 'calibration', 'warning: %s' % f['text'])
            return
        if kind == 'cal_sample':
            self.cal_samples.append(dict(f, at=now))
            del self.cal_samples[:-CAL_SAMPLES_KEEP]
            return
        if kind == 'cal_found':
            found = dict(f, at=now)
            found['usable'] = self.cal_found_usable(found)
            self.cal_found[f['side']] = found
            self._event(now, 'calibration', 'found %s pot=%d reason=%s' % (f['side'], f['pot'], f['reason']))
            if job is not None and job['state'] == 'waiting' and job['expect'] == 'found' and \
                    job['action'] == 'find_' + f['side']:
                job['index'] += 1
                job['state'] = 'next'
                job['found'] = found
            return
        if job is None or job['state'] != 'waiting':
            return
        if kind == 'cal_find_start':
            job['scan_started'] = now
            return
        if kind == 'cal_error':
            self._cal_finish(now, ok=False, error='calibration_refused', detail=f['text'])
            return
        if kind == 'ok_cal' and job['expect'] == 'ok_cal':
            job['got_ok'] = True
            return
        if kind == 'cal' and (job['expect'] == 'cal' or (job['expect'] == 'ok_cal' and job['got_ok'])):
            job['index'] += 1
            job['state'] = 'next'

    def _cal_complete(self, now):
        job = self.cal_job
        want, cal = job.get('want'), self.cal or {}
        if want is not None and any(cal.get(k) != want[k] for k in ('right', 'center', 'left')):
            self._cal_finish(now, ok=False, error='calibration_readback_mismatch',
                             detail='want %(right)d/%(center)d/%(left)d' % want)
            return
        if job['action'] in ('apply', 'set_center') and cal.get('store') != 'NVS':
            self._cal_finish(now, ok=False, error='calibration_not_saved')
            return
        found = job.get('found')
        if job['action'] in ('find_left', 'find_right') and not (found and found.get('usable')):
            self._cal_finish(now, ok=False, error='calibration_end_not_found',
                             detail=found.get('reason') if found else None)
            return
        self._cal_finish(now, ok=True)

    def _cal_finish(self, now, ok, error=None, detail=None):
        job, self.cal_job = self.cal_job, None
        if job is None:
            return
        self.cal_ended_at = now
        self.status_sent_at = None       # STATUS again at once
        self.zero_refresh_at = now       # and the zero refresh
        self.cal_last = {'action': job['action'], 'job': job['id'], 'ok': bool(ok), 'error': error, 'detail': detail,
                         'started': job['started'], 'ended': now, 'cal_before': job.get('cal_before'),
                         'cal_after': dict(self.cal) if self.cal else None, 'want': job.get('want'),
                         'margins': job.get('margins'), 'found': job.get('found'),
                         'step': job.get('line')}
        self._event(now, 'calibration', '%s %s%s' % (job['action'], 'ok' if ok else 'failed',
                                                     '' if ok else ': %s' % error))

    def calibration_block(self, now=None):
        now = self.clock() if now is None else now
        job = self.cal_job

        def found_view(found):
            if not found:
                return None
            view = {k: found.get(k) for k in ('side', 'pot', 'reason', 'start', 'pulses', 'drive_ms', 'time_ms',
                                              'usable')}
            view['age_s'] = round(now - found['at'], 1)
            return view
        proposal, sides, problem = self.cal_proposal()
        right_min, left_max = self.cal_guards()
        guard_line = ((self.cal or {}).get('guard_right'), (self.cal or {}).get('guard_left'))
        status = self.status or {}
        last = None
        if self.cal_last:
            last = {k: v for k, v in self.cal_last.items() if k not in ('started', 'ended', 'found')}
            last['age_s'] = round(now - self.cal_last['ended'], 1)
            last['duration_s'] = round(self.cal_last['ended'] - self.cal_last['started'], 1)
        return {
            'supported': self.cal_supported,
            'blocker': self.cal_blocker(now),
            'state': 'running' if job else 'idle',
            'job': None if job is None else {
                'action': job['action'], 'id': job['id'], 'phase': job['state'], 'step': job.get('line'),
                'pulse_ms': job.get('pulse_ms'), 'elapsed_s': round(now - job['started'], 1)},
            'last': last,
            'found': {side: found_view(self.cal_found.get(side)) for side in ('left', 'right')},
            'samples': [{k: s[k] for k in ('side', 'n', 'before', 'peak', 'settled', 'progress', 'rebound',
                                           'drive_ms')} for s in self.cal_samples[-12:]],
            'cal': dict(self.cal) if self.cal else None,
            'pot': None if status.get('invalid') else status.get('pot'),
            'live_pot': self.cal_samples[-1]['settled'] if job and self.cal_samples else None,
            'proposal': {'marks': proposal, 'sides': sides, 'problem': problem,
                         'margin_counts': CAL_MARGIN_DEFAULT},
            'defaults': dict(CAL_DEFAULTS),
            'guards': {'right_min': right_min, 'left_max': left_max, 'center_min': CAL_CENTER_MIN,
                       'center_max': CAL_CENTER_MAX, 'min_span': CAL_MIN_SPAN,
                       'source': 'cal_line' if (right_min, left_max) == guard_line else 'fallback'},
            'margin_range': list(CAL_MARGIN_RANGE),
            'default_margin_counts': CAL_MARGIN_DEFAULT,
            'max_jump_deg': round(self.max_jump_deg(), 2),
            'smooth': self.smooth,
            'find_pulse_ms': {'default': 100, 'range': list(CAL_FIND_PULSE_RANGE),
                              'supported': self.smooth is not None},
            'scale': 'potentiometer counts; ANGLE +-20 = the CAL marks; physical wheel degrees unverified',
        }

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
        if self.link == 'ready' and self._status_stale(now):
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
            'smooth': self.smooth_block(now),
            'scale': 'firmware command degrees (+-20 = CAL marks); physical wheel angle unverified',
        }

    def smooth_block(self, now=None):
        """The hold method: FW token SMOOTH (True / False / None = no token) and the last SM line (mode, parameters,
        store) with the last SM answer. Read only: the bridge never changes the SM parameters."""
        now = self.clock() if now is None else now
        sm = self.sm or {}
        reply = None
        if self.sm_reply is not None:
            reply = {'kind': self.sm_reply[1], 'text': self.sm_reply[2], 'age_s': round(now - self.sm_reply[0], 1)}
        return {
            'firmware': self.smooth,
            'mode': sm.get('mode'),
            'params': dict(sm['params']) if sm.get('params') else None,
            'store': sm.get('store'),
            'age_s': round(now - sm['at'], 1) if 'at' in sm else None,
            'last_reply': reply,
            'data_lines': self.counts['data_lines'],
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
                'command_limit_deg': COMMAND_LIMIT_DEG, 'angle_quantum_deg': self.angle_quantum(),
                'angle_min_interval_ms': int(round(self.angle_interval_s() * 1000)),
                'angle_max_jump_deg': round(self.max_jump_deg(), 2),
                'stage_max_counts': None if self.smooth else STAGE_MAX_COUNTS,
                'smooth': self.smooth,
                'sign': 'steer_mrad left +; firmware ANGLE right +',
            },
            'steering': self.steering_block(now),
            'calibration': self.calibration_block(now),
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
