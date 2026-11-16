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

cal_runtime=True models the runtime-calibration firmware (runs/steer_runtime_cal_20261116/fw/cal_runtime, FW line
token CAL_RUNTIME=1): the CAL marks are variables loaded at boot from nvs (a dict that survives boot(); pass the same
dict to a new FakeEsp32 to model a power cycle), hard guards RIGHT >= 1300, LEFT <= 2500, CENTER 1800..2000, span >= 150,
POT valid 1200..2600, the commands CAL / CAL CENTER / CAL SET RIGHT|LEFT|CENTER n / CAL SAVE / CAL DEFAULT /
CAL FIND LEFT|RIGHT (scan: PWM 100, 100 ms pulse, 650 ms quiet, stop at >= 20 rebound, <= 3 progress, a hard guard,
40 pulses / 4 s powered / 40 s; any input byte aborts with REASON=ABORTED_INPUT), all refused unless every motor is
stopped (motors 2/3 off for 1 s). stop_left / stop_right add a mechanical stop: past it the wheel moves at compliance of
the free rate (at most max_past counts) while powered and springs back to the stop when the driver is off.
Time comes from an injected clock in seconds; call advance() (or run()) to let the firmware loop act.
"""
import math
import re

RIGHT_LIMIT, CENTER, LEFT_LIMIT = 1681, 1900, 2180
HARD_RIGHT_MIN, HARD_LEFT_MAX, CENTER_MIN, CENTER_MAX, MIN_SPAN = 1300, 2500, 1800, 2000, 150
FW_LINE = 'FW: ABS-HOLD-8-REBOUND-LIMITS F=RIGHT POT_DECREASES'
CAL_LINE = 'CAL: RIGHT=1681 CENTER=1900 LEFT=2180 MAX_COMMAND=20 (physical degrees unverified)'
HELP = ['Commands: 1 F 100 / 1 R 100 / 1 S / STOP / POT / POT ON / POT OFF',
        'Angles: LEFT 10 / RIGHT 15 / CENTER; direction: DIR F RIGHT / DIR F LEFT',
        'Absolute: ANGLE -10=LEFT, ANGLE +10=RIGHT, ANGLE 0=CENTER; STATUS']
CAL_HELP = ('Calibration: CAL / CAL CENTER / CAL SET RIGHT|LEFT|CENTER n / CAL SAVE / CAL DEFAULT / '
            'CAL FIND LEFT|RIGHT')
ERROR_LINES = ['ERROR: use 1 F 100 / 1 R 100 / 1 S / STOP / POT / POT ON / POT OFF',
               'Angles: LEFT 10 / RIGHT 15 / CENTER; direction: DIR F RIGHT / DIR F LEFT',
               'Absolute: ANGLE -10=LEFT, ANGLE +10=RIGHT, ANGLE 0=CENTER; STATUS']
FLOAT = r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?'
# calibration scan of the runtime-calibration firmware (calFind)
FIND_PWM, FIND_PULSE_MS, FIND_QUIET_MS, SETTLE_WINDOW_MS, PEAK_TAIL_MS = 100, 100, 650, 150, 50
FIND_MAX_PULSES, FIND_MAX_DRIVE_MS, FIND_MAX_MS, REBOUND_STOP, PROGRESS_STOP, GUARD_APPROACH = 40, 4000, 40000, 20, 3, 15
DRIVE_QUIET_MS = 1000


def lround(x):
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


def pot_to_angle(value, right=RIGHT_LIMIT, center=CENTER, left=LEFT_LIMIT):
    if value <= center:
        return 20.0 * (center - value) / (center - right)
    return -20.0 * (value - center) / (left - center)


def cal_problem(right, center, left):
    """None when the three marks pass the hard guards of the runtime-calibration firmware, else its reason."""
    if right < HARD_RIGHT_MIN:
        return 'RIGHT BELOW HARD GUARD 1300'
    if left > HARD_LEFT_MAX:
        return 'LEFT ABOVE HARD GUARD 2500'
    if center < CENTER_MIN or center > CENTER_MAX:
        return 'CENTER OUTSIDE 1800..2000'
    if center - right < MIN_SPAN:
        return 'RIGHT SPAN BELOW 150'
    if left - center < MIN_SPAN:
        return 'LEFT SPAN BELOW 150'
    return None


class FakeEsp32:
    def __init__(self, clock, *, pot=1966.0, rate_counts_s=400.0, reset_on_open=True, ignore_stops=False,
                 stuck=False, rebound_counts=0, drive_watchdog_ms=None, step_ms=2, cal_runtime=False, nvs=None,
                 stop_left=None, stop_right=None, compliance=0.45, max_past=70.0, spring_tau_ms=25.0):
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
        self.cal_runtime = cal_runtime
        self.nvs = {} if nvs is None else nvs           # NVS namespace "steer_cal" (survives boot())
        self.stop_left, self.stop_right = stop_left, stop_right
        self.compliance, self.max_past, self.spring_tau_ms = compliance, max_past, spring_tau_ms
        self.out = []
        self.inbuf = b''
        self.received = []              # every command line, upper-cased, with the fake time (ms)
        self.boots = 0
        self.ms = int(clock() * 1000)
        self.powered = True
        self.drive_on_ms = 0            # ms with motor 2 or 3 energized (tests: never during calibration)
        self.steer_max_pwm = 0          # largest steering PWM seen
        self._reset_state()
        if cal_runtime:
            self._load_cal()

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
        self.drive_active_ms = None     # last time motor 2 or 3 stopped after running
        self.ready = True
        self.cal_right, self.cal_center, self.cal_left = RIGHT_LIMIT, CENTER, LEFT_LIMIT
        self.cal_store = 'DEFAULT'
        self.find = None                # running CAL FIND scan
        self.center_job = None          # running CAL CENTER measurement

    def _load_cal(self):
        self.cal_right, self.cal_center, self.cal_left, self.cal_store = RIGHT_LIMIT, CENTER, LEFT_LIMIT, 'DEFAULT'
        if not self.nvs:
            return
        values = (self.nvs.get('right', -1), self.nvs.get('center', -1), self.nvs.get('left', -1))
        problem = 'VERSION OR INCOMPLETE SAVE' if self.nvs.get('ver') != 1 else cal_problem(*values)
        if problem:
            self._print('CAL WARNING: STORED VALUES IGNORED (%s) RIGHT=%d CENTER=%d LEFT=%d' % ((problem,) + values))
            return
        self.cal_right, self.cal_center, self.cal_left = values
        self.cal_store = 'NVS'

    def boot(self):
        """Power-on / reset: all outputs off, then the setup banner."""
        self.boots += 1
        self.ms = int(self.clock() * 1000)
        self._reset_state()
        if self.cal_runtime:
            self._load_cal()
            self.target = self.cal_center
        self._status()
        self._print('READY: 1=STEER, 2=LEFT, 3=RIGHT')
        for line in HELP:
            self._print(line)
        if self.cal_runtime:
            self._print(CAL_HELP)

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
        if data and (self.find is not None or self.center_job is not None):
            self._abort_blocking()      # the firmware checks Serial.available() in its scan loops
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
        if any(self.motors[n]['direction'] for n in (1, 2)):
            self.drive_on_ms += dt_ms
        if m['direction']:
            self.steer_max_pwm = max(self.steer_max_pwm, m['pwm'])
        if m['direction'] and not self.stuck:
            # F with steer_right_direction 1 turns right: the value decreases toward the right
            sign = -1 if m['direction'] == self.steer_right_direction else 1
            delta = sign * self.rate * (m['pwm'] / 100.0) * dt_ms / 1000.0
            nxt = self.pot + delta
            if self.stop_left is not None and delta > 0 and nxt > self.stop_left:
                free = max(0.0, self.stop_left - self.pot)
                nxt = min(max(self.pot, self.stop_left) + (delta - free) * self.compliance,
                          self.stop_left + self.max_past)
            if self.stop_right is not None and delta < 0 and nxt < self.stop_right:
                free = max(0.0, self.pot - self.stop_right)
                nxt = max(min(self.pot, self.stop_right) + (delta + free) * self.compliance,
                          self.stop_right - self.max_past)
            self.pot = nxt
            if self.stop_left is None and self.stop_right is None:
                self.pot = max(RIGHT_LIMIT - 60, min(LEFT_LIMIT + 60, self.pot))
        elif not m['direction']:
            k = math.exp(-dt_ms / self.spring_tau_ms)
            if self.stop_left is not None and self.pot > self.stop_left:
                self.pot = self.stop_left + (self.pot - self.stop_left) * k
            if self.stop_right is not None and self.pot < self.stop_right:
                self.pot = self.stop_right - (self.stop_right - self.pot) * k

    def _loop(self):
        if self.find is not None:
            self._find_step()           # calFind blocks the firmware loop
            return
        if self.center_job is not None:
            self._center_step()
            return
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
        if not self.valid(value):
            return -1
        return value

    def valid(self, value):
        if self.cal_runtime:
            return 100 < value < 4000 and HARD_RIGHT_MIN - 100 < value < HARD_LEFT_MAX + 100
        return 100 < value < 4000 and RIGHT_LIMIT - 100 < value < LEFT_LIMIT + 100

    def angle_of(self, value):
        return pot_to_angle(value, self.cal_right, self.cal_center, self.cal_left)

    def confirmed_limit(self, right):
        value = self.read_pot()
        return value <= self.cal_right if right else value >= self.cal_left

    def _deenergize(self, index):
        if index in (1, 2) and self.motors[index]['direction']:
            self.drive_active_ms = self.ms
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

    def fw_line(self):
        line = FW_LINE + ('' if self.drive_watchdog_ms is None else ' DRIVE_WD=%d' % self.drive_watchdog_ms)
        return line + (' CAL_RUNTIME=1' if self.cal_runtime else '')

    def cal_line(self):
        if not self.cal_runtime:
            return CAL_LINE
        return ('CAL: RIGHT=%d CENTER=%d LEFT=%d MAX_COMMAND=20 STORE=%s GUARD=%d..%d (physical degrees unverified)'
                % (self.cal_right, self.cal_center, self.cal_left, self.cal_store, HARD_RIGHT_MIN, HARD_LEFT_MAX))

    def _status(self):
        value = self.read_pot()
        self._print(self.fw_line())
        self._print(self.cal_line())
        if self.valid(value):
            self._print('STATUS: POT=%d COMMAND_EQUIV=%+.2f HOLD=%s TARGET_POT=%d TARGET_COMMAND=%+.2f' % (
                value, self.angle_of(value), 'ON' if self.target_active else 'OFF', self.target,
                self.angle_of(self.target)))
        else:
            self._print('STATUS: INVALID POT=%d' % value)

    def _print_pot(self):
        value = self.read_pot()
        if self.valid(value):
            self._print('POT: %d CENTER_OFFSET: %+d' % (value, value - self.cal_center))
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
                                                                 self.cal_right if right else self.cal_left))
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
            if (self.ms - self.steer_start_ms >= self.pulse_ms or (right and value <= self.cal_right - 15)
                    or (not right and value >= self.cal_left + 15)):
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
        if (right and value <= self.cal_right) or (not right and value >= self.cal_left):
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
        span = self.cal_left - self.cal_center if left else self.cal_right - self.cal_center
        target = self.cal_center + lround(span * degrees / 20.0)
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

    # ---- runtime calibration (cal_runtime=True) --------------------------------------------------------------------
    def _cal_idle(self, what):
        why = None
        if self.motors[1]['direction'] or self.motors[2]['direction']:
            why = 'DRIVE MOTOR 2/3 RUNNING'
        elif self.drive_active_ms is not None and self.ms - self.drive_active_ms < DRIVE_QUIET_MS:
            why = 'DRIVE STOPPED < 1000 MS'
        elif self.target_active or self.phase or self.motors[0]['direction']:
            why = 'STEERING ACTIVE'
        elif self.steer_right_direction == 0:
            why = 'DIRECTION UNCONFIRMED'
        elif not self.valid(self.read_pot()):
            why = 'INVALID POT'
        if why:
            self._print('CAL ERROR: %s REFUSED: %s (STOP first)' % (what, why))
            return False
        return True

    def _cal_apply(self, right, center, left, what):
        problem = cal_problem(right, center, left)
        if problem:
            self._print('CAL ERROR: %s REFUSED: %s (RIGHT=%d CENTER=%d LEFT=%d)' % (what, problem, right, center, left))
            return False
        self.cal_right, self.cal_center, self.cal_left = right, center, left
        self.target = center
        self.cal_store = 'UNSAVED'
        self._print('OK: CAL %s RIGHT=%d CENTER=%d LEFT=%d (CAL SAVE keeps it)' % (what, right, center, left))
        self._print(self.cal_line())
        return True

    def _cal_command(self, words):
        if len(words) == 4 and words[1] == 'SET' and re.fullmatch(r'[+-]?\d+', words[3]):
            if not self._cal_idle('SET'):
                return
            value, side = int(words[3]), words[2]
            if side == 'RIGHT':
                self._cal_apply(value, self.cal_center, self.cal_left, 'SET RIGHT')
            elif side == 'LEFT':
                self._cal_apply(self.cal_right, self.cal_center, value, 'SET LEFT')
            elif side == 'CENTER':
                self._cal_apply(self.cal_right, value, self.cal_left, 'SET CENTER')
            else:
                self._print('CAL ERROR: use CAL SET RIGHT n / CAL SET LEFT n / CAL SET CENTER n')
            return
        if len(words) == 3 and words[1] == 'FIND' and words[2] in ('LEFT', 'RIGHT'):
            self._find_start(words[2] == 'LEFT')
            return
        if len(words) == 1:
            self._print(self.cal_line())
        elif words[1:] == ['CENTER']:
            if self._cal_idle('CENTER'):
                self.center_job = dict(t0=self.ms, samples=[])
        elif words[1:] == ['SAVE']:
            if self._cal_idle('SAVE'):
                self.nvs.pop('ver', None)
                self.nvs.update(right=self.cal_right, center=self.cal_center, left=self.cal_left)
                self.nvs['ver'] = 1
                self.cal_store = 'NVS'
                self._print('OK: CAL SAVED (NVS)')
                self._print(self.cal_line())
        elif words[1:] == ['DEFAULT']:
            if self._cal_idle('DEFAULT'):
                self.nvs.clear()
                self.cal_right, self.cal_center, self.cal_left = RIGHT_LIMIT, CENTER, LEFT_LIMIT
                self.target = CENTER
                self.cal_store = 'DEFAULT'
                self._print('OK: CAL DEFAULT (NVS cleared)')
                self._print(self.cal_line())
        else:
            self._print('CAL ERROR: unknown CAL command; ' + CAL_HELP)

    def _find_start(self, left):
        if not self._cal_idle('FIND LEFT' if left else 'FIND RIGHT'):
            return
        right = not left
        self.find = dict(side='LEFT' if left else 'RIGHT', right=right,
                         direction=self.steer_right_direction if right else -self.steer_right_direction,
                         t0=self.ms, phase='quiet0', phase_t0=self.ms, window=[], before=None, start=None,
                         last_good=None, pulses=0, drive_ms=0, peak=[], guard=False)
        self._print('CAL FIND START: SIDE=%s POT=%d PWM=%d PULSE_MS=%d QUIET_MS=%d MAX_PULSES=%d GUARD=%d' % (
            self.find['side'], self.read_pot(), FIND_PWM, FIND_PULSE_MS, FIND_QUIET_MS, FIND_MAX_PULSES,
            HARD_RIGHT_MIN if right else HARD_LEFT_MAX))

    def _find_end(self, reason):
        f = self.find
        self.find = None
        self.stop_motor(0)
        last = f['last_good'] if f['last_good'] is not None else self.read_pot()
        start = f['start'] if f['start'] is not None else last
        self._print('CAL FOUND %s POT=%d REASON=%s START=%d PULSES=%d DRIVE_MS=%d TIME_MS=%d' % (
            f['side'], last, reason, start, f['pulses'], f['drive_ms'], self.ms - f['t0']))

    def _settled(self, samples):
        if len(samples) < 8 or max(samples) - min(samples) > 18:
            return None
        return sorted(samples)[len(samples) // 2]

    def _find_step(self):
        f = self.find
        value = self.read_pot()
        if not self.valid(value):
            self._find_end('INVALID_POT')
            return
        elapsed = self.ms - f['phase_t0']
        if f['phase'] == 'pulse':
            f['peak'].append(value)
            if (value <= HARD_RIGHT_MIN) if f['right'] else (value >= HARD_LEFT_MAX):
                f['guard'] = True
            if elapsed >= FIND_PULSE_MS or f['guard']:
                f['drive_ms'] += elapsed
                self._deenergize(0)
                f['phase'], f['phase_t0'], f['window'] = 'quiet', self.ms, []
            return
        # quiet phases: peak tail, settle window
        if f['phase'] == 'quiet' and elapsed <= PEAK_TAIL_MS:
            f['peak'].append(value)
        if elapsed + SETTLE_WINDOW_MS >= FIND_QUIET_MS:
            f['window'].append(value)
        if elapsed < FIND_QUIET_MS:
            return
        settled = self._settled(f['window'])
        if settled is None:
            self._find_end('UNSETTLED')
            return
        if f['phase'] == 'quiet0':
            f['before'] = f['start'] = f['last_good'] = settled
        else:
            before = f['before']
            ordered = sorted(f['peak'] + [before])
            peak = ordered[min(1, len(ordered) - 1)] if f['right'] else ordered[-2] if len(ordered) > 1 else ordered[-1]
            progress = before - settled if f['right'] else settled - before
            rebound = settled - peak if f['right'] else peak - settled
            self._print('CAL SAMPLE: SIDE=%s N=%d BEFORE=%d PEAK=%d SETTLED=%d PROGRESS=%d REBOUND=%d DRIVE_MS=%d' % (
                f['side'], f['pulses'], before, peak, settled, progress, rebound, f['drive_ms']))
            if rebound >= REBOUND_STOP:
                self._find_end('REBOUND')
                return
            if progress <= PROGRESS_STOP:
                self._find_end('NO_PROGRESS')
                return
            if f['guard']:
                self._find_end('GUARD')
                return
            f['last_good'] = f['before'] = settled
        # next pulse, or the end
        if f['pulses'] >= FIND_MAX_PULSES or f['drive_ms'] + FIND_PULSE_MS > FIND_MAX_DRIVE_MS:
            self._find_end('BUDGET')
            return
        if self.ms - f['t0'] + FIND_PULSE_MS + FIND_QUIET_MS > FIND_MAX_MS:
            self._find_end('TIME')
            return
        before = f['before']
        if (before <= HARD_RIGHT_MIN + GUARD_APPROACH) if f['right'] else (before >= HARD_LEFT_MAX - GUARD_APPROACH):
            self._find_end('GUARD')
            return
        f['pulses'] += 1
        f['peak'], f['guard'] = [], False
        f['phase'], f['phase_t0'] = 'pulse', self.ms
        self.motors[0] = dict(direction=f['direction'], pwm=FIND_PWM)
        self.steer_start_ms = self.ms

    def _center_step(self):
        job = self.center_job
        value = self.read_pot()
        if not self.valid(value):
            self.center_job = None
            self._print('CAL ERROR: CENTER NOT MEASURED: INVALID_POT')
            return
        elapsed = self.ms - job['t0']
        if elapsed + SETTLE_WINDOW_MS >= FIND_QUIET_MS:
            job['samples'].append(value)
        if elapsed < FIND_QUIET_MS:
            return
        self.center_job = None
        settled = self._settled(job['samples'])
        if settled is None:
            self._print('CAL ERROR: CENTER NOT MEASURED: UNSETTLED')
            return
        self._cal_apply(self.cal_right, settled, self.cal_left, 'CENTER')

    def _abort_blocking(self):
        if self.find is not None:
            self._find_end('ABORTED_INPUT')
        if self.center_job is not None:
            self.center_job = None
            self._print('CAL ERROR: CENTER NOT MEASURED: ABORTED_INPUT')

    def _command(self, text):
        line = text.upper()
        self.received.append((self.ms, line.strip()))
        words = line.split()
        if self.ignore_stops and (line.strip() == 'STOP' or re.fullmatch(r'\s*\d+\s*S\s*', line)):
            return
        if self.cal_runtime and words and words[0] == 'CAL':
            self._cal_command(words)
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
        if self.cal_runtime:
            self._print('Drive watchdog: motors 2/3 stop 300 ms after the last drive command or HB')
            self._print(CAL_HELP)

    # ---- views for the tests ---------------------------------------------------------------------------------------
    def drive(self):
        """Signed firmware PWM of motors 2 and 3 (F positive)."""
        return {n: self.motors[n - 1]['direction'] * self.motors[n - 1]['pwm'] for n in (2, 3)}

    def command_angle(self):
        return self.angle_of(self.pot)

    def cal(self):
        return dict(right=self.cal_right, center=self.cal_center, left=self.cal_left, store=self.cal_store)

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
