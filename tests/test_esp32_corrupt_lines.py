"""One corrupted line from the ESP32-S3 (bytes dropped in the USB CDC stream) must not stop the bridge for good.

2026-11-17 08:47 on the vehicle (bridge da39e8a): one FW line read 'FW: MITS F=RIGHT ...' ('FW: ' spliced onto the tail
of 'ABS-HOLD-8-REBOUND-LIMITS'); the bridge latched firmware_mismatch, stayed there although good FW lines followed
every 0.1 s, and refused every arm until a restart. Checks against the fake board (tests/fake_esp32.py): a lone bad FW
line is counted and ignored (ready, armed control kept, arm allowed), FW_MISMATCH_LINES in a row still latch as before,
the same during the confirm phase after open and after a board reset, a FW line cut short does not flip the tokens, a
corrupted or once-changed CAL line does not replace the marks, and a bad STATUS line latches nothing.
Run: python -B -m unittest tests.test_esp32_corrupt_lines
"""
import unittest

from scripts import esp32_abs_hold as esp
from tests.test_esp32_bridge import Rig
from tests.test_esp32_smooth import SmoothRig

CORRUPT_FW = 'FW: MITS F=RIGHT POT_DECREASES DRIVE_WD=300 CAL_RUNTIME=1 SMOOTH=1'
FW_OPTIONS = dict(drive_watchdog_ms=300, cal_runtime=True)


def fw_index(lines):
    return next(i for i, line in enumerate(lines) if line.startswith('FW: '))


class Parser(unittest.TestCase):
    def test_lines(self):
        kind, f = esp.parse_line(CORRUPT_FW)
        self.assertEqual((kind, f['name']), ('fw', 'MITS'))
        good = ('CAL: RIGHT=1475 CENTER=2029 LEFT=2513 MAX_COMMAND=20 STORE=NVS GUARD=1300..2600 '
                '(physical degrees unverified)')
        self.assertEqual(esp.parse_line(good)[0], 'cal')
        for bad in ('CAL: RIGHT=1475 CENTER=2029 LEF', good.replace(' (physical', ' (phys'),
                    good.replace('STORE=NVS', 'STORE=NS'), good + 'STATUS: POT=2029'):
            self.assertEqual(esp.parse_line(bad)[0], 'cal_corrupt', bad)


class CorruptFwLine(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(dict(FW_OPTIONS))
        self.rig.ready()

    def test_one_bad_fw_line_is_ignored_and_arm_works(self):
        rig = self.rig
        rig.fake.out.append(CORRUPT_FW)
        rig.run(0.5)
        motor = rig.motor
        self.assertEqual((motor.link, motor.firmware, motor.drive_wd_ms, motor.cal_runtime),
                         ('ready', esp.FIRMWARE_NAME, 300, True))
        self.assertEqual(motor.latches, {})
        self.assertEqual(motor.counts['fw_line_mismatch'], 1)
        self.assertEqual([text for _, kind, text in motor.events if kind == 'fw_line_corrupt'], [CORRUPT_FW])
        self.assertEqual(motor.fw_mismatch_run, 0, 'the next good FW line resets the count')
        self.assertTrue(rig.bridge.snapshot['connected'])
        self.assertTrue(rig.arm()['ok'])

    def test_armed_control_survives_bad_lines_and_events_are_rate_limited(self):
        rig = self.rig
        self.assertTrue(rig.arm()['ok'])
        for _ in range(5):
            rig.fake.out.append(CORRUPT_FW)
            rig.drive(0, 100)
            rig.run(0.1)
            rig.fake.out.append(CORRUPT_FW)
            rig.drive(0, 100)
            rig.run(0.1)
        self.assertIsNotNone(rig.bridge.control.lease, 'no stop, the operator keeps control')
        self.assertEqual(rig.motor.latches, {})
        self.assertEqual(rig.motor.counts['fw_line_mismatch'], 10)
        self.assertEqual(len([1 for _, kind, _ in rig.motor.events if kind == 'fw_line_corrupt']), 1)

    def test_two_bad_lines_in_a_row_are_still_ignored(self):
        rig = self.rig
        rig.fake.out.extend([CORRUPT_FW, CORRUPT_FW])
        rig.run(0.3)
        self.assertEqual((rig.motor.link, rig.motor.latches), ('ready', {}))

    def test_three_in_a_row_latch_as_before(self):
        rig = self.rig
        rig.fake.out.extend([CORRUPT_FW] * esp.FW_MISMATCH_LINES)
        rig.run(0.3)
        self.assertEqual((rig.motor.link, rig.motor.firmware), ('firmware_mismatch', 'MITS'))
        self.assertIn('firmware_mismatch', rig.motor.latches)
        self.assertEqual(rig.call('arm', vehicle_profile='front_steer')['error'], 'firmware_mismatch')
        writes = len(rig.tr.writes)
        rig.run(1.0)
        self.assertEqual(len(rig.tr.writes), writes, 'nothing more is written (as before)')

    def test_a_short_fw_line_does_not_flip_the_tokens(self):
        rig = SmoothRig()
        rig.ready()
        rig.fake.out.append('FW: ABS-HOLD-8-REBOUND-LIMITS F=RIGHT POT_DECR')
        rig.run(0.3)
        motor = rig.motor
        self.assertEqual((motor.drive_wd_ms, motor.cal_runtime, motor.smooth), (300, True, True))
        self.assertEqual(motor.counts['fw_line_tokens_once'], 1)
        self.assertEqual(rig.bridge.snapshot['steer_control']['angle_min_interval_ms'], 200)


class ConfirmPhase(unittest.TestCase):
    def test_bad_fw_line_in_the_banner_after_open(self):
        rig = Rig(dict(FW_OPTIONS))
        rig.clock.t += 0.02
        rig.bridge.step()                           # opens: the fake board resets and prints its banner
        rig.fake.out[fw_index(rig.fake.out)] = CORRUPT_FW
        rig.ready()
        self.assertEqual((rig.motor.link, rig.motor.firmware, rig.motor.latches), ('ready', esp.FIRMWARE_NAME, {}))
        self.assertEqual(rig.motor.counts['fw_line_mismatch'], 1)
        self.assertTrue(rig.arm()['ok'])

    def test_bad_fw_line_in_the_banner_after_a_board_reset(self):
        rig = Rig(dict(FW_OPTIONS))
        rig.ready()
        rig.fake.boot()
        rig.fake.out[fw_index(rig.fake.out)] = CORRUPT_FW
        rig.until(lambda: rig.motor.link == 'ready', limit=3)
        rig.run(0.3)
        self.assertEqual(rig.motor.link, 'ready')
        self.assertEqual(sorted(rig.motor.latches), ['mcu_reset_detected'], 'the reset, not a firmware mismatch')
        self.assertTrue(rig.arm()['ok'], 'the operator re-arms as after any reset')

    def test_another_firmware_after_open_still_latches(self):
        rig = Rig(dict(FW_OPTIONS, fw_name='OTHER-FIRMWARE'))
        rig.run(1.5)
        self.assertEqual(rig.motor.link, 'firmware_mismatch')
        self.assertIn('firmware_mismatch', rig.motor.latches)


class CorruptCalAndStatus(unittest.TestCase):
    def setUp(self):
        self.rig = SmoothRig()
        self.rig.ready()
        self.marks = {k: self.rig.motor.cal[k] for k in ('right', 'center', 'left')}

    def marks_now(self):
        return {k: self.rig.motor.cal[k] for k in ('right', 'center', 'left')}

    def test_cut_or_spliced_cal_lines_keep_the_marks(self):
        rig = self.rig
        rig.fake.out.append('CAL: RIGHT=1475 CENTER=2029 LEF')
        rig.fake.out.append('CAL: RIGHT=14 CENTER=2029 LEFT=2513 MAX_COMMAND=20 STORE=NVS GUARD=1300..2600 '
                            '(physical degrees unverified)')
        rig.run(0.5)
        self.assertEqual(self.marks_now(), {'right': 1475, 'center': 2029, 'left': 2513})
        self.assertEqual(rig.motor.counts['cal_line_corrupt'], 1)
        self.assertEqual(rig.motor.counts['cal_line_changed_once'], 1)
        self.assertIsNone(rig.motor.cal_pending, 'the next good CAL line clears the once-seen value')
        self.assertEqual(rig.motor.latches, {})
        self.assertTrue(rig.arm()['ok'])

    def test_a_real_change_is_taken_on_the_second_line(self):
        rig = self.rig
        rig.fake.cal_left = 2490                    # (the firmware itself never does this outside a CAL command)
        rig.run(0.5)
        self.assertEqual(self.marks_now()['left'], 2490)

    def test_bad_status_lines_latch_nothing(self):
        rig = self.rig
        self.assertTrue(rig.arm()['ok'])
        for bad in ('STATUS: POT=19', 'STATUS: POT=2029 COMMAND_EQUIV=-0.3',
                    'STATUS: POT=2029 COMMAND_EQUIV=+0.00 HOLD=O'):
            rig.fake.out.append(bad)
            rig.drive(0, 0)
            rig.run(0.1)
        rig.drive(0, 0)
        rig.run(0.1)
        self.assertEqual(rig.motor.latches, {})
        self.assertIsNotNone(rig.bridge.control.lease)
        self.assertNotIn('pot_invalid', rig.bridge.snapshot['steering']['faults'])
        self.assertEqual(rig.motor.link, 'ready')


if __name__ == '__main__':
    unittest.main()
