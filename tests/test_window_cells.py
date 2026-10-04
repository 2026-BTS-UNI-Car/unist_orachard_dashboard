import base64,copy,gzip,json,unittest
import numpy as np
from dashboard_server.window_cells import compact_window,packed_cells
from dashboard_server.tile_transport import encode_cells

class WindowCellsTest(unittest.TestCase):
    def test_lossless_timed_changes_reset_and_independent_seed(self):
        a=np.zeros(4096,dtype=np.uint8);a[100:400]=1
        b=a.copy();b[103]=2;b[203]=3
        c=b.copy();c[203]=0
        tile=lambda v:dict(x=-1,y=2,**encode_cells(v))
        original=dict(seed=dict(tiles=[tile(a)]),events=[dict(frame_t_s=.1,tiles=[tile(b)]),dict(frame_t_s=.2,tiles=[tile(c)]),dict(frame_t_s=.3,tiles_reset=True,tiles=[tile(a)])])
        before=copy.deepcopy(original);compact=compact_window(original)
        self.assertEqual(original,before)
        current=packed_cells(tile(a))
        for event,expected in zip(compact['events'],(b,c,a)):
            t=event['tiles'][0]
            if t['encoding']=='xor2-v1':current=current ^ np.frombuffer(base64.b64decode(t['cells']),dtype=np.uint8)
            else:current=packed_cells(t)
            np.testing.assert_array_equal(current,packed_cells(tile(expected)))
        self.assertEqual([e['frame_t_s'] for e in compact['events']],[.1,.2,.3])
    def test_unchanged_cells_are_omitted_without_omitting_pose(self):
        tile=dict(x=0,y=0,**encode_cells(np.ones(4096,dtype=np.uint8)))
        v=compact_window(dict(seed=dict(tiles=[tile]),events=[dict(tiles=[tile],pose=dict(x=1),frame_t_s=2)]))
        self.assertEqual(v['events'][0]['tiles'],[]);self.assertEqual(v['events'][0]['pose'],dict(x=1))

if __name__=='__main__':unittest.main()
