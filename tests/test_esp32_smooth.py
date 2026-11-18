"""The ESP32-S3 bridge with the SMOOTH firmware (FW token SMOOTH=1, runs/steer_smooth_20261116, r2: BRAKE_MS, centre
guard 1850..2150) against the fake board (tests/fake_esp32.py, smooth=True). No hardware, no socket, no ROS; injected
clock. Checks: the new lines, the pacing (one target per 0.1 s at most, 0.25-degree steps, no staging), a held A/D key
as one target and one continuous run, the pulse-hold rules again after 'SM OFF', the one 'SM' read, TRACE lines counted,
a stall cancel not forced again, and the calibration with the 2026-11-16 values (centre 2029, left end above 2500,
CAL FIND with 60 ms pulses).
Run: python -B -m unittest tests.test_esp32_smooth
The pot rate, stops and the absence of coasting are placeholders of the fake board, not measurements of the vehicle.
"""
import json
import unittest

from scripts import esp32_abs_hold as esp
from tests.test_esp32_bridge import Rig

# 2026-11-16 lifted-wheel field values (not driving results): straight ahead about pot 2029, ends found near 1445 (right)
# and 2543..2551 (left); the marks set that evening 1475 / 2029 / 2513.
MARKS_NVS = {'right': 1475, 'center': 2029, 'left': 2513, 'ver': 1}
SMOOTH_FAKE = dict(smooth=True, drive_watchdog_ms=300, pot=2029.0, rate_counts_s=390.0, stop_left=2551.0,
                   stop_right=1445.0)
SM_LINE = ('SM: MODE=SMOOTH PWM_MIN=60 PWM_MAX=100 SLOW=120 STOP_TOL=6 RESTART_TOL=16 RAMP=12 SETTLE_MS=120 '
           'STALL_MS=200 BOOST=15 MAX_RUN_MS=5000 BRAKE_MS=40 STORE=DEFAULT')


class SmoothRig(Rig):
    def __init__(self, fake_options=None, nvs=None, **motor_options):
        options = dict(SMOOTH_FAKE, nvs=dict(MARKS_NVS) if nvs is None else nvs)
        options.update(fake_options or {})
        super().__init__(options, **motor_options)

    def hold(self, steer_mrad, seconds=None, until=None, limit=10.0, throttle=0):
        """The page while a key or the drag is held: one drive request every 0.1 s."""
        waited = 0.0
        while (seconds is not None and waited < seconds - 1e-9) or (until is not None and not until()):
            if waited >= limit:
                raise AssertionError('condition not reached in %.1f s' % limit)
            reply = self.drive(throttle, steer_mrad)
            if not reply.get('ok'):
                raise AssertionError(reply)
            self.run(0.08)
            waited += 0.1
        return waited

    def runs_during(self, steer_mrad, target_pot, limit=6.0):
        """Hold one request until the pot is within 10 counts of target_pot and the hold is quiet; returns the number
        of smooth runs the firmware started meanwhile and whether its steering driver ever went off on the way."""
        runs = self.fake.smooth_runs_started
        gaps, started = 0, False
        waited = 0.0
        while not (abs(self.fake.pot - target_pot) <= 10 and self.fake.sm_phase == 0):
            if waited >= limit:
                raise AssertionError('not reached: pot %.0f target %d' % (self.fake.pot, target_pot))
            self.drive(0, steer_mrad)
            for _ in range(5):
                self.clock.t += 0.02
                self.bridge.step()
                on = self.fake.motors[0]['direction'] != 0
                if on:
                    started = True
                elif started and abs(self.fake.pot - target_pot) > 10:
                    gaps += 1
            waited += 0.1
        return self.fake.smooth_runs_started - runs, gaps


class ParserTest(unittest.TestCase):
    def test_smooth_lines(self):
        kind, f = esp.parse_line('FW: ABS-HOLD-8-REBOUND-LIMITS F=RIGHT POT_DECREASES DRIVE_WD=300 CAL_RUNTIME=1 '
                                 'SMOOTH=1')
        self.assertEqual((kind, f['name'], f['cal_runtime'], f['smooth'], f['drive_wd_ms']),
                         ('fw', esp.FIRMWARE_NAME, True, True, 300))
        self.assertIs(esp.parse_line('FW: ABS-HOLD-8-REBOUND-LIMITS F=RIGHT CAL_RUNTIME=1 SMOOTH=0')[1]['smooth'], False)
        self.assertIsNone(esp.parse_line('FW: ABS-HOLD-8-REBOUND-LIMITS F=RIGHT CAL_RUNTIME=1')[1]['smooth'])
        kind, f = esp.parse_line(SM_LINE)
        self.assertEqual((kind, f['mode'], f['store']), ('sm', 'SMOOTH', 'DEFAULT'))
        self.assertEqual(f['params'], {'PWM_MIN': 60, 'PWM_MAX': 100, 'SLOW': 120, 'STOP_TOL': 6, 'RESTART_TOL': 16,
                                       'RAMP': 12, 'SETTLE_MS': 120, 'STALL_MS': 200, 'BOOST': 15, 'MAX_RUN_MS': 5000,
                                       'BRAKE_MS': 40})
        self.assertEqual(esp.parse_line('SM: MODE=PULSE PWM_MIN=60 STORE=UNSAVED')[1]['mode'], 'PULSE')
        self.assertEqual(esp.parse_line('OK: SM SET SLOW=150 (SM SAVE keeps it)'),
                         ('ok_sm', {'text': 'SET SLOW=150 (SM SAVE keeps it)'}))
        self.assertEqual(esp.parse_line('SM ERROR: unknown parameter X')[0], 'sm_error')
        self.assertEqual(esp.parse_line('SM WARNING: STORED VALUES IGNORED (VERSION)')[0], 'sm_warning')
        self.assertEqual(esp.parse_line('DATA,756,1899,1911,0,455,0,1900'),
                         ('data', {'ms': 756, 'raw': 1899, 'filtered': 1911, 'steer_on': 0, 'age_ms': 455,
                                   'steer_pwm': 0, 'target': 1900, 'quiet': None}))
        self.assertEqual(esp.parse_line('DATA,broken')[0], 'data')
        self.assertEqual(esp.parse_line('OK: TRACE ON 25000 MS (DATA,ms,raw)')[0], 'ok_trace')
        self.assertEqual(esp.parse_line('Smooth: SM / SM ON / SM OFF')[0], 'help')
        self.assertEqual(esp.parse_line('STEER HOLD CANCELLED: STALL (no force retry)'),
                         ('hold_cancelled', {'text': 'STALL (no force retry)'}))
        self.assertEqual(esp.parse_line('QUIET HOLD TARGET: LEFT 20.0 POT=2513 PWM=100')[0], 'hold_target')

    def test_commands(self):
        self.assertEqual(esp.angle_line(-12.25, 2), b'ANGLE -12.25\n')
        self.assertEqual(esp.angle_line(0.0, 2), b'ANGLE +0.00\n')
        self.assertEqual(esp.angle_line(-12.25), b'ANGLE -12.2\n', 'the pulse-hold format stays one decimal')
        self.assertEqual(esp.cal_find_line('left'), b'CAL FIND LEFT\n')
        self.assertEqual(esp.cal_find_line('right', 60), b'CAL FIND RIGHT 60\n')
        for bad in (39, 101, 60.0, True):
            with self.assertRaises(ValueError):
                esp.cal_find_line('left', bad)
        self.assertEqual(esp.quantize_command(-12.3, esp.ANGLE_QUANTUM_SMOOTH_DEG), -12.25)


class SmoothStatus(unittest.TestCase):
    def test_status_shows_the_smooth_rules(self):
        rig = SmoothRig()
        rig.ready()
        snap = rig.bridge.snapshot
        self.assertIs(rig.motor.smooth, True)
        sc = snap['steer_control']
        self.assertEqual((sc['smooth'], sc['angle_min_interval_ms'], sc['angle_quantum_deg'], sc['angle_max_jump_deg'],
                          sc['stage_max_counts'], sc['keys']), (True, 100, 0.25, 40.0, None, 'full_limit'))
        smooth = snap['steering']['smooth']
        self.assertEqual((smooth['firmware'], smooth['mode'], smooth['store']), (True, 'SMOOTH', 'DEFAULT'))
        self.assertEqual(smooth['params']['BRAKE_MS'], 40)
        cal = snap['calibration']
        self.assertEqual({k: cal['cal'][k] for k in ('right', 'center', 'left', 'store')},
                         {'right': 1475, 'center': 2029, 'left': 2513, 'store': 'NVS'})
        self.assertEqual((cal['guards']['right_min'], cal['guards']['left_max'], cal['guards']['center_min'],
                          cal['guards']['center_max'], cal['guards']['source']), (1300, 2600, 1850, 2150, 'cal_line'))
        self.assertEqual((cal['smooth'], cal['find_pulse_ms']['supported'], cal['max_jump_deg']), (True, True, 40.0))
        self.assertEqual(rig.written('SM'), [], 'the banner brought the SM line: no SM read')
        self.assertEqual(rig.motor.counts['unknown_lines'], 0)
        json.dumps(snap, allow_nan=False)

    def test_sm_is_read_once_when_the_board_did_not_reset(self):
        rig = SmoothRig(dict(reset_on_open=False))
        rig.fake.boot()
        rig.fake.out.clear()                     # the banner went to nobody
        rig.ready()
        rig.run(1.0)
        self.assertEqual(rig.written('SM'), ['SM'])
        self.assertEqual(rig.bridge.snapshot['steering']['smooth']['mode'], 'SMOOTH')
        rig.run(2.0)
        self.assertEqual(rig.written('SM'), ['SM'], 'once per link')

    def test_trace_lines_are_counted_apart(self):
        rig = SmoothRig()
        rig.ready()
        rig.fake.feed(b'TRACE ON 300\n')          # not from the bridge (it never sends TRACE)
        rig.run(0.6)
        counts = rig.motor.counts
        self.assertGreaterEqual(counts['data_lines'], 20)
        self.assertEqual(counts['unknown_lines'], 0)
        self.assertEqual(rig.bridge.snapshot['steering']['smooth']['data_lines'], counts['data_lines'])
        self.assertIn('ok_trace', [kind for _, kind, _ in rig.motor.events])


class SmoothSteering(unittest.TestCase):
    def setUp(self):
        self.rig = SmoothRig()
        self.rig.ready()
        self.assertTrue(self.rig.arm()['ok'])

    def test_held_key_is_one_target_and_one_run(self):
        rig = self.rig
        runs, gaps = rig.runs_during(esp.LIMIT_MRAD, 2513)                 # A held: full left
        self.assertEqual([text for _, text in rig.angle_writes()], ['ANGLE -20.00'])
        self.assertEqual((runs, gaps), (1, 0), 'one continuous run of the steering motor')
        runs, gaps = rig.runs_during(0, 2029)                               # released: back to the centre
        self.assertEqual(rig.angle_writes()[-1][1], 'ANGLE +0.00')
        self.assertEqual((runs, gaps), (1, 0))
        runs, gaps = rig.runs_during(-esp.LIMIT_MRAD, 1475)                # D held: full right
        self.assertEqual(rig.angle_writes()[-1][1], 'ANGLE +20.00')
        self.assertEqual((runs, gaps), (1, 0))
        runs, gaps = rig.runs_during(esp.LIMIT_MRAD, 2513, limit=8.0)      # full swing right -> left in one target
        self.assertEqual(rig.angle_writes()[-1][1], 'ANGLE -20.00')
        self.assertEqual((runs, gaps), (1, 0))
        self.assertEqual(len(rig.angle_writes()), 4, 'never staged')
        self.assertEqual(rig.motor.angle_stages, 0)
        self.assertNotIn('hold_cancelled', [kind for _, kind, _ in rig.motor.events])
        self.assertEqual(rig.fake.drive_on_ms, 0)

    def test_drag_targets_are_fine_and_paced(self):
        rig = self.rig
        rig.run(0.3)
        start = len(rig.angle_writes())
        for i in range(1, 31):                     # a drag from 0 to 9 degrees left over 3 s (0.3 deg per 0.1 s)
            rig.drive(0, esp.round_half_away(esp.command_deg_to_mrad(-0.3 * i)))
            rig.run(0.08)
        rig.hold(esp.round_half_away(esp.command_deg_to_mrad(-9.0)), seconds=1.0)
        writes = rig.angle_writes()[start:]
        gaps = [b[0] - a[0] for a, b in zip(writes, writes[1:])]
        self.assertTrue(all(g >= esp.ANGLE_MIN_INTERVAL_SMOOTH_S - 1e-9 for g in gaps), gaps)
        self.assertGreaterEqual(len(writes), 12, 'about one target per 0.1 s while dragging: %r' % writes)
        for _, text in writes:
            value = float(text.split()[1])
            self.assertEqual(len(text.split()[1].split('.')[1]), 2, text)
            self.assertAlmostEqual(value / 0.25, round(value / 0.25), places=6)
        self.assertEqual(writes[-1][1], 'ANGLE -9.00')
        rig.hold(esp.round_half_away(esp.command_deg_to_mrad(-9.0)),
                 until=lambda: abs(rig.fake.command_angle() + 9.0) < 0.5 and rig.fake.sm_phase == 0, limit=3)
        # A slow drag (3 deg/s here) moves each target only ~15 counts: the firmware reaches it, settles and starts
        # again (short runs). A drag faster than the wheel keeps the next target ahead: taken over while running.
        runs = rig.fake.smooth_runs_started
        for i in range(1, 11):                     # 9 -> 20 degrees left in 0.8 s (the page clamps to the limit)
            mrad = esp.round_half_away(esp.command_deg_to_mrad(-9.0 - 1.5 * i))
            self.assertTrue(rig.drive(0, min(mrad, esp.LIMIT_MRAD))['ok'])
            rig.run(0.08)
        rig.hold(esp.LIMIT_MRAD, until=lambda: abs(rig.fake.pot - 2513) <= 10 and rig.fake.sm_phase == 0, limit=4)
        self.assertGreater(rig.fake.takeovers, 0, 'targets ahead in the running direction are taken over')
        self.assertLessEqual(rig.fake.smooth_runs_started - runs, 2)

    def test_stall_cancel_is_not_forced_again(self):
        rig = SmoothRig(dict(stuck=True))
        rig.ready()
        rig.arm()
        rig.hold(-esp.LIMIT_MRAD, until=lambda: rig.fake.lines('ANGLE') and not rig.fake.target_active, limit=4)
        self.assertTrue(any('STALL (no force retry)' in text for _, _, text in rig.motor.events))
        rig.run(0.2)
        self.assertIn('steer_hold_cancelled', rig.bridge.snapshot['steering']['faults'])
        count = len(rig.angle_writes())
        rig.hold(-esp.LIMIT_MRAD, seconds=1.0)
        self.assertEqual(len(rig.angle_writes()), count, 'the same target is not sent again')
        rig.hold(0, seconds=0.4)
        self.assertEqual(len(rig.angle_writes()), count + 1)

    def test_sm_off_brings_the_pulse_rules_back(self):
        rig = self.rig
        rig.fake.feed(b'SM OFF\n')                # (an operator's serial session; the bridge never sends SM OFF)
        rig.run(0.3)
        self.assertIs(rig.motor.smooth, False)
        sc = rig.bridge.snapshot['steer_control']
        self.assertEqual((sc['smooth'], sc['angle_min_interval_ms'], sc['angle_quantum_deg'], sc['stage_max_counts']),
                         (False, 500, 1.0, 280))
        self.assertLess(sc['angle_max_jump_deg'], 20.0, 'the wide marks are staged again (280 counts)')
        self.assertEqual(rig.bridge.snapshot['steering']['smooth']['mode'], 'PULSE')
        self.assertIn('smooth', [kind for _, kind, _ in rig.motor.events])
        start = len(rig.angle_writes())
        rig.hold(esp.LIMIT_MRAD, seconds=1.5)
        writes = rig.angle_writes()[start:]
        self.assertTrue(writes and all(len(text.split()[1].split('.')[1]) == 1 for _, text in writes), writes)
        self.assertNotEqual(writes[0][1], 'ANGLE -20.0', 'staged: the first target is an intermediate one')
        self.assertGreater(rig.motor.angle_stages, 0)


class SmoothCalibration(unittest.TestCase):
    def test_centre_2029_and_a_left_end_above_2500(self):
        rig = SmoothRig(nvs={})                   # the board's default marks 1681/1900/2180
        rig.ready()
        mark = len(rig.tr.writes)
        reply = rig.call('calibrate', action='set_center')
        self.assertTrue(reply['ok'], reply)
        rig.until(lambda: rig.motor.cal_job is None, limit=6)
        self.assertTrue(rig.motor.cal_last['ok'], rig.motor.cal_last)
        self.assertEqual((rig.motor.cal['center'], rig.motor.cal['store']), (2029, 'NVS'))
        reply = rig.call('calibrate', action='find_left', pulse_ms=60)
        self.assertTrue(reply['ok'], reply)
        rig.until(lambda: rig.motor.cal_job is None, limit=60)
        written = b''.join(d for _, d in rig.tr.writes[mark:]).decode().splitlines()
        self.assertIn('CAL FIND LEFT 60', written)
        self.assertTrue(rig.motor.cal_last['ok'], rig.motor.cal_last)
        found = rig.motor.cal_found['left']
        self.assertGreater(found['pot'], 2500)
        self.assertTrue(found['usable'], found)
        reply = rig.call('calibrate', action='apply', left_margin_counts=30)
        self.assertTrue(reply['ok'], reply)
        rig.until(lambda: rig.motor.cal_job is None, limit=6)
        self.assertTrue(rig.motor.cal_last['ok'], rig.motor.cal_last)
        self.assertEqual((rig.motor.cal['left'], rig.motor.cal['center'], rig.motor.cal['store']),
                         (found['pot'] - 30, 2029, 'NVS'))
        self.assertGreater(rig.fake.cal()['left'], 2500)
        self.assertEqual(rig.fake.drive_on_ms, 0)

    def test_pulse_checks(self):
        rig = SmoothRig()
        rig.ready()
        for bad in (39, 101, 60.0, '60', True):
            self.assertEqual(rig.call('calibrate', action='find_left', pulse_ms=bad)['error'],
                             'calibration_pulse_invalid')
        self.assertEqual(rig.call('calibrate', action='read', pulse_ms=60)['error'], 'calibration_pulse_invalid')
        old = Rig(dict(cal_runtime=True, drive_watchdog_ms=300))
        old.ready()
        mark = len(old.tr.writes)
        self.assertEqual(old.call('calibrate', action='find_left', pulse_ms=60)['error'],
                         'calibration_pulse_needs_firmware_update')
        old.run(0.3)
        written = b''.join(d for _, d in old.tr.writes[mark:]).decode().splitlines()
        self.assertFalse([line for line in written if line.startswith('CAL')])
        self.assertIsNone(old.motor.smooth)
        self.assertEqual(old.bridge.snapshot['steer_control']['angle_min_interval_ms'], 500)


if __name__ == '__main__':
    unittest.main()
