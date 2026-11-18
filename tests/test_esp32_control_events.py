"""Control events of the ESP32-S3 bridge (scripts/esp32_steer_bridge.py, 2026-11-18, field: "the Arm keeps turning
off"): arm, disarm with its reason, board link and not-ready changes, the repeat limit, the status field and the event
log file next to the status file. Fake board, injected clock, temporary folder; no hardware.
Run: python -B -m unittest tests.test_esp32_control_events
"""
import json
import tempfile
import unittest
from pathlib import Path

from scripts import esp32_steer_bridge as bridge_mod
from tests.fake_esp32 import FakeEsp32, FakeTransport
from tests.test_esp32_bridge import OWNER, Rig


def events(rig, name=None):
    return [e for e in rig.bridge.control_events if name is None or e['event'] == name]


class ControlEvents(unittest.TestCase):
    def test_arm_disarm_reasons_and_board_changes(self):
        rig = Rig(dict(drive_watchdog_ms=300))
        rig.ready()
        self.assertEqual(events(rig)[0]['event'], 'bridge_start')
        self.assertTrue(rig.arm()['ok'])
        rig.drive(30)
        rig.run(1.0)                                     # no more input: the bridge's 0.75 s deadline
        disarmed = events(rig, 'disarmed')
        self.assertEqual(disarmed[-1]['reason'], 'input_expired')
        self.assertEqual(events(rig, 'armed')[-1]['armed'], True)
        self.assertTrue(rig.arm()['ok'])
        rig.fake.pot_invalid = True                      # a loose potentiometer contact (2026-11-18)
        rig.run(0.5)
        self.assertEqual(events(rig, 'disarmed')[-1]['reason'], 'pot_invalid')
        self.assertIn('pot_invalid', [e['reason'] for e in events(rig, 'not_ready')])
        rig.fake.pot_invalid = False
        rig.run(0.5)
        self.assertEqual(events(rig)[-1]['event'], 'ready')
        rig.fake.boot()                                  # a board reset (power)
        rig.run(1.0)
        links = [e['link'] for e in events(rig, 'board_link')]
        self.assertIn('confirming', links)
        self.assertEqual(links[-1], 'ready')
        snap = rig.bridge.snapshot
        self.assertLessEqual(len(snap['control_events']), 10)
        text = json.dumps(snap, allow_nan=False)
        self.assertNotIn(OWNER, text, 'the owner (a dashboard session) is never written')

    def test_repeats_are_limited(self):
        rig = Rig()
        rig.ready()
        for _ in range(5):
            self.assertTrue(rig.arm()['ok'])
            rig.call('stop', reason='operator_stop')
            rig.run(0.04)
        self.assertEqual(len(events(rig, 'disarmed')), 1)
        self.assertGreaterEqual(rig.bridge.events_suppressed, 4)
        rig.run(2.1)
        self.assertTrue(rig.arm()['ok'])
        rig.call('stop', reason='operator_stop')
        rig.run(0.04)
        self.assertEqual(len(events(rig, 'disarmed')), 2)

    def test_event_log_file_next_to_the_status_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / 'rover_bridge_status.json'
            args = bridge_mod.build_parser().parse_args(['--identity', 'Fake_ESP32', '--status-file', str(status),
                                                         '--no-ros'])
            rig = Rig()
            runner = bridge_mod.Runner(args, transport=FakeTransport(FakeEsp32(rig.clock)), clock=rig.clock)
            for _ in range(250):
                rig.clock.t += 0.02
                runner.bridge.step()
            runner.publish()
            log = Path(tmp) / 'rover_control_events.jsonl'
            raw = log.read_bytes()
            self.assertNotIn(b'\r', raw)
            lines = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
            self.assertEqual(lines[0]['event'], 'bridge_start')
            self.assertIn('board_link', [line['event'] for line in lines])
            runner.publish()
            self.assertEqual(log.read_bytes(), raw, 'nothing new: nothing appended')


if __name__ == '__main__':
    unittest.main()
