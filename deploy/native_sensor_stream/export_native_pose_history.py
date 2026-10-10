"""Explicit read-only UDS export into NEW private files; default inert.

The service permits export only after its owned map worker has joined. Every
chunk carries the same immutable full-document SHA; file IO is outside source
callbacks/fit/map workers. This script neither starts nor stops any service.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import socket
import time

CAP=40*1024*1024


def export(request,*,include_scans=False):
    result=bytearray();sha=None;total=None;offset=0
    while True:
        args=dict(op='map_pose_history',offset=offset,include_scans=include_scans)
        if sha is not None:args['sha256']=sha
        row=request(args)
        if not (row.get('ok') is True and row.get('offset')==offset and
                row.get('runtime_authority') is False and row.get('motor_command_sent') is False):
            raise ValueError('owned_stopped_pose_export_required')
        if sha is None:sha,total=row['sha256'],row['total_bytes']
        if type(total) is not int or not 0<total<=CAP or row.get('sha256')!=sha or row.get('total_bytes')!=total:
            raise ValueError('immutable_export_identity_changed')
        part=base64.b64decode(row['data_base64'],validate=True)
        if not 0<len(part)<=65536 or len(result)+len(part)>total:raise ValueError('bounded_export_chunk_required')
        result.extend(part);offset+=len(part)
        if row['next_offset'] is None:
            if offset!=total:raise ValueError('export_truncated')
            break
        if type(row['next_offset']) is not int or row['next_offset']!=offset:raise ValueError('export_offset_changed')
    if hashlib.sha256(result).hexdigest()!=sha:raise ValueError('full_export_SHA_mismatch')
    return bytes(result),sha


def rpc(path,value):
    deadline=time.monotonic()+.6
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(.6);s.connect(path);s.sendall((json.dumps(value,separators=(',',':'))+'\n').encode())
        data=bytearray()
        while b'\n' not in data:
            left=deadline-time.monotonic()
            if left<=0:raise TimeoutError('pose_export_request_deadline')
            s.settimeout(left);part=s.recv(min(131073-len(data),65536))
            if not part or len(data)+len(part)>131072:raise ValueError('bounded_complete_pose_export_reply_required')
            data.extend(part)
        if data.count(b'\n')!=1 or not data.endswith(b'\n'):raise ValueError('pose_export_reply_framing')
        return json.loads(data)


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--export-stopped',action='store_true')
    p.add_argument('--socket',default='/run/ubts-native-stream/observe.sock')
    p.add_argument('--output');p.add_argument('--include-original-scans',action='store_true');a=p.parse_args(argv)
    if not a.export_stopped:
        print(json.dumps(dict(default_inert=True,files_written=False,process_started=False,motor_command_sent=False)));return 0
    if a.socket!='/run/ubts-native-stream/observe.sock':raise ValueError('fixed_original_owner_socket_required')
    raw,sha=export(lambda v:rpc(a.socket,v),include_scans=a.include_original_scans)
    out=Path(a.output).resolve()
    with out.open('xb') as f:f.write(raw)
    print(json.dumps(dict(output=str(out),sha256=sha,bytes=len(raw),source_times_unchanged=True,
        runtime_authority=False,motor_command_sent=False)))
    return 0


if __name__=='__main__':raise SystemExit(main())
