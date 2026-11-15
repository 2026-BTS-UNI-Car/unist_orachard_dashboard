"""Fake ESP32-S3 with the 'ABS-HOLD-8-REBOUND-LIMITS' firmware protocol (BTS7960_Serial_Control.ino, 2026-11-16).

A test double, not a simulation of the vehicle. It follows the firmware source for what the host sees:
  * the setup banner (FW, CAL, STATUS, READY and three help lines) after a reset; a reset clears every output
  * processCommand: STATUS, ANGLE n, CENTER, LEFT n / RIGHT n, POT, POT ON/OFF, STOP, 'n S', 'n F/R pwm',
    DIR F RIGHT/LEFT, TEST F, and the three ERROR lines for anything else (the input is upper-cased first)
  * the drive motors 2 and 3 have no timeout (they run until STOP, 'n S', 'n F 0' or a reset)
  * manual steering ('1 F n') stops after 1000 ms (STEER STOP: TIMEOUT); the ANGLE hold with its quiet phases
    (pulse 50/80 ms at PWM 100, 300 ms settle, tolerance 10 / restart 16 counts, 1000 ms correction budget,
    no-progress cancel) and the same absolute-target rule ('OK: SAME ABSOLUTE TARGET')
Simplified: the potentiometer is a number that moves at rate_counts_s while the steering motor is on (no median
filter, no rebound unless rebound_counts is given), the 20 ms direction-change pause is not modelled.
Options for fault tests: reset_on_open, ignore_stops (drops 'STOP' and 'n S' lines), pot_invalid, stuck (the pot does
not move), drive_watchdog_ms (models the proposed firmware drive watchdog: motors 2 and 3 stop when no drive command
or 'HB' line came for that long; the FW line then carries DRIVE_WD=<ms>).
Time comes from an injected clock in seconds; call advance() (or run()) to let the firmware loop act.
"""
import math
import re

RIGHT_LIMIT, CENTER, LEFT_LIMIT = 1681, 1900, 2180
FW_LINE = 'FW: ABS-HOLD-8-REBOUND-LIMITS F=RIGHT POT_DECREASES'
CAL_LINE = 'CAL: RIGHT=1681 CENTER=1900 LEFT=2180 MAX_COMMAND=20 (physical degrees unverified)'
HELP = ['Commands: 1 F 100 / 1 R 100 / 1 S / STOP / POT / POT ON / POT OFF',
        'Angles: LEFT 10 / RIGHT 15 / CENTER; direction: DIR F RIGHT / DIR F LEFT',
        'Absolute: ANGLE -10=LEFT, ANGLE +10=RIGHT, ANGLE 0=CENTER; STATUS']
ERROR_LINES = ['ERROR: use 1 F 100 / 1 R 100 / 1 S / STOP / POT / POT ON / POT OFF',
               'Angles: LEFT 10 / RIGHT 15 / CENTER; direction: DIR F RIGHT / DIR F LEFT',
               'Absolute: ANGLE -10=LEFT, ANGLE +10=RIGHT, ANGLE 0=CENTER; STATUS']
FLOAT = r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?'


def lround(x):
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


def pot_to_angle(value):
    if value <= CENTER:
        return 20.0 * (CENTER - value) / (CENTER - RIGHT_LIMIT)
    return -20.0 * (value - CENTER) / (LEFT_LIMIT - CENTER)


class FakeEsp32:
    def __init__(self, clock, *, pot=1966.0, rate_counts_s=400.0, reset_on_open=True, ignore_stops=False,
                 stuck=False, rebound_counts=0, drive_watchdog_ms=None, step_ms=2):
        self.clock = clock
        self.pot = float(pot)
        self.rate = rate_counts_s
        self.reset_on_open = reset_on_open
        self.ignore_stops = ignore_stops
        self.pot_invalid = False
        self.mute = False               # True: the board answers nothing (a hung USB link)
        self.stuck = stuck
        self.rebound_counts = rebound_counts
        self.drive_watchdog_ms = drive_watchdog_ms
        self.step_ms = step_ms
        self.out = []
        self.inbuf = b''
        self.received = []              # every command line, upper-cased, with the fake time (ms)
        self.boots = 0
        self.ms = int(clock() * 1000)
        self.powered = True
        self._reset_state()

    # ---- firmware state ----------------------------------------------------------------------------------------
    def _reset_state(self):
        self.motors = [dict(direction=0, pwm=0) for _ in range(3)]
        self.steer_right_direction = 1
        self.target_active = False
        self.target = CENTER
        self.phase = 0
        self.pulse_ms = 0
        self.settle_ms = 0
        self.drive_ms = 0
        self.pulse_before = CENTER
        self.pulse_peak = CENTER
        self.pulse_direction = 0
        self.no_progress = 0
        self.pulse_pending = False
        self.steer_start_ms = 0
        self.pot_streaming = False
        self.pot_report_ms = 0
        self.drive_command_ms = 0
        self.ready = True

    def boot(self):
        """Power-on / reset: all outputs off, then the setup banner."""
        self.boots += 1
        self.ms = int(self.clock() * 1000)
        self._reset_state()
        self._status()
        self._print('READY: 1=STEER, 2=LEFT, 3=RIGHT')
        for line in HELP:
            self._print(line)

    def on_open(self):
        if self.reset_on_open:
            self.boot()

    # ---- I/O -----------------------------------------------------------------------------------------------------
    def _print(self, line):
        if not self.mute:
            self.out.append(line)

    def read_output(self):
        data = ''.join(line + '\r\n' for line in self.out).encode('ascii')
        self.out = []
        return data

    def feed(self, data):
        """Bytes from the host. Each complete line is a command at the current fake time."""
        self.advance_to_now()
        self.inbuf += bytes(data)
        while True:
            cut = [i for i in (self.inbuf.find(b'\r'), self.inbuf.find(b'\n')) if i >= 0]
            if not cut:
                break
            i = min(cut)
            line, self.inbuf = self.inbuf[:i], self.inbuf[i + 1:]
            if not line:
                continue
            if len(line) > 63:
                self._print('ERROR: command too long')
                continue
            self._command(line.decode('ascii', 'replace'))

    # ---- time ----------------------------------------------------------------------------------------------------
    def advance_to_now(self):
        target = int(self.clock() * 1000)
        while self.ms < target:
            step = min(self.step_ms, target - self.ms)
            self._physics(step)
            self.ms += step
            self._loop()

    def _physics(self, dt_ms):
        m = self.motors[0]
        if m['direction'] and not self.stuck:
            # F with steer_right_direction 1 turns right: the value decreases toward the right
            sign = -1 if m['direction'] == self.steer_right_direction else 1
            self.pot += sign * self.rate * (m['pwm'] / 100.0) * dt_ms / 1000.0
            self.pot = max(RIGHT_LIMIT - 60, min(LEFT_LIMIT + 60, self.pot))

    def _loop(self):
        self._check_steer_timeout()
        self._check_drive_watchdog()
        if self.pot_streaming and self.ms - self.pot_report_ms >= 200:
            self.pot_report_ms = self.ms
            self._print_pot()

    # ---- firmware functions (names follow the .ino) ----------------------------------------------------------------
    def read_pot(self):
        if self.pot_invalid:
            return -1
        value = int(round(self.pot))
        if not (100 < value < 4000 and RIGHT_LIMIT - 100 < value < LEFT_LIMIT + 100):
            return -1
        return value

    @staticmethod
    def valid(value):
        return 100 < value < 4000 and RIGHT_LIMIT - 100 < value < LEFT_LIMIT + 100

    def confirmed_limit(self, right):
        value = self.read_pot()
        return value <= RIGHT_LIMIT if right else value >= LEFT_LIMIT

    def _deenergize(self, index):
        self.motors[index] = dict(direction=0, pwm=0)

    def stop_motor(self, index):
        self._deenergize(index)
        if index == 0:
            self.target_active = False
            self.phase = 0
            self.pulse_pending = False
            self.drive_ms = 0
            self.no_progress = 0

    def stop_all(self):
        for i in range(3):
            self.stop_motor(i)

    def _status(self):
        value = self.read_pot()
        self._print(FW_LINE + ('' if self.drive_watchdog_ms is None else ' DRIVE_WD=%d' % self.drive_watchdog_ms))
        self._print(CAL_LINE)
        if self.valid(value):
            self._print('STATUS: POT=%d COMMAND_EQUIV=%+.2f HOLD=%s TARGET_POT=%d TARGET_COMMAND=%+.2f' % (
                value, pot_to_angle(value), 'ON' if self.target_active else 'OFF', self.target,
                pot_to_angle(self.target)))
        else:
            self._print('STATUS: INVALID POT=%d' % value)

    def _print_pot(self):
        value = self.read_pot()
        if self.valid(value):
            self._print('POT: %d CENTER_OFFSET: %+d' % (value, value - CENTER))
        else:
            self._print('POT: INVALID (IGNORED)')

    def _steering_allowed(self, direction):
        value = self.read_pot()
        if not self.valid(value):
            self.stop_motor(0)
            self._print('STEER STOP: INVALID POT')
            return False
        right = direction == self.steer_right_direction
        if self.confirmed_limit(right):
            self.stop_motor(0)
            self._print('STEER STOP: %s LIMIT POT=%d LIMIT=%d' % ('RIGHT' if right else 'LEFT', value,
                                                                 RIGHT_LIMIT if right else LEFT_LIMIT))
            return False
        return True

    def _check_steer_timeout(self):
        m = self.motors[0]
        if m['direction'] != 0 and self.ms - self.steer_start_ms >= 1000:
            self.stop_motor(0)
            self._print('STEER STOP: TIMEOUT 1000ms POT=%d TARGET=%d HOLD=OFF' % (self.read_pot(), self.target))
        if self.target_active:
            self._update_hold()
        elif self.motors[0]['direction'] != 0:
            self._steering_allowed(self.motors[0]['direction'])

    def _check_drive_watchdog(self):
        if self.drive_watchdog_ms is None:
            return
        if (self.motors[1]['direction'] or self.motors[2]['direction']) and \
                self.ms - self.drive_command_ms >= self.drive_watchdog_ms:
            self.stop_motor(1)
            self.stop_motor(2)
            self._print('DRIVE STOP: WATCHDOG %dms' % self.drive_watchdog_ms)

    def _drive_motor(self, index, direction, pwm):
        m = self.motors[index]
        if pwm == 0:
            self.stop_motor(index)
            return
        was = m['direction'] != 0
        if was and m['direction'] != direction:
            self.stop_motor(index)
            was = False
        if index == 0 and was and self.ms - self.steer_start_ms >= 1000:
            self.stop_motor(index)
            self._print('STEER STOP: TIMEOUT 1000ms POT=%d TARGET=%d HOLD=OFF' % (self.read_pot(), self.target))
            return
        if index == 0 and not self._steering_allowed(direction):
            return
        self.motors[index] = dict(direction=direction, pwm=pwm)
        if index == 0 and not was:
            self.steer_start_ms = self.ms

    def _finish_pulse(self):
        self.drive_ms += self.ms - self.steer_start_ms
        self._deenergize(0)
        self.phase = 2
        self.settle_ms = self.ms
        self.pulse_pending = True
        if self.rebound_counts:
            right = self.pulse_direction == self.steer_right_direction
            self.pot += self.rebound_counts if right else -self.rebound_counts

    def _update_hold(self):
        value = self.read_pot()
        if not self.valid(value):
            self.stop_motor(0)
            self._print('STEER HOLD CANCELLED: ADC STALE/INVALID')
            return
        right_dir = self.steer_right_direction
        if self.phase == 1:
            right = self.pulse_direction == right_dir
            self.pulse_peak = min(self.pulse_peak, value) if right else max(self.pulse_peak, value)
            if (self.ms - self.steer_start_ms >= self.pulse_ms or (right and value <= RIGHT_LIMIT - 15)
                    or (not right and value >= LEFT_LIMIT + 15)):
                self._finish_pulse()
            return
        if self.phase == 2:
            if self.pulse_pending and self.ms - self.settle_ms < 100:
                right = self.pulse_direction == right_dir
                self.pulse_peak = min(self.pulse_peak, value) if right else max(self.pulse_peak, value)
            if self.ms - self.settle_ms < 300:
                return
            self.phase = 0
        error = self.target - value
        if self.pulse_pending:
            right = self.pulse_direction == right_dir
            progress = self.pulse_before - value if right else value - self.pulse_before
            rebound = value - self.pulse_peak if right else self.pulse_peak - value
            self.pulse_pending = False
            self._print('QUIET: POT=%d TARGET=%d PROGRESS=%d REBOUND=%d DRIVE_MS=%d' % (
                value, self.target, progress, rebound, self.drive_ms))
            if abs(error) > 10:
                if rebound >= 20:
                    self.stop_motor(0)
                    self._print('STEER HOLD CANCELLED: REBOUND (no force retry)')
                    return
                self.no_progress = self.no_progress + 1 if progress < 3 else 0
                if self.no_progress >= 3:
                    self.stop_motor(0)
                    self._print('STEER HOLD CANCELLED: NO QUIET PROGRESS')
                    return
        if abs(error) <= 10:
            self.drive_ms = 0
            self.no_progress = 0
            return
        if abs(error) <= 16 and self.drive_ms == 0:
            return
        if self.drive_ms >= 1000 - 20:
            self.stop_motor(0)
            self._print('STEER HOLD CANCELLED: 1000ms CORRECTION DRIVE BUDGET')
            return
        right = error < 0
        if (right and value <= RIGHT_LIMIT) or (not right and value >= LEFT_LIMIT):
            self.stop_motor(0)
            self._print('STEER HOLD CANCELLED: QUIET OPERATING LIMIT')
            return
        self.pulse_direction = right_dir if right else -right_dir
        self.pulse_before = value
        self.pulse_peak = value
        self.pulse_ms = min(80 if abs(error) > 30 else 50, 1000 - self.drive_ms)
        self.motors[0] = dict(direction=self.pulse_direction, pwm=100)
        self.steer_start_ms = self.ms
        self.phase = 1

    def _move_to_angle(self, left, degrees):
        if not math.isfinite(degrees) or degrees < 0 or degrees > 20:
            self._print('ERROR: angle must be 0..20 degrees')
            return
        value = self.read_pot()
        if not self.valid(value):
            self.stop_motor(0)
            self._print('STEER STOP: INVALID POT')
            return
        span = LEFT_LIMIT - CENTER if left else RIGHT_LIMIT - CENTER
        target = CENTER + lround(span * degrees / 20.0)
        self._print('ABS TARGET: %s COMMAND=%.1f POT=%d CURRENT=%d' % ('LEFT' if left else 'RIGHT', degrees, target,
                                                                       value))
        if self.target_active and self.target == target:
            self._print('OK: SAME ABSOLUTE TARGET (budget unchanged)')
            return
        self.stop_motor(0)
        self.target = target
        self.target_active = True
        self.phase = 2
        self.settle_ms = self.ms
        self._print('QUIET HOLD TARGET: %s %.1f POT=%d PWM=%d' % ('LEFT' if left else 'RIGHT', degrees, target, 100))

    def _command(self, text):
        line = text.upper()
        self.received.append((self.ms, line.strip()))
        words = line.split()
        if self.ignore_stops and (line.strip() == 'STOP' or re.fullmatch(r'\s*\d+\s*S\s*', line)):
            return
        if words == ['STATUS']:
            self._status()
            return
        if words == ['HB'] and self.drive_watchdog_ms is not None:
            self.drive_command_ms = self.ms
            return
        m = re.fullmatch(r'\s*(\S+)\s+(%s)\s*' % FLOAT, line)
        if m and m[1] == 'ANGLE':
            degrees = float(m[2])
            self._move_to_angle(degrees < 0, abs(degrees))
            return
        if words == ['TEST', 'F']:
            self._print('ERROR: TEST requires steering away from both limits')
            return
        if len(words) == 3 and words[:2] == ['DIR', 'F'] and words[2] in ('RIGHT', 'LEFT'):
            self.stop_motor(0)
            self.steer_right_direction = 1 if words[2] == 'RIGHT' else -1
            self._print('OK: F TURNS %s (reset clears setting)' % words[2])
            return
        if words == ['CENTER']:
            self._move_to_angle(True, 0.0)
            return
        if m and m[1] in ('LEFT', 'RIGHT'):
            self._move_to_angle(m[1] == 'LEFT', float(m[2]))
            return
        if words == ['POT']:
            self._print_pot()
            return
        if len(words) == 2 and words[0] == 'POT' and words[1] in ('ON', 'OFF'):
            self.pot_streaming = words[1] == 'ON'
            self.pot_report_ms = self.ms
            self._print('OK: POT ON' if self.pot_streaming else 'OK: POT OFF')
            return
        if words == ['STOP']:
            self.stop_all()
            self._print('OK: ALL STOP')
            return
        m = re.fullmatch(r'\s*([+-]?\d+)\s*(\S)\s*', line)
        if m and 1 <= int(m[1]) <= 3 and m[2] == 'S':
            motor = int(m[1])
            self.stop_motor(motor - 1)
            if motor in (2, 3):
                self.drive_command_ms = self.ms
            self._print('OK: MOTOR %d STOP' % motor)
            return
        m = re.fullmatch(r'\s*([+-]?\d+)\s*(\S)\s*([+-]?\d+)\s*', line)
        if m and 1 <= int(m[1]) <= 3 and m[2] in ('F', 'R') and 0 <= int(m[3]) <= 255:
            motor, direction, pwm = int(m[1]), m[2], int(m[3])
            if motor == 1:
                self.target_active = False
            else:
                self.drive_command_ms = self.ms
            self._drive_motor(motor - 1, 1 if direction == 'F' else -1, pwm)
            if self.motors[motor - 1]['direction'] == 0:
                self._print('OK: MOTOR %d STOP' % motor)
            else:
                self._print('OK: MOTOR %d %s PWM %d' % (motor, direction, pwm))
            return
        for err in ERROR_LINES:
            self._print(err)

    # ---- views for the tests ---------------------------------------------------------------------------------------
    def drive(self):
        """Signed firmware PWM of motors 2 and 3 (F positive)."""
        return {n: self.motors[n - 1]['direction'] * self.motors[n - 1]['pwm'] for n in (2, 3)}

    def command_angle(self):
        return pot_to_angle(self.pot)

    def lines(self, prefix=''):
        return [line for _, line in self.received if line.startswith(prefix)]


class FakeTransport:
    """In-memory transport between Esp32SteerMotor and a FakeEsp32 (same interface as UsbSerialTransport)."""

    def __init__(self, fake, port='/dev/fake-esp32', identity='Fake_ESP32'):
        self.fake = fake
        self.port, self.identity = port, identity
        self.is_open = False
        self.open_error = None
        self.write_error = None
        self.read_error = None
        self.writes = []
        self.opens = 0
        self.closes = 0

    def open(self):
        if self.open_error:
            raise OSError(self.open_error)
        self.is_open = True
        self.opens += 1
        self.fake.on_open()

    def write(self, data):
        if not self.is_open:
            raise OSError('ESP disconnected')
        if self.write_error:
            raise OSError(self.write_error)
        self.writes.append((self.fake.clock(), bytes(data)))
        self.fake.feed(data)

    def read(self):
        if not self.is_open:
            raise OSError('ESP disconnected')
        if self.read_error:
            raise OSError(self.read_error)
        self.fake.advance_to_now()
        return self.fake.read_output()

    def close(self):
        self.is_open = False
        self.closes += 1

    def written(self):
        return b''.join(data for _, data in self.writes).decode('ascii').splitlines()
