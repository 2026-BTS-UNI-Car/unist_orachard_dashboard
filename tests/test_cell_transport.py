import base64
import unittest
import numpy as np
from dashboard_server.tile_transport import StreamingOccupancyMap, encode_cells
from dashboard_server.integrated_mapping import integrate
from test_integrated_mapping import scan


def decode(payload):
    if payload['encoding']=='rle2-v1':
        values=np.repeat(payload['cells'][::2],payload['cells'][1::2]).astype(np.uint8)
    else:
        packed=np.frombuffer(base64.b64decode(payload['cells']),dtype=np.uint8)
        values=((packed[:,None]>>np.arange(0,8,2))&3).astype(np.uint8).ravel()
    return values.reshape(64,64)


class CellTransportTests(unittest.TestCase):
    def test_both_encodings_preserve_every_state_and_row(self):
        solid=np.full((64,64),3,dtype=np.uint8)
        solid[0,0]=2;solid[0,1]=1;solid[-1,-1]=0
        noisy=np.arange(4096,dtype=np.uint16).reshape(64,64).astype(np.uint8)%4
        self.assertEqual(encode_cells(solid)['encoding'],'rle2-v1')
        self.assertEqual(encode_cells(noisy)['encoding'],'packed2-v1')
        for states in (solid,noisy):
            np.testing.assert_array_equal(states,decode(encode_cells(states)))
        with self.assertRaises(ValueError):encode_cells(np.zeros((32,32),dtype=np.uint8))

    def test_dropped_polls_full_replacements_recover_exact_cells_and_path(self):
        grid=StreamingOccupancyMap(laser_x=0,max_beams=450)
        integrate(grid,scan(),[-.25,-.3,0])
        first=grid.tile_packet();version=first['tile_version']
        client={(t['x'],t['y']):decode(t) for t in first['tiles']}
        path=list(first['path'])
        for i in range(1,10):integrate(grid,scan(1000+i*.1,i*.08),[-.25+i*.08,-.3,0])
        later=grid.tile_packet(version)
        self.assertFalse(later['tiles_reset']);self.assertTrue(later['path_append'])
        for t in later['tiles']:client[t['x'],t['y']]=decode(t)
        path.extend(later['path'])
        self.assertEqual(path,grid.path)
        self.assertEqual(client.keys(),grid._tiles.keys())
        for key,odds in grid._tiles.items():np.testing.assert_array_equal(client[key],grid.cell_states(odds))
        self.assertEqual(later['grid']['encoding'],'tiles-cells-v1')
        self.assertNotIn('image_base64',str(later));self.assertNotIn('png',str(later))
        self.assertTrue(any(x<0 for x,y in client))
        unchanged=grid.tile_packet(grid.scan_count)
        self.assertFalse(unchanged['tiles']);self.assertFalse(unchanged['path'])
        self.assertTrue(grid.tile_packet(grid.scan_count+1)['tiles_reset'])


if __name__=='__main__':unittest.main()
