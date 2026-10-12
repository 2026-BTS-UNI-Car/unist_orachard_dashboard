"""Bridge search, retry policy and fences of the asynchronous historical map.

Synthetic 2D scenes only (no physical accuracy claim). The pinned solver and
its gates are used unchanged; these tests pin the map-side policy around it.
"""
import base64
import math
from pathlib import Path
import random
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'optimized-package'))
sys.path.insert(0, str(HERE))

from kernel.raw_localization import scan_points, surfaces  # noqa: E402
import keyframe_registration as kr  # noqa: E402
from committed_native_map import (CommittedNativeMapAdapter, BRIDGE_FREE_ATTEMPTS,  # noqa: E402
                                  BRIDGE_BACKOFF_BASE_NS, BRIDGE_BACKOFF_MAX_NS, SEED_CENTER_MAX_GAP_NS)
from native_map_transport import NativeMapTransport, encode_cells, encode_tile_bytes, tile_facts  # noqa: E402


def segments():
    """Orchard-like block: two hedge lines, irregular trunks, a cross wall."""
    rng = random.Random(4)
    segs = [((-20, -2.6), (30, -2.6)), ((-20, 2.6), (30, 2.6)), ((30, -6), (30, 6))]
    for k in range(-8, 14):
        x = k * 2.1 + rng.uniform(-.3, .3)
        for side in (-1.9, 1.9):
            y = side + rng.uniform(-.15, .15)
            r = rng.uniform(.08, .16)
            segs += [((x - r, y - r), (x + r, y - r)), ((x + r, y - r), (x + r, y + r)),
                     ((x + r, y + r), (x - r, y + r)), ((x - r, y + r), (x - r, y - r))]
    return np.array(segs, dtype=float)


SEGS = segments()


def scan(pose, beams=1080, max_range=19.):
    x, y, a = pose
    angles = -math.pi + np.arange(beams) * (2 * math.pi / beams)
    d = np.stack([np.cos(angles + a), np.sin(angles + a)], 1)
    p, q = SEGS[:, 0], SEGS[:, 1]
    e = q - p
    o = np.array([x, y])
    best = np.full(beams, np.inf)
    for i in range(len(SEGS)):
        denom = d[:, 0] * e[i, 1] - d[:, 1] * e[i, 0]
        w = p[i] - o
        with np.errstate(divide='ignore', invalid='ignore'):
            t = (w[0] * e[i, 1] - w[1] * e[i, 0]) / denom
            u = (w[0] * d[:, 1] - w[1] * d[:, 0]) / denom
        hit = (np.abs(denom) > 1e-12) & (t > .05) & (u >= 0) & (u <= 1)
        best = np.where(hit & (t < best), t, best)
    best[best > max_range] = np.inf
    msg = dict(ranges=[float(v) for v in best], angle_min=-math.pi, angle_increment=2 * math.pi / beams,
               range_min=.05, range_max=30.)
    return kr.freeze_geometry(surfaces(scan_points(msg)))


def relative(k, h):
    """Pose of h in k's frame: p_k = R(yaw) p_h + xy."""
    c, s = math.cos(k[2]), math.sin(k[2])
    dx, dy = h[0] - k[0], h[1] - k[1]
    return (c * dx + s * dy, -s * dx + c * dy, math.atan2(math.sin(h[2] - k[2]), math.cos(h[2] - k[2])))


class SeedCentredRegistration(unittest.TestCase):
    def test_identity_grid_alone_cannot_reach_beyond_its_bound(self):
        # Beyond the 0.8 m identity bound the true shift is unreachable. In
        # this trunk-periodic scene the identity grid can even converge to an
        # aliased shift one trunk spacing away; it must never return the truth.
        k, h = (0., 0., 0.), (1.5, .05, .03)
        want = relative(k, h)
        fit = kr.register_keyframe(scan(k), scan(h), max_wall_s=2.)
        if fit['accepted']:
            self.assertGreater(math.hypot(fit['xy'][0] - want[0], fit['xy'][1] - want[1]), 1.)

    def test_shifted_grid_bound_reaches_its_centre(self):
        k, h = (0., 0., 0.), (1.5, .05, .03)
        want = relative(k, h)
        fit = kr.register_keyframe(scan(k), scan(h), max_wall_s=2., seed_center=(1.35, 0., 0.))
        self.assertTrue(fit['accepted'], fit.get('reason'))
        self.assertTrue(fit['shifted_seed_grid'])
        self.assertLess(math.hypot(fit['xy'][0] - want[0], fit['xy'][1] - want[1]), .05)
        self.assertLess(abs(fit['yaw'] - want[2]), math.radians(1))

    def test_centre_outside_gate_adds_no_grid(self):
        target = scan((0., 0., 0.))
        for centre in ((3.5, 0., 0.), (0., 0., math.radians(95)), (.1, 0., math.radians(2))):
            fit = kr.register_keyframe(target, target, max_wall_s=2., seed_center=centre)
            self.assertFalse(fit['shifted_seed_grid'], centre)

    def test_seed_centre_must_be_finite_floats(self):
        target = scan((0., 0., 0.))
        for bad in ((1, 0., 0.), (float('nan'), 0., 0.), (1., 0.)):
            with self.assertRaises(ValueError):
                kr.register_keyframe(target, target, max_wall_s=2., seed_center=bad)

    def test_aliased_shift_is_rejected_as_ambiguous(self):
        # Trunks every 2.1 m and parallel hedges: a corridor with no unique
        # along-track feature must not be accepted at either period.
        rng_free = [((-60, -2.6), (60, -2.6)), ((-60, 2.6), (60, 2.6))]
        posts = []
        for k in range(-25, 25):
            x = k * 2.1
            for y in (-1.9, 1.9):
                r = .12
                posts += [((x - r, y - r), (x + r, y - r)), ((x + r, y - r), (x + r, y + r)),
                          ((x + r, y + r), (x - r, y + r)), ((x - r, y + r), (x - r, y - r))]
        global SEGS
        saved = SEGS
        try:
            SEGS = np.array(rng_free + posts, dtype=float)
            fit = kr.register_keyframe(scan((0., 0., 0.), max_range=8.), scan((1.05, 0., 0.), max_range=8.),
                                       max_wall_s=2., seed_center=(1.05, 0., 0.))
            if fit['accepted']:
                # A unique answer is acceptable only if it is the true shift.
                self.assertLess(abs(fit['xy'][0] - 1.05), .05)
        finally:
            SEGS = saved


def bare_adapter():
    """Adapter object for policy methods only (no factory, no worker)."""
    a = CommittedNativeMapAdapter.__new__(CommittedNativeMapAdapter)
    a._keyframe_motion = None
    a._bridge_retry = None
    a._keyframe_geometry = None
    return a


class Frame:
    def __init__(self, context, stamps, delta):
        self.context, self.scan_stamps_ns, self.current_to_previous = context, stamps, delta


class AdapterBridgePolicy(unittest.TestCase):
    CTX = ('s', 1, 'ROS_SYSTEM_TIME', 'CLOCK_MONOTONIC_RAW:x', 'e' * 32, 'laser')

    def test_seed_centre_is_the_se2_exponential_of_the_lidar_twist(self):
        a = bare_adapter()
        a._keyframe_motion = (self.CTX, (0, 100_000_000), (.09, 0., .02))
        f = Frame(self.CTX, (900_000_000, 1_000_000_000), (.09, 0., .02))
        cx, cy, ca = a._seed_center(f)
        vx, w, gap = .9, .2, 0.9
        theta = w * gap
        self.assertAlmostEqual(ca, theta, places=9)
        self.assertAlmostEqual(cx, math.sin(theta) / w * vx, places=9)
        self.assertAlmostEqual(cy, (1 - math.cos(theta)) / w * vx, places=9)

    def test_no_centre_across_context_change_long_gap_or_without_motion(self):
        a = bare_adapter()
        f = Frame(self.CTX, (900_000_000, 1_000_000_000), (.09, 0., 0.))
        self.assertIsNone(a._seed_center(f))
        a._keyframe_motion = (self.CTX[:4] + ('other', 'laser'), (0, 100_000_000), (.09, 0., 0.))
        self.assertIsNone(a._seed_center(f))
        a._keyframe_motion = (self.CTX, (0, 100_000_000), (.09, 0., 0.))
        far = Frame(self.CTX, (100_000_000 + SEED_CENTER_MAX_GAP_NS, 200_000_000 + SEED_CENTER_MAX_GAP_NS), (.09, 0., 0.))
        self.assertIsNone(a._seed_center(far))
        self.assertIsNotNone(a._seed_center(f))

    def test_retry_backoff_is_bounded_and_resets_per_keyframe(self):
        a = bare_adapter()
        k1, k2 = (5, 'a' * 64, (0., 0., 0.), b''), (6, 'b' * 64, (0., 0., 0.), b'')
        stamp = 10_000_000_000
        waits = []
        for n in range(12):
            f = Frame(self.CTX, (stamp - 100_000_000, stamp), (0., 0., 0.))
            self.assertTrue(a._bridge_allowed(k1, f))
            a._bridge_failed(k1, f, 'unmatched_native_keyframe')
            waits.append(a._bridge_retry['next_scan_ns'] - stamp)
            stamp = a._bridge_retry['next_scan_ns']
            early = Frame(self.CTX, (stamp - 200_000_000, stamp - 1), (0., 0., 0.))
            if waits[-1]:
                self.assertFalse(a._bridge_allowed(k1, early))
        # Attempts 1..BRIDGE_FREE_ATTEMPTS run back to back; the wait starts after the last free failure.
        self.assertEqual(waits[:BRIDGE_FREE_ATTEMPTS - 1], [0] * (BRIDGE_FREE_ATTEMPTS - 1))
        self.assertEqual(waits[BRIDGE_FREE_ATTEMPTS - 1], BRIDGE_BACKOFF_BASE_NS)
        self.assertEqual(max(waits), BRIDGE_BACKOFF_MAX_NS)
        self.assertTrue(all(b >= c for b, c in zip(waits[BRIDGE_FREE_ATTEMPTS:], waits[BRIDGE_FREE_ATTEMPTS - 1:])))
        f = Frame(self.CTX, (stamp - 100_000_000, stamp - 50), (0., 0., 0.))
        self.assertTrue(a._bridge_allowed(k2, f))

    def test_retained_geometry_is_sealed_and_rewrapped(self):
        a = bare_adapter()
        g = scan((0., 0., 0.))
        k = (1, 'c' * 64, (0., 0., 0.), b'')
        a._keyframe_geometry = (k, g, kr.geometry_digest(g))
        first, seal = a._retained_geometry(k)
        second, _ = a._retained_geometry(k)
        self.assertIsNot(first, second)
        self.assertIs(first.points, g.points)
        self.assertFalse(first.points.flags.writeable)
        # A rebinding on a returned wrapper does not reach the retained copy.
        first.points = first.points[:10]
        third, _ = a._retained_geometry(k)
        self.assertEqual(len(third.points), len(g.points))
        # A change to the retained copy itself breaks the seal.
        g.points = g.points[:-1]
        with self.assertRaises(ValueError):
            a._retained_geometry(k)


class TransportSummaryFacts(unittest.TestCase):
    def test_numpy_encoding_equals_reference_encoder(self):
        rng = random.Random(3)
        cases = [bytes(4096), bytes([1]) * 4096, bytes([2]) * 4096, bytes([0, 1, 2, 1] * 1024)]
        for n in (2, 50, 170, 171, 172, 175, 200, 400):
            a = bytearray(4096)
            for j in sorted(rng.sample(range(1, 4096), n)):
                a[j:] = bytes([rng.randrange(3)]) * (4096 - j)
            cases.append(bytes(a))
        for _ in range(60):
            cases.append(bytes(rng.choice((0, 0, 1, 2)) for _ in range(4096)))
        for states in cases:
            self.assertEqual(encode_tile_bytes(states), encode_cells(list(states)))

    def test_facts_are_derived_from_states(self):
        a = bytearray(4096)
        a[(3 * 64) + 5] = 1
        a[(10 * 64) + 40] = 2
        value, count, box, occupied = tile_facts(bytes(a))
        self.assertEqual((count, box, occupied), (2, (5, 3, 40, 10), (40, 10, 40, 10)))
        with self.assertRaises(ValueError):
            tile_facts(bytes(4095) + b'\x03')

    def test_summary_must_match_derived_facts_and_empty_generation_reason(self):
        a = bytearray(4096)
        a[(3 * 64) + 5] = 1
        a[(10 * 64) + 40] = 2
        states = bytes(a)
        good = (1, -1, states, 2, (64 + 5, -64 + 3, 64 + 40, -64 + 10), (64 + 40, -64 + 10, 64 + 40, -64 + 10))
        transport = NativeMapTransport(type('S', (), dict(display_snapshot=lambda s: {}, status=lambda s: {}))(),
                                       push_only=True)
        base = dict(schema='ubts.native.laser_local.complete_display.v1', map_version=1, resolution_m=.1,
                    context=dict(source_session='s', generation=1, source_clock_domain='a', receipt_clock_domain='b',
                                 map_epoch='e' * 32, map_frame='f'),
                    registered_context_transition=None, historical_laser_pose=(0., 0., 0.),
                    latest_scan_source_ns=1, latest_scan_callback_RAW_ns=1, latest_scan_CDR_sha256='a' * 64,
                    retained_historical_generation=True, live_input_eligible=False, runtime_authority=False,
                    motor_command_sent=False, execution_eligible=False, physical_accuracy_verified=False,
                    global_last_row_confirmed=False)
        transport._build(dict(base, tiles=(good,), cell_count=2))
        loose = good[:4] + ((64 + 4, -64 + 3, 64 + 40, -64 + 10),) + good[5:]
        wrong_occupied = good[:5] + ((64 + 5, -64 + 3, 64 + 40, -64 + 10),)
        for row, reason in ((loose, 'native_tile_bounds_required'),
                            (wrong_occupied, 'native_tile_occupied_bounds_required'),
                            (good[:3] + (3,) + good[4:], 'native_tile_states_required')):
            with self.assertRaises(ValueError) as caught:
                transport._build(dict(base, tiles=(row,), cell_count=row[3]))
            self.assertEqual(str(caught.exception), reason)
        with self.assertRaises(ValueError) as caught:
            transport._build(dict(base, tiles=(), cell_count=0))
        self.assertEqual(str(caught.exception), 'native_map_cells_capacity')
        far = (2**24, 0, states, 2, (2**30 + 5, 3, 2**30 + 40, 10), (2**30 + 40, 10, 2**30 + 40, 10))
        with self.assertRaises(ValueError):
            transport._build(dict(base, tiles=(far,), cell_count=2))


if __name__ == '__main__':
    unittest.main()
