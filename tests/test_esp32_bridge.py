"""ESP32-S3 'ABS-HOLD' bridge (scripts/esp32_abs_hold.py, scripts/esp32_steer_bridge.py) against the fake firmware
(tests/fake_esp32.py, written from BTS7960_Serial_Control.ino). No hardware, no socket, no ROS; injected clock.
Run: python -B -m unittest tests.test_esp32_bridge
These are placeholder-free protocol checks with a fake board, not a measurement on the vehicle.
"""
import concurrent.futures
import math
import unittest

from scripts import esp32_abs_hold as esp
from scripts import esp_rover_bridge
from scripts.esp32_steer_bridge import Esp32Bridge
from tests.fake_esp32 import CENTER, FakeEsp32, FakeTransport

OWNER = 'operator-a'


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Rig:
    def __init__(self, fake_options=None, **motor_options):
        self.clock = Clock()
        self.fake = FakeEsp32(self.clock, **(fake_options or {}))
        self.tr = FakeTransport(self.fake)
        self.motor = esp.Esp32SteerMotor(self.tr, self.clock, **motor_options)
        self.bridge = Esp32Bridge(self.motor, self.clock, wall_clock=lambda: 1.7e9 + self.clock.t)
        self.lease = self.ticket = None
        self.seq = 0

    def run(self, seconds, dt=0.02):
        steps = int(round(seconds / dt))
        for _ in range(steps):
            self.clock.t += dt
            self.bridge.step()

    def until(self, predicate, limit=10.0, dt=0.02):
        waited = 0.0
        while not predicate():
            if waited >= limit:
                raise AssertionError('condition not reached in %.1f s' % limit)
            self.clock.t += dt
            waited += dt
            self.bridge.step()
        return waited

    def ready(self):
        self.until(lambda: self.motor.link == 'ready' and self.motor.status_at is not None)
        self.run(0.2)

    def keep(self, seconds, dt=0.02, period=0.4, until=None, limit=None):
        """Wait while the operator stays connected (a keepalive every 0.4 s, as the screen sends every 0.5 s)."""
        waited, since = 0.0, 0.0
        while (until is None and waited < seconds - 1e-9) or (until is not None and not until()):
            if limit is not None and waited >= limit:
                raise AssertionError('condition not reached in %.1f s' % limit)
            if self.lease and since >= period:
                self.owned('keepalive')
                since = 0.0
            self.clock.t += dt
            waited += dt
            since += dt
            self.bridge.step()
        return waited

    def call(self, op, owner=OWNER, **fields):
        future = concurrent.futures.Future()
        self.bridge.pending.put_nowait((self.clock(), dict(op=op, owner=owner, **fields), future))
        self.clock.t += 0.02
        self.bridge.step()
        return future.result(timeout=0)

    def arm(self):
        reply = self.call('arm', vehicle_profile='front_steer')
        if reply.get('ok'):
            self.lease, self.ticket, self.seq = reply['lease'], reply['ticket'], reply['seq']
        return reply

    def owned(self, op, **fields):
        reply = self.call(op, lease=self.lease, ticket=self.ticket, seq=self.seq + 1, **fields)
        if reply.get('ok'):
            self.ticket, self.seq = reply['ticket'], reply['seq']
        return reply

    def drive(self, throttle, steer_mrad=0):
        return self.owned('drive', deadman=True, throttle=throttle, steer_mrad=steer_mrad)

    def written(self, prefix=''):
        return [line for line in self.tr.written() if line.startswith(prefix)]

    def angle_writes(self):
        return [(t, data.decode().strip()) for t, data in self.tr.writes if data.startswith(b'ANGLE')]


class ParserAndUnits(unittest.TestCase):
    def test_lines(self):
        kind, f = esp.parse_line('STATUS: POT=1966 COMMAND_EQUIV=-4.71 HOLD=OFF TARGET_POT=1900 TARGET_COMMAND=+0.00')
        self.assertEqual(kind, 'status')
        self.assertEqual((f['pot'], f['command_equiv'], f['hold'], f['target_pot']), (1966, -4.71, False, 1900))
        self.assertEqual(esp.parse_line('STATUS: INVALID POT=-1'), ('status_invalid', {'pot': -1}))
        self.assertEqual(esp.parse_line('OK: MOTOR 2 F PWM 100'),
                         ('ok_motor', {'motor': 2, 'direction': 'F', 'pwm': 100}))
        self.assertEqual(esp.parse_line('OK: MOTOR 3 STOP\r'), ('ok_motor', {'motor': 3, 'direction': None, 'pwm': 0}))
        self.assertEqual(esp.parse_line('OK: ALL STOP')[0], 'ok_all_stop')
        kind, f = esp.parse_line('FW: ABS-HOLD-8-REBOUND-LIMITS F=RIGHT POT_DECREASES')
        self.assertEqual((kind, f['name'], f['drive_wd_ms']), ('fw', esp.FIRMWARE_NAME, None))
        self.assertEqual(esp.parse_line('FW: ABS-HOLD-8-REBOUND-LIMITS F=RIGHT DRIVE_WD=300')[1]['drive_wd_ms'], 300)
        self.assertEqual(esp.parse_line('CAL: RIGHT=1681 CENTER=1900 LEFT=2180 MAX_COMMAND=20 (physical degrees '
                                        'unverified)')[1], {'right': 1681, 'center': 1900, 'left': 2180,
                                                            'max_command': 20})
        self.assertEqual(esp.parse_line('READY: 1=STEER, 2=LEFT, 3=RIGHT')[0], 'ready')
        self.assertEqual(esp.parse_line('STEER HOLD CANCELLED: REBOUND (no force retry)')[0], 'hold_cancelled')
        self.assertEqual(esp.parse_line('STEER STOP: TIMEOUT 1000ms POT=1900 TARGET=1900 HOLD=OFF')[0], 'steer_stop')
        self.assertEqual(esp.parse_line('QUIET: POT=1990 TARGET=2180 PROGRESS=32 REBOUND=0 DRIVE_MS=80')[1]['progress'],
                         32)
        self.assertEqual(esp.parse_line('DRIVE STOP: WATCHDOG 300ms'), ('drive_watchdog', {'ms': 300}))
        self.assertEqual(esp.parse_line('ESP-ROM:esp32s3-20210327')[0], 'boot_rom')
        self.assertEqual(esp.parse_line('ERROR: angle must be 0..20 degrees')[0], 'error')
        self.assertEqual(esp.parse_line('something else')[0], 'other')

    def test_commands(self):
        self.assertEqual(esp.angle_line(20), b'ANGLE +20.0\n')
        self.assertEqual(esp.angle_line(-12.04), b'ANGLE -12.0\n')
        self.assertEqual(esp.angle_line(-0.0), b'ANGLE +0.0\n')
        self.assertEqual(esp.angle_line(35), b'ANGLE +20.0\n', 'clamped to the firmware range (it refuses > 20)')
        self.assertEqual(esp.drive_line(2, 'F', 102), b'2 F 102\n')
        self.assertEqual(esp.drive_line(3, 'R', 0), b'3 S\n')
        with self.assertRaises(ValueError):
            esp.drive_line(1, 'F', 100)          # never the steering motor from the drive path
        with self.assertRaises(ValueError):
            esp.angle_line(float('nan'))

    def test_sign_convention(self):
        # steering API: left positive (steer_mrad); firmware ANGLE and COMMAND_EQUIV: right positive
        self.assertEqual(esp.LIMIT_MRAD, 349)
        self.assertLess(esp.mrad_to_command_deg(+349), 0)
        self.assertEqual(esp.quantize_command(esp.mrad_to_command_deg(+349)), -20.0)
        self.assertEqual(esp.quantize_command(esp.mrad_to_command_deg(-349)), +20.0)
        self.assertEqual(esp.quantize_command(esp.mrad_to_command_deg(0)), 0.0)
        self.assertAlmostEqual(esp.command_deg_to_mrad(-10.0), 10 * math.pi / 180 * 1000)
        self.assertEqual(esp.throttle_to_pwm(40), 102)
        self.assertEqual(esp.throttle_to_pwm(-60), 153)
        self.assertEqual(esp.throttle_to_pwm(100, cap=120), 120)


class FakeFirmware(unittest.TestCase):
    """The fake keeps the two properties the host safety rests on."""

    def test_drive_has_no_timeout_and_manual_steering_stops_after_1000_ms(self):
        clock = Clock()
        fake = FakeEsp32(clock)
        fake.boot()
        fake.feed(b'2 F 100\n3 R 80\n')
        clock.t += 10.0
        fake.advance_to_now()
        self.assertEqual(fake.drive(), {2: 100, 3: -80}, 'drive motors keep running without any host')
        fake.feed(b'1 F 20\n')                  # slowly to the right, far from the limit
        clock.t += 0.9
        fake.advance_to_now()
        self.assertEqual(fake.motors[0]['direction'], 1)
        clock.t += 0.2
        fake.advance_to_now()
        self.assertEqual(fake.motors[0]['direction'], 0)
        self.assertTrue(any(line.startswith('STEER STOP: TIMEOUT 1000ms') for line in fake.out))
        fake.feed(b'STOP\n')
        self.assertEqual(fake.drive(), {2: 0, 3: 0})

    def test_angle_hold_reaches_target_and_same_target_is_kept(self):
        clock = Clock()
        fake = FakeEsp32(clock, pot=CENTER)
        fake.boot()
        fake.feed(b'ANGLE -10\n')
        clock.t += 4.0
        fake.advance_to_now()
        self.assertAlmostEqual(fake.command_angle(), -10.0, delta=0.8)
        fake.out.clear()
        fake.feed(b'ANGLE -10.0\n')
        self.assertIn('OK: SAME ABSOLUTE TARGET (budget unchanged)', fake.out)


class Startup(unittest.TestCase):
    def test_reset_banner_then_stop_then_ready(self):
        rig = Rig()
        rig.clock.t += 0.02
        rig.bridge.step()
        self.assertEqual(rig.tr.opens, 1)
        self.assertEqual(rig.fake.boots, 1, 'opening the port resets the fake board')
        self.assertEqual(rig.tr.written()[0], 'STOP', 'STOP is the first thing written after open')
        reply = rig.call('arm', vehicle_profile='front_steer')
        self.assertFalse(reply['ok'])
        rig.ready()
        self.assertEqual(rig.motor.firmware, esp.FIRMWARE_NAME)
        self.assertFalse(rig.written('ANGLE') or [l for l in rig.written() if l[:1] in '23' and ' S' not in l])
        self.assertTrue(rig.arm()['ok'])

    def test_no_reset_on_open_still_stops_a_running_drive(self):
        rig = Rig(fake_options=dict(reset_on_open=False))
        rig.fake.boot()
        rig.fake.feed(b'2 F 200\n3 F 200\n')     # left running by an earlier process
        rig.fake.out.clear()
        rig.clock.t += 0.02
        rig.bridge.step()
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0}, 'the STOP on open stops it at once')
        waited = rig.until(lambda: rig.motor.link == 'ready')
        self.assertGreaterEqual(waited, esp.BOOT_WAIT_S - 0.05, 'no READY: waits the boot time, then confirms')

    def test_wrong_firmware_never_arms(self):
        # another firmware name on FW_MISMATCH_LINES FW lines in a row (banner + STATUS answers): believed and latched
        rig = Rig(dict(fw_name='SOMETHING-ELSE'))
        rig.clock.t += 0.02
        rig.bridge.step()
        rig.run(1.0)
        self.assertEqual(rig.motor.link, 'firmware_mismatch')
        self.assertEqual(rig.motor.firmware, 'SOMETHING-ELSE')
        self.assertIn('firmware_mismatch', rig.motor.latches)
        self.assertEqual(rig.call('arm', vehicle_profile='front_steer')['error'], 'firmware_mismatch')
        writes = len(rig.tr.writes)
        rig.run(2.0)
        self.assertEqual(len(rig.tr.writes), writes, 'nothing more is written to a board with another firmware')


class Armed(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.ready()
        self.assertTrue(self.rig.arm()['ok'])

    def test_left_request_is_firmware_angle_minus(self):
        rig = self.rig
        self.assertTrue(rig.drive(0, +349)['ok'])
        self.assertEqual(rig.angle_writes()[-1][1], 'ANGLE -20.0')
        rig.keep(0, until=lambda: rig.fake.command_angle() < -18, limit=8)
        rig.keep(0.3)
        steering = rig.bridge.snapshot['steering']
        self.assertGreater(steering['measured_mrad'], 300, 'left is positive in the steering API')
        self.assertLess(steering['command_equiv_deg'], -18)
        self.assertTrue(steering['hold'])
        self.assertGreater(steering['target_permille'], 900)
        self.assertTrue(rig.drive(0, -349)['ok'])
        rig.keep(0.6)
        first = rig.angle_writes()[-1][1]
        self.assertTrue(first.startswith('ANGLE +') and 0 <= float(first[6:]) <= 2.0,
                        'a full swing goes through an intermediate target at most 20 deg away: %s' % first)
        rig.keep(0, until=lambda: rig.fake.command_angle() > 18, limit=15)
        self.assertEqual(rig.angle_writes()[-1][1], 'ANGLE +20.0')
        self.assertEqual(rig.motor.angle_stages, 1)
        self.assertNotIn('steer_hold_cancelled', rig.bridge.snapshot['steering']['faults'])
        rig.keep(0.2)
        self.assertLess(rig.bridge.snapshot['steering']['measured_mrad'], -300, 'right is negative')
        self.assertTrue(rig.drive(0, 0)['ok'])
        rig.keep(0.6)
        self.assertEqual(rig.angle_writes()[-1][1], 'ANGLE +0.0')

    def test_throttle_maps_to_both_drive_motors(self):
        rig = self.rig
        rig.drive(40)
        self.assertEqual(rig.fake.drive(), {2: 102, 3: 102})
        rig.drive(-40)
        self.assertEqual(rig.fake.drive(), {2: -102, 3: -102})
        rig.drive(0)
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})
        rig.run(0.1)
        out = rig.bridge.snapshot['outputs']
        self.assertEqual((out['D8'], out['D5'], out['D3'], out['D0']), (0, 0, 0, 0))

    def test_drive_outputs_report_acknowledged_duty(self):
        rig = self.rig
        rig.drive(40)
        rig.run(0.04)
        out = rig.bridge.snapshot['outputs']
        self.assertEqual((out['D8'], out['D5'], out['D3'], out['D0'], out['D1']), (102, 0, 102, 0, 255))
        self.assertEqual(rig.bridge.snapshot['drive']['left']['acked'], 102)

    def test_input_expiry_stops_the_drive(self):
        rig = self.rig
        rig.drive(40)
        rig.run(0.6)
        self.assertEqual(rig.fake.drive(), {2: 102, 3: 102})
        rig.run(0.2)
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0}, 'no drive after the 0.75 s input deadline')
        self.assertIsNone(rig.bridge.control.lease)
        self.assertEqual(rig.bridge.snapshot['reason'], 'input_expired')

    def test_continuous_input_keeps_driving_without_extra_writes(self):
        rig = self.rig
        for _ in range(20):
            rig.drive(40)
            rig.run(0.08)
        self.assertEqual(rig.fake.drive(), {2: 102, 3: 102})
        self.assertEqual(len(rig.written('2 F')), 1, 'an unchanged drive value is written once (no re-assert)')

    def test_stop_op_wins_at_once(self):
        rig = self.rig
        rig.drive(40, 200)
        self.assertEqual(rig.bridge.request({'op': 'stop', 'reason': 'operator_stop'})['state'], 'stop_requested')
        rig.clock.t += 0.02
        rig.bridge.step()
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})
        self.assertFalse(rig.fake.target_active, 'STOP also ends the steering hold')
        self.assertIsNone(rig.bridge.control.lease)
        self.assertEqual(rig.written()[-1] if rig.written()[-1] == 'STOP' else rig.written('STOP')[-1], 'STOP')

    def test_lease_loss_stops(self):
        rig = self.rig
        rig.drive(40)
        reply = rig.call('drive', lease=rig.lease, ticket='stale', seq=rig.seq + 1, deadman=True, throttle=40,
                         steer_mrad=0)
        self.assertEqual(reply['error'], 'stale_input_rearm_required')
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})
        self.assertTrue(rig.arm()['ok'])
        rig.drive(30)
        reply = rig.call('drive', lease=rig.lease, ticket=rig.ticket, seq=rig.seq + 5, deadman=True, throttle=30,
                         steer_mrad=0)
        self.assertEqual(reply['error'], 'invalid_sequence')
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})

    def test_other_owner_and_differential_commands(self):
        rig = self.rig
        reply = rig.call('arm', owner='operator-b', vehicle_profile='front_steer')
        self.assertEqual(reply['error'], 'control_in_use')
        reply = rig.call('drive', owner='operator-b', lease=rig.lease, ticket=rig.ticket, seq=rig.seq + 1,
                         deadman=True, throttle=40, steer_mrad=0)
        self.assertEqual(reply['error'], 'control_not_owned')
        rig.drive(40)
        reply = rig.owned('drive', deadman=True, left=40, right=40)
        self.assertEqual(reply['error'], 'differential_command_rejected')
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})

    def test_release_zeroes_drive_and_keeps_steering(self):
        rig = self.rig
        rig.drive(40, 200)
        rig.run(0.1)
        self.assertTrue(rig.fake.target_active)
        before = len(rig.written('STOP'))
        self.assertTrue(rig.owned('release')['ok'])
        rig.run(0.1)
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})
        self.assertTrue(rig.fake.target_active, 'release keeps the steering target')
        self.assertEqual(len(rig.written('STOP')), before)

    def test_shutdown_sends_stop_and_closes(self):
        rig = self.rig
        rig.drive(40)
        rig.bridge.shutdown('shutdown')
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})
        self.assertFalse(rig.tr.is_open)
        self.assertEqual(rig.tr.written()[-1], 'STOP')

    def test_write_error_latches_and_reopen_resets_the_board(self):
        rig = self.rig
        rig.drive(40)
        rig.tr.write_error = 'EIO'
        reply = rig.drive(50)               # the write fails: the link is closed at once
        self.assertEqual(reply['error'], 'hardware_fault')
        self.assertEqual(rig.motor.link, 'closed')
        self.assertIsNone(rig.bridge.control.lease)
        self.assertEqual(rig.fake.drive(), {2: 102, 3: 102}, 'a dead link cannot carry the STOP (residual risk)')
        rig.tr.write_error = None
        rig.until(lambda: rig.motor.link != 'closed', limit=3)
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0}, 'the reopen (board reset + STOP) stops it')
        rig.ready()
        self.assertIn('hardware_fault', rig.bridge.snapshot['steering']['faults'])
        self.assertTrue(rig.arm()['ok'], 'an operator re-arm clears the latch')
        self.assertNotIn('hardware_fault', rig.bridge.snapshot['steering']['faults'])

    def test_read_error_still_writes_the_stop(self):
        rig = self.rig
        rig.drive(40)
        rig.tr.read_error = 'ESP disconnected'
        rig.run(0.04)
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0}, 'best-effort STOP on a link whose read failed')
        self.assertIsNone(rig.bridge.control.lease)
        self.assertEqual(rig.bridge.snapshot['reason'], 'hardware_fault')

    def test_board_reset_while_armed(self):
        rig = self.rig
        rig.drive(40, 200)
        rig.fake.boot()                     # brown-out or watchdog reset of the board
        rig.run(0.06)
        self.assertIsNone(rig.bridge.control.lease)
        self.assertEqual(rig.bridge.snapshot['reason'], 'mcu_reset_detected')
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})
        rig.run(1.0)
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0}, 'the host never restarts the drive by itself')
        self.assertFalse(rig.fake.target_active)

    def test_unacknowledged_stop_latches_and_reopens(self):
        rig = self.rig
        rig.fake.ignore_stops = True
        rig.drive(40)
        rig.bridge.request({'op': 'stop'})
        rig.run(0.4)
        self.assertGreaterEqual(len(rig.written('STOP')), 3, 'STOP is written again until acknowledged')
        rig.run(0.4)
        self.assertIn('stop_unconfirmed', rig.motor.latches)
        self.assertGreaterEqual(rig.tr.closes, 1)
        rig.fake.ignore_stops = False
        rig.until(lambda: rig.tr.opens >= 2, limit=3)
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0}, 'the reopen resets the fake board')

    def test_angle_targets_are_rate_limited_newest_wins(self):
        rig = self.rig
        rig.run(0.6)
        start = len(rig.angle_writes())
        for i in range(1, 21):                  # a drag from 0 to 300 mrad (left) over 2 s
            rig.drive(0, 15 * i)
            rig.run(0.08)
        rig.run(0.6)
        writes = rig.angle_writes()[start:]
        gaps = [b[0] - a[0] for a, b in zip(writes, writes[1:])]
        self.assertTrue(all(g >= 0.5 - 1e-9 for g in gaps), gaps)
        self.assertLessEqual(len(writes), 6)
        self.assertEqual(writes[-1][1], 'ANGLE %+.1f' % esp.quantize_command(esp.mrad_to_command_deg(300)))
        rig.until(lambda: abs(rig.fake.command_angle() - esp.mrad_to_command_deg(300)) < 1.0, limit=8)

    def test_cancelled_hold_is_not_forced_again(self):
        rig = Rig(fake_options=dict(stuck=True))
        rig.ready()
        rig.arm()
        rig.drive(0, -349)
        rig.keep(0, until=lambda: rig.fake.lines('ANGLE') and not rig.fake.target_active, limit=4)
        rig.keep(0.2)
        self.assertIn('steer_hold_cancelled', rig.bridge.snapshot['steering']['faults'])
        count = len(rig.angle_writes())
        for _ in range(10):
            self.assertTrue(rig.drive(0, -349)['ok'])
            rig.run(0.1)
        self.assertEqual(len(rig.angle_writes()), count, 'the same target is not sent again')
        self.assertTrue(rig.drive(0, 0)['ok'])
        rig.keep(0.6)
        self.assertEqual(len(rig.angle_writes()), count + 1)

    def test_full_swing_in_one_target_would_run_out_of_budget(self):
        # Why one target moves at most 20 deg: with one -20 -> +20 target the fake firmware (rate from the
        # 2026-11-16 verification log, about 390 counts/s at PWM 100) uses up its 1000 ms correction budget.
        rig = Rig(angle_max_jump_deg=40.0)
        rig.ready()
        rig.arm()
        rig.drive(0, 349)
        rig.keep(0, until=lambda: rig.fake.command_angle() < -18, limit=8)
        rig.keep(0.3)
        rig.drive(0, -349)
        rig.keep(0, until=lambda: not rig.fake.target_active, limit=8)
        self.assertTrue(any('1000ms CORRECTION DRIVE BUDGET' in text for _, kind, text in rig.motor.events))
        self.assertLess(rig.fake.command_angle(), 18)

    def test_first_target_at_the_measured_angle_is_not_sent(self):
        rig = self.rig
        measured = rig.bridge.snapshot['steering']['measured_mrad']
        rig.drive(0, measured)
        rig.run(0.6)
        self.assertEqual(rig.angle_writes(), [])
        self.assertEqual(rig.motor.angle_skipped, 1)

    def test_invalid_pot_stops_and_blocks_arm(self):
        rig = self.rig
        rig.drive(40)
        rig.fake.pot_invalid = True
        rig.run(0.3)
        self.assertIsNone(rig.bridge.control.lease)
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})
        self.assertEqual(rig.call('arm', vehicle_profile='front_steer')['error'], 'pot_invalid')
        self.assertIn('pot_invalid', rig.bridge.snapshot['steering']['faults'])

    def test_silent_board_stops(self):
        rig = self.rig
        rig.drive(40)
        rig.fake.mute = True
        rig.run(0.7)
        self.assertIsNone(rig.bridge.control.lease)
        self.assertIn(rig.bridge.snapshot['reason'], ('device_unavailable', 'steering_status_stale'))
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0}, 'a board that does not answer still receives the STOP')

    def test_status_snapshot_for_the_screen(self):
        rig = self.rig
        snap = rig.bridge.snapshot
        self.assertEqual(snap['vehicle_profile'], 'front_steer')
        self.assertEqual(snap['steer_limit_mrad'], {'left': 349, 'right': 349})
        self.assertEqual(snap['steer_control']['kind'], 'esp32_abs_hold')
        self.assertEqual(snap['steer_control']['keys'], 'full_limit')
        steering = snap['steering']
        self.assertLessEqual(steering['age_ms'], steering['age_limit_ms'])
        self.assertEqual(steering['raw'], 1966)
        self.assertEqual(steering['measured_mrad'], esp.round_half_away(esp.command_deg_to_mrad(-4.71)))
        self.assertTrue(snap['connected'])
        self.assertTrue(snap['armed'])
        self.assertEqual(snap['firmware'], esp.FIRMWARE_NAME)
        self.assertEqual(snap['drive']['firmware_watchdog_ms'], None)
        for key in ('D8', 'D5', 'D3', 'D0', 'D1', 'D2'):
            self.assertIsInstance(snap['outputs'][key], int)


class MotorOwnTtl(unittest.TestCase):
    def test_drive_ttl_without_the_control_core(self):
        rig = Rig()
        rig.ready()
        rig.motor.enable()
        rig.motor.drive_steer(40, 0)
        self.assertEqual(rig.fake.drive(), {2: 102, 3: 102})
        for _ in range(37):
            rig.clock.t += 0.02
            rig.motor.poll()
        self.assertEqual(rig.fake.drive(), {2: 102, 3: 102})
        for _ in range(3):
            rig.clock.t += 0.02
            rig.motor.poll()
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0}, 'the adapter stops on its own TTL')
        self.assertEqual(rig.motor.drive_ttl_stops, 1)

    def test_direction_and_cap_options(self):
        rig = Rig(left_forward='R', max_drive_pwm=100)
        rig.ready()
        rig.motor.enable()
        rig.motor.drive_steer(60, 0)
        self.assertEqual(rig.fake.drive(), {2: -100, 3: 100})
        with self.assertRaises(ValueError):
            esp.Esp32SteerMotor(rig.tr, rig.clock, drive_ttl_s=2.0)

    def test_zero_is_refreshed_while_idle(self):
        rig = Rig()
        rig.ready()
        before = len(rig.written('2 S'))
        rig.run(3.0)
        self.assertGreaterEqual(len(rig.written('2 S')) - before, 2)


class WatchdogFirmware(unittest.TestCase):
    """The proposed firmware drive watchdog (DRIVE_WD in the FW line) and the residual risk without it."""

    def test_without_watchdog_a_dead_host_leaves_the_drive_running(self):
        rig = Rig()
        rig.ready()
        rig.arm()
        rig.drive(40)
        rig.clock.t += 10.0                     # the host process is gone: nothing polls, nothing writes
        rig.fake.advance_to_now()
        self.assertEqual(rig.fake.drive(), {2: 102, 3: 102})

    def test_heartbeat_feeds_the_watchdog_and_a_dead_host_stops(self):
        rig = Rig(fake_options=dict(drive_watchdog_ms=300))
        rig.ready()
        self.assertEqual(rig.motor.drive_wd_ms, 300)
        rig.arm()
        for _ in range(20):
            rig.drive(40)
            rig.run(0.1)
        self.assertEqual(rig.fake.drive(), {2: 102, 3: 102}, 'HB lines keep the patched firmware driving')
        self.assertTrue(rig.written('HB'))
        rig.clock.t += 0.35                     # host gone
        rig.fake.advance_to_now()
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0}, 'the firmware watchdog stops the drive')

    def test_watchdog_stop_seen_by_host_disarms(self):
        rig = Rig(fake_options=dict(drive_watchdog_ms=300))
        rig.ready()
        rig.arm()
        rig.drive(40)
        rig.clock.t += 0.4                      # one late loop
        rig.fake.advance_to_now()
        rig.run(0.04)
        self.assertIsNone(rig.bridge.control.lease)
        self.assertEqual(rig.bridge.snapshot['reason'], 'mcu_drive_watchdog')
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})


class Selector(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(esp_rover_bridge.module_for(None), 'scripts.rover_control_bridge')
        self.assertEqual(esp_rover_bridge.module_for(''), 'scripts.rover_control_bridge')
        self.assertEqual(esp_rover_bridge.module_for('esp32_abs_hold'), 'scripts.esp32_steer_bridge')
        with self.assertRaises(ValueError):
            esp_rover_bridge.module_for('esp32')


if __name__ == '__main__':
    unittest.main()
