"""Independent tile-key oracle and retained signed map equality with native C."""
import os
import unittest
from unittest.mock import patch

import numpy as np

from dashboard_server import native_raster, tiled_occupancy
from dashboard_server.tile_transport import StreamingOccupancyMap


def scalar_keys(values):
    return np.asarray(sorted({(int(x), int(y)) for x, y in values}), dtype=np.int64).reshape(-1, 2)


class NativeTouchedTileTests(unittest.TestCase):
    def test_signed_order_duplicates_strided_input_and_owned_result(self):
        values = np.array([(x, y) for x in (-65, -64, -1, 0, 63, 64)
                           for y in (-129, -64, 0, 64, 127)], dtype=np.int64) // 64
        values = np.concatenate((values, values[::-1], values[:5]))[::2]
        saved = values.copy()
        result = tiled_occupancy._native_touched_tile_keys(values)
        np.testing.assert_array_equal(result, scalar_keys(values))
        result[0] = [100, 100]
        np.testing.assert_array_equal(values, saved)

    def test_random_membership_matches_independent_signed_set(self):
        rng = np.random.default_rng(10607)
        for _ in range(200):
            nx = int(rng.integers(1, 129))
            ny = int(rng.integers(1, min(128, 4096 // nx) + 1))
            low = rng.integers(-100000, 100001, 2)
            values = rng.integers([0, 0], [nx, ny], (int(rng.integers(1, 1500)), 2)) + low
            np.testing.assert_array_equal(tiled_occupancy._native_touched_tile_keys(values), scalar_keys(values))

    def test_exact_extent_and_int32_boundaries(self):
        for nx, ny in ((128, 32), (32, 128), (64, 64), (1, 128), (128, 1)):
            for low in (-2147483648, 2147483520):
                values = np.array([[low, low], [low + nx - 1, low + ny - 1]], dtype=np.int64)
                np.testing.assert_array_equal(tiled_occupancy._native_touched_tile_keys(values), scalar_keys(values))
        for nx, ny in ((129, 1), (1, 129), (128, 33), (33, 128), (65, 64)):
            self.assertIsNone(tiled_occupancy._native_touched_tile_keys(np.array([[0, 0], [nx - 1, ny - 1]], dtype=np.int64)))

    def test_invalid_inputs_reject_before_bitmap_allocation(self):
        values = [[], np.empty((0, 2), np.int64), np.ones((2, 2), np.float64),
                  np.ones((2, 2), np.int32), np.ones((2, 2), bool), np.ones((2, 3), np.int64),
                  np.ones(2, np.int64), np.array([[-2147483649, 0]], np.int64),
                  np.array([[2147483648, 0]], np.int64),
                  np.array([[-2**63, 0], [2**63 - 1, 0]], np.int64)]
        for value in values:
            with patch.object(tiled_occupancy.np, 'zeros', side_effect=AssertionError('invalid bitmap allocated')):
                self.assertIsNone(tiled_occupancy._native_touched_tile_keys(value))

    def require_native(self):
        self.assertIsNotNone(native_raster.UPDATE, 'required compiled native tile kernel missing')
        self.assertIsNotNone(native_raster.KERNEL, 'required compiled native ray kernel missing')

    def equal_maps(self, before, after):
        for name in ('_cell_count', 'bounds', 'limited', 'pose', 'stamp', 'path', 'scan_count',
                     'tile_versions', 'object_bounds', 'dirty_bounds', 'scan_stamps'):
            self.assertEqual(getattr(before, name), getattr(after, name), name)
        self.assertEqual(list(before._tiles), list(after._tiles))
        for key in before._tiles:
            np.testing.assert_array_equal(before._tiles[key], after._tiles[key])
        self.assertEqual(before.tile_packet(), after.tile_packet())
        self.assertEqual(before.tile_packet(since=max(0, before.scan_count - 1)),
                         after.tile_packet(since=max(0, after.scan_count - 1)))

    def test_supported_native_tile_update_never_sorts_full_cell_keys(self):
        self.require_native()
        coordinates = np.array([(x, y) for x in range(-80, 81) for y in range(-35, 36)], dtype=np.int64)
        grid = tiled_occupancy.TiledOccupancyMap()
        with patch.dict(os.environ, {'UBTS_NATIVE_RASTER': '1'}), \
                patch.object(tiled_occupancy.np, 'unique', side_effect=AssertionError('supported keys sorted')):
            grid._update_raster(coordinates, 17, -13, 9)
        self.assertEqual(grid._cell_count, len(coordinates))

    def test_full_native_ray_epochs_match_previous_key_selection_packets_and_path(self):
        self.require_native()
        for count in (3200, 3240):
            angles = np.arange(count) * 2 * np.pi / count - np.pi
            for epoch in range(2):
                before = StreamingOccupancyMap(laser_x=0., laser_y=0., conservative_free=True, endpoint_margin_cells=1)
                after = StreamingOccupancyMap(laser_x=0., laser_y=0., conservative_free=True, endpoint_margin_cells=1)
                for sequence in range(4):
                    ranges = (12 + np.sin(angles * 7 + sequence * .2)).tolist()
                    ranges[0] = None
                    ranges[count // 4] = 30.
                    ranges[count // 2] = .01
                    sample = dict(stamp=sequence + 1, pose=dict(x=-1 + sequence * .2, y=-2 + .1 * epoch, yaw=sequence * .05),
                                  preserve_hit_endpoints=True, scan=dict(ranges=ranges, angles=angles.tolist(), angle_min=-np.pi,
                                  angle_increment=2 * np.pi / count, range_min=.05, range_max=30.))
                    with patch.dict(os.environ, {'UBTS_NATIVE_RASTER': '1'}):
                        with patch.object(tiled_occupancy, '_native_touched_tile_keys', return_value=None):
                            old_result = before.update(sample)
                        new_result = after.update(sample)
                    self.assertEqual(old_result, new_result)
                    self.equal_maps(before, after)


if __name__ == '__main__':
    unittest.main()
