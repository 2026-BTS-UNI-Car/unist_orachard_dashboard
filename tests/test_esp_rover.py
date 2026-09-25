"""No hardware: ownership, deadlines, and delayed requests at the motor boundary."""
import unittest
from scripts.rover_control_core import RoverControl, ControlError


class Motor:
    ready = True
    firmware = "UBTS_WHEEL_PWM_V2"
    error = None
    outputs = {}
    stopped = True
    def __init__(self): self.calls = []
    def hard_stop(self): self.calls.append("STOP"); self.stopped = True
    def enable(self): self.calls.append("enable")
    def drive(self, left, right): self.calls.append((left, right)); self.stopped = False
    def release(self): self.calls.append("release")


class RoverControlTest(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.motor = Motor()
        self.core = RoverControl(self.motor, lambda: self.now)
        self.reply = self.core.dispatch({"op": "arm", "owner": "operator-a"})

    def request(self, op, **extra):
        return {"op": op, "owner": "operator-a", "lease": self.reply["lease"],
                "ticket": self.reply["ticket"], "seq": self.reply["seq"] + 1, **extra}

    def send(self, op, **extra):
        self.reply = self.core.dispatch(self.request(op, **extra))

    def test_second_operator_cannot_take_ownership(self):
        with self.assertRaisesRegex(ControlError, "control_in_use"):
            self.core.dispatch({"op": "arm", "owner": "operator-b"})
        with self.assertRaisesRegex(ControlError, "control_not_owned"):
            self.core.dispatch(self.request("drive", owner="operator-b", left=30, right=30, deadman=True))
        self.assertNotIn((30, 30), self.motor.calls)

    def test_keepalive_does_not_extend_motion_deadline(self):
        self.send("drive", left=30, right=30, deadman=True)
        self.now += .6
        self.send("keepalive")
        self.now += .16
        self.core.tick()
        self.assertIsNone(self.core.lease)
        self.assertEqual(self.core.reason, "input_expired")
        self.assertEqual(self.motor.calls[-1], "STOP")

    def test_stop_revokes_already_queued_motion(self):
        delayed = self.request("drive", left=100, right=100, deadman=True)
        self.core.stop()
        with self.assertRaises(ControlError): self.core.dispatch(delayed)
        self.assertNotIn((100, 100), self.motor.calls)

    def test_replayed_ticket_stops(self):
        old = self.request("drive", left=25, right=25, deadman=True)
        self.core.dispatch(old)
        with self.assertRaisesRegex(ControlError, "stale_input"): self.core.dispatch(old)
        self.assertIsNone(self.core.lease)

    def test_delayed_first_motion_expires(self):
        self.now += 1.51
        with self.assertRaisesRegex(ControlError, "stale_input"):
            self.send("drive", left=30, right=30, deadman=True)
        self.assertNotIn((30, 30), self.motor.calls)

    def test_invalid_motion_revokes_control(self):
        for value in (True, 101, float("nan"), "30"):
            with self.subTest(value=value):
                self.setUp()
                with self.assertRaises(ControlError): self.send("drive", left=value, right=0, deadman=True)
                self.assertIsNone(self.core.lease)

    def test_release_uses_ramp_with_bounded_completion(self):
        self.send("drive", left=100, right=-100, deadman=True)
        self.send("release")
        self.assertEqual(self.motor.calls[-1], "release")
        self.now += 1.11
        self.core.tick()
        self.assertEqual(self.motor.calls[-1], "STOP")

    def test_release_completed_does_not_disarm_idle_operator(self):
        self.send("drive", left=25, right=25, deadman=True)
        self.send("release")
        self.motor.stopped = True
        self.now += .5
        self.core.tick()
        self.assertFalse(self.core.moving)
        self.assertTrue(self.core.lease)

    def test_device_loss_revokes_lease_before_stop_write(self):
        self.motor.ready = False
        self.core.tick()
        self.assertIsNone(self.core.lease)
        self.assertEqual(self.core.reason, "device_unavailable")

    def test_failed_stop_still_revokes_control(self):
        def fail(): raise OSError("unplugged")
        self.motor.hard_stop = fail
        with self.assertRaises(OSError): self.core.stop()
        self.assertIsNone(self.core.lease)

    def test_idle_owner_expires(self):
        self.now += 30.01
        self.core.tick()
        self.assertIsNone(self.core.lease)

    def test_bridge_stop_flushes_pending_requests(self):
        from scripts.rover_control_bridge import Bridge
        from concurrent.futures import Future
        import time
        motor = self.motor
        motor.poll = lambda: None
        motor.port, motor.fd, motor.identity = "/fake", 1, "test"
        bridge = Bridge(motor)
        future = Future()
        bridge.pending.put((time.monotonic(), {"op": "arm", "owner": "a"}, future))
        self.assertEqual(bridge.request({"op": "stop"})["state"], "stop_requested")
        bridge.step()
        self.assertEqual(future.result()["error"], "stop_barrier")
        self.assertFalse(bridge.snapshot["armed"])
