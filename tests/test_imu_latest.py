import asyncio,json,socket,threading,time,unittest
from types import SimpleNamespace
from scripts.imu_ws_bridge import ImuBridgeState,serve

class LatestImuTest(unittest.IsolatedAsyncioTestCase):
    async def test_latest_flow_does_not_wait_for_network_ack(self):
        import websockets
        asyncio.get_running_loop().set_debug(False)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        state=ImuBridgeState();state.raw_count=1;stop=threading.Event()
        args=SimpleNamespace(host='127.0.0.1',port=port,path='/ws/imu',max_hz=20)
        task=asyncio.create_task(serve(state,args,stop))
        try:
            for _ in range(100):
                try:
                    reader,writer=await asyncio.open_connection('127.0.0.1',port)
                    writer.close();await writer.wait_closed();break
                except OSError:await asyncio.sleep(.01)
            async with websockets.connect(f'ws://127.0.0.1:{port}/ws/imu?profile=auto&flow=latest') as ws:
                frames=[];started=time.monotonic()
                while len(frames)<7:
                    value=json.loads(await asyncio.wait_for(ws.recv(),1))
                    if value.get('type')=='imu':
                        frames.append(value);state.raw_count+=1
                elapsed=time.monotonic()-started
                self.assertLess(elapsed,1.5)
                self.assertEqual(frames[-1]['raw_count'],7)
                self.assertTrue(all(f['ack_required'] is False for f in frames))
                print('Latest IMU delivered 7 distinct samples without any ACK',round(elapsed,3),'s',flush=True)
        finally:
            stop.set();await asyncio.wait_for(task,3)

if __name__=='__main__':unittest.main()
