"""C1b+G reject-only bridge gates (keyframe registration, gates=True; default OFF).

Synthetic 2D scenes only (no physical accuracy claim). The gates may only turn an
accept into a reject; the accepted pose never changes.
"""
import inspect
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
from committed_native_map import CommittedNativeMapAdapter  # noqa: E402
from native_map_transport import NativeMapSession  # noqa: E402

BEAMS = 1080


def box(x, y, r):
    return [((x - r, y - r), (x + r, y - r)), ((x + r, y - r), (x + r, y + r)),
            ((x + r, y + r), (x - r, y + r)), ((x - r, y + r), (x - r, y - r))]


def orchard(seed=4):
    rng = random.Random(seed)
    segs = [((-20, -2.6), (30, -2.6)), ((-20, 2.6), (30, 2.6)), ((30, -6), (30, 6))]
    for k in range(-8, 14):
        x = k * 2.1 + rng.uniform(-.3, .3)
        for side in (-1.9, 1.9):
            segs += box(x, side + rng.uniform(-.15, .15), rng.uniform(.08, .16))
    return np.array(segs, dtype=float)


def clutter(seed=7):
    """Irregular boxes and angled walls: translation and rotation are well constrained."""
    rng = random.Random(seed)
    segs = [((-12, -9), (-4, -11)), ((6, -10), (14, -6)), ((-13, 8), (-6, 11)), ((5, 9), (13, 12))]
    for _ in range(45):
        x, y = rng.uniform(-14, 16), rng.uniform(-9, 9)
        if abs(y) < 1.5 and -3 < x < 6:
            continue
        segs += box(x, y, rng.uniform(.1, .5))
    return np.array(segs, dtype=float)


def corridor():
    """Two long featureless walls: translation along the corridor is unobservable."""
    return np.array([((-60, -2.), (60, -2.)), ((-60, 2.), (60, 2.))], dtype=float)


def curved_rows(radius=30., spacing=2.6):
    """Identical posts on two concentric arcs: a rotation about the centre maps the rows onto themselves."""
    segs = []
    step = spacing / radius
    for k in range(-25, 26):
        a = k * step
        for r in (radius - 1.9, radius + 1.9):
            segs += box(r * math.sin(a), radius - r * math.cos(a), .12)
    return np.array(segs, dtype=float)


def scan(segs, pose, max_range=19.):
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
    msg = dict(ranges=[float(v) for v in best], angle_min=-math.pi, angle_increment=2 * math.pi / BEAMS,
               range_min=.05, range_max=30.)
    return kr.freeze_geometry(surfaces(scan_points(msg)))


def relative(k, h):
    c, s = math.cos(k[2]), math.sin(k[2])
    dx, dy = h[0] - k[0], h[1] - k[1]
    return (c * dx + s * dy, -s * dx + c * dy, math.atan2(math.sin(h[2] - k[2]), math.cos(h[2] - k[2])))


def both(target, current, **kw):
    return (kr.register_keyframe(target, current, max_wall_s=2., **kw),
            kr.register_keyframe(target, current, max_wall_s=2., gates=True, **kw))


class Switch(unittest.TestCase):
    def test_default_off_and_typed(self):
        self.assertIs(inspect.signature(kr.register_keyframe).parameters['gates'].default, False)
        self.assertIs(inspect.signature(CommittedNativeMapAdapter.__init__)
                      .parameters['keyframe_bridge_gates_enabled'].default, False)
        with self.assertRaisesRegex(ValueError, 'typed_keyframe_bridge_gates'):
            CommittedNativeMapAdapter(None, keyframe_bridge_gates_enabled=1)
        with self.assertRaisesRegex(ValueError, 'typed_keyframe_bridge_gates'):
            NativeMapSession(CommittedNativeMapAdapter, map_epoch='e' * 32, keyframe_bridge_gates_enabled='on')
        self.assertFalse(NativeMapSession(CommittedNativeMapAdapter, map_epoch='e' * 32)._keyframe_bridge_gates_enabled)


class RejectOnly(unittest.TestCase):
    CASES = [
        (orchard, (0., 0., 0.), (.4, .03, .02), None),
        (orchard, (0., 0., 0.), (1.5, .05, .03), (1.35, 0., 0.)),
        (orchard, (2., -.3, .1), (2.3, -.2, .15), None),
        (clutter, (0., 0., 0.), (1.2, .1, .05), (1.1, 0., 0.)),
        (corridor, (0., 0., 0.), (1., 0., 0.), (1., 0., 0.)),
        (curved_rows, (0., 0., 0.), (1., 0., math.radians(2)), (1., 0., math.radians(2))),
    ]

    def test_gates_never_add_an_accept_or_move_a_pose(self):
        for make, k, h, centre in self.CASES:
            segs = make()
            off, on = both(scan(segs, k), scan(segs, h), seed_center=centre)
            if on['accepted']:
                self.assertTrue(off['accepted'], make.__name__)
                self.assertEqual(float(on['yaw']), float(off['yaw']))
                self.assertTrue(np.array_equal(on['xy'], off['xy']))

    def test_gates_off_is_the_unchanged_bridge(self):
        segs = orchard()
        target, current = scan(segs, (0., 0., 0.)), scan(segs, (1.5, .05, .03))
        a = kr.register_keyframe(target, current, max_wall_s=2., seed_center=(1.35, 0., 0.))
        b = kr.register_keyframe(target, current, max_wall_s=2., seed_center=(1.35, 0., 0.), gates=False)
        self.assertEqual((a['accepted'], a['reason']), (b['accepted'], b['reason']))
        self.assertTrue(np.array_equal(a['xy'], b['xy']))

    def test_irregular_scene_truth_still_accepted(self):
        segs = clutter()
        k, h = (0., 0., 0.), (1.5, .05, .03)
        _, on = both(scan(segs, k), scan(segs, h), seed_center=(1.35, 0., 0.))
        self.assertTrue(on['accepted'], on.get('reason'))
        want = relative(k, h)
        self.assertLess(math.hypot(on['xy'][0] - want[0], on['xy'][1] - want[1]), .05)


class Gates(unittest.TestCase):
    def test_featureless_corridor_is_weakly_constrained(self):
        segs = corridor()
        target, current = scan(segs, (0., 0., 0.)), scan(segs, (1., 0., 0.))
        fit = dict(xy=np.array([1., 0.]), yaw=0.)
        self.assertLess(kr._weak_normal(target, current, fit), kr.GATE_MIN_WEAK_NORMAL)
        _, on = both(target, current, seed_center=(1., 0., 0.))
        self.assertFalse(on['accepted'])

    def test_irregular_scene_is_well_constrained(self):
        segs = clutter()
        target, current = scan(segs, (0., 0., 0.)), scan(segs, (.4, .03, .02))
        fit = kr.register_keyframe(target, current, max_wall_s=2.)
        self.assertTrue(fit['accepted'])
        self.assertGreaterEqual(kr._weak_normal(target, current, fit), kr.GATE_MIN_WEAK_NORMAL)

    def test_curved_rows_never_accept_a_rotated_period(self):
        segs = curved_rows()
        period = 2.6 / 30.
        k = (0., 0., 0.)
        for true_a in (period * .4, period * .6):
            # True pose: rotate about the arc centre (0, 30) by true_a.
            h = (30. * math.sin(true_a), 30. - 30. * math.cos(true_a), true_a)
            wrong = (30. * math.sin(true_a - period), 30. - 30. * math.cos(true_a - period), true_a - period)
            off, on = both(scan(segs, k, max_range=8.), scan(segs, h, max_range=8.),
                           seed_center=tuple(float(v) for v in relative(k, wrong)))
            want = relative(k, h)

            def right(f):
                return (math.hypot(f['xy'][0] - want[0], f['xy'][1] - want[1]) < .1 and
                        abs(f['yaw'] - want[2]) < math.radians(1))
            if off['accepted'] and not right(off):
                # The ungated bridge takes the rotated period here; the gates must reject it.
                self.assertFalse(on['accepted'], on.get('reason'))
            if on['accepted']:
                self.assertTrue(right(on))


if __name__ == '__main__':
    unittest.main()
