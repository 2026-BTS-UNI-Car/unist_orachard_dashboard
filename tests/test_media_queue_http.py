"""Media control proxy never invokes a recorder or encoder in this test."""
import http.cookiejar
import json
import os
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPCookieProcessor, Request, urlopen

os.environ['DASHBOARD_LIVE_MAP_WORKER']='0'
from dashboard_server import app


class MediaQueueHTTPTests(unittest.TestCase):
    def test_actions_are_session_scoped_same_origin_allowlisted_and_no_motor_endpoint(self):
        server=app.create_server('127.0.0.1',0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        client=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        provider=app.DashboardHandler.provider
        try:
            with patch.object(provider,'_recorder_get',return_value=dict(ok=True,jobs=[])) as get, \
                 patch.object(provider,'_recorder_post',return_value=dict(ok=True,state='paused')) as post:
                body=json.dumps(dict(recording_id='20261003T081934Z_both',action='pause',discarded='unused')).encode()
                with self.assertRaises(HTTPError) as error:urlopen(Request(url+'/api/recordings/media-jobs/action',data=body))
                self.assertEqual(error.exception.code,403);post.assert_not_called()
                with client.open(url+'/api/recordings/media-jobs') as response:self.assertEqual(json.load(response)['jobs'],[])
                get.assert_called_once_with('/media-jobs')
                with self.assertRaises(HTTPError) as error:
                    client.open(Request(url+'/api/recordings/media-jobs/action',data=body,headers={'Origin':'https://foreign.invalid'}))
                self.assertEqual(error.exception.code,403);post.assert_not_called()
                with client.open(Request(url+'/api/recordings/media-jobs/action',data=body)) as response:self.assertTrue(json.load(response)['ok'])
                post.assert_called_once_with('/media-jobs/action',dict(recording_id='20261003T081934Z_both',action='pause'))
                post.reset_mock()
                for invalid in ([],dict(recording_id='record',action='start'),dict(recording_id=2,action='cancel')):
                    with self.assertRaises(HTTPError) as error:
                        client.open(Request(url+'/api/recordings/media-jobs/action',data=json.dumps(invalid).encode()))
                    self.assertEqual(error.exception.code,400)
                post.assert_not_called()
        finally:
            server.shutdown();server.server_close();thread.join()
            for name in ('archive_mapping','live_mapping'):
                resource=getattr(app.DashboardHandler,name,None)
                if resource is not None:resource.close()


if __name__=='__main__':unittest.main()
