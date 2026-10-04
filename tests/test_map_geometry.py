import math,unittest
from unittest.mock import patch
import numpy as np
from dashboard_server.tile_transport import StreamingOccupancyMap
from dashboard_server.integrated_mapping import integrate
from dashboard_server.occupancy_map import raster_cells,_raster_cells_sorted,line_cells
from tests.test_integrated_mapping import scan


class MapGeometryTests(unittest.TestCase):
    def test_narrow_object_survives_free_ray_sampling(self):
        # A real endpoint between the 450 sampled ray indices must remain
        # occupied, including if a sampled free ray passes through its cell.
        raw=scan();raw['ranges']=[25.]*3240
        raw['angle_increment']=2*math.pi/3240
        raw['ranges'][1621]=5.
        grid=StreamingOccupancyMap(laser_x=0,max_beams=450)
        integrate(grid,raw,[0,0,0])
        a=raw['angle_min']+1621*raw['angle_increment']
        cell=(math.floor(5*math.cos(a)/.1),math.floor(5*math.sin(a)/.1))
        self.assertGreaterEqual(grid.cells[cell],9)
        previous=StreamingOccupancyMap(laser_x=0,max_beams=450)
        previous.update(dict(stamp=1000.,pose=dict(x=0,y=0,yaw=0),scan=raw))
        self.assertLessEqual(previous.cells.get(cell,0),0)

    def test_same_hit_has_same_confidence_at_different_beam_densities(self):
        raw=scan();grid=StreamingOccupancyMap(laser_x=0,max_beams=45)
        integrate(grid,raw,[0,0,0])
        other=StreamingOccupancyMap(laser_x=0,max_beams=450)
        integrate(other,raw,[0,0,0])
        occupied={cell:odds for cell,odds in grid.cells.items() if odds>0}
        self.assertEqual(occupied,{cell:odds for cell,odds in other.cells.items() if odds>0})

    def test_unsampled_observed_ray_does_not_leave_a_false_unknown_stripe(self):
        raw=scan();raw['ranges']=[10.]*3240;raw['angle_increment']=2*math.pi/3240
        grid=StreamingOccupancyMap(laser_x=0,max_beams=450);integrate(grid,raw,[0,0,0])
        old=StreamingOccupancyMap(laser_x=0,max_beams=450)
        old.update(dict(stamp=1000.,pose=dict(x=0,y=0,yaw=0),scan=raw))
        demonstrated=False
        for i in range(1,3240,8):
            angle=raw['angle_min']+i*raw['angle_increment']
            end=(math.floor(10*math.cos(angle)/.1),math.floor(10*math.sin(angle)/.1))
            for cell in line_cells(0,0,*end):
                if math.hypot(*cell)*.1<9.5 and old.cells.get(cell,127)==127 and grid.cells.get(cell,127)<=-4:
                    demonstrated=True;break
            if demonstrated:break
        self.assertTrue(demonstrated,'a measured ray must clear its cells even between sampled angles')

    def test_chunked_bitmap_matches_reference_in_all_octants_and_at_zero(self):
        random=np.random.default_rng(428)
        for count in (1,40,450,3240):
            for trial in range(3):
                rays=np.column_stack((random.integers(-80,81,(count,2)),random.integers(0,2,count))).tolist()
                expected=_raster_cells_sorted(rays);actual=raster_cells(rays)
                for first,second in zip(expected,actual):np.testing.assert_array_equal(first,second)


if __name__=='__main__':unittest.main()
