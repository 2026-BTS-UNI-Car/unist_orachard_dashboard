"""Archive progress, historical seek and interrupted generation regression."""
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlparse,parse_qs

import cv2
import numpy as np
from dashboard_server.archive_mapping import ArchiveMapping
from dashboard_server.occupancy_map import atomic_json


class FakeRenderer:
    def __init__(self, mapper, archive_id, fail_after=None):
        self.mapper=mapper;self.archive_id=archive_id;self.times=[.5,1.5,2.5,3.5,4.5,5.5]
        self.fail_after=fail_after;self.partial_status=[];self.deleted=False

    @contextmanager
    def open(self,request,timeout=0):
        url=request if isinstance(request,str) else request.full_url
        method='GET' if isinstance(request,str) else request.get_method()
        class Response:
            def __init__(self,data,headers=None):self.data=data;self.headers=headers or {}
            def read(self,size=-1):return self.data if size<0 else self.data[:size]
        if method=='POST':
            yield Response(json.dumps({'session_id':'fixture','duration_s':6.,'frame_times_s':self.times}).encode())
        elif method=='DELETE':
            self.deleted=True;yield Response(b'{}')
        else:
            target=float(parse_qs(urlparse(url).query)['t_s'][0])
            self.partial_status.append(self.mapper.status(self.archive_id))
            if self.fail_after is not None and target>=self.fail_after:raise OSError('fixture interrupted')
            index=max(i for i,t in enumerate(self.times) if t<=target)
            image=np.full((80,80,3),146,dtype=np.uint8);image[10:20,10:10+index+1]=25
            ok,png=cv2.imencode('.png',image);assert ok
            meta={'frame_index':index,'frame_t_s':self.times[index],'render_world_bounds_m':[-4.,4.,-4.,4.]}
            yield Response(png.tobytes(),{'X-OGM-Meta':json.dumps(meta)})


class Tests(unittest.TestCase):
    def fixture(self,root):
        name='20261003T000000Z_both';directory=Path(root)/name;directory.mkdir()
        atomic_json(directory/'metadata.json',{'duration_s':6.})
        (directory/'timeline.json').write_text('{}')
        mapper=ArchiveMapping(root);mapper.running.add(name)
        return name,directory,mapper

    def test_progress_and_backward_seek_never_return_future_grid(self):
        with tempfile.TemporaryDirectory() as root:
            name,directory,mapper=self.fixture(root);renderer=FakeRenderer(mapper,name)
            with patch('dashboard_server.archive_mapping.urlopen',renderer.open):mapper._render(name)
            self.assertTrue(renderer.deleted)
            self.assertTrue(any(s.get('grid') and s['state']=='regenerating' for s in renderer.partial_status))
            self.assertTrue(any(0<s['generation'].get('progress',0)<1 for s in renderer.partial_status))
            before=mapper.status(name,.4);self.assertNotIn('grid',before)
            late=mapper.status(name,4);early=mapper.status(name,1)
            self.assertEqual(late['frame_t_s'],3.5);self.assertEqual(early['frame_t_s'],.5)
            self.assertNotEqual(late['grid']['image_base64'],early['grid']['image_base64'])
            self.assertEqual(late['map_epoch'],early['map_epoch'])
            self.assertTrue(early['preserve_view_on_seek'])
            unchanged=mapper.status(name,1,f"{early['map_epoch']}:{early['version']}")
            self.assertNotIn('image_base64',unchanged['grid'])
            history=json.loads((directory/'dashboard_occupancy_grid_history.json').read_text())
            self.assertTrue(history['complete']);self.assertEqual(len(history['snapshots']),4)

    def test_failure_preserves_partial_history_without_marking_saved(self):
        with tempfile.TemporaryDirectory() as root:
            name,directory,mapper=self.fixture(root);renderer=FakeRenderer(mapper,name,fail_after=4.)
            with patch('dashboard_server.archive_mapping.urlopen',renderer.open):mapper._render(name)
            value=ArchiveMapping(root).status(name,5.)
            self.assertEqual(value['state'],'interrupted');self.assertEqual(value['frame_t_s'],1.5)
            self.assertFalse((directory/'dashboard_occupancy_grid.json').exists())
            self.assertTrue(renderer.deleted)

    def test_changed_source_and_legacy_final_png_are_not_historical_frames(self):
        with tempfile.TemporaryDirectory() as root:
            name,directory,mapper=self.fixture(root);renderer=FakeRenderer(mapper,name)
            with patch('dashboard_server.archive_mapping.urlopen',renderer.open):mapper._render(name)
            (directory/'timeline.json').write_text('{"changed":true}')
            value=mapper.status(name,1.)
            self.assertEqual(value['state'],'outdated');self.assertNotIn('grid',value)
            self.assertTrue(value['needs_regeneration'])
            (directory/'dashboard_occupancy_grid_history.json').unlink()
            self.assertNotIn('grid',mapper.status(name,1.))
            self.assertIn('grid',mapper.status(name,6.))
            for target in ('nan','inf',-1,'not-time'):
                with self.assertRaises(ValueError):mapper.status(name,target)


if __name__=='__main__':unittest.main()
