"""Single live sensor mapper: bounded inboxes and immutable HTTP generations.

Stop disconnects private raw subscriptions and stops registration. Existing
hardware owners/recorder continue. Resume needs observed re-localization before
inserting unknown travel; stopped-time motion is never invented.
"""
import asyncio
from collections import deque
import json
import math
import os
import threading
import time
import uuid
import numpy as np
from .live_mapping import LiveMapping
from .continuous_mapping import ContinuousMapping
from .tile_transport import StreamingOccupancyMap
from .occupancy_map import atomic_json


class SourceLiveMapping(LiveMapping):
    def __init__(self,state_dir,start_worker=True):
        self.engine=None;self.cell_snapshot=None
        super().__init__(state_dir,start_worker=False)
        self.engine=ContinuousMapping()
        if not self.version:self.map=self.engine.map
        elif self.restored_snapshot is None:
            self.map=StreamingOccupancyMap.load_checkpoint(self.state_dir/'last_native_map.json')
            for key,tile in self.map._tiles.items():
                states=self.map.cell_states(tile);self.map.tile_classes[key]=states
                self.map.tile_versions[key]=self.map.scan_count
                ys,xs=np.nonzero(states==2)
                if len(xs):self.map.object_bounds[key]=(key[0]*64+int(xs.min()),key[1]*64+int(ys.min()),
                    key[0]*64+int(xs.max())+1,key[1]*64+int(ys.max())+1)
        self.pending_scans=deque(maxlen=8);self.imu_history=deque(maxlen=100)
        self.wakeup=threading.Event();self.processing_lock=threading.RLock()
        self.command_lock=threading.RLock();self.work_generation=0
        self.dropped_source_scans=0;self.source_times={};self.connections={}
        self.last_progress=None;self.last_attempt=None;self.processing=False
        self.received_counts={'scan':0,'imu':0,'gps':0}
        self._publish()
        if start_worker:
            sources=[('scan',os.environ.get('LIDAR_WS_UPSTREAM','ws://127.0.0.1:8765/ws/lidar')+'?profile=mapping'),
                     ('imu',os.environ.get('IMU_WS_UPSTREAM','ws://127.0.0.1:8775/ws/imu')+'?profile=mapping'),
                     ('gps',os.environ.get('GPS_WS_UPSTREAM','ws://127.0.0.1:8778/ws/gps')+'?profile=detail')]
            for kind,url in sources:
                thread=threading.Thread(target=lambda k=kind,u=url:asyncio.run(self._receive(k,u)),daemon=True,name='map-source-'+kind)
                thread.start();self.threads.append(thread)
            thread=threading.Thread(target=self._run,daemon=True,name='source-rate-integrated-map')
            thread.start();self.threads.append(thread)

    async def _receive(self,kind,url):
        import websockets
        last_stamp=None
        while not self.stop_event.is_set():
            if not self.tracking:
                self.connections[kind]='idle';await asyncio.sleep(.2);continue
            try:
                async with websockets.connect(url,max_queue=2,max_size=2**20,open_timeout=3,close_timeout=1) as socket:
                    self.connections[kind]='connected'
                    while self.tracking and not self.stop_event.is_set():
                        try:packet=json.loads(await asyncio.wait_for(socket.recv(),.25))
                        except asyncio.TimeoutError:continue
                        if kind=='gps' and packet.get('stream_seq'):
                            await socket.send(json.dumps({'ack':packet['stream_seq']}))
                        if packet.get('type')!=kind:continue
                        row=(packet,time.monotonic())
                        with self.inbox_lock:
                            if not self.tracking:continue
                            self.latest[kind]=row;self.source_times[kind]=row[1];self.received_counts[kind]+=1
                            if kind=='imu':self.imu_history.append(row)
                            if kind=='scan' and packet.get('stamp')!=last_stamp:
                                if len(self.pending_scans)==self.pending_scans.maxlen:self.dropped_source_scans+=1
                                self.pending_scans.append(row);last_stamp=packet.get('stamp')
                        self.wakeup.set()
            except (OSError,ValueError,websockets.exceptions.WebSocketException):
                self.connections[kind]='reconnecting';await asyncio.sleep(.5)

    def _run(self):
        while not self.stop_event.is_set():
            self.wakeup.wait(.1 if self.tracking else .5);self.wakeup.clear()
            if not self.tracking:continue
            with self.inbox_lock:
                if not self.pending_scans:continue
                scan=self.pending_scans[0];history=list(self.imu_history);gps=self.latest.get('gps')
            def delta(row):
                try:
                    stamp=row[0]['raw']['stamp'];return abs(stamp['sec']+stamp['nanosec']*1e-9-scan[0]['stamp'])
                except (KeyError,TypeError):return math.inf
            imu=min(history,key=delta) if history else None
            if (imu is None or delta(imu)>.1) and time.monotonic()-scan[1]<.15:
                self.wakeup.set();time.sleep(.015);continue
            with self.inbox_lock:
                if self.pending_scans and self.pending_scans[0] is scan:self.pending_scans.popleft()
            try:
                if gps and time.monotonic()-gps[1]<1:self.engine.gps(gps[0])
                self.ingest(scan,imu)
            except (KeyError,ValueError,TypeError,OverflowError) as error:
                with self.lock:
                    if self.tracking:self.state='waiting_for_sensors';self.reason=str(error);self._publish()
            except Exception as error:
                # A bad observation cannot silently kill the single worker.
                with self.lock:
                    self.state='worker_error';self.reason=type(error).__name__+': '+str(error)[:160];self._publish()

    def ingest(self,scan,imu,now=None):
        now=time.monotonic() if now is None else now
        frame,received=scan;raw_scan=frame.get('raw_scan')
        if not raw_scan:raise ValueError('waiting_for_original_LaserScan_bridge')
        if not 0<=now-received<=1.5:raise ValueError('stale_original_LaserScan')
        raw=self.native_imu(imu[0]) if imu else None
        with self.inbox_lock:history=list(self.imu_history)
        # HTTP reads never wait for registration. Stop/reset waits for the
        # current scan so no rays arrive after its acknowledged response.
        with self.processing_lock:
            with self.lock:
                if not self.tracking:return False
                engine=self.engine;generation=self.work_generation;draw=self.active
                self.processing=True;self.last_attempt=time.monotonic()
            try:
                for packet,_ in history:
                    native=self.native_imu(packet)
                    if native:engine.imu(native)
                result=engine.add(raw_scan,raw,draw=draw)
                with self.lock:
                    if self.engine is not engine or generation!=self.work_generation:return False
                    self.map=engine.map;self.pose=engine.localizer.pose.copy();self.version+=1
                    eligible=result['accepted'] and result.get('map_eligible',True)
                    self.state=('drawing' if draw else 'paused') if eligible else 'waiting_for_geometry'
                    self.reason=None if eligible else result['reason']
                    if eligible:self.last_progress=time.monotonic()
                    self.diagnostic={**engine.diagnostic,'source_queue_dropped':self.dropped_source_scans,
                        'receipt_to_map_ms':round((time.monotonic()-received)*1000,2)}
                    self._publish()
                return result['accepted']
            finally:self.processing=False

    @staticmethod
    def native_imu(packet):
        raw=packet.get('raw',{})
        if not raw or (raw.get('availability',{}).get('linear_acceleration') or {}).get('available') is False:return None
        return {**raw,'header':dict(stamp=raw['stamp'],frame_id=raw.get('frame_id','imu_link')),
            **{name+'_covariance':raw.get('covariance',{}).get(name,[0]*9)
               for name in ('orientation','angular_velocity','linear_acceleration')}}

    def _publish(self):
        if self.engine is None:return super()._publish()
        if self.restored_snapshot is not None:super()._publish();return
        info=self.engine.info()
        if self.state=='needs_reset':info.update(source='restored_native_cells_new_localization_required')
        self.snapshot=dict(ok=True,simulated=False,active=self.active,state=self.state,reason=self.reason,
            version=self.version,map_epoch=self.map_epoch,tile_epoch=f'{self.map_epoch}:0:0',view_epoch=f'{self.map_epoch}:0',
            scan_count=self.map.scan_count,**info)
        self.snapshot['diagnostic']=dict(self.diagnostic)
        if self.persistence:
            self.snapshot['persistence']=dict(self.persistence)
            if self.persistence.get('native_cells'):self.snapshot['native_checkpoint']='last_native_map.json'
        packet=self.map.tile_packet()
        packet['tiles']=[dict(t,revision=self.map.tile_versions.get((t['x'],t['y']),0)) for t in packet['tiles']]
        self.cell_snapshot={**packet,**self.snapshot}

    def task_status(self):
        now=time.monotonic()
        with self.inbox_lock:
            times=dict(self.source_times);depth=len(self.pending_scans);counts=dict(self.received_counts)
        return dict(id='live',kind='live',priority=0,epoch=self.map_epoch,state=self.snapshot.get('state'),
            active=self.active,tracking=self.tracking,processing=self.processing,
            stage='registering_scan' if self.processing else 'waiting_sensors' if self.tracking and not depth else 'idle' if not self.tracking else 'queued_scan',
            reason=self.snapshot.get('reason'),scan_count=self.map.scan_count,input_scans=self.engine.source.input_scans,
            queue_scans=depth,dropped_scans=self.dropped_source_scans,
            last_progress_age_s=None if self.last_progress is None else now-self.last_progress,
            last_attempt_age_s=None if self.last_attempt is None else now-self.last_attempt,
            source_age_s={k:max(0.,now-v) for k,v in times.items()},received=counts,
            connections=dict(self.connections),diagnostic=dict(self.diagnostic),runtime_authority=False)

    def status(self,since=None,transport=None,tile_since=None,tile_epoch=None,segment=None):
        data=self.cell_snapshot
        if self.restored_snapshot is not None:return super().status(since)
        if data is None:return dict(self.snapshot)
        try:revision=int(tile_since) if tile_epoch==data['tile_epoch'] else -1
        except (ValueError,TypeError):revision=-1
        full=not 0<=revision<=data['tile_version']
        result={**data,'tiles':data['tiles'] if full else [t for t in data['tiles'] if t['revision']>revision],
                'tiles_reset':full,'worker':self.task_status()}
        if self.tracking:
            now=time.monotonic()
            if now-self.source_times.get('scan',0)>1.5:result.update(state='waiting_for_sensors',reason='LiDAR_source_stale')
            elif now-self.source_times.get('imu',0)>1.5:result.update(state='waiting_for_sensors',reason='IMU_source_stale')
        return result

    def action(self,operation,expected_epoch=None):
        with self.command_lock:
            if expected_epoch is not None and expected_epoch!=self.map_epoch:raise ValueError('mapping_epoch_changed_refresh_before_action')
            if operation=='start' and self.active:return self.status(transport='tiles')
            with self.processing_lock,self.lock:
                if operation=='reset':
                    if self.map.scan_count:self.map.save_checkpoint(self.state_dir/f'previous-{self.map_epoch}.json')
                    self.engine.closed=True;self.engine=ContinuousMapping();self.map=self.engine.map
                    self.pose=np.zeros(3);self.map_epoch=uuid.uuid4().hex;self.work_generation+=1
                    self.version=0;self.reason=None;self.active=self.tracking=False;self.state='idle'
                    self.restored_snapshot=None;self.persistence={};self.last_progress=None;self.last_attempt=None
                elif operation=='start':
                    if self.state=='needs_reset':raise ValueError('새 지도를 눌러 위치 추정을 다시 시작하세요')
                    self.active=self.tracking=True;self.state='waiting_for_sensors';self.wakeup.set()
                elif operation=='stop':
                    self.active=self.tracking=False
                    if self.state!='needs_reset':self.state='paused'
                else:raise ValueError('unknown_map_operation')
                with self.inbox_lock:
                    if operation in ('stop','reset'):
                        self.pending_scans.clear();self.imu_history.clear();self.latest.clear()
                self._publish()
                if operation in ('stop','reset'):
                    if self.restored_snapshot is None:
                        try:self.persistence={'saved':True,'native_cells':True,**self.map.save_checkpoint(self.state_dir/'last_native_map.json')}
                        except (OSError,ValueError,TypeError) as error:self.persistence={'saved':False,'error':str(error)[:160]}
                        self._publish()
                    atomic_json(self.state_dir/'last_map.json',self.snapshot)
            return self.status(transport='tiles')
