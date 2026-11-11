"""Protocol test: SignedBridgeClient (real UnixSocketTransport) against the REAL dashboard ESP bridge (review).

Same in-process harness as test_bridge_protocol_linux.py (real Bridge + Handler + Server + RoverControl from
the read-only dashboard checkout, temporary Unix socket, fake motor: no serial port, no hardware, no motor
output anywhere). Checks the turn's signed path end to end: deadman true on every drive (pivots and 0/0),
one request per connection, sequence / ticket, the bridge keeps the lease on 0/0, a guard refusal latches
and stops without sending the drive, LATCHED never re-arms by itself, and the 0.75 s input expiry also
covers a pivot. Linux only (rover_control_bridge imports fcntl / termios).
"""
import sys
import time
import unittest

import rowexec_testlib  # noqa: F401  (path setup)
from row_executor.bridge_client import ARMED, LATCHED, BridgeError, UnixSocketTransport
from row_executor.guard import TurnGuard
from row_executor.turn_bridge import SignedBridgeClient

LINUX = sys.platform.startswith('linux')
if LINUX:
    from test_bridge_protocol_linux import BridgeHarness, Recording


def left_turn_guard():
    return TurnGuard(1, pivot_min_pct=60, pivot_cap_pct=80, drive_min_pct=20, drive_cap_pct=65, drive_diff_cap_pct=10)


@unittest.skipUnless(LINUX, 'real ESP bridge imports fcntl/termios (Linux only)')
class SignedProtocolTest(unittest.TestCase):
    def setUp(self):
        self.h = BridgeHarness()
        self.addCleanup(self.h.close)
        self.transport = Recording(UnixSocketTransport(self.h.path, .6))
        self.client = SignedBridgeClient(self.h.path, owner='turn-assist:test', transport=self.transport,
                                         allow_signed=True)
        self.guard = left_turn_guard()

    def test_pivot_and_zero_with_deadman_one_request_per_connection(self):
        self.client.arm()
        for left, right in ((0, 0), (-60, 60), (-60, 60), (0, 0), (0, 0), (55, 55), (0, 0)):
            self.client.drive_signed(left, right, self.guard.shape_problem)
            time.sleep(.05)
        self.assertEqual(self.client.state, ARMED)
        drives = [r for r in self.transport.requests if r['op'] == 'drive']
        self.assertEqual(len(drives), 7)
        self.assertTrue(all(r['deadman'] is True for r in drives))
        self.assertEqual([r['seq'] for r in drives], list(range(1, 8)))
        self.assertEqual(self.transport.inner.connections, len(self.transport.requests))
        self.assertEqual(self.h.accepted, len(self.transport.requests))
        self.assertIn((-60, 60), self.h.motor.calls)
        self.assertEqual(self.h.motor.calls[-1], (0, 0))
        self.assertEqual(self.h.bridge.control.lease, self.client.lease)

    def test_zero_keeps_the_lease_across_ticket_and_input_ttl(self):
        self.client.arm()
        lease = self.h.bridge.control.lease
        end = time.monotonic() + 2.
        while time.monotonic() < end:
            self.client.drive_signed(0, 0, self.guard.shape_problem)
            time.sleep(.1)
        self.assertEqual(self.client.state, ARMED)
        self.assertEqual(self.h.bridge.control.lease, lease)

    def test_pivot_expires_without_renewal(self):
        self.client.arm()
        self.client.drive_signed(-60, 60, self.guard.shape_problem)
        time.sleep(1.2)                                   # > INPUT_TTL 0.75 s without a new drive
        self.assertIsNone(self.h.bridge.control.lease)
        self.assertEqual(self.h.bridge.control.reason, 'input_expired')
        with self.assertRaises(BridgeError):
            self.client.drive_signed(0, 0, self.guard.shape_problem)
        self.assertEqual(self.client.state, LATCHED)

    def test_guard_refusal_latches_stops_and_never_rearms(self):
        self.client.arm()
        self.client.drive_signed(-60, 60, self.guard.shape_problem)
        before = len(self.transport.requests)
        with self.assertRaises(BridgeError):
            self.client.drive_signed(60, -60, self.guard.shape_problem)     # right pivot in a left turn
        self.assertEqual(self.client.state, LATCHED)
        sent = self.transport.requests[before:]
        self.assertEqual([r['op'] for r in sent], ['stop'])                # nothing but the op stop
        deadline = time.monotonic() + 1.                                   # op stop runs on the next bridge step
        while self.h.bridge.control.lease is not None and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertIsNone(self.h.bridge.control.lease)
        with self.assertRaises(BridgeError):
            self.client.arm()                                               # LATCHED: no re-arm
        with self.assertRaises(BridgeError):
            self.client.drive_signed(0, 0, self.guard.shape_problem)       # not armed: nothing sent
        self.assertEqual(len(self.transport.requests), before + 1)

    def test_default_client_never_sends_a_pivot(self):
        client = SignedBridgeClient(self.h.path, owner='turn-assist:default', transport=self.transport)
        client.arm()
        before = len(self.transport.requests)
        with self.assertRaises(BridgeError):
            client.drive_signed(-60, 60, self.guard.shape_problem)
        self.assertEqual([r['op'] for r in self.transport.requests[before:]], ['stop'])
        self.assertNotIn((-60, 60), self.h.motor.calls)


if __name__ == '__main__':
    unittest.main()
