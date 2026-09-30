"""Retired orientation-zero APIs are explicit, inert HTTP410 responses."""
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock
import urllib.error
import urllib.request

from dashboard_server.app import DashboardHandler
from dashboard_server.telemetry import TelemetryProvider

try:
    from scripts.data_recorder_service import Handler, RecordingState
except ModuleNotFoundError as error:
    if error.name not in {'rclpy','sensor_msgs','scripts','scripts.data_recorder_service'}:
        raise
    Handler = RecordingState = None


class RemovedImuApiTests(unittest.TestCase):
    @unittest.skipUnless(Handler is not None,'ROS2 runtime required')
    def test_both_real_http_handlers_send410_json_for_retired_zero(self):
        provider=SimpleNamespace()
        provider.zero_imu=lambda:TelemetryProvider.zero_imu(provider)
        state=SimpleNamespace()
        state.zero_imu=lambda:RecordingState.zero_imu(state)
        for base, attributes, path in ((DashboardHandler,{'provider':provider},'/api/imu/zero'),
                                       (Handler,{'state':state},'/imu/zero')):
            handler=type('IsolatedZeroHandler',(base,),attributes)
            server=ThreadingHTTPServer(('127.0.0.1',0),handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}',
                                               data=b'{}',method='POST')
                with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request,timeout=2)
                self.assertEqual(error.exception.code,410)
                response=json.loads(error.exception.read())
                self.assertFalse(response['ok'])
                self.assertEqual(response['state'],'removed')
            finally:
                server.shutdown();server.server_close();thread.join(timeout=2)

    def test_public_zero_returns410_without_contacting_recorder(self):
        provider = SimpleNamespace(_recorder_post=Mock(side_effect=AssertionError('must not call recorder')))
        provider.zero_imu = lambda: TelemetryProvider.zero_imu(provider)
        sent=[]
        handler=SimpleNamespace(path='/api/imu/zero',provider=provider,
                                _send_json=lambda payload,**kwargs:sent.append((payload,kwargs)))
        DashboardHandler.do_POST(handler)
        self.assertEqual(sent[0][1],{'status':410})
        self.assertFalse(sent[0][0]['ok'])
        self.assertEqual(sent[0][0]['state'],'removed')
        provider._recorder_post.assert_not_called()

    @unittest.skipUnless(Handler is not None,'ROS2 runtime required')
    def test_private_zero_returns410_without_reading_or_writing_sensor_state(self):
        state=SimpleNamespace()
        state.zero_imu=lambda:RecordingState.zero_imu(state)
        sent=[]
        handler=SimpleNamespace(path='/imu/zero',state=state,
                                send_json=lambda payload,**kwargs:sent.append((payload,kwargs)))
        Handler.do_POST(handler)
        self.assertEqual(sent[0][1],{'status':410})
        self.assertFalse(sent[0][0]['ok'])
        self.assertEqual(sent[0][0]['state'],'removed')
        self.assertEqual(set(vars(state)),{'zero_imu'})


if __name__=='__main__':unittest.main()
