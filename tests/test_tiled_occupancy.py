import base64
import unittest
import cv2
import numpy as np
from dashboard_server.occupancy_map import OccupancyMap
from dashboard_server.tiled_occupancy import TiledOccupancyMap


def sample(x,y,t):
    return dict(stamp=t,pose=dict(x=x,y=y,yaw=.2,stamp=t),scan=dict(
        ranges=[3.,4.,2.,None],angle_min=-2.,angle_increment=1.,range_min=.05,range_max=5.))


class TiledStorageTest(unittest.TestCase):
    def test_negative_tiles_preserve_exact_logodds(self):
        dense=OccupancyMap(max_span=256);tiled=TiledOccupancyMap(max_span=256)
        for t,(x,y) in enumerate(((-4.,-3.),(0.,0.),(4.,2.),(-3.,2.)),1):
            self.assertTrue(dense.update(sample(x,y,t)));self.assertTrue(tiled.update(sample(x,y,t)))
        self.assertEqual(tiled.cells,dense.cells)
        self.assertEqual(tiled.grid(),dict(dense.grid(),source_resolution=.1,downsample_factor=1))

    def test_long_extent_retained_and_occupied_survives_overview(self):
        grid=TiledOccupancyMap(max_span=64,max_cells=10000)
        for t,x in enumerate((-100.,0.,100.),1):grid.update(sample(x,0.,t))
        self.assertFalse(grid.limited)
        self.assertTrue(any(x< -990 for x,y in grid.cells));self.assertTrue(any(x>990 for x,y in grid.cells))
        image=grid.grid();self.assertLessEqual(max(image['width'],image['height']),64)
        self.assertGreater(image['downsample_factor'],1)
        png=cv2.imdecode(np.frombuffer(base64.b64decode(image['image_base64']),np.uint8),cv2.IMREAD_GRAYSCALE)
        self.assertIn(25,png)
        self.assertIsNotNone(image['occupied_bounds'])

    def test_memory_limit_keeps_existing_cells_updatable(self):
        grid=TiledOccupancyMap(max_cells=2,max_span=64)
        grid._update_raster(np.array([[0,0],[1,0],[2,0]]),0,0,9)
        grid._update_raster(np.array([[0,0],[100000,100000]]),0,0,-4)
        self.assertEqual(grid._cell_count,2);self.assertTrue(grid.limited)
        grid._cells_cache=None
        self.assertEqual(grid.cells[(0,0)],5);self.assertNotIn((100000,100000),grid.cells)


if __name__=='__main__':unittest.main()
