"""GNSS UTC clock step: evidence, idle gates and decisions (no real clock change)."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts import gnss_time_sync as sync

OFFSET = 10595.0


def pvt(epoch, *, received_at=None, protocol='UBX_NAV_PVT', **changes):
    whole = int(epoch)
    sample = dict(schema='orchard.gps.v1', protocol=protocol,
                  gps_time=datetime.fromtimestamp(whole, timezone.utc).isoformat(),
                  gps_time_fraction_ns=int(round((epoch - whole) * 1e9)),
                  gps_time_valid_date=True, gps_time_valid_time=True, gps_time_fully_resolved=True,
                  gps_time_accuracy_s=4.4e-08, received_at=epoch - OFFSET if received_at is None else received_at)
    sample.update(changes)
    return sample


def filled(n=6, jitter=0.0, start=1791612000.0):
    evidence = sync.ClockEvidence()
    for i in range(n):
        epoch = start + i
        evidence.observe(pvt(epoch, received_at=epoch - OFFSET + (jitter if i % 2 else 0.0)), 100.0 + i)
    return evidence


class EvidenceTests(unittest.TestCase):
    def test_distinct_valid_epochs_give_median_offset(self):
        estimate = filled(jitter=0.02).estimate(106.0)
        self.assertTrue(estimate['ok'])
        self.assertAlmostEqual(estimate['offset_s'], OFFSET - 0.01, places=3)
        self.assertLessEqual(estimate['spread_s'], 0.021)

    def test_hpposllh_repeats_and_unqualified_time_never_count(self):
        evidence = sync.ClockEvidence()
        t = 1791612000.0
        self.assertFalse(evidence.observe(pvt(t, protocol='UBX_NAV_HPPOSLLH'), 100))
        self.assertFalse(evidence.observe(pvt(t, gps_time_fully_resolved=False), 100))
        self.assertFalse(evidence.observe(pvt(t, gps_time_accuracy_s=0.2), 100))
        self.assertTrue(evidence.observe(pvt(t), 100))
        self.assertFalse(evidence.observe(pvt(t), 101))  # same epoch cannot renew evidence
        self.assertEqual(evidence.estimate(101)['reason'], 'insufficient_epochs')

    def test_host_jump_and_spread_hold(self):
        evidence = filled(5)
        epoch = 1791612005.0
        evidence.observe(pvt(epoch, received_at=epoch - OFFSET + 30), 105.0)  # host clock stepped by 30 s
        self.assertEqual(evidence.estimate(105.0)['reason'], 'host_clock_jump_inside_window')
        noisy = sync.ClockEvidence()
        for i in range(6):
            epoch = 1791612000.0 + i
            noisy.observe(pvt(epoch, received_at=epoch - OFFSET + (0.0 if i < 3 else 0.15)), 100.0 + i + (0.15 if i >= 3 else 0))
        self.assertIn(noisy.estimate(106.0)['reason'], ('offset_spread_too_large', 'receiver_host_rate_mismatch'))

    def test_window_expiry(self):
        self.assertEqual(filled().estimate(100.0 + sync.WINDOW_S + 10)['reason'], 'insufficient_epochs')


OPEN = dict(recorder_idle=True, rover_inactive=True, mapping_idle=True, native_owner_idle=True)
UNSYNC = dict(available=True, synchronized=False)
GOOD = dict(ok=True, offset_s=OFFSET)


class DeciderTests(unittest.TestCase):
    def test_ntp_synchronized_or_small_offset_is_left_alone(self):
        decider = sync.Decider('step', 'boot')
        self.assertEqual(decider.decide(GOOD, dict(available=True, synchronized=True), OPEN, 0)['reason'], 'kernel_clock_synchronized')
        self.assertEqual(decider.decide(dict(ok=True, offset_s=0.4), UNSYNC, OPEN, 0)['reason'], 'within_tolerance')
        self.assertEqual(decider.decide(GOOD, dict(available=False), OPEN, 0)['action'], 'hold')

    def test_observe_mode_never_steps(self):
        self.assertEqual(sync.Decider('observe', 'boot').decide(GOOD, UNSYNC, OPEN, 0)['action'], 'observe_only')

    def test_any_activity_defers(self):
        decider = sync.Decider('step', 'boot')
        for gate in OPEN:
            decision = decider.decide(GOOD, UNSYNC, dict(OPEN, **{gate: False}), 0)
            self.assertEqual((decision['action'], decision['closed_gates']), ('defer', [gate]))

    def test_step_then_verify_cooldown_and_limit(self):
        decider = sync.Decider('step', 'boot')
        self.assertEqual(decider.decide(GOOD, UNSYNC, OPEN, 0)['action'], 'step')
        decider.stepped(1.0)
        self.assertEqual(decider.decide(dict(ok=True, offset_s=0.05), UNSYNC, OPEN, 5)['action'], 'verified')
        self.assertEqual(decider.decide(GOOD, UNSYNC, OPEN, 10)['reason'], 'step_cooldown')
        decider.steps = sync.MAX_STEPS_PER_BOOT
        self.assertEqual(decider.decide(GOOD, UNSYNC, OPEN, 10 ** 6)['reason'], 'step_limit_reached')
        decider = sync.Decider('step', 'boot')
        decider.stepped(0)
        self.assertEqual(decider.decide(dict(ok=True, offset_s=3.0), UNSYNC, OPEN, 5)['action'], 'residual_after_step')

    @unittest.skipUnless(hasattr(time, 'clock_settime'), 'POSIX clock_settime')
    def test_step_clock_applies_offset_once(self):
        calls = []
        with patch.object(sync.time, 'clock_settime', side_effect=lambda clock, value: calls.append(value)), \
             patch.object(sync.time, 'clock_gettime', return_value=1000.0):
            result = sync.step_clock(OFFSET)
        self.assertEqual(calls, [1000.0 + OFFSET])
        self.assertEqual(result['applied_offset_s'], OFFSET)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Path(tempfile.mkdtemp(prefix='ugts'))

    def tearDown(self):
        shutil.rmtree(self.runtime, ignore_errors=True)

    def gates(self, recorder, rover, mapping, native=True):
        (self.runtime / 'rover_bridge_status.json').write_text(json.dumps(rover))
        def get(url, timeout=0.5):
            return recorder if '8790' in url else mapping
        with patch.object(sync, '_json_get', side_effect=get), \
             patch.object(sync, 'native_owner_idle', return_value=native):
            return sync.activity_gates(self.runtime, 'http://127.0.0.1:8790/status',
                                       'http://127.0.0.1:8080/api/live/mapping-jobs', '/nonexistent')

    def test_all_idle_and_each_activity(self):
        idle_recorder = dict(state='idle', active=False, finalizing=False)
        idle_rover = dict(armed=False, moving=False, updated_at=time.time())
        idle_map = dict(ok=True, jobs=[dict(id='live', tracking=False, active=False, processing=False)])
        self.assertTrue(all(self.gates(idle_recorder, idle_rover, idle_map).values()))
        self.assertFalse(self.gates(dict(idle_recorder, state='recording', active=True), idle_rover, idle_map)['recorder_idle'])
        self.assertFalse(self.gates(idle_recorder, dict(idle_rover, armed=True), idle_map)['rover_inactive'])
        self.assertFalse(self.gates(idle_recorder, dict(idle_rover, updated_at=time.time() - 30), idle_map)['rover_inactive'])
        self.assertFalse(self.gates(idle_recorder, idle_rover, dict(idle_map, jobs=[dict(id='live', tracking=True)]))['mapping_idle'])
        self.assertFalse(self.gates(idle_recorder, idle_rover, dict(ok=True, jobs=[]))['mapping_idle'])
        self.assertFalse(self.gates(idle_recorder, idle_rover, idle_map, native=False)['native_owner_idle'])


@unittest.skipUnless(sys.platform.startswith('linux'), 'AF_UNIX native owner probe')
class NativeOwnerProbeTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='ugno'))
        self.path = self.dir / 'observe.sock'

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def serve(self, owned):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.path))
        server.listen(1)
        def reply():
            conn, _ = server.accept()
            with conn:
                conn.recv(4096)
                conn.sendall((json.dumps(dict(ok=True, status=dict(owner=dict(owned=owned)))) + '\n').encode())
        thread = threading.Thread(target=reply, daemon=True)
        thread.start()
        return server, thread

    def test_missing_stale_idle_and_owned(self):
        self.assertTrue(sync.native_owner_idle(self.path))
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(self.path))
        stale.close()  # file remains, nobody listens
        self.assertTrue(sync.native_owner_idle(self.path))
        os.unlink(self.path)
        for owned, expected in ((False, True), (True, False)):
            server, thread = self.serve(owned)
            try:
                self.assertEqual(sync.native_owner_idle(self.path), expected)
            finally:
                thread.join(2)
                server.close()
                os.unlink(self.path)


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux adjtimex')
class KernelStateTests(unittest.TestCase):
    def test_read_only_adjtimex_reports_a_state(self):
        state = sync.kernel_clock_state()
        self.assertTrue(state['available'])
        self.assertIsInstance(state['synchronized'], bool)


if __name__ == '__main__':
    unittest.main()
