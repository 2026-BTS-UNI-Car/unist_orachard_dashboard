"""Bit equality against independent Python raster and sequential log odds."""
import os
import unittest
from unittest.mock import patch

import numpy as np

from dashboard_server import native_raster
from dashboard_server.occupancy_map import raster_cells
from dashboard_server.tiled_occupancy import TiledOccupancyMap


class NativeRasterTests(unittest.TestCase):
    def test_randomized_exact_ray_union_endpoint_precedence_and_margin(self):
        self.assertIsNotNone(native_raster.KERNEL, 'build the Linux native kernel before this required test')
        rng=np.random.default_rng(731)
        for count in (1, 17, 3240):
            for margin in (0, 1, 3):
                xy=rng.integers(-300,301,(count,2));hits=rng.integers(0,2,(count,1))
                rays=np.concatenate((xy,hits),axis=1).tolist()
                rays.extend([[0,0,1],[1,0,0],[1,1,1],[-1,-1,1]])
                with patch.dict(os.environ,{'UBTS_NATIVE_RASTER':'0'}):reference=raster_cells(rays,margin)
                with patch.dict(os.environ,{'UBTS_NATIVE_RASTER':'1'}):actual=raster_cells(rays,margin)
                for expected,obtained in zip(reference,actual):np.testing.assert_array_equal(expected,obtained)

    def test_absent_kernel_and_unsupported_extent_keep_python_fallback(self):
        rays=[[1,2,1],[-1,-3,1],[0,0,0]]
        with patch.dict(os.environ,{'UBTS_NATIVE_RASTER':'0'}):reference=raster_cells(rays,1)
        with patch.object(native_raster,'KERNEL',None):actual=raster_cells(rays,1)
        for expected,obtained in zip(reference,actual):np.testing.assert_array_equal(expected,obtained)
        self.assertIsNone(native_raster.raster_bitmap(np.array([[5000,0,1]]),0,0,5001,1,0))

    def test_large_batch_tiles_exact_signed_saturated_and_conservative_updates(self):
        coordinates=np.array([(x,y) for x in range(-80,81) for y in range(-55,56)],dtype=np.int64)
        for conservative in (False,True):
            fast=TiledOccupancyMap(max_cells=100000,conservative_free=conservative)
            slow=TiledOccupancyMap(max_cells=100000,conservative_free=conservative)
            for delta in (9,9,9,-4,-4,9,-4,9,9,9,9,9,9,9,-4,-4,-4):
                fast._update_raster(coordinates,17,-13,delta)
                # Disjoint chunks force the pre-existing per-tile path. No
                # cell repeats, so scan-level admission/log odds are identical.
                for first in range(0,len(coordinates),2000):
                    slow._update_raster(coordinates[first:first+2000],17,-13,delta)
            self.assertEqual(fast._cell_count,slow._cell_count)
            self.assertEqual(fast.bounds,slow.bounds)
            self.assertEqual(set(fast._tiles),set(slow._tiles))
            for key in fast._tiles:np.testing.assert_array_equal(fast._tiles[key],slow._tiles[key])

    def test_cell_limit_admission_keeps_global_order_and_existing_cells(self):
        coordinates=np.array([(x,y) for x in range(-50,51) for y in range(-50,51)],dtype=np.int64)
        fast=TiledOccupancyMap(max_cells=4100);slow=TiledOccupancyMap(max_cells=4100)
        fast._update_raster(coordinates,0,0,9)
        for row in coordinates:slow._update_raster(np.array([row]),0,0,9)
        fast._update_raster(coordinates,0,0,-4)
        for row in coordinates:slow._update_raster(np.array([row]),0,0,-4)
        self.assertEqual(fast._cell_count,4100);self.assertTrue(fast.limited)
        for key in fast._tiles:np.testing.assert_array_equal(fast._tiles[key],slow._tiles[key])


if __name__=='__main__':unittest.main()
