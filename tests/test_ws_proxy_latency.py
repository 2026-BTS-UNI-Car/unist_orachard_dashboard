"""Real TCP proxy/socket options and JPEG decode-ACK cadence, without ROS."""
import asyncio
from contextlib import suppress
import json
import os
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import urlopen

import cv2
import numpy as np
import websockets

from dashboard_server.app import DashboardHandler, DashboardServer
from scripts.camera_ws_bridge import CameraState, serve_websocket


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


class ProxyLatencyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def proxy(self, enabled=True):
        observed=[]
        class ObservedHandler(DashboardHandler):
            provider=SimpleNamespace(health=lambda:{'ok':True,'service':'proxy_test'})
            def do_GET(self):
                if self.path == '/api/health':
                    observed.append({'http':self.connection.getsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY)})
                return super().do_GET()
            def _relay_websocket(self,upstream,initial_upstream=b'',deadline=None):
                if not enabled:
                    # Reproduce the original steady-state socket defaults for
                    # comparison without changing production application code.
                    self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,0)
                    upstream.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,0)
                observed.append({'client':self.connection.getsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY),
                                 'upstream':upstream.getsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY)})
                return super()._relay_websocket(upstream,initial_upstream,deadline)
        server=DashboardServer(('127.0.0.1',0),ObservedHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        return server,thread,observed

    async def cookie(self,port):
        def read():
            with urlopen(f'http://127.0.0.1:{port}/api/health',timeout=2) as response:
                return response.headers['Set-Cookie'].split(';',1)[0]
        return await asyncio.to_thread(read)

    async def shutdown_proxy(self,server,thread):
        await asyncio.to_thread(server.shutdown)
        server.server_close();thread.join(timeout=2)

    async def test_real_tcp_options_preserve_http_and_authenticated_bidirectional_frames(self):
        observed_paths=[]
        async def echo(ws,path):
            observed_paths.append(path)
            await ws.send(b'jpeg-tail-fixture')
            acknowledgement=await ws.recv()
            await ws.send(acknowledgement)
        async with websockets.serve(echo,'127.0.0.1',0) as bridge:
            port=bridge.sockets[0].getsockname()[1]
            with patch.dict(os.environ,{'CAMERA_WS_UPSTREAM':f'ws://127.0.0.1:{port}/ws/camera'}):
                server,thread,options=self.proxy()
                proxy_port=server.server_address[1]
                try:
                    with self.assertRaises(websockets.InvalidStatusCode) as rejected:
                        async with websockets.connect(f'ws://127.0.0.1:{proxy_port}/ws/camera'):
                            pass
                    self.assertEqual(rejected.exception.status_code,403)
                    cookie=await self.cookie(proxy_port)
                    async with websockets.connect(f'ws://127.0.0.1:{proxy_port}/ws/camera?flow=decode-ack',
                                                  extra_headers={'Cookie':cookie}) as client:
                        self.assertEqual(await client.recv(),b'jpeg-tail-fixture')
                        await client.send('{"type":"frame_ack","frame_count":7}')
                        self.assertEqual(json.loads(await client.recv())['frame_count'],7)
                    self.assertIn({'http':0},options)
                    self.assertIn({'client':1,'upstream':1},options)
                    self.assertEqual(observed_paths,['/ws/camera?flow=decode-ack'])
                finally:
                    await self.shutdown_proxy(server,thread)

    async def test_bridge_proxy_binary_decode_ack_has_no_framerate_halving(self):
        state,stop,port=CameraState(),threading.Event(),unused_port()
        # Moderate texture gives a multi-packet JPEG while staying below even
        # the original local simulation bandwidth cap at 30 frames/second.
        rng=np.random.default_rng(8)
        frame=rng.integers(96,160,(360,640,3),dtype=np.uint8)
        state.update_frame('camera',1,0,frame,'bgr8')
        query='?profile=detail&resolution=640x360&fps=30&transport=binary-v1&flow=decode-ack'
        config={'DASHBOARD_MODE':'simulation','CAMERA_PREVIEW_LOCAL_FPS':'30',
                'CAMERA_WS_UPSTREAM':f'ws://127.0.0.1:{port}/ws/camera'}
        with patch.dict(os.environ,config):
            bridge=asyncio.create_task(serve_websocket(state,'127.0.0.1',port,'/ws/camera',stop))
            for _ in range(100):
                try:
                    _,writer=await asyncio.open_connection('127.0.0.1',port)
                    writer.close();await writer.wait_closed();break
                except OSError:
                    await asyncio.sleep(.02)
            async def produce():
                count=1
                while True:
                    count+=1
                    state.update_frame('camera',count,0,frame,'bgr8')
                    await asyncio.sleep(1/30)
            producer=asyncio.create_task(produce())
            results={}
            try:
                for name,enabled in [('direct',None),('nagle_baseline',False),('nodelay',True)]:
                    server=thread=None
                    headers={}
                    target=f'ws://127.0.0.1:{port}/ws/camera{query}'
                    if enabled is not None:
                        server,thread,options=self.proxy(enabled)
                        proxy_port=server.server_address[1]
                        headers['Cookie']=await self.cookie(proxy_port)
                        target=f'ws://127.0.0.1:{proxy_port}/ws/camera{query}'
                    try:
                        async with websockets.connect(target,extra_headers=headers,max_queue=1) as ws:
                            times=[];counts=[];sizes=[]
                            deadline=time.monotonic()+2.2
                            while time.monotonic()<deadline:
                                message=await asyncio.wait_for(ws.recv(),1)
                                if not isinstance(message,bytes):continue
                                length=int.from_bytes(message[5:9],'big')
                                meta=json.loads(message[9:9+length])
                                jpeg=message[9+length:]
                                decoded=cv2.imdecode(np.frombuffer(jpeg,dtype=np.uint8),cv2.IMREAD_COLOR)
                                self.assertIsNotNone(decoded)
                                self.assertEqual(decoded.shape,(360,640,3))
                                times.append(time.monotonic());counts.append(meta['frame_count']);sizes.append(len(jpeg))
                                await ws.send(json.dumps({'type':'frame_ack','frame_count':meta['frame_count']}))
                            self.assertTrue(all(b>a for a,b in zip(counts,counts[1:])))
                            results[name]={'fps':round((len(times)-1)/(times[-1]-times[0]),2),
                                           'source_fps':round((counts[-1]-counts[0])/(times[-1]-times[0]),2),
                                           'frames':len(times),'jpeg_bytes':round(sum(sizes)/len(sizes))}
                            if enabled is not None:
                                self.assertIn({'client':int(enabled),'upstream':int(enabled)},options)
                    finally:
                        if server:await self.shutdown_proxy(server,thread)
                print('PROXY_CAMERA_DECODE_ACK',json.dumps(results),flush=True)
                self.assertGreater(results['nodelay']['fps'],results['direct']['fps']*.8)
                self.assertGreater(results['nodelay']['fps'],20)
            finally:
                producer.cancel()
                with suppress(asyncio.CancelledError):await producer
                stop.set();await asyncio.wait_for(bridge,3)


if __name__=='__main__':unittest.main()
