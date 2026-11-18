"""Soft start / soft release of the ESP32-S3 bridge drive output (2026-11-18 field: "sudden start, sudden stop").
drive_ramp=True: a start jumps to DRIVE_RAMP_FLOOR_PWM and rises at DRIVE_RAMP_UP_PWM_S; letting go falls at
DRIVE_RAMP_DOWN_PWM_S; the STOP op and faults still zero at once. Fake board, injected clock; no hardware.
Run: python -B -m unittest tests.test_esp32_drive_ramp
"""
import unittest

from scripts import esp32_abs_hold as esp
from tests.test_esp32_bridge import Rig


class DriveRamp(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(dict(drive_watchdog_ms=300), drive_ramp=True)
        self.rig.ready()
        self.assertTrue(self.rig.arm()['ok'])

    def hold(self, throttle, seconds):
        seen = []
        for _ in range(int(round(seconds / 0.1))):
            self.rig.drive(throttle)
            self.rig.run(0.1)
            seen.append(self.rig.fake.drive()[2])
        return seen

    def test_soft_start_rises_then_reaches_target(self):
        seen = self.hold(100, 2.0)
        self.assertLess(seen[0], 255)
        self.assertGreaterEqual(seen[0], esp.DRIVE_RAMP_FLOOR_PWM)
        self.assertEqual(seen, sorted(seen), 'never falls while the throttle is held')
        self.assertEqual(seen[-1], 255)
        self.assertGreater(sum(1 for v in seen if 0 < v < 255), 5)

    def test_release_ramps_down(self):
        self.hold(60, 2.0)                      # pwm 153
        self.assertEqual(self.rig.fake.drive()[2], 153)
        seen = self.hold(0, 1.0)
        self.assertGreater(seen[0], 0, 'no sudden stop on release')
        self.assertEqual(seen[-1], 0)
        self.assertIsNotNone(self.rig.bridge.control.lease)

    def test_release_op_ramps_down(self):
        self.hold(60, 2.0)
        r = self.rig.owned('release')
        self.assertTrue(r.get('ok'), r)
        self.rig.run(0.1)
        self.assertGreater(self.rig.fake.drive()[2], 0, 'letting go is a soft release')
        self.rig.run(1.0)
        self.assertEqual(self.rig.fake.drive(), {2: 0, 3: 0})

    def test_stop_op_is_immediate(self):
        self.hold(60, 2.0)
        self.rig.call('stop', reason='operator_stop')
        self.rig.run(0.06)
        self.assertEqual(self.rig.fake.drive(), {2: 0, 3: 0})



class SteerTrace(unittest.TestCase):
    def test_eight_field_data_line(self):
        kind, f = esp.parse_line('DATA,1961,623,-1,0,1961,0,2017,-1')
        self.assertEqual((kind, f['steer_pwm'], f['target'], f['quiet']), ('data', 0, 2017, -1))
        kind, f = esp.parse_line('DATA,10,2000,2000,1,3,95,2100')
        self.assertEqual((kind, f['steer_pwm'], f['quiet']), ('data', 95, None))

    def test_trace_is_kept_on_and_the_pwm_reaches_the_status(self):
        rig = Rig(dict(smooth=True), steer_trace=True)
        rig.ready()
        rig.run(1.0)
        self.assertTrue(rig.written('TRACE ON 30000'))
        block = rig.motor.steering_block()
        self.assertIn('pwm', block)
        self.assertIsNotNone(block['pwm'], 'DATA lines arrive while the trace is on')
        self.assertIn('pwm_peak', block)
        n = len(rig.written('TRACE ON'))
        rig.run(esp.STEER_TRACE_RENEW_S + 0.5)
        self.assertGreater(len(rig.written('TRACE ON')), n, 'renewed before the 30 s trace ends')

    def test_off_by_default(self):
        rig = Rig(dict(smooth=True))
        rig.ready()
        rig.run(1.0)
        self.assertFalse(rig.written('TRACE'))
        self.assertIsNone(rig.motor.steering_block()['pwm'])


if __name__ == '__main__':
    unittest.main()
