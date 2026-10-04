import math,unittest
from unittest.mock import patch
import numpy as np
from dashboard_server.tile_transport import StreamingOccupancyMap
from dashboard_server.integrated_mapping import integrate
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


if __name__=='__main__':unittest.main()
