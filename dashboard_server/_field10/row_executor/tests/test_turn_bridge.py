"""SignedBridgeClient (opt-in pivot path) against the REAL dashboard RoverControl core, in-process."""
import json
import time
import unittest

import rowexec_testlib as L
from row_executor.bridge_client import ARMED, LATCHED, BridgeClient, BridgeError
from row_executor.guard import TurnGuard
from row_executor.turn_bridge import SignedBridgeClient

core_mod = L.import_dash('rover_control_core')
RoverControl, ControlError = core_mod.RoverControl, core_mod.ControlError


class FakeMotor:
    firmware = 'UBTS_WHEEL_PWM_V2'

    def __init__(self):
        self.calls = []
        self.error = None
        self.outputs = {}
        self.stopped = True

    @property
    def ready(self):
        return not self.error

    def hard_stop(self):
        self.calls.append('STOP')
        self.stopped = True

    def enable(self):
        self.calls.append('enable')

    def drive(self, left, right):
        self.calls.append((left, right))
        self.stopped = not (left or right)

    def release(self):
        self.calls.append('release')


class CoreTransport:
    def __init__(self, control):
        self.control = control
        self.requests = []

    def __call__(self, request):
        self.requests.append(json.loads(json.dumps(request)))
        if request.get('op') == 'status':
            self.control.tick()
            return dict(self.control.status(), updated_at=time.time(), mode='manual')
        if request.get('op') == 'stop':
            self.control.stop(str(request.get('reason', 'operator_stop'))[:80])
            return {'ok': True, 'state': 'stop_requested'}
        try:
            return self.control.dispatch(request)
        except (ControlError, ValueError) as exc:
            return {'ok': False, 'error': str(exc)}


class SignedClient(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.motor = FakeMotor()
        self.control = RoverControl(self.motor, lambda: self.now)
        self.transport = CoreTransport(self.control)
        self.guard = TurnGuard(1, 60, 80, 20, 65, 10)

    def client(self, **kw):
        c = SignedBridgeClient(owner='turn-assist:test', transport=self.transport, **kw)
        c.arm()
        return c

    def drives(self):
        return [(r['left'], r['right'], r['deadman']) for r in self.transport.requests if r['op'] == 'drive']

    def test_default_refuses_signed(self):
        c = self.client()
        self.assertFalse(c.allow_signed)
        with self.assertRaises(BridgeError):
            c.drive_signed(-60, 60, self.guard.shape_problem)
        self.assertEqual(c.state, LATCHED)
        self.assertEqual(self.drives(), [])
        self.assertIn('STOP', self.motor.calls)          # best-effort op stop with the lease held

    def test_pivot_passes_real_core_with_deadman(self):
        c = self.client(allow_signed=True)
        c.drive_signed(-60, 60, self.guard.shape_problem)
        self.assertEqual(self.drives(), [(-60, 60, True)])
        self.assertEqual(self.motor.calls[-1], (-60, 60))
        self.assertEqual(self.control.requested, {'left': -60, 'right': 60})
        c.drive_signed(0, 0, self.guard.shape_problem)
        c.drive_signed(55, 55, self.guard.shape_problem)   # forward still goes through drive()
        self.assertEqual(self.drives()[-1], (55, 55, True))
        self.assertEqual(c.state, ARMED)
        self.assertEqual(c.counts['signed_drives'], 3)

    def test_guard_refusal_sends_nothing(self):
        c = self.client(allow_signed=True)
        for bad in ((60, -60), (-60, 70), (-90, 90), (-40, -40), (0, 50)):
            c2 = SignedBridgeClient(owner='turn-assist:t%r' % (bad,), transport=self.transport, allow_signed=True)
            self.control.stop('reset')
            c2.arm()
            before = len(self.drives())
            with self.assertRaises(BridgeError):
                c2.drive_signed(bad[0], bad[1], self.guard.shape_problem)
            self.assertEqual(len(self.drives()), before, bad)
            self.assertEqual(c2.state, LATCHED)
            self.assertTrue(c2.latch_reason.startswith('turn_guard:'), c2.latch_reason)
        del c

    def test_missing_or_broken_guard(self):
        c = self.client(allow_signed=True)
        with self.assertRaises(BridgeError):
            c.drive_signed(-60, 60, None)
        self.assertEqual(c.state, LATCHED)
        self.control.stop('reset')
        c = SignedBridgeClient(owner='turn-assist:b', transport=self.transport, allow_signed=True)
        c.arm()

        def broken(left, right):
            raise RuntimeError('guard bug')
        with self.assertRaises(BridgeError):
            c.drive_signed(-60, 60, broken)
        self.assertEqual(c.latch_reason, 'turn_guard:guard_error:RuntimeError')

    def test_range_and_type(self):
        c = self.client(allow_signed=True)
        with self.assertRaises(BridgeError):
            c.drive_signed(-101, 101, lambda l, r: None)
        self.assertEqual(self.drives(), [])

    def test_not_armed_never_sends(self):
        c = SignedBridgeClient(owner='turn-assist:x', transport=self.transport, allow_signed=True)
        with self.assertRaises(BridgeError):
            c.drive_signed(-60, 60, self.guard.shape_problem)
        self.assertEqual(self.transport.requests, [])

    def test_forward_only_client_unchanged(self):
        c = BridgeClient(owner='row-executor:x', transport=self.transport)
        c.arm()
        self.assertFalse(hasattr(c, 'drive_signed'))
        with self.assertRaises(BridgeError):
            c.drive(-60, 60)
        self.assertEqual(c.state, LATCHED)

    def test_allow_signed_type(self):
        with self.assertRaises(ValueError):
            SignedBridgeClient(owner='x', transport=self.transport, allow_signed=1)


if __name__ == '__main__':
    unittest.main()
