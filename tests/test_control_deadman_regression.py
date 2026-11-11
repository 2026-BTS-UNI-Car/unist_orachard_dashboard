"""Bridge deadman regression guard for the UI performance work (no hardware).

The dashboard changes rely on the rover bridge stopping a moving rover 0.75 s
after the last drive input, independent of the browser or the network.
"""
import unittest

from scripts.rover_control_core import ControlError, RoverControl


class Motor:
    ready = True
    firmware = "UBTS_WHEEL_PWM_V2"
    error = None
    outputs = {}
    stopped = True

    def __init__(self):
        self.calls = []

    def hard_stop(self):
        self.calls.append("STOP"); self.stopped = True

    def enable(self):
        self.calls.append("enable")

    def drive(self, left, right):
        self.calls.append((left, right)); self.stopped = False

    def release(self):
        self.calls.append("release")


class DeadmanRegression(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.motor = Motor()
        self.core = RoverControl(self.motor, lambda: self.now)
        self.reply = self.core.dispatch({"op": "arm", "owner": "browser"})

    def send(self, op, **extra):
        self.reply = self.core.dispatch({"op": op, "owner": "browser", "lease": self.reply["lease"],
                                         "ticket": self.reply["ticket"], "seq": self.reply["seq"] + 1, **extra})

    def test_constants_unchanged(self):
        self.assertEqual((RoverControl.INPUT_TTL, RoverControl.TICKET_TTL, RoverControl.OWNER_TTL), (0.75, 1.5, 30.0))

    def test_browser_drive_period_keeps_moving_for_ten_seconds(self):
        for _ in range(int(10 / 0.14)):
            self.send("drive", left=30, right=30, deadman=True)
            self.now += 0.14
            self.core.tick()
            self.assertTrue(self.core.moving)
        self.assertNotIn("STOP", self.motor.calls[1:])

    def test_silence_stops_at_input_ttl(self):
        self.send("drive", left=40, right=40, deadman=True)
        self.now += 0.74; self.core.tick()
        self.assertTrue(self.core.moving)
        self.now += 0.02; self.core.tick()
        self.assertFalse(self.core.moving)
        self.assertEqual(self.core.reason, "input_expired")
        self.assertEqual(self.motor.calls[-1], "STOP")
        self.assertIsNone(self.core.lease)

    def test_keepalive_never_extends_motion(self):
        self.send("drive", left=40, right=40, deadman=True)
        for _ in range(5):
            self.now += 0.14
            self.send("keepalive")
        self.now += 0.1; self.core.tick()
        self.assertEqual(self.core.reason, "input_expired")

    def test_stop_needs_no_owner_and_always_writes_stop(self):
        self.send("drive", left=40, right=40, deadman=True)
        status = self.core.dispatch({"op": "stop", "reason": "dashboard_stop"})
        self.assertFalse(status["armed"])
        self.assertEqual(self.motor.calls[-1], "STOP")
        self.assertEqual(self.core.reason, "dashboard_stop")
        with self.assertRaises(ControlError):
            self.send("drive", left=40, right=40, deadman=True)


if __name__ == "__main__":
    unittest.main()
