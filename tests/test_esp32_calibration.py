"""Runtime steering calibration (firmware token CAL_RUNTIME=1) through the ESP32-S3 bridge, end to end against the fake
board (tests/fake_esp32.py, cal_runtime=True: CAL commands, NVS that survives a reset, a mechanical stop with
springback). No hardware, no socket, no ROS; injected clock.
Run: python -B -m unittest tests.test_esp32_calibration
The stop positions, rates and springback are placeholders of the fake board, not measurements of the vehicle.
"""
import json
import unittest

from scripts import esp32_abs_hold as esp
from tests.fake_esp32 import FakeEsp32, FakeTransport
from tests.test_esp32_bridge import Rig

STOP_LEFT, STOP_RIGHT = 2320.0, 1460.0
CAL_FAKE = dict(cal_runtime=True, drive_watchdog_ms=300, pot=1902.0, rate_counts_s=390.0, stop_left=STOP_LEFT,
                stop_right=STOP_RIGHT)


def motor23(lines):
    return [line for line in lines if line.split()[:1] in (['2'], ['3'])]


class CalRig(Rig):
    def __init__(self, fake_options=None, **motor_options):
        super().__init__(dict(CAL_FAKE, **(fake_options or {})), **motor_options)

    def calibrate(self, action, **fields):
        return self.call('calibrate', action=action, **fields)

    def written_since(self, mark):
        return b''.join(data for _, data in self.tr.writes[mark:]).decode('ascii').splitlines()

    def finish(self, limit=60.0):
        self.until(lambda: self.motor.cal_job is None, limit=limit)
        self.run(0.2)
        return self.motor.cal_last

    def job(self, action, limit=60.0, **fields):
        """One calibration action from the request to its end: (reply, last, lines written from the op's STOP to the
        end of the job). The loop step that takes the request first runs its usual poll (maybe a STATUS), so the
        window starts at the STOP; it ends where the job ended (the zero refresh starts again in the next step)."""
        mark = len(self.tr.writes)
        reply = self.calibrate(action, **fields)
        if not reply.get('ok'):
            return reply, None, []
        self.until(lambda: self.motor.cal_job is None, limit=limit)
        lines = b''.join(data for _, data in self.tr.writes[mark:]).decode('ascii').splitlines()
        lines = lines[lines.index('STOP'):]
        self.run(0.2)
        return reply, self.motor.cal_last, lines

    def settle_at(self, pot, seconds=8.0):
        self.keep(0, until=lambda: abs(self.fake.pot - pot) <= 12 and not self.fake.target_active
                  or abs(self.fake.pot - pot) <= 12 and self.fake.phase == 0, limit=seconds)


class ParserTest(unittest.TestCase):
    def test_new_lines(self):
        kind, f = esp.parse_line('FW: ABS-HOLD-8-REBOUND-LIMITS F=RIGHT POT_DECREASES DRIVE_WD=300 CAL_RUNTIME=1')
        self.assertEqual((kind, f['name'], f['drive_wd_ms'], f['cal_runtime']), ('fw', esp.FIRMWARE_NAME, 300, True))
        self.assertFalse(esp.parse_line('FW: ABS-HOLD-8-REBOUND-LIMITS F=RIGHT DRIVE_WD=300')[1]['cal_runtime'])
        self.assertEqual(esp.parse_line('CAL: RIGHT=1490 CENTER=1900 LEFT=2290 MAX_COMMAND=20 STORE=NVS '
                                        'GUARD=1300..2500 (physical degrees unverified)'),
                         ('cal', {'right': 1490, 'center': 1900, 'left': 2290, 'max_command': 20, 'store': 'NVS',
                                  'guard_right': 1300, 'guard_left': 2500}))
        self.assertEqual(esp.parse_line('CAL SAMPLE: SIDE=LEFT N=3 BEFORE=1981 PEAK=2019 SETTLED=2019 PROGRESS=38 '
                                        'REBOUND=0 DRIVE_MS=300'),
                         ('cal_sample', {'side': 'left', 'n': 3, 'before': 1981, 'peak': 2019, 'settled': 2019,
                                         'progress': 38, 'rebound': 0, 'drive_ms': 300}))
        self.assertEqual(esp.parse_line('CAL FOUND RIGHT POT=1450 REASON=NO_PROGRESS START=2330 PULSES=25 '
                                        'DRIVE_MS=2500 TIME_MS=19400'),
                         ('cal_found', {'side': 'right', 'pot': 1450, 'reason': 'NO_PROGRESS', 'start': 2330,
                                        'pulses': 25, 'drive_ms': 2500, 'time_ms': 19400}))
        self.assertEqual(esp.parse_line('CAL FIND START: SIDE=LEFT POT=1905 PWM=100 PULSE_MS=100')[0],
                         'cal_find_start')
        self.assertEqual(esp.parse_line('CAL ERROR: SET REFUSED: DRIVE MOTOR 2/3 RUNNING (STOP first)'),
                         ('cal_error', {'text': 'SET REFUSED: DRIVE MOTOR 2/3 RUNNING (STOP first)'}))
        self.assertEqual(esp.parse_line('OK: CAL SAVED (NVS)'), ('ok_cal', {'text': 'SAVED (NVS)'}))
        self.assertEqual(esp.parse_line('CAL WARNING: STORED VALUES IGNORED (X)')[0], 'cal_warning')
        self.assertEqual(esp.parse_line('Calibration: CAL / CAL CENTER')[0], 'help')

    def test_hard_guards_match_the_firmware(self):
        # fallback guards (no GUARD on the CAL line): RIGHT >= 1300, LEFT <= 2600; CENTER 1850..2150 (SMOOTH r2)
        self.assertIsNone(esp.cal_problem(1681, 1900, 2180))
        self.assertIsNone(esp.cal_problem(1300, 1900, 2600))
        self.assertIsNone(esp.cal_problem(1475, 2029, 2513), 'the 2026-11-16 lifted-wheel values')
        self.assertEqual(esp.cal_problem(1299, 1900, 2180), 'calibration_right_below_guard')
        self.assertEqual(esp.cal_problem(1681, 1900, 2601), 'calibration_left_above_guard')
        self.assertEqual(esp.cal_problem(1500, 1849, 2180), 'calibration_center_outside')
        self.assertEqual(esp.cal_problem(1500, 2151, 2500), 'calibration_center_outside')
        self.assertEqual(esp.cal_problem(1751, 1900, 2180), 'calibration_right_span_small')
        self.assertEqual(esp.cal_problem(1681, 1900, 2049), 'calibration_left_span_small')
        self.assertEqual(esp.cal_problem(1681.0, 1900, 2180), 'calibration_value_invalid')
        # the CAL line's guards (the CAL_RUNTIME firmware before SMOOTH says GUARD=1300..2500)
        self.assertEqual(esp.cal_problem(1681, 1900, 2501, 1300, 2500), 'calibration_left_above_guard')
        self.assertIsNone(esp.cal_problem(1681, 1900, 2500, 1300, 2500))

    def test_guards_come_from_the_cal_line(self):
        rig = CalRig()
        rig.ready()
        self.assertEqual(rig.motor.cal_guards(), (1300, 2500), 'old CAL_RUNTIME firmware: GUARD=1300..2500')
        guards = rig.bridge.snapshot['calibration']['guards']
        self.assertEqual((guards['left_max'], guards['source']), (2500, 'cal_line'))
        rig.motor.cal = {'right': 1681, 'center': 1900, 'left': 2180, 'max_command': 20}   # a CAL line without GUARD
        self.assertEqual(rig.motor.cal_guards(), (esp.CAL_HARD_RIGHT_MIN, esp.CAL_HARD_LEFT_MAX))


class OldFirmwareTest(unittest.TestCase):
    def test_without_cal_runtime_nothing_is_sent(self):
        rig = Rig(dict(drive_watchdog_ms=300))
        rig.ready()
        mark = len(rig.tr.writes)
        reply = rig.call('calibrate', action='find_left')
        self.assertEqual(reply['error'], 'calibration_needs_firmware_update')
        rig.run(0.5)
        written = b''.join(d for _, d in rig.tr.writes[mark:]).decode().splitlines()
        self.assertFalse([line for line in written if line.startswith('CAL')])
        block = rig.bridge.snapshot['calibration']
        self.assertEqual((block['supported'], block['blocker']), (False, 'calibration_needs_firmware_update'))
        self.assertEqual(rig.bridge.snapshot['steer_control']['angle_max_jump_deg'], 20.0)


class CalibrationTest(unittest.TestCase):
    def setUp(self):
        self.rig = CalRig()
        self.rig.ready()

    def test_ready_and_reports_the_default_marks(self):
        block = self.rig.bridge.snapshot['calibration']
        self.assertTrue(block['supported'])
        self.assertIsNone(block['blocker'])
        self.assertEqual(block['cal'], {'right': 1681, 'center': 1900, 'left': 2180, 'max_command': 20,
                                        'store': 'DEFAULT', 'guard_right': 1300, 'guard_left': 2500})
        self.assertEqual(block['default_margin_counts'], 30)
        self.assertEqual(block['proposal']['problem'], 'calibration_nothing_found')
        json.dumps(self.rig.bridge.snapshot, allow_nan=False)

    def test_find_apply_then_angle_20_reaches_the_new_marks(self):
        rig = self.rig
        reply, last, lines = rig.job('find_left')
        self.assertTrue(reply['ok'], reply)
        self.assertTrue(last['ok'], last)
        self.assertEqual(lines[0], 'STOP', 'STOP first')
        self.assertEqual([line for line in lines if line != 'STOP'][:1], ['CAL FIND LEFT'])
        found_left = rig.motor.cal_found['left']
        self.assertTrue(found_left['usable'])
        self.assertIn(found_left['reason'], esp.CAL_USABLE_REASONS)
        self.assertLessEqual(abs(found_left['pot'] - STOP_LEFT), 25, found_left)
        self.assertGreaterEqual(len(rig.motor.cal_samples), 5)
        reply, last, lines2 = rig.job('find_right')
        self.assertTrue(last['ok'], last)
        found_right = rig.motor.cal_found['right']
        self.assertLessEqual(abs(found_right['pot'] - STOP_RIGHT), 25, found_right)
        for chunk in (lines, lines2):
            self.assertEqual(motor23(chunk), [], 'no motor 2/3 line during a scan')
            self.assertNotIn('STATUS', chunk, 'no STATUS during a scan (it would abort it)')
        self.assertEqual(rig.fake.drive_on_ms, 0)
        self.assertLessEqual(rig.fake.steer_max_pwm, 100)
        self.assertEqual(rig.fake.cal()['store'], 'DEFAULT', 'a scan changes no mark')
        # apply with the default margin: 30 counts inward of each found end, saved, CAL reloaded
        proposal = rig.bridge.snapshot['calibration']['proposal']
        self.assertEqual(proposal['marks'], {'right': found_right['pot'] + 30, 'center': 1900,
                                             'left': found_left['pot'] - 30})
        reply, last, lines = rig.job('apply')
        self.assertTrue(last['ok'], last)
        self.assertEqual([line for line in lines if line.startswith('CAL')],
                         ['CAL SET LEFT %d' % (found_left['pot'] - 30), 'CAL SET RIGHT %d' % (found_right['pot'] + 30),
                          'CAL SAVE'])
        self.assertEqual(motor23(lines), [])
        new_left, new_right = found_left['pot'] - 30, found_right['pot'] + 30
        self.assertEqual({k: rig.motor.cal[k] for k in ('right', 'center', 'left', 'store')},
                         {'right': new_right, 'center': 1900, 'left': new_left, 'store': 'NVS'})
        self.assertEqual(rig.fake.nvs, {'right': new_right, 'center': 1900, 'left': new_left, 'ver': 1})
        rig.run(0.5)
        self.assertIsNone(rig.bridge.snapshot['calibration']['blocker'])
        self.assertLess(rig.bridge.snapshot['steer_control']['angle_max_jump_deg'], 20.0)
        # command +-20 = the calibrated near-maximum positions (staged by counts, no budget cancel)
        self.assertTrue(rig.arm()['ok'])
        events_before = len(rig.motor.events)
        rig.drive(0, esp.LIMIT_MRAD)                              # left 20 (steer_mrad left +)
        rig.keep(0, until=lambda: abs(rig.fake.pot - new_left) <= 10 and rig.fake.phase == 0, limit=12)
        rig.drive(0, -esp.LIMIT_MRAD)                             # right 20
        rig.keep(0, until=lambda: abs(rig.fake.pot - new_right) <= 10 and rig.fake.phase == 0, limit=16)
        cancels = [e for e in rig.motor.events[events_before:] if e[1] == 'hold_cancelled']
        self.assertEqual(cancels, [])
        targets = [text for _, text in rig.angle_writes()]
        self.assertIn('ANGLE -20.0', targets)
        self.assertIn('ANGLE +20.0', targets)
        self.assertGreater(len(targets), 3, 'staged through intermediate targets: %r' % targets)
        rig.run(0.3)
        self.assertAlmostEqual(rig.bridge.snapshot['steering']['command_equiv_deg'], 20.0, delta=1.0)

    def test_refused_while_armed_and_arm_refused_while_calibrating(self):
        rig = self.rig
        self.assertTrue(rig.arm()['ok'])
        reply = rig.calibrate('find_left')
        self.assertEqual(reply['error'], 'calibration_needs_disarmed')
        self.assertEqual(rig.bridge.snapshot['calibration']['blocker'], 'calibration_needs_disarmed')
        rig.drive(30)
        self.assertEqual(rig.calibrate('read')['error'], 'calibration_needs_disarmed')
        rig.call('stop')
        rig.run(1.2)
        self.assertTrue(rig.calibrate('find_right')['ok'])
        rig.run(0.5)
        self.assertEqual(rig.call('arm', vehicle_profile='front_steer')['error'], 'calibrating')
        self.assertEqual(rig.calibrate('find_left')['error'], 'calibration_busy')
        self.assertTrue(rig.bridge.snapshot['connected'], 'the paused STATUS does not read as a lost link')
        self.assertNotIn('steering_status_stale', rig.bridge.snapshot['steering']['faults'])
        rig.finish()
        self.assertTrue(rig.arm()['ok'])

    def test_operator_stop_aborts_a_find(self):
        rig = self.rig
        mark = len(rig.tr.writes)
        self.assertTrue(rig.calibrate('find_right')['ok'])
        rig.run(3.0)
        self.assertIsNotNone(rig.fake.find)
        self.assertEqual(rig.bridge.request({'op': 'stop', 'reason': 'operator_stop'}),
                         {'ok': True, 'state': 'stop_requested'})
        rig.run(0.2)
        self.assertIsNone(rig.fake.find)
        self.assertEqual(rig.fake.motors[0]['direction'], 0)
        self.assertEqual(rig.motor.cal_last['error'], 'calibration_stopped')
        self.assertEqual(rig.motor.cal_found['right']['reason'], 'ABORTED_INPUT')
        self.assertFalse(rig.motor.cal_found['right']['usable'])
        pot = rig.fake.pot
        rig.run(3.0)
        self.assertEqual(rig.fake.pot, pot, 'no steering motion after the STOP')
        self.assertEqual(motor23(rig.written_since(mark)[:rig.written_since(mark).index('STOP', 1) + 1]), [])
        self.assertEqual(rig.bridge.snapshot['calibration']['proposal']['problem'], 'calibration_nothing_found')

    def test_no_motor_2_3_line_and_no_status_during_any_action(self):
        rig = self.rig
        for action in ('read', 'set_center', 'find_left', 'default'):
            reply, last, lines = rig.job(action)
            self.assertTrue(reply['ok'], (action, reply))
            self.assertTrue(last['ok'], (action, last))
            self.assertEqual(motor23(lines), [], action)
            self.assertNotIn('STATUS', lines, action)
            self.assertEqual(lines[0], 'STOP', action)
        self.assertEqual(rig.fake.drive_on_ms, 0)
        self.assertEqual(rig.fake.lines('2 F') + rig.fake.lines('3 F') + rig.fake.lines('2 R') + rig.fake.lines('3 R'),
                         [])

    def test_set_center_measures_and_saves(self):
        rig = CalRig(dict(pot=1925.0))
        rig.ready()
        reply, last, lines = rig.job('set_center')
        self.assertTrue(last['ok'], last)
        self.assertEqual([line for line in lines if line.startswith('CAL')], ['CAL CENTER', 'CAL SAVE'])
        self.assertEqual((rig.motor.cal['center'], rig.motor.cal['store']), (1925, 'NVS'))
        self.assertEqual(rig.fake.nvs['center'], 1925)

    def test_calibration_survives_a_board_reset_and_default_clears_it(self):
        rig = self.rig
        rig.job('find_left')
        rig.job('find_right')
        _, last, _ = rig.job('apply', left_margin_counts=40, right_margin_counts=20)
        self.assertTrue(last['ok'], last)
        want = dict(rig.motor.cal)
        self.assertEqual(want['left'], rig.motor.cal_found['left']['pot'] - 40)
        self.assertEqual(want['right'], rig.motor.cal_found['right']['pot'] + 20)
        rig.fake.boot()                                     # a reset: RAM gone, NVS kept
        rig.until(lambda: rig.motor.link == 'ready', limit=5)
        rig.run(0.3)
        self.assertIn('mcu_reset_detected', rig.motor.latches)
        self.assertEqual(rig.motor.cal, dict(want))
        self.assertEqual(rig.fake.cal()['store'], 'NVS')
        # a new board object with the same NVS (power cycle) reads the same marks
        clone = FakeEsp32(rig.clock, cal_runtime=True, nvs=rig.fake.nvs)
        self.assertEqual((clone.cal_right, clone.cal_left, clone.cal_store), (want['right'], want['left'], 'NVS'))
        _, last, _ = rig.job('default')
        self.assertTrue(last['ok'], last)
        self.assertEqual(rig.fake.nvs, {})
        rig.fake.boot()
        rig.until(lambda: rig.motor.link == 'ready', limit=5)
        rig.run(0.3)
        self.assertEqual({k: rig.motor.cal[k] for k in ('right', 'center', 'left', 'store')},
                         {'right': 1681, 'center': 1900, 'left': 2180, 'store': 'DEFAULT'})

    def test_a_silent_board_during_a_find_is_stopped(self):
        rig = self.rig
        self.assertTrue(rig.calibrate('find_left')['ok'])
        rig.run(2.0)
        rig.fake.mute = True
        rig.until(lambda: rig.motor.cal_job is None, limit=6)
        self.assertEqual(rig.motor.cal_last['error'], 'calibration_silent')
        self.assertIsNone(rig.fake.find, 'the STOP after the silence aborted the scan')
        rig.fake.mute = False

    def test_apply_checks(self):
        rig = self.rig
        self.assertEqual(rig.calibrate('apply')['error'], 'calibration_nothing_found')
        for bad in (5, 121, '30', True, 30.0):
            self.assertEqual(rig.calibrate('apply', left_margin_counts=bad)['error'], 'calibration_margin_invalid')
        self.assertEqual(rig.calibrate('wiggle')['error'], 'invalid_calibration_action')
        self.assertEqual(rig.call('calibrate', owner='', action='read')['error'], 'operator_required')

    def test_a_stuck_wheel_gives_no_usable_end(self):
        rig = CalRig(dict(stuck=True))
        rig.ready()
        _, last, _ = rig.job('find_left')
        self.assertFalse(last['ok'])
        self.assertEqual((last['error'], last['detail']), ('calibration_end_not_found', 'NO_PROGRESS'))
        self.assertFalse(rig.motor.cal_found['left']['usable'])
        self.assertEqual(rig.calibrate('apply')['error'], 'calibration_nothing_found')

    def test_firmware_refusal_is_reported(self):
        rig = self.rig
        rig.fake.motors[1] = dict(direction=1, pwm=60)       # a drive the host did not ask for
        _, last, lines = rig.job('find_left')
        self.assertEqual(lines[0], 'STOP')
        self.assertFalse(last['ok'])
        self.assertEqual(last['error'], 'calibration_refused')
        self.assertIn('DRIVE STOPPED < 1000 MS', last['detail'])
        rig.run(1.2)
        _, last, _ = rig.job('read')
        self.assertTrue(last['ok'])

    def test_snapshot_carries_progress_for_the_status_file(self):
        rig = self.rig
        rig.calibrate('find_left')
        rig.run(4.0)
        block = rig.bridge.snapshot['calibration']
        self.assertEqual((block['state'], block['job']['action'], block['job']['phase']),
                         ('running', 'find_left', 'waiting'))
        self.assertGreaterEqual(len(block['samples']), 2)
        self.assertEqual(block['samples'][-1]['side'], 'left')
        self.assertIsNotNone(block['live_pot'])
        text = json.dumps(rig.bridge.snapshot, allow_nan=False)
        self.assertIn('"calibration"', text)
        self.assertNotIn('operator-a', text, 'the owner (a dashboard session) is never written to the status')
        rig.finish()
        block = rig.bridge.snapshot['calibration']
        self.assertEqual((block['state'], block['last']['action'], block['last']['ok']), ('idle', 'find_left', True))
        self.assertIsNone(block['job'])


if __name__ == '__main__':
    unittest.main()
