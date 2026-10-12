"""Incremental tile state and tile transport must equal the full rebuild.

The reference below is the previous algorithm verbatim: copy every cell,
apply the free/occupied rule, sort all rows, and re-encode every tile from
sorted rows. Each committed generation is compared exactly.
"""
import math
import random
import unittest

import numpy as np

from native_map_owner import (NativeAcceptedMapOwner, AcceptedNativeFrame, _Cell, _TiledCells,
                              SOURCE_AGE_NS, RAW_AGE_NS)
from native_map_transport import NativeMapTransport
from batched_map_owner import BatchedNativeAcceptedMapOwner
from batched_rays import raster_ray_cells, _scalar_rays

CTX = ('session-a', 3, 'ROS_SYSTEM_TIME', 'CLOCK_MONOTONIC_RAW:test', 'e' * 32, 'unverified_native_laser_local')


def sha(n):
    return format(n + 1, '064x')


def frame(seq, delta, rng, beams=360, far=9.5):
    stamp = 1_000_000_000_000 + seq * 100_000_000
    ranges = []
    for i in range(beams):
        r = rng.random()
        ranges.append(math.inf if r < .05 else float('nan') if r < .07 else round(rng.uniform(.3, far), 3))
    return AcceptedNativeFrame(CTX, seq, (stamp - 100_000_000, stamp), (stamp - 90_000_000, stamp + 10_000_000),
                               (sha(seq - 1), sha(seq)), 'a1' * 16, stamp - 5_000_000, stamp + 1_000_000,
                               sha(10_000 + seq), 'b2' * 16, 'laser', tuple(ranges), -math.pi,
                               2 * math.pi / beams, .05, 12., 1e-4, delta)


class Reference:
    """Previous full-copy owner update and complete row ordering."""
    def __init__(self):
        self.cells = {}

    def apply(self, f, free, hits):
        cells = dict(self.cells)
        new = _Cell(0, f.scan_stamps_ns[1], f.scan_receipts_ns[1], f.context)
        for key in free:
            if key not in cells or cells[key].kind == 0:
                cells[key] = new
        new = _Cell(100, f.scan_stamps_ns[1], f.scan_receipts_ns[1], f.context)
        for key in hits:
            cells[key] = new
        self.cells = cells
        return tuple((x, y, c.kind, c.source_ns, c.receipt_ns) for (x, y), c in sorted(cells.items()))


def old_proposal(cells, gx, gy, n, lineage, now_s, now_r):
    """The previous per-cell proposal raster loop, verbatim."""
    array = np.full((n, n), -1, dtype=np.int8)
    source_floor = []
    for (x, y), cell in cells.items():
        fresh = 0 <= now_s - cell.source_ns < SOURCE_AGE_NS and 0 <= now_r - cell.receipt_ns < RAW_AGE_NS
        if cell.lineage == lineage and gx <= x < gx + n and gy <= y < gy + n and fresh:
            array[y - gy, x - gx] = cell.kind
            source_floor.append(cell.source_ns)
    return array, (min(source_floor) if source_floor else None)


class IncrementalMapParity(unittest.TestCase):
    def make_owner(self, batched):
        kwargs = dict(enabled=True, historical_display_only=True, accepted_native_validator=lambda f: True,
                      registered_bridge_validator=lambda b, f: True, resolution_m=.1)
        if batched:
            return BatchedNativeAcceptedMapOwner(batched_rays_enabled=True, **kwargs)
        return NativeAcceptedMapOwner(**kwargs)

    def run_sequence(self, seed, steps, batched=False):
        rng = random.Random(seed)
        owner = self.make_owner(batched)
        owner.start(CTX, explicit=True)
        reference = Reference()
        fast, full = NativeMapTransport(owner, push_only=True), NativeMapTransport(owner, push_only=True)
        for seq in range(1, steps + 1):
            delta = (rng.uniform(-.3, .3), rng.uniform(-.2, .2), rng.uniform(-.2, .2))
            f = frame(seq, delta, rng)
            ticket = owner.prepare(f, source_now_ns=0, receipt_now_ns=0)
            self.assertIsNotNone(ticket)
            self.assertTrue(owner.commit(ticket, source_now_ns=0, receipt_now_ns=0))
            state = owner._state
            # Unchanged scalar oracle for the pose this generation used.
            expected = reference.apply(f, *NativeAcceptedMapOwner._rays(owner, f, state.pose))
            self.assertEqual(state.cell_rows, expected)
            self.assertEqual(len(state.cells), len(expected))
            self.assertEqual(owner.status()['historical_cell_count'], len(expected))
            rows_snapshot = owner.display_snapshot()
            tile_snapshot = owner.display_snapshot(tiles=True)
            self.assertNotIn('cells', tile_snapshot)
            self.assertEqual(rows_snapshot['cells'], expected)
            self.assertEqual(full._build(rows_snapshot), fast._build(tile_snapshot))
            # Window view sees exactly the cells a full scan of the map selects.
            n = 64 + seq
            gx, gy = math.floor(state.pose[0] / .1) - n // 2, math.floor(state.pose[1] / .1) - n // 2
            want = sorted(k for k in reference.cells if gx <= k[0] < gx + n and gy <= k[1] < gy + n)
            got = sorted(k for k, _ in state.cells.window_items(gx, gy, n) if gx <= k[0] < gx + n and gy <= k[1] < gy + n)
            self.assertEqual(got, want)
            stamp = f.scan_stamps_ns[1]
            for now in (stamp, stamp + 120_000_000, stamp + 240_000_000, stamp + 900_000_000):
                for lineage in (CTX, CTX[:4] + ('other', CTX[5])):
                    a, low = state.cells.proposal_window(gx, gy, n, lineage, now, now + 30_000_000)
                    b, low_ref = old_proposal(state.cells, gx, gy, n, lineage, now, now + 30_000_000)
                    self.assertTrue(np.array_equal(a, b))
                    self.assertEqual(low, low_ref)
        return owner, fast

    def test_generations_equal_full_rebuild(self):
        for seed in (1, 2, 3):
            self.run_sequence(seed, 25)

    def test_batched_generations_equal_full_rebuild(self):
        for seed in (4, 5):
            self.run_sequence(seed, 25, batched=True)

    def test_numpy_raster_equals_scalar_oracle(self):
        rng = random.Random(9)
        for seq in range(1, 30):
            f = frame(seq, (0., 0., 0.), rng, beams=720, far=11.5)
            pose = (rng.uniform(-40, 40), rng.uniform(-40, 40), rng.uniform(-math.pi, math.pi))
            free, hits = raster_ray_cells(f, pose, resolution_m=.1, max_cells=250_000, max_ray_visits=4_096_000)
            ref_free, ref_hits = _scalar_rays(f, pose, .1, 250_000, 4_096_000, None)
            self.assertEqual(set(map(tuple, free.tolist())), ref_free)
            self.assertEqual(set(map(tuple, hits.tolist())), ref_hits)
            self.assertEqual(len(free), len(ref_free))

    def test_unchanged_tiles_reuse_encoding(self):
        owner, fast = self.run_sequence(7, 6)
        before = dict(fast._tile_cache)
        fast._build(owner.display_snapshot(tiles=True))
        self.assertTrue(all(fast._tile_cache[k] is before[k] for k in fast._tile_cache if k in before))
        self.assertEqual(set(fast._tile_cache), {t.states_bytes for t in owner._state.cells.tiles.values()})

    def test_free_never_erases_occupied(self):
        old, added = _TiledCells().with_evidence(np.zeros((0, 2), np.int64), np.array([[5, -3]]), 1, 1, CTX)
        self.assertEqual(added, 1)
        again, added = old.with_evidence(np.array([[5, -3], [6, -3]]), np.zeros((0, 2), np.int64), 2, 2, CTX)
        self.assertEqual(added, 1)
        self.assertEqual(again.get((5, -3)), _Cell(100, 1, 1, CTX))
        self.assertEqual(again.get((6, -3)), _Cell(0, 2, 2, CTX))
        self.assertIs(old.tiles[(0, -1)].states.flags.writeable, False)
        tile = old.tiles[(0, -1)]
        self.assertEqual(tile.states[(-3 & 63) * 64 + 5], 2)
        self.assertEqual(tile.occupied, (5, -3, 5, -3))
        self.assertEqual(tile.bounds, (5, -3, 5, -3))
        self.assertEqual(old.get((5, -3)).kind, 100)
        self.assertIsNone(old.get((6, -3)))
        self.assertNotIn((6, -3), old)
        self.assertEqual(len(old), 1)

    def test_rejects_inconsistent_tile_summaries(self):
        owner, fast = self.run_sequence(11, 3)
        good = owner.display_snapshot(tiles=True)
        tx, ty, states, count, bounds, occupied = good['tiles'][0]
        bad_rows = [
            (tx, ty, states, count + 1, bounds, occupied),
            (tx, ty, states[:-1] + b'\x03', count, bounds, occupied),
            (tx, ty, states, count, (bounds[0] - 64, *bounds[1:]), occupied),
        ]
        for row in bad_rows:
            snapshot = dict(good, tiles=(row,) + good['tiles'][1:])
            with self.assertRaises(ValueError):
                fast._build(snapshot)
        with self.assertRaises(ValueError):
            fast._build(dict(good, tiles=tuple(reversed(good['tiles']))))
        with self.assertRaises(ValueError):
            fast._build(dict(good, cell_count=good['cell_count'] + 1))


if __name__ == '__main__':
    unittest.main()
