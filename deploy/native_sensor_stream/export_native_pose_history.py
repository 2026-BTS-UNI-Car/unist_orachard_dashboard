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


def dispatch_live_history(ledger,value):
    """Fixed read-only UDS operations; no start/stop/reset or sensor admission."""
    op=value.get('op')
    if op=='map_live_history_open':
        if set(value)!={'op'}:raise ValueError('exact_live_history_open_required')
        return ledger.open_live_prefix()
    if op=='map_live_history_release':
        if set(value)!={'op','snapshot'}:raise ValueError('exact_live_history_release_required')
        return ledger.release_live_prefix(value['snapshot'])
    if op!='map_live_history_chunk' or set(value)!={'op','snapshot','kind','entry','offset','sha256'}:
        raise ValueError('exact_live_history_chunk_required')
    return ledger.live_prefix_chunk(value['snapshot'],kind=value['kind'],entry=value['entry'],
        offset=value['offset'],expected_sha256=value['sha256'])


def export_live_prefix(request):
    opened=request(dict(op='map_live_history_open'))
    if opened.get('ok') is not True or opened.get('runtime_authority') is not False or opened.get('motor_command_sent') is not False:
        raise ValueError('owned_live_history_open_required')
    token=opened['snapshot']
    def collect(kind,entry,sha):
        raw=bytearray();total=None;offset=0
        while True:
            row=request(dict(op='map_live_history_chunk',snapshot=token,kind=kind,entry=entry,offset=offset,sha256=sha))
            if not (row.get('ok') is True and row.get('snapshot')==token and row.get('kind')==kind and
                    type(row.get('entry')) is int and row['entry']==entry and row.get('sha256')==sha and
                    type(row.get('offset')) is int and row['offset']==offset and
                    row.get('runtime_authority') is False and row.get('motor_command_sent') is False):
                raise ValueError('live_history_chunk_identity_changed')
            if total is None:total=row['total_bytes']
            if type(total) is not int or not 0<total<=1024*1024 or row.get('total_bytes')!=total:
                raise ValueError('bounded_live_history_entry_required')
            part=base64.b64decode(row['data_base64'],validate=True)
            if not 0<len(part)<=65536 or offset+len(part)>total:raise ValueError('bounded_live_history_chunk_required')
            raw.extend(part);offset+=len(part)
            if row['next_offset'] is None:
                if offset!=total:raise ValueError('live_history_entry_truncated')
                break
            if type(row['next_offset']) is not int or row['next_offset']!=offset:
                raise ValueError('live_history_entry_offset_changed')
        if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('live_history_entry_SHA_mismatch')
        return bytes(raw)
    try:
        index=json.loads(collect('index',0,opened['index_sha256']))
        if (index.get('schema')!='ubts.native.live_pose_history.index.v1' or
            len(index['pose_entry_sha256'])!=opened['pose_rows'] or
            len(index['GNSS_entry_sha256'])!=opened['quality_GNSS_pairs'] or
            len(index['pose_entry_sha256'])>4096 or len(index['GNSS_entry_sha256'])>256):
            raise ValueError('bounded_live_history_index_required')
        poses=[];gps=[];size=0
        for i,sha in enumerate(index['pose_entry_sha256']):
            raw=collect('pose',i,sha);length=int.from_bytes(raw[:8],'big')
            if not 0<length<=2048 or len(raw)-8-length<4:raise ValueError('live_pose_entry_framing')
            row=json.loads(raw[8:8+length]);cdr=raw[8+length:]
            if hashlib.sha256(cdr).hexdigest()!=row['scan_sha256']:raise ValueError('live_pose_original_SHA_mismatch')
            row['original_scan_CDR_hex']=cdr.hex();poses.append(row)
            size+=len(raw)
            if size>CAP:raise ValueError('live_history_export_capacity')
        for i,sha in enumerate(index['GNSS_entry_sha256']):
            raw=collect('GNSS',i,sha);gps.append(json.loads(raw));size+=len(raw)
            if size>CAP:raise ValueError('live_history_export_capacity')
        result=dict(schema='ubts.ROOT.reviewed.native_lidar_imu.pose_history.v2',rows=poses,
            original_GNSS_pairs=gps,contains_original_scans=True,
            complete_retained_history=index['complete_retained_history'],ledger_reason=index['ledger_reason'],
            complete_retained_quality_GNSS_history=index['complete_retained_quality_GNSS_history'],
            snapshot_scope='fixed_prefix_at_open',current_run_complete=False,
            snapshot_index_sha256=opened['index_sha256'],source_stamps_unchanged=True,
            host_ROS_stamps_are_receiver_epoch=False,GNSS_correction_applied_to_cells=False,
            fused_occupancy_qualified=False,physical_accuracy_verified=False,
            runtime_authority=False,motor_command_sent=False,autonomy_output_enabled=False)
        raw=json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        if len(raw)>CAP:raise ValueError('live_history_export_capacity')
        return raw,hashlib.sha256(raw).hexdigest()
    finally:
        request(dict(op='map_live_history_release',snapshot=token))


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
    p=argparse.ArgumentParser();p.add_argument('--export-stopped',action='store_true');p.add_argument('--export-live-prefix',action='store_true')
    p.add_argument('--socket',default='/run/ubts-native-stream/observe.sock')
    p.add_argument('--output');p.add_argument('--include-original-scans',action='store_true');a=p.parse_args(argv)
    if not a.export_stopped and not a.export_live_prefix:
        print(json.dumps(dict(default_inert=True,files_written=False,process_started=False,motor_command_sent=False)));return 0
    if a.socket!='/run/ubts-native-stream/observe.sock':raise ValueError('fixed_original_owner_socket_required')
    if a.export_stopped and a.export_live_prefix:raise ValueError('one_export_mode_required')
    raw,sha=(export_live_prefix(lambda v:rpc(a.socket,v)) if a.export_live_prefix else
        export(lambda v:rpc(a.socket,v),include_scans=a.include_original_scans))
    out=Path(a.output).resolve()
    with out.open('xb') as f:f.write(raw)
    print(json.dumps(dict(output=str(out),sha256=sha,bytes=len(raw),source_times_unchanged=True,
        runtime_authority=False,motor_command_sent=False)))
    return 0


if __name__=='__main__':raise SystemExit(main())
