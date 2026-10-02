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
