"""Persist renderer results beside raw archives, without rewriting raw data."""
import base64
import json
from pathlib import Path
import re
import threading
from urllib.request import Request, urlopen

from dashboard_server.occupancy_map import atomic_json, read_json


class ArchiveMapping:
    def __init__(self, root, renderer='http://127.0.0.1:8810'):
        self.root=Path(root);self.renderer=renderer.rstrip('/')
        self.lock=threading.Lock();self.running=set();self.errors={}

    def directory(self, archive_id):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',archive_id) or archive_id in ('.','..'):
            raise ValueError('invalid_archive_id')
        path=self.root/archive_id
        if not path.is_dir() or path.is_symlink():raise ValueError('archive_not_found')
        return path

    def status(self, archive_id):
        path=self.directory(archive_id)
        value=read_json(path/'dashboard_occupancy_grid.json')
        with self.lock:
            return {**value,'ok':True,'state':'regenerating' if archive_id in self.running else 'saved' if value else 'not_generated',
                    'regeneration_error':self.errors.get(archive_id)}

    def regenerate(self, archive_id):
        self.directory(archive_id)
        with self.lock:
            if archive_id in self.running:return {'ok':True,'state':'regenerating'}
            # One CPU-intensive archive render at a time; live streams retain
            # their separate latest-only readers and hardware owners.
            if self.running:raise ValueError('다른 지도를 만드는 중입니다. 완료 후 다시 시도하세요')
            self.running.add(archive_id);self.errors.pop(archive_id,None)
        threading.Thread(target=self._render,args=(archive_id,),daemon=True,name='archive-map').start()
        return {'ok':True,'state':'regenerating'}

    def _render(self, archive_id):
        session_id=None
        try:
            body=json.dumps({'archive_id':archive_id}).encode()
            with urlopen(Request(self.renderer+'/api/v1/sessions',data=body,headers={'Content-Type':'application/json'}),timeout=180) as response:
                session=json.load(response)
            session_id=session['session_id']
            duration=float(session.get('duration_s') or 0)
            with urlopen(self.renderer+f'/api/v1/sessions/{session_id}/frame.png?t_s={duration}',timeout=180) as response:
                png=response.read(16*1024*1024+1)
                meta=json.loads(response.headers['X-OGM-Meta'])
            if len(png)>16*1024*1024:raise ValueError('render_png_too_large')
            import cv2
            import numpy as np
            image=cv2.imdecode(np.frombuffer(png,dtype=np.uint8),cv2.IMREAD_COLOR)
            if image is None or image.shape[0]*image.shape[1]>4_194_304:raise ValueError('render_size_exceeded')
            # Existing renderer uses screen-style max-Y-first PNG rows.
            ok,encoded=cv2.imencode('.png',cv2.flip(image,0))
            if not ok:raise ValueError('render_encode_failed')
            xmin,ymin,xmax,ymax=meta['render_world_bounds_m']
            h,w=image.shape[:2];resolution=(xmax-xmin)/w
            if abs(resolution-(ymax-ymin)/h)>.001:raise ValueError('non_square_grid_scale')
            value=dict(ok=True,scan_count=int(meta.get('frame_index',0))+1,version=1,map_epoch='archive-'+archive_id,
                       coordinate_frame='archive_unqualified_lidar_imu_display',path=[],pose=None,
                       runtime_authority=False,source='raw_recorded_sensors',renderer_metadata=meta,
                       grid=dict(width=w,height=h,resolution=resolution,origin=dict(x=xmin,y=ymin),encoding='png',row_order='min_y_first',image_base64=base64.b64encode(encoded).decode()))
            atomic_json(self.directory(archive_id)/'dashboard_occupancy_grid.json',value)
        except Exception as error:
            with self.lock:self.errors[archive_id]=str(error)[:240]
        finally:
            if session_id:
                try:
                    with urlopen(Request(self.renderer+f'/api/v1/sessions/{session_id}',method='DELETE'),timeout=5):pass
                except OSError:pass
            with self.lock:self.running.discard(archive_id)
