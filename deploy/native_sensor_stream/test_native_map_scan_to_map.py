"""Scan-to-map bridge (C6): local map target, acceptance, alias rejection and ring policy.

Synthetic 2D scenes encoded as original LaserScan CDR (no physical accuracy claim).
The pinned solver and its gates are unchanged; these tests pin the map-side rules.
"""
import hashlib
import math
from pathlib import Path
import random
import struct
import sys
import threading
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'optimized-package'))
sys.path.insert(0, str(HERE))

import keyframe_registration as kr  # noqa: E402
import committed_native_map as cnm  # noqa: E402
from committed_native_map import (CommittedNativeMapAdapter, MAP_RING_MAX, MAP_RING_SPAN_NS,  # noqa: E402
                                  ring_digest)
from native_map_owner import RegisteredNativeBridge  # noqa: E402
from native_map_transport import NativeMapSession  # noqa: E402

BEAMS = 1080


def orchard(seed=4):
    """Irregular orchard block: two hedge lines, jittered trunks, a cross wall."""
    rng = random.Random(seed)
    segs = [((-20, -2.6), (30, -2.6)), ((-20, 2.6), (30, 2.6)), ((30, -6), (30, 6))]
    for k in range(-8, 14):
        x = k * 2.1 + rng.uniform(-.3, .3)
        for side in (-1.9, 1.9):
            segs += box(x, side + rng.uniform(-.15, .15), rng.uniform(.08, .16))
    return np.array(segs, dtype=float)


def periodic():
    """Exactly periodic corridor: identical trunks every 2.1 m between endless hedges."""
    segs = [((-80, -2.6), (80, -2.6)), ((-80, 2.6), (80, 2.6))]
    for k in range(-35, 35):
        for side in (-1.9, 1.9):
            segs += box(k * 2.1, side, .12)
    return np.array(segs, dtype=float)


def box(x, y, r):
    return [((x - r, y - r), (x + r, y - r)), ((x + r, y - r), (x + r, y + r)),
            ((x + r, y + r), (x - r, y + r)), ((x - r, y + r), (x - r, y - r))]


def ranges(segs, pose, max_range):
    x, y, a = pose
    angles = -math.pi + np.arange(BEAMS) * (2 * math.pi / BEAMS)
    d = np.stack([np.cos(angles + a), np.sin(angles + a)], 1)
    p, e, o = segs[:, 0], segs[:, 1] - segs[:, 0], np.array([x, y])
    best = np.full(BEAMS, np.inf)
    for i in range(len(segs)):
        denom = d[:, 0] * e[i, 1] - d[:, 1] * e[i, 0]
        w = p[i] - o
        with np.errstate(divide='ignore', invalid='ignore'):
            t = (w[0] * e[i, 1] - w[1] * e[i, 0]) / denom
            u = (w[0] * d[:, 1] - w[1] * d[:, 0]) / denom
        hit = (np.abs(denom) > 1e-12) & (t > .05) & (u >= 0) & (u <= 1)
        best = np.where(hit & (t < best), t, best)
    best[best > max_range] = np.inf
    return best


def scan_cdr(segs, pose, ns, max_range=19.):
    """Test-only CDR1 LaserScan encoder, used to create originals before use."""
    data = bytearray(b'\x00\x01\x00\x00')

    def raw(value, alignment=1):
        data.extend(b'\0' * ((-(len(data) - 4)) % alignment))
        data.extend(value)
    raw(struct.pack('<i', ns // 1_000_000_000), 4)
    raw(struct.pack('<I', ns % 1_000_000_000), 4)
    raw(struct.pack('<I', 6), 4)
    raw(b'laser\0')
    inc = 2 * math.pi / BEAMS
    raw(struct.pack('<7f', -math.pi, -math.pi + (BEAMS - 1) * inc, inc, 0., .1, .05, 30.), 4)
    r = ranges(segs, pose, max_range)
    raw(struct.pack('<I', BEAMS), 4)
    raw(struct.pack('<%df' % BEAMS, *r), 4)
    raw(struct.pack('<I', BEAMS), 4)
    raw(struct.pack('<%df' % BEAMS, *((1.,) * BEAMS)), 4)
    return bytes(data)


def relative(k, h):
    """Pose of h in k's frame."""
    c, s = math.cos(k[2]), math.sin(k[2])
    dx, dy = h[0] - k[0], h[1] - k[1]
    return (c * dx + s * dy, -s * dx + c * dy, math.atan2(math.sin(h[2] - k[2]), math.cos(h[2] - k[2])))


def target_for(segs, k_pose, history, max_range=19.):
    """MapBridgeTarget of K at k_pose plus earlier scans at the given world poses."""
    members = []
    for i, pose in enumerate([k_pose] + list(history)):
        cdr = scan_cdr(segs, pose, 1_000_000_000 + i, max_range)
        members.append((cdr, hashlib.sha256(cdr).hexdigest(), (0., 0., 0.) if i == 0 else relative(k_pose, pose)))
    return kr.MapBridgeTarget(members)


def current_for(segs, pose, max_range=19.):
    cdr = scan_cdr(segs, pose, 2_000_000_000, max_range)
    wire, geometry = kr.scan_geometry(cdr, hashlib.sha256(cdr).hexdigest())
    return geometry, kr.ScanPolar(wire)


class MapTargetContract(unittest.TestCase):
    def test_keyframe_first_and_exact_cdr(self):
        segs = orchard()
        cdr = scan_cdr(segs, (0., 0., 0.), 1)
        sha = hashlib.sha256(cdr).hexdigest()
        with self.assertRaisesRegex(ValueError, 'keyframe_first'):
            kr.MapBridgeTarget([(cdr, sha, (.1, 0., 0.))])
        with self.assertRaisesRegex(ValueError, 'keyframe_first'):
            kr.MapBridgeTarget([])
        with self.assertRaisesRegex(ValueError, 'exact_map_member_CDR'):
            kr.MapBridgeTarget([(cdr, '0' * 64, (0., 0., 0.))])
        with self.assertRaisesRegex(ValueError, 'exact_map_member_CDR'):
            kr.MapBridgeTarget([(bytearray(cdr), sha, (0., 0., 0.))])

    def test_target_is_k_first_thinned_and_frozen(self):
        segs = orchard()
        t = target_for(segs, (0., 0., 0.), [(-.2 * i, 0., 0.) for i in range(1, 6)])
        self.assertEqual(t.members, 6)
        self.assertFalse(t.surf.dense.flags.writeable)
        self.assertLessEqual(len(t.surf.points), kr.MAP_ANCHORS)
        keys = np.floor(t.surf.dense / kr.MAP_VOXEL_M).astype(np.int64)
        self.assertEqual(len(np.unique(keys, axis=0)), len(keys))
        # K's own returns are kept wherever K saw the cell.
        k_only = target_for(segs, (0., 0., 0.), [])
        k_keys = {tuple(v) for v in np.floor(k_only.surf.dense / kr.MAP_VOXEL_M).astype(np.int64)}
        mine = {tuple(v) for v in np.floor(t.k_dense / kr.MAP_VOXEL_M).astype(np.int64)}
        self.assertEqual(mine, k_keys)

    def test_polar_line_of_sight(self):
        segs = np.array([((3., -5.), (3., 5.))])
        cdr = scan_cdr(segs, (0., 0., 0.), 1)
        polar = kr.ScanPolar(kr.scan_geometry(cdr, hashlib.sha256(cdr).hexdigest())[0])
        seen = polar.observable(np.array([[2., 0.], [3., 0.], [5., 0.], [25., 0.]]))
        # In front of the wall and on it: visible. Behind it: occluded. Beyond 20 m: unknown.
        self.assertEqual(seen.tolist(), [True, True, False, False])


class ScanToMapRegistration(unittest.TestCase):
    def test_straight_gap_accepted_at_truth_with_seed_centre(self):
        segs = orchard()
        k = (0., 0., 0.)
        history = [(-.2 * i, .01 * i, -.002 * i) for i in range(1, 10)]
        h = (1.5, .05, .03)
        want = relative(k, h)
        geometry, polar = current_for(segs, h)
        fit = kr.register_map(target_for(segs, k, history), geometry, polar, max_wall_s=2.,
                              seed_center=(1.35, 0., 0.))
        self.assertTrue(fit['accepted'], fit.get('reason'))
        self.assertEqual(fit['reason'], 'unverified_bounded_native_scan_to_map_registration')
        self.assertFalse(fit['physical_accuracy_verified'])
        self.assertFalse(fit['search_global_uniqueness_verified'])
        self.assertEqual(fit['map_members'], 10)
        self.assertLess(math.hypot(fit['xy'][0] - want[0], fit['xy'][1] - want[1]), .05)
        self.assertLess(abs(fit['yaw'] - want[2]), math.radians(1))
        m = fit['map_metrics']
        self.assertGreaterEqual(min(m['F_vis'], m['B_visK']), kr.MAP_F_MIN)

    def test_periodic_corridor_never_accepts_a_wrong_period(self):
        # A seed centre one trunk period off: an exactly periodic corridor has
        # equally good minima at both shifts, so the bridge must reject or be right.
        segs = periodic()
        k = (0., 0., 0.)
        history = [(-.2 * i, 0., 0.) for i in range(1, 10)]
        for true_x, centre_x in ((.6, 2.7), (2.7, .6), (1.5, 1.5 + 2.1 - 1.)):
            geometry, polar = current_for(segs, (true_x, 0., 0.), max_range=8.)
            fit = kr.register_map(target_for(segs, k, history, max_range=8.), geometry, polar, max_wall_s=2.,
                                  seed_center=(float(centre_x), 0., 0.))
            if fit['accepted']:
                self.assertLess(abs(fit['xy'][0] - true_x), .05, (true_x, centre_x, fit['xy']))
            else:
                self.assertIn(fit['reason'], ('ambiguous_native_map', 'alias_outranks_native_map_best',
                                              'ambiguous_native_map_alias', 'unmatched_native_map'))

    def test_unrelated_scene_is_unmatched(self):
        k_segs, h_segs = orchard(4), np.array([((-1., -9.), (9., 1.)), ((4., -9.), (4., 9.))])
        geometry, polar = current_for(h_segs, (0., 0., 0.))
        fit = kr.register_map(target_for(k_segs, (0., 0., 0.), [(-.2, 0., 0.)]), geometry, polar, max_wall_s=2.)
        self.assertFalse(fit['accepted'])

    def test_budget_and_cancellation(self):
        segs = orchard()
        geometry, polar = current_for(segs, (.3, 0., 0.))
        target = target_for(segs, (0., 0., 0.), [])
        self.assertEqual(kr.register_map(target, geometry, polar, cancelled=lambda: True)['reason'],
                         'recovery_cancelled')
        for bad in (0., 2.5, float('nan'), '1'):
            with self.assertRaises(ValueError):
                kr.register_map(target, geometry, polar, max_wall_s=bad)
        with self.assertRaisesRegex(ValueError, 'owned_map_bridge_target'):
            kr.register_map(object(), geometry, polar)


class Frame:
    def __init__(self, context, stamp, sha):
        self.context, self.scan_stamps_ns, self.scan_cdr_sha256 = context, (stamp - 100_000_000, stamp), ('a' * 64, sha)


class State:
    def __init__(self, version, pose):
        self.version, self.pose = version, pose


def bare_adapter():
    a = CommittedNativeMapAdapter.__new__(CommittedNativeMapAdapter)
    a._ring, a._ring_digest, a._map_target = [], None, None
    return a


class RingPolicy(unittest.TestCase):
    CTX = ('session', 1, 'ROS_SYSTEM_TIME', 'CLOCK_MONOTONIC_RAW:d', 'epoch', 'map')

    def publish(self, a, n, start=10_000_000_000, step=100_000_000, bridge=None, context=None):
        out = []
        for i in range(n):
            cdr = b'scan-%d-%d' % (start, i)
            sha = hashlib.sha256(cdr).hexdigest()
            stamp = start + i * step
            a._extend_ring(Frame(context or self.CTX, stamp, sha), State(stamp, (float(i), 0., 0.)), cdr,
                           bridge if i == 0 else None)
            out.append((stamp, cdr))
        return out

    def test_every_second_scan_within_span_and_cap(self):
        a = bare_adapter()
        published = self.publish(a, 40)
        stamps = [e[2] for e in a._ring]
        self.assertEqual(stamps[-1], published[-1][0])
        self.assertLessEqual(stamps[-1] - stamps[0], MAP_RING_SPAN_NS)
        self.assertLessEqual(len(a._ring), MAP_RING_MAX)
        gaps = {b - c for b, c in zip(stamps[1:-1], stamps[:-2])}
        self.assertEqual(gaps, {200_000_000})
        self.assertEqual(a._ring_digest, ring_digest(a._ring))

    def test_registered_bridge_and_context_change_restart(self):
        a = bare_adapter()
        self.publish(a, 8)
        bridge = RegisteredNativeBridge(self.CTX, 'a' * 64, 'b' * 64, 1, (0., 0., 0.))
        self.publish(a, 1, start=20_000_000_000, bridge=bridge)
        self.assertEqual(len(a._ring), 1)
        self.assertEqual(a._ring[0][6], 'bridge')
        self.publish(a, 5, start=21_000_000_000)
        self.assertGreater(len(a._ring), 1)
        other = (*self.CTX[:4], 'other-epoch', 'map')
        self.publish(a, 1, start=22_000_000_000, context=other)
        self.assertEqual(len(a._ring), 1)

    def test_ring_must_end_at_k_and_target_is_sealed(self):
        a = bare_adapter()
        self.publish(a, 3)
        last = a._ring[-1]
        k = (last[0], last[1], last[3], last[4])
        self.assertTrue(a._ring_matches(k))
        copy = last[4][:-1] + last[4][-1:]
        self.assertIsNot(copy, last[4])
        self.assertFalse(a._ring_matches((last[0], last[1], last[3], copy)))
        self.assertFalse(a._ring_matches((last[0] + 1, last[1], last[3], last[4])))
        self.assertFalse(bare_adapter()._ring_matches(k))
        # A replaced ring (hashes intact) no longer matches the publish seal.
        a._ring = a._ring[1:]
        with self.assertRaisesRegex(ValueError, 'retained_map_ring_changed'):
            a._retained_map_target()
        # A changed retained CDR breaks its own hash before any target is built.
        a._ring = [(*e[:4], e[4] + b'x', *e[5:]) for e in a._ring]
        with self.assertRaisesRegex(ValueError, 'retained_map_ring_CDR_changed'):
            a._retained_map_target()

    def test_stop_keeps_ring_new_map_clears(self):
        a = bare_adapter()
        a._fence, a._cv, a._stop = threading.Lock(), threading.Condition(threading.RLock()), threading.Event()
        a._serial = 1
        a._pending = a._head = a._keyframe_motion = None
        a._capture_last = a._chain_token = a._chain_pose = a._chain_digest = None
        a.pose_ledger = type('L', (), dict(inhibit=lambda self: None))()
        a._map = type('M', (), dict(stop=lambda self, reason: None))()
        a.status = lambda: {}
        self.publish(a, 6)
        ring = list(a._ring)
        a.stop()
        self.assertEqual(a._ring, ring)
        a._clear_ring()
        self.assertEqual((a._ring, a._ring_digest, a._map_target), ([], None, None))


class FeatureSwitch(unittest.TestCase):
    def test_typed_default_off_adapter_and_session_enable(self):
        with self.assertRaisesRegex(ValueError, 'typed_scan_to_map_bridge'):
            CommittedNativeMapAdapter(None, scan_to_map_bridge_enabled=1)
        with self.assertRaisesRegex(ValueError, 'typed_scan_to_map_bridge'):
            NativeMapSession(CommittedNativeMapAdapter, map_epoch='e' * 32, scan_to_map_bridge_enabled='yes')
        self.assertTrue(NativeMapSession(CommittedNativeMapAdapter, map_epoch='e' * 32)._scan_to_map_bridge_enabled)
        import inspect
        default = inspect.signature(CommittedNativeMapAdapter.__init__).parameters['scan_to_map_bridge_enabled'].default
        self.assertIs(default, False)


if __name__ == '__main__':
    unittest.main()
