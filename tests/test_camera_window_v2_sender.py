"""Actual async sender/receiver with in-memory WS; no network/GPU/ROS."""
import asyncio,json,sys,threading,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.camera_ws_bridge import serve_websocket
from scripts.camera_stream_policy import CameraAckWindow

async def main():
    began=time.perf_counter();stop=threading.Event();windows=[]
    class TrackedWindow(CameraAckWindow):
        def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);windows.append(self)
    class State:
        sim_display_cadence=None;frame_notify=None;counter=1;clients=0
        def snapshot(self,include_frame=False):return dict(client_count=self.clients)
        def preview_snapshot(self,include_jpeg=False):
            return dict(frame_count=self.counter,frame_id='camera',stamp_s=float(self.counter),last_frame_age_sec=0.,
                source_width=1920,source_height=1080,source_fps=30.)
        def adjust_clients(self,n):self.clients+=n
    state=State()
    class Cache:
        async def latest(self,state,settings,budget,compressed):
            return state.preview_snapshot(),(b'not-a-real-JPEG',0.,640,360,1920,1080)
    class Socket:
        path='/ws/camera?transport=binary-v1&flow=decode-ack-v2&ack_window=3&profile=detail&resolution=640x360&fps=30'
        def __init__(self):self.received=asyncio.Queue();self.frames=[];self.tasks=[];self.acks=0;self.closes=[]
        async def recv(self):return await self.received.get()
        async def send(self,message):
            if not isinstance(message,bytes):return
            size=int.from_bytes(message[5:9],'big');meta=json.loads(message[9:9+size]);self.frames.append(meta);state.counter+=1
            async def reply():
                await asyncio.sleep(.08)
                ack={k:meta[k]for k in('ack_protocol','stream_session','source_epoch','delivery_sequence','frame_count')}
                ack.update(type='frame_ack',decode_status='decoded');await self.received.put(json.dumps(ack));self.acks+=1
                if self.acks>=4:stop.set()
            self.tasks.append(asyncio.create_task(reply()))
        async def close(self,**kw):self.closes.append(kw);stop.set()
    ws=Socket()
    class Server:
        def __init__(self,handler,*args,**kwargs):self.handler=handler
        async def __aenter__(self):self.task=asyncio.create_task(self.handler(ws));return self
        async def __aexit__(self,*args):
            stop.set();await asyncio.wait_for(self.task,.3)
            for t in ws.tasks:t.cancel()
            await asyncio.gather(*ws.tasks,return_exceptions=True)
    with patch('websockets.serve',Server),patch('scripts.camera_ws_bridge.CameraPreviewCache',Cache),patch('scripts.camera_stream_policy.CameraAckWindow',TrackedWindow):
        await asyncio.wait_for(serve_websocket(state,'127.0.0.1',0,'/ws/camera',stop),.8)
    assert len(windows)==1 and windows[0].peak_frames==3 and windows[0].closed
    assert windows[0].bytes==0 and not windows[0].pending and state.clients==0
    assert not ws.closes and len(ws.frames)>=4
    assert [m['delivery_sequence']for m in ws.frames]==list(range(1,len(ws.frames)+1))
    assert all(m['ack_protocol']==2 and m['max_inflight_frames']==3 for m in ws.frames)
    delivered_frames=len(ws.frames)
    # A blocked byte/FPS pacer must not keep an expired ACK/window alive.
    stop=threading.Event();state=State();cancelled=[]
    class NoAckSocket(Socket):
        async def send(self,message):
            if isinstance(message,bytes):
                size=int.from_bytes(message[5:9],'big');self.frames.append(json.loads(message[9:9+size]));state.counter+=1
    class SlowPacer:
        def __init__(self):self.calls=0
        async def wait(self,*args):
            self.calls+=1
            if self.calls>1:
                try:await asyncio.sleep(10)
                finally:cancelled.append(True)
            return 0.
        def sent(self,*args):pass
    ws=NoAckSocket()
    with patch('websockets.serve',Server),patch('scripts.camera_ws_bridge.CameraPreviewCache',Cache),patch('scripts.camera_stream_policy.CameraAckWindow',TrackedWindow),patch('scripts.camera_ws_bridge.CAMERA_ACK_TIMEOUT_S',.05),patch('scripts.stream_policy.FramePacer',SlowPacer):
        await asyncio.wait_for(serve_websocket(state,'127.0.0.1',0,'/ws/camera',stop),.5)
    assert cancelled and ws.closes==[dict(code=1013,reason='preview acknowledgement timeout')]
    assert windows[-1].closed and windows[-1].bytes==0 and state.clients==0
    # Nor may a later blocked send extend an older frame's deadline.
    stop=threading.Event();state=State();send_cancelled=[]
    class BlockedSendSocket(NoAckSocket):
        async def send(self,message):
            if isinstance(message,bytes)and self.frames:
                try:await asyncio.sleep(10)
                finally:send_cancelled.append(True)
            await super().send(message)
    class FastPacer:
        async def wait(self,*args):return 0.
        def sent(self,*args):pass
    ws=BlockedSendSocket()
    with patch('websockets.serve',Server),patch('scripts.camera_ws_bridge.CameraPreviewCache',Cache),patch('scripts.camera_stream_policy.CameraAckWindow',TrackedWindow),patch('scripts.camera_ws_bridge.CAMERA_ACK_TIMEOUT_S',.05),patch('scripts.stream_policy.FramePacer',FastPacer):
        await asyncio.wait_for(serve_websocket(state,'127.0.0.1',0,'/ws/camera',stop),.5)
    assert send_cancelled and ws.closes==[dict(code=1013,reason='preview acknowledgement timeout')]
    assert windows[-1].closed and windows[-1].bytes==0 and state.clients==0
    assert time.perf_counter()-began<1.
    print(json.dumps(dict(ok=True,actual_async_product_sender=True,actual_network=False,actual_JPEG=False,
        source_and_image_fixture_only=True,peak_inflight_frames=windows[0].peak_frames,frames=delivered_frames,
        final_pending=0,final_inflight_bytes=0,final_client_count=state.clients,
        expired_ACK_interrupts_blocked_pacer=True,older_ACK_deadline_interrupts_later_blocked_send=True,
        wall_seconds=time.perf_counter()-began)))
if __name__=='__main__':asyncio.run(main())
