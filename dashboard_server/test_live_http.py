"""Isolated Linux HTTP contract: no sensor sockets, motor socket or recorder."""
import http.cookiejar
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import build_opener, HTTPCookieProcessor, Request
from urllib.error import HTTPError

os.environ['DASHBOARD_LIVE_MAP_WORKER']='0'
from dashboard_server import app
from dashboard_server.live_mapping import LiveMapping
from dashboard_server.autonomy_settings import AutonomySettings


class Tests(unittest.TestCase):
    def test_inline_playback_cells_preserve_ordered_control_and_old_clients(self):
        import base64
        import zlib
        import numpy as np
        from dashboard_server.tile_transport import encode_cells
        calls=[]
        class Archive:
            def playback(self,archive,time,playing,control):
                calls.append(('playback',archive,time,playing,control))
                return dict(ok=True,playback_driven=True,stale_control_ignored=control.get('sequence')==0)
            def status(self,archive,time,**options):
                calls.append(('status',archive,time,options))
                return dict(ok=True,frame_t_s=time,tiles=[dict(x=-1,y=2,**encode_cells(np.full(4096,2,dtype=np.uint8)))])
        with tempfile.TemporaryDirectory() as root:
            server=app.create_server('127.0.0.1',0)
            previous=app.DashboardHandler.archive_mapping
            app.DashboardHandler.archive_mapping=Archive()
            app.DashboardHandler.live_mapping=LiveMapping(root,start_worker=False)
            app.DashboardHandler.autonomy_settings=AutonomySettings(Path(root)/'settings.json')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}'
            opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            try:
                with opener.open(url+'/api/autonomy') as response:response.read()
                def post(payload):
                    with opener.open(Request(url+'/api/live/recordings/record/map-playback',
                            data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})) as response:
                        return json.load(response)
                payload=dict(t_s=2.5,playing=False,control=dict(client='browser',sequence=2),transport='tiles',
                    codec='xor2-deflate-v1',tile_since=7,tile_epoch='epoch:0')
                result=post(payload)
                self.assertEqual(calls[0],('playback','record',2.5,False,payload['control']))
                self.assertEqual(calls[1][3],dict(transport='tiles',tile_since=7,tile_epoch='epoch:0'))
                snapshot=result['map_snapshot'];self.assertEqual(snapshot['seed']['frame_t_s'],2.5)
                self.assertEqual(snapshot['cell_codec'],'xor2-deflate-v1')
                self.assertEqual(zlib.decompress(base64.b64decode(snapshot['cell_blob'])),bytes([170])*1024)
                # A delayed lower control sequence cannot return a future map.
                calls.clear();result=post(dict(payload,control=dict(client='browser',sequence=0)))
                self.assertTrue(result['stale_control_ignored']);self.assertNotIn('map_snapshot',result)
                self.assertEqual(len(calls),1)
                # Old callers remain a lightweight acknowledgement only.
                calls.clear();result=post({k:v for k,v in payload.items() if k not in ('transport','codec')})
                self.assertNotIn('map_snapshot',result);self.assertEqual(len(calls),1)
            finally:
                server.shutdown();server.server_close();thread.join()
                app.DashboardHandler.archive_mapping=previous

    def test_http_auth_settings_and_unqualified_start(self):
        with tempfile.TemporaryDirectory() as root:
            server=app.create_server('127.0.0.1',0)
            app.DashboardHandler.live_mapping=LiveMapping(root,start_worker=False)
            app.DashboardHandler.autonomy_settings=AutonomySettings(Path(root)/'settings.json')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}'
            opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            try:
                with opener.open(url+'/api/autonomy') as response:
                    self.assertFalse(json.load(response)['enabled'])
                def post(path,data,origin=None):
                    return opener.open(Request(url+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json',**({'Origin':origin} if origin else {})}))
                with post('/api/autonomy/speed',{'speed_percent':35}) as response:
                    self.assertEqual(json.load(response)['speed_percent'],35)
                with self.assertRaises(HTTPError) as error:post('/api/autonomy/start',{})
                self.assertEqual(error.exception.code,409)
                with self.assertRaises(HTTPError) as error:post('/api/live/map/start',{},'http://foreign.invalid')
                self.assertEqual(error.exception.code,403)
                with post('/api/live/map/start',{}) as response:self.assertTrue(json.load(response)['active'])
                with post('/api/live/map/stop',{}) as response:self.assertFalse(json.load(response)['active'])
                with opener.open(url+'/static/metric-map.mjs') as response:
                    self.assertIn('javascript',response.headers['Content-Type'])
                with opener.open(url+'/rover-control') as response:
                    self.assertIn(b'liveMapCanvas',response.read())
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
