"""Source-rate adapter. Existing USB/ROS owners stay in their bridge services."""
import asyncio
from collections import deque
import json
import math
import os
import threading
import time
from dashboard_server.live_mapping import LiveMapping
from dashboard_server.continuous_mapping import ContinuousMapping
from dashboard_server.tile_transport import StreamingOccupancyMap


class SourceLiveMapping(LiveMapping):
    def __init__(self, state_dir, start_worker=True):
        self.engine=None
        super().__init__(state_dir,start_worker=False)
        self.engine=ContinuousMapping()
        if not self.version:self.map=self.engine.map
        elif self.restored_snapshot is None:
            # A validated native checkpoint can be displayed as cells, even
            # though unknown travel after restart still prohibits resuming it.
            self.map=StreamingOccupancyMap.load_checkpoint(self.state_dir/'last_native_map.json')
        self.pending_scans=deque(maxlen=8);self.imu_history=deque(maxlen=100)
        self.wakeup=threading.Event();self.dropped_source_scans=0
        if start_worker:
            sources=[('scan',os.environ.get('LIDAR_WS_UPSTREAM','ws://127.0.0.1:8765/ws/lidar')+'?profile=mapping'),
                     ('imu',os.environ.get('IMU_WS_UPSTREAM','ws://127.0.0.1:8775/ws/imu')+'?profile=mapping'),
                     ('gps',os.environ.get('GPS_WS_UPSTREAM','ws://127.0.0.1:8778/ws/gps')+'?profile=detail')]
            for kind,url in sources:
                thread=threading.Thread(target=lambda k=kind,u=url:asyncio.run(self._receive(k,u)),daemon=True)
                thread.start();self.threads.append(thread)
            thread=threading.Thread(target=self._run,daemon=True,name='source-rate-integrated-map')
            thread.start();self.threads.append(thread)

    async def _receive(self,kind,url):
        import websockets
        last_stamp=None
        while not self.stop_event.is_set():
            try:
                async with websockets.connect(url,max_queue=2,max_size=2**20,open_timeout=3,close_timeout=1) as socket:
                    while not self.stop_event.is_set():
                        try:packet=json.loads(await asyncio.wait_for(socket.recv(),1))
                        except asyncio.TimeoutError:continue
                        if kind=='gps' and packet.get('stream_seq'):
                            await socket.send(json.dumps({'ack':packet['stream_seq']}))
                        if packet.get('type')!=kind:continue
                        row=(packet,time.monotonic())
                        with self.inbox_lock:
                            self.latest[kind]=row
                            if kind=='imu':self.imu_history.append(row)
                            if kind=='scan' and packet.get('stamp')!=last_stamp:
                                if len(self.pending_scans)==self.pending_scans.maxlen:self.dropped_source_scans+=1
                                self.pending_scans.append(row);last_stamp=packet.get('stamp')
                        self.wakeup.set()
            except (OSError,ValueError,websockets.exceptions.WebSocketException):await asyncio.sleep(1)

    def _run(self):
        while not self.stop_event.is_set():
            self.wakeup.wait(.025);self.wakeup.clear()
            with self.lock:tracking=self.tracking
            with self.inbox_lock:
                if not tracking:self.pending_scans.clear();continue
                if not self.pending_scans:continue
                scan=self.pending_scans[0];history=list(self.imu_history);gps=self.latest.get('gps')
            def delta(row):
                try:
                    stamp=row[0]['raw']['stamp'];return abs(stamp['sec']+stamp['nanosec']*1e-9-scan[0]['stamp'])
                except (KeyError,TypeError):return math.inf
            imu=min(history,key=delta) if history else None
            if (imu is None or delta(imu)>.1) and time.monotonic()-scan[1]<.15:continue
            with self.inbox_lock:
                if self.pending_scans and self.pending_scans[0] is scan:self.pending_scans.popleft()
            if gps and time.monotonic()-gps[1]<1:self.engine.gps(gps[0])
            try:self.ingest(scan,imu)
            except (KeyError,ValueError,TypeError,OverflowError) as error:
                with self.lock:
                    self.state='waiting_for_sensors';self.reason=str(error)
                    if str(error)=='retained_scan_limit_new_map_required':
                        self.active=self.tracking=False;self.state='needs_reset'
                    self._publish()

    def ingest(self,scan,imu,now=None):
        now=time.monotonic() if now is None else now
        frame,received=scan
        raw_scan=frame.get('raw_scan')
        if not raw_scan:raise ValueError('waiting_for_original_LaserScan_bridge')
        if not 0<=now-received<=1.5:raise ValueError('stale_original_LaserScan')
        raw=self.native_imu(imu[0]) if imu else None
        with self.inbox_lock:history=list(self.imu_history)
        with self.lock:
            if not self.tracking:return False
            draw=self.active
            engine=self.engine
        for packet,_ in history:
            native=self.native_imu(packet)
            if native:engine.imu(native)
        result=engine.add(raw_scan,raw,draw=draw)
        with self.lock:
            if self.engine is not engine:return False
            self.map=self.engine.map;self.pose=self.engine.localizer.pose.copy()
            self.version+=1
            self.state=('drawing' if draw else 'paused') if result['accepted'] and result.get('map_eligible',True) else 'waiting_for_geometry'
            self.reason=None if result['accepted'] else result['reason']
            self.diagnostic={**self.engine.diagnostic,'source_queue_dropped':self.dropped_source_scans,
                             'receipt_to_map_ms':round((time.monotonic()-received)*1000,2)}
            self._publish()
        return result['accepted']

    @staticmethod
    def native_imu(packet):
        raw=packet.get('raw',{})
        if not raw or (raw.get('availability',{}).get('linear_acceleration') or {}).get('available') is False:return None
        return {**raw,'header':dict(stamp=raw['stamp'],frame_id=raw.get('frame_id','imu_link')),
            **{name+'_covariance':raw.get('covariance',{}).get(name,[0]*9)
               for name in ('orientation','angular_velocity','linear_acceleration')}}

    def _publish(self):
        if self.engine is None or self.restored_snapshot is not None or (self.state=='needs_reset' and not self.engine.records):
            return super()._publish()
        self.snapshot=dict(ok=True,simulated=False,active=self.active,state=self.state,reason=self.reason,
            version=self.version,map_epoch=f'{self.map_epoch}:{self.engine.segment}:{self.engine.generation}',
            scan_count=self.map.scan_count,**self.engine.info())
        self.snapshot['diagnostic']=dict(self.diagnostic)
        # Persistence validates the UUID base separately from display generations.
        self.snapshot['map_epoch']=self.map_epoch
        self.snapshot['tile_epoch']=f'{self.map_epoch}:{self.engine.segment}:{self.engine.generation}'
        self.snapshot['view_epoch']=f'{self.map_epoch}:{self.engine.segment}'
        if self.persistence:
            self.snapshot['persistence']=dict(self.persistence)
            if self.persistence.get('native_cells'):self.snapshot['native_checkpoint']='last_native_map.json'

    def status(self,since=None,transport=None,tile_since=None,tile_epoch=None,segment=None):
        with self.lock:
            if self.restored_snapshot is not None or (not self.engine or (self.state=='needs_reset' and not self.engine.records)):
                value=super().status(since)
                if transport=='tiles' and hasattr(self.map,'tile_packet') and self.restored_snapshot is None:
                    epoch=f'{self.map_epoch}:restored'
                    try:version=int(tile_since) if tile_epoch==epoch else None
                    except (ValueError,TypeError):version=None
                    return {**{k:v for k,v in value.items() if k not in ('grid','path','pose','stamp')},
                            **self.map.tile_packet(version),'tile_epoch':epoch,'view_epoch':epoch,
                            'source':'restored_native_cells_new_localization_required'}
                return value
            with self.engine.lock:
                self.map=self.engine.map
                self._publish();meta=dict(self.snapshot)
                if transport=='tiles':
                    try:version=int(tile_since) if tile_epoch==meta['tile_epoch'] else None
                    except (ValueError,TypeError):version=None
                    packet=self.map.tile_packet(version)
                else:packet=self.map.snapshot()
                return {**packet,**meta}

    def action(self,operation):
        if operation=='reset':
            with self.lock:
                self.engine.closed=True
                self.engine=ContinuousMapping();self.map=self.engine.map
            value=super().action(operation)
            with self.lock:self.map=self.engine.map;self._publish()
            return self.status(transport='tiles')
        with self.lock, self.engine.lock:
            if operation=='start':self.engine.background_gnss=True
            if operation=='stop':self.engine.background_gnss=False
            value=super().action(operation)
        if operation=='stop':
            for row in self.engine.old_segments:
                row['map'].save_checkpoint(self.state_dir/f'segment-{self.map_epoch}-{row["segment"]}.json')
        return self.status(transport='tiles')
