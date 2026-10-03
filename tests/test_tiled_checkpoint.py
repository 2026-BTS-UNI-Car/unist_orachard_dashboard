import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from dashboard_server.tiled_occupancy import TiledOccupancyMap


class CheckpointTests(unittest.TestCase):
    def populate(self):
        grid=TiledOccupancyMap(max_span=64,laser_x=0.,laser_y=0.)
        for t,x in enumerate((-100.,0.,100.),1):
            grid.update(dict(stamp=float(t),pose=dict(x=x,y=-1.,yaw=.2,stamp=float(t)),
                scan=dict(ranges=[3.,4.,2.,None],angle_min=-2.,angle_increment=1.,range_min=.05,range_max=5.)))
        return grid

    def test_exact_native_roundtrip_not_overview_pixels(self):
        with tempfile.TemporaryDirectory() as root:
            grid=self.populate();path=Path(root)/'map.json'
            proof=grid.save_checkpoint(path)
            loaded=TiledOccupancyMap.load_checkpoint(path)
            self.assertTrue(grid.snapshot()['display_downsampled'])
            self.assertEqual(loaded.cells,grid.cells)
            self.assertEqual(loaded.snapshot(),grid.snapshot())
            self.assertEqual(loaded.config(),grid.config())
            self.assertEqual(proof['cell_count'],grid._cell_count)
            self.assertEqual(proof['tile_count'],len(grid._tiles))
            loaded._update_raster(np.array([[0,0]]),0,0,9)
            loaded._cells_cache=None
            self.assertNotEqual(loaded.cells,grid.cells)

    def test_corrupt_or_inconsistent_generation_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'map.json';self.populate().save_checkpoint(path)
            original=path.read_text();value=json.loads(original)
            blob=path.with_name(value['blob']);data=blob.read_bytes()
            blob.write_bytes(data[:-1]+bytes([data[-1]^1]))
            with self.assertRaisesRegex(ValueError,'hash_mismatch'):TiledOccupancyMap.load_checkpoint(path)
            blob.write_bytes(data)
            value['cell_count']-=1;path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'cell_count'):TiledOccupancyMap.load_checkpoint(path)
            value=json.loads(original);value['tile_count']=65537;path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):TiledOccupancyMap.load_checkpoint(path)
            value=json.loads(original);value['blob']='../outside.gz';path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'blob_path'):TiledOccupancyMap.load_checkpoint(path)

    def test_failed_manifest_install_preserves_previous_generation(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'map.json';grid=self.populate();grid.save_checkpoint(path)
            before=path.read_bytes();grid._update_raster(np.array([[0,0]]),0,0,9)
            with patch('dashboard_server.tiled_checkpoint.atomic_json',side_effect=OSError('disk failed')):
                with self.assertRaises(OSError):grid.save_checkpoint(path)
            self.assertEqual(path.read_bytes(),before)
            self.assertNotEqual(TiledOccupancyMap.load_checkpoint(path).cells,grid.cells)

    def test_empty_map_is_a_valid_checkpoint(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'map.json';grid=TiledOccupancyMap();grid.save_checkpoint(path)
            loaded=TiledOccupancyMap.load_checkpoint(path)
            self.assertEqual(loaded.snapshot(),grid.snapshot())

    def test_repeated_stop_reuses_verified_unchanged_tile_blob(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'map.json';grid=self.populate()
            first=grid.save_checkpoint(path)
            grid.pose={**grid.pose,'stamp':99.}
            second=grid.save_checkpoint(path)
            self.assertEqual(first['sha256'],second['sha256'])
            self.assertNotEqual(first['manifest_sha256'],second['manifest_sha256'])
            self.assertEqual(len(list(Path(root).glob('*.tiles.gz'))),1)
            self.assertEqual(TiledOccupancyMap.load_checkpoint(path).pose,grid.pose)


if __name__=='__main__':unittest.main()
