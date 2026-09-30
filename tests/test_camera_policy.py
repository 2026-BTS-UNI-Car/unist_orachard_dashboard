"""Camera-only policy, real JPEG payloads and bounded feedback; no devices."""
import asyncio
from contextlib import suppress
import hashlib
import json
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from scripts.camera_stream_policy import CameraStreamBudget
from scripts.camera_ws_bridge import CameraState, client_settings, network_preview, wait_for_camera_decode_ack, serve_websocket
from scripts.stream_policy import PROFILES, FramePacer, StreamBudget


class CameraPolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock=SimpleNamespace(value=0.)
        self.budget=CameraStreamBudget(clock=lambda:self.clock.value)

    def ack(self,seconds=.04,size=30000,status="decoded",count=1):
        for _ in range(count):
            self.clock.value+=seconds
            self.budget.acknowledge(seconds,size,decode_status=status)

    def test_auto_starts_readable_without_changing_other_sensor_profiles(self):
        before=dict(PROFILES)
        self.assertEqual(self.budget.values[:2],(640,360));self.assertEqual(self.budget.values[3],70)
        self.ack(.8,count=40)
        self.assertEqual(self.budget.values[:2],(640,360));self.assertEqual(self.budget.values[3],70)
        self.assertLess(self.budget.values[2],2);self.assertEqual(self.budget.delivery_state,'bandwidth_limited')
        self.assertEqual(PROFILES,before);self.assertEqual(StreamBudget().values[:2],(160,90))

    def test_feedback_paces_service_rate_with_headroom_and_explicit_ceiling(self):
        self.ack(.2,50000)
        self.assertAlmostEqual(self.budget.values[2],4.25)
        self.assertAlmostEqual(self.budget.values[4],212500)
        fast=CameraStreamBudget('detail');fast.acknowledge(.0001,500000,decode_status='decoded')
        self.assertEqual(fast.values[2],30);self.assertEqual(fast.values[4],4_000_000)

    def test_resolution_upgrade_dwell_and_slow_hysteresis(self):
        self.ack(count=50);self.assertEqual(self.budget.level,0)  # only 2 seconds
        self.clock.value=4;self.ack(count=10);self.assertEqual(self.budget.level,1)
        self.ack(.5,count=2);self.assertEqual(self.budget.level,1)
        self.ack(.5);self.assertEqual(self.budget.level,0)
        self.ack(count=60);self.assertEqual(self.budget.level,0)  # cooldown

    def test_sustained_fast_delivery_reaches_960_then_1280_within_ten_seconds(self):
        first_seen={0:0.}
        while self.clock.value<10:
            self.ack(.04)
            first_seen.setdefault(self.budget.level,self.clock.value)
        self.assertEqual(set(first_seen),{0,1,2})
        self.assertLess(first_seen[1],4.);self.assertLess(first_seen[2],10.)
        self.assertEqual(self.budget.values[:2],(1280,720))

    def test_steady_wan_service_does_not_oscillate_or_drop_below_floor(self):
        seen=[]
        for _ in range(100):self.ack(.3,60000);seen.append(self.budget.level)
        self.assertEqual(set(seen),{0});self.assertEqual(self.budget.values[3],70)
        self.assertAlmostEqual(self.budget.values[2],.85/.3)

    def test_decode_failures_and_legacy_ack_never_upgrade_resolution(self):
        for status in ('error','timeout','not_visible','unknown'):
            self.clock.value=10;self.ack(.02,status=status,count=30)
            self.assertEqual(self.budget.level,0);self.assertEqual(self.budget.last_decode_status,status)

    def test_client_budgets_independent_and_manual_emergency_profiles_explicit(self):
        fast=CameraStreamBudget('detail');slow=CameraStreamBudget('auto')
        slow.acknowledge(.8,60000,decode_status='decoded')
        self.assertEqual(fast.values[2],30);self.assertLess(slow.values[2],2)
        for profile in ('tiny','slow'):
            budget=CameraStreamBudget(profile);budget.acknowledge(.01,30000,decode_status='decoded')
            self.assertEqual(budget.values,PROFILES[profile])

    def test_invalid_feedback_does_not_invent_service_capacity(self):
        for duration,size in ((float('nan'),30),(0,30),(-1,30),(True,30),(.1,False),(.1,-1)):
            self.budget.acknowledge(duration,size,decode_status='decoded')
            self.assertIsNone(self.budget.service_s)
            self.assertEqual(self.budget.delivery_state,'invalid_feedback')

    def test_bright_and_detailed_jpegs_preserve_input_and_auto_quality_floor(self):
        settings=client_settings('/ws/camera?profile=auto&quality=35')
        rng=np.random.default_rng(906)
        for frame in (np.full((720,1280,3),220,np.uint8),rng.integers(0,256,(720,1280,3),np.uint8)):
            source=cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,70])[1].tobytes()
            original=hashlib.sha256(source).hexdigest()
            from scripts.camera_ws_bridge import encode_camera_preview
            with patch('scripts.camera_ws_bridge.encode_camera_preview',wraps=encode_camera_preview) as encode:
                jpeg,_,w,h,sw,sh=network_preview({'jpeg':source},settings,self.budget,True)
            self.assertEqual((w,h,sw,sh),(640,360,1280,720));self.assertEqual(encode.call_args.args[3],70)
            self.assertEqual(cv2.imdecode(np.frombuffer(jpeg,np.uint8),1).shape[:2],(360,640))
            self.assertEqual(hashlib.sha256(source).hexdigest(),original)

    def test_small_source_is_not_upscaled_and_explicit_smaller_viewport_is_honored(self):
        frame=np.full((240,320,3),180,np.uint8)
        settings=client_settings('/ws/camera?profile=auto')
        out=network_preview({'frame_bgr':frame},settings,self.budget,False)
        self.assertEqual(out[2:4],(320,240));self.assertTrue(np.all(frame==180))
        settings=client_settings('/ws/camera?profile=auto&resolution=320x180')
        out=network_preview({'frame_bgr':frame},settings,self.budget,False)
        self.assertEqual(out[2:4],(240,180))


class CameraAckTests(unittest.IsolatedAsyncioTestCase):
    async def test_sub_one_fps_link_is_byte_paced_with_truthful_timer_ceiling(self):
        clock=SimpleNamespace(value=0.)
        async def sleep(delay):clock.value+=delay
        budget=CameraStreamBudget();budget.acknowledge(6.,60000,decode_status='decoded')
        pacer=FramePacer(clock=lambda:clock.value,sleep=sleep)
        self.assertEqual(budget.values[2],1.)
        await pacer.wait(budget.values[2],budget.values[4]);pacer.sent(0.,60000)
        await pacer.wait(budget.values[2],budget.values[4])
        self.assertAlmostEqual(clock.value,6/.85)

    async def test_exact_frame_and_decoded_state_not_fabricated_from_legacy_ack(self):
        class Socket:
            def __init__(self,items):self.items=iter(items)
            async def recv(self):return json.dumps(next(self.items))
        replies=[{'type':'frame_ack','frame_count':True,'decode_status':'decoded'},
                 {'type':'frame_ack','frame_count':1.9,'decode_status':'decoded'},
                 {'type':'frame_ack','frame_count':'1','decode_status':'decoded'},
                 {'type':'frame_ack','frame_count':2,'decode_status':'decoded'},
                 {'type':'frame_ack','frame_count':1,'decode_status':'timeout'}]
        result=await wait_for_camera_decode_ack(Socket(replies),1)
        self.assertEqual(result,{'frame_count':1,'decode_status':'timeout'})
        result=await wait_for_camera_decode_ack(Socket([{'type':'frame_ack','frame_count':3}]),3)
        self.assertEqual(result['decode_status'],'unknown')

    async def test_obsolete_ack_noise_does_not_extend_absolute_deadline(self):
        class Socket:
            async def recv(self):
                await asyncio.sleep(.002)
                return '{"type":"frame_ack","frame_count":99,"decode_status":"decoded"}'
        started=asyncio.get_running_loop().time()
        self.assertIsNone(await wait_for_camera_decode_ack(Socket(),1,.1))
        self.assertLess(asyncio.get_running_loop().time()-started,.3)


class ShapedCameraSocketTest(unittest.IsolatedAsyncioTestCase):
    async def test_actual_websocket_slow_tcp_reader_retains_floor_and_latest_only(self):
        """TCP bytes are paced, not just ACK sleeps; this is local, not a WAN claim."""
        import websockets
        asyncio.get_running_loop().set_debug(False)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        frame=np.random.default_rng(803).integers(0,256,(720,1280,3),np.uint8)
        original=hashlib.sha256(frame.tobytes()).hexdigest()
        state=CameraState();state.update_frame('camera',1,0,frame,'bgr8')
        stop=threading.Event();server=asyncio.create_task(serve_websocket(state,'127.0.0.1',port,'/ws/camera',stop))
        for _ in range(100):
            if server.done():await server
            try:
                _,writer=await asyncio.open_connection('127.0.0.1',port);writer.close();await writer.wait_closed();break
            except OSError:await asyncio.sleep(.02)
        sessions=set()
        async def proxy(client_reader,client_writer):
            sessions.add(asyncio.current_task());remote_writer=None
            async def relay(reader,writer,pace):
                while True:
                    chunk=await reader.read(8192)
                    if not chunk:return
                    if pace:await asyncio.sleep(len(chunk)/15625)  # 0.125 Mbps byte service
                    writer.write(chunk);await writer.drain()
            try:
                remote_reader,remote_writer=await asyncio.open_connection('127.0.0.1',port)
                a=asyncio.create_task(relay(client_reader,remote_writer,False));b=asyncio.create_task(relay(remote_reader,client_writer,True))
                try:await asyncio.wait((a,b),return_when=asyncio.FIRST_COMPLETED)
                finally:
                    a.cancel();b.cancel()
                    await asyncio.gather(a,b,return_exceptions=True)
            finally:
                client_writer.close()
                if remote_writer:remote_writer.close()
                sessions.discard(asyncio.current_task())
        gateway=await asyncio.start_server(proxy,'127.0.0.1',0)
        proxy_port=gateway.sockets[0].getsockname()[1]
        async def produce():
            count=2
            while True:
                state.update_frame('camera',count,0,frame,'bgr8');count+=1
                await asyncio.sleep(1/30)
        producer=asyncio.create_task(produce())
        try:
            uri=f'ws://127.0.0.1:{proxy_port}/ws/camera?profile=auto&transport=binary-v1&flow=decode-ack'
            async with websockets.connect(uri,open_timeout=3,close_timeout=.5) as ws:
                async def read_frame():
                    while True:
                        value=await ws.recv()
                        if not isinstance(value,bytes):continue
                        size=int.from_bytes(value[5:9],'big');meta=json.loads(value[9:9+size])
                        image=cv2.imdecode(np.frombuffer(value[9+size:],np.uint8),1)
                        self.assertEqual(image.shape[:2],(360,640));self.assertEqual(meta['preview_quality'],70)
                        return meta
                first=await asyncio.wait_for(read_frame(),8)
                await asyncio.sleep(.12)
                with self.assertRaises(asyncio.TimeoutError):await asyncio.wait_for(read_frame(),.08)
                expected_newest=state.snapshot(False)['frame_count']
                await ws.send(json.dumps({'type':'frame_ack','frame_count':first['frame_count'],'decode_status':'decoded'}))
                second=await asyncio.wait_for(read_frame(),9)
                self.assertGreaterEqual(second['frame_count'],expected_newest)
                self.assertEqual(second['effective_profile'],'readable')
                self.assertEqual(second['delivery_state'],'bandwidth_limited')
                await ws.send(json.dumps({'type':'frame_ack','frame_count':second['frame_count'],'decode_status':'decoded'}))
                self.assertEqual(second['ack_deadline_s'],8)
                print('CAMERA_SHAPED_LOCAL_TCP',json.dumps({'bytes_per_second_limit':15625,'jpeg_bytes':second['encoded_bytes'],'first_frame':first['frame_count'],'next_frame':second['frame_count'],'ack_ms':second['ack_latency_ms'],'width':second['width'],'quality':second['preview_quality'],'ack_deadline_s':second['ack_deadline_s']}),flush=True)
        finally:
            producer.cancel()
            with suppress(asyncio.CancelledError):await producer
            gateway.close();await gateway.wait_closed()
            for task in list(sessions):task.cancel()
            await asyncio.gather(*list(sessions),return_exceptions=True)
            stop.set();await asyncio.wait_for(server,3)
        self.assertEqual(hashlib.sha256(frame.tobytes()).hexdigest(),original)
