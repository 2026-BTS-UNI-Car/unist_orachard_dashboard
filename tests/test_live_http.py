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


class Tests(unittest.TestCase):
    def test_old_supervisor_routes_are_gone_and_start_nothing(self):
        # The differential-era supervisor, preflight and observation routes (/api/autonomy*) left on 2026-11-22;
        # autonomy runs through the row executor relay (/api/executor/*).
        from dashboard_server.source_live_mapping import SourceLiveMapping
        with tempfile.TemporaryDirectory() as root:
            server=app.create_server('127.0.0.1',0)
            initial_live=app.DashboardHandler.live_mapping
            initial_archive=app.DashboardHandler.archive_mapping
            live=SourceLiveMapping(Path(root)/'live',start_worker=False)
            app.DashboardHandler.live_mapping=live
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            url=f'http://127.0.0.1:{server.server_port}'
            try:
                with opener.open(url+'/api/live/map') as response:response.read()
                for path in ('/api/autonomy','/api/autonomy/preflight','/api/autonomy/observe','/api/autonomy/supervisor',
                             '/api/autonomy/native-map/status'):
                    with self.assertRaises(HTTPError) as error:opener.open(url+path)
                    self.assertEqual(error.exception.code,404,path)
                for path in ('/api/autonomy/start','/api/autonomy/speed','/api/autonomy/observe/start'):
                    with self.assertRaises(HTTPError) as error:
                        opener.open(Request(url+path,data=b'{}',headers={'Content-Type':'application/json'}))
                    self.assertEqual(error.exception.code,404,path)
                self.assertFalse(live.active)
            finally:
                server.shutdown();server.server_close();thread.join()
                live.close();initial_archive.close();initial_live.close()

    def test_live_jobs_and_multi_device_mapping_http_contract(self):
        import gzip
        import hashlib
        from dashboard_server.mapping_operations import ManagedArchiveMapping,MappingOperations
        from dashboard_server.source_live_mapping import SourceLiveMapping
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'records'/'record';source=folder/'mapping_source';source.mkdir(parents=True)
            with gzip.open(source/'sensors.jsonl.gz','wb') as stream:stream.write(b'')
            (source/'manifest.json').write_text(json.dumps(dict(complete=True,output_sha256={'sensors.jsonl.gz':hashlib.sha256((source/'sensors.jsonl.gz').read_bytes()).hexdigest()})))
            (folder/'metadata.json').write_text(json.dumps(dict(started_at='1970-01-01T00:16:40Z',duration_s=1.)))
            server=app.create_server('127.0.0.1',0)
            initial_live=app.DashboardHandler.live_mapping;initial_archive=app.DashboardHandler.archive_mapping
            live=SourceLiveMapping(Path(root)/'live',start_worker=False)
            archives=ManagedArchiveMapping(Path(root)/'records',live,start_monitor=False)
            app.DashboardHandler.live_mapping=live;app.DashboardHandler.archive_mapping=archives
            app.DashboardHandler.mapping_operations=MappingOperations(live,archives)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}'
            opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            try:
                with opener.open(url+'/api/live/mapping-jobs') as response:response.read()
                def post(path,payload,origin=None):
                    with opener.open(Request(url+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json',**({'Origin':origin} if origin else {})})) as response:return json.load(response)
                post('/api/live/map/start',{})
                first=post('/api/live/recordings/record/new-map',dict(expected_epoch=None,client='A'))
                post('/api/live/recordings/record/map-playback',dict(t_s=.5,playing=True,control=dict(client='A',sequence=1)))
                with opener.open(url+'/api/live/mapping-jobs') as response:jobs=json.load(response)
                self.assertTrue(jobs['live_first']);self.assertEqual(jobs['max_recorded_workers'],1)
                self.assertEqual(jobs['jobs'][1]['state'],'waiting_live_priority')
                with self.assertRaises(HTTPError) as error:post('/api/live/recordings/record/map-playback',dict(t_s=0,playing=False,control=dict(client='B',sequence=1)))
                self.assertEqual(error.exception.code,409)
                with self.assertRaises(HTTPError) as error:post('/api/live/mapping-jobs/stop',dict(job_id='live'),'http://foreign.invalid')
                self.assertEqual(error.exception.code,403)
                stopped=post('/api/live/mapping-jobs/stop',dict(job_id='archive:record',expected_epoch=first['map_epoch']))
                self.assertTrue(stopped['partial_map_preserved'])
                with self.assertRaises(HTTPError) as error:post('/api/live/recordings/record/map-playback',dict(t_s=.7,playing=True,control=dict(client='A',sequence=2)))
                self.assertEqual(error.exception.code,409)
                post('/api/live/mapping-jobs/stop',dict(job_id='live',expected_epoch=live.map_epoch))
                self.assertFalse(live.tracking)
            finally:
                server.shutdown();server.server_close();thread.join()
                archives.close();live.close();initial_archive.close();initial_live.close()

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
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}'
            opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            try:
                with opener.open(url+'/api/live/map') as response:response.read()
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

    def test_http_auth_and_live_map_routes(self):
        with tempfile.TemporaryDirectory() as root:
            server=app.create_server('127.0.0.1',0)
            app.DashboardHandler.live_mapping=LiveMapping(root,start_worker=False)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}'
            opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            try:
                with opener.open(url+'/api/live/map') as response:response.read()
                def post(path,data,origin=None):
                    return opener.open(Request(url+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json',**({'Origin':origin} if origin else {})}))
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
