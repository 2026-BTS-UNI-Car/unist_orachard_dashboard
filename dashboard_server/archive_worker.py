"""Default-OFF archive-only child. JSON results stay in owned files, never IPC."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from collections import OrderedDict

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard_server.archive_protocol import (SCHEMA,MAX_BODY,MAX_MESSAGE,ArchiveProcessError,
    cache_budget,decode,encode,guard_cache,hex_id,need,no_link,output_path,params,root_paths,source_stamp)

def owned_parent(parent):
    need(type(parent) is int and parent>0,'archive_parent_identity')
    if sys.platform=='linux':
        import ctypes
        need(os.getppid()==parent,'archive_parent_changed')
        need(ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL,0,0,0)==0,'archive_parent_death_guard')
        need(os.getppid()==parent,'archive_parent_changed')
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(1<<30,1<<30))
        resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_BODY+(1<<20),MAX_BODY+(1<<20)))
        resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
    elif sys.platform=='win32':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        handle=kernel.OpenProcess(0x100000,False,parent)
        need(bool(handle),'archive_parent_handle')
        def monitor():
            if kernel.WaitForSingleObject(handle,0xffffffff)==0:os._exit(125)
        threading.Thread(target=monitor,daemon=True,name='archive-parent-only').start()
    else:raise ArchiveProcessError('archive_worker_platform_unsupported')

def encode_body(root,rid,value,wants_gzip,deadline):
    plain=output_path(root,rid,'.plain.tmp');wire=output_path(root,rid,'.wire.tmp');final=output_path(root,rid)
    size=0
    need(not os.path.lexists(final),'archive_existing_response_preserved')
    encoder=json.JSONEncoder(ensure_ascii=False,separators=(',',':'))
    with plain.open('xb') as output:
        for part in encoder.iterencode(value):
            need(time.monotonic()<deadline,'archive_worker_deadline')
            raw=part.encode('utf-8');size+=len(raw)
            need(size<=MAX_BODY,'archive_response_too_large',413)
            output.write(raw)
        output.flush();os.fsync(output.fileno())
    encoding='identity'
    if wants_gzip and size>=1024:
        with plain.open('rb') as source,wire.open('xb') as raw:
            with gzip.GzipFile(fileobj=raw,mode='wb',filename='',compresslevel=1) as compressed:
                for block in iter(lambda:source.read(1<<20),b''):
                    need(time.monotonic()<deadline,'archive_worker_deadline')
                    compressed.write(block)
                    need(raw.tell()<=MAX_BODY,'archive_response_too_large',413)
            raw.flush();os.fsync(raw.fileno())
        plain.unlink();os.link(wire,final);wire.unlink();encoding='gzip'
    else:os.link(plain,final);plain.unlink()
    digest=hashlib.sha256();wire_size=0
    with final.open('rb') as source:
        for block in iter(lambda:source.read(1<<20),b''):
            need(time.monotonic()<deadline,'archive_worker_deadline')
            digest.update(block);wire_size+=len(block)
    need(0<wire_size<=MAX_BODY,'archive_response_too_large',413)
    return {'file':rid+'.body','bytes':wire_size,'sha256':digest.hexdigest(),'encoding':encoding,'decoded_bytes':size}

def dispatch(provider,op,value):
    if op=='recordings':return provider.recordings()
    if op=='detail':return provider.recording_detail(value['recording_id'])
    if op=='timeline':
        return provider.recording_timeline(value['recording_id'],start_s=value['start_s'],
          duration_s=value['duration_s'],profile=value['profile'])
    if op=='prewarm':
        provider._prewarm_playback_cache(value['recording_id'])
        return {'ok':True,'prewarm_attempted':True}
    raise ArchiveProcessError('archive_operation_not_allowed',400)

def run(config_path):
    no_link(config_path)
    need(config_path.stat().st_size<=MAX_MESSAGE,'archive_config_limit')
    config=decode(config_path.read_bytes())
    need(set(config)=={'schema','nonce','generation','parent_PID','archive','cache','recovery_cache','output'}
         and config['schema']==SCHEMA and hex_id(config['nonce'],64)
         and type(config['generation']) is int and config['generation']>0,'archive_worker_config')
    root,cache=root_paths(config['archive'],config['cache'])
    output=no_link(config['output']).resolve()
    need(output.parent==cache and output.name.startswith('archive-http-') and output.is_dir(),
         'archive_owned_output_directory')
    owned_parent(config['parent_PID'])
    # Explicit archive factory: no normal Provider init, fan/proc/recorder/sensors.
    from dashboard_server.telemetry import TelemetryProvider
    provider=TelemetryProvider.for_archive(root,cache,recovery_cache=config['recovery_cache'])
    common={'schema':SCHEMA,'nonce':config['nonce'],'generation':config['generation']}
    sys.stdout.buffer.write(encode({**common,'kind':'ready','PID':os.getpid(),'archive_only':True}));sys.stdout.buffer.flush()
    previous=OrderedDict()
    while True:
        raw=sys.stdin.buffer.readline(MAX_MESSAGE+1)
        if not raw:return 0
        request=decode(raw)
        need(set(request)=={'schema','nonce','generation','id','op','params','gzip','deadline'}
             and all(request[k]==v for k,v in common.items()) and hex_id(request['id'])
             and type(request['gzip']) is bool and type(request['deadline']) in (int,float)
             and time.monotonic()<request['deadline']<=time.monotonic()+30.0,'archive_rpc_request')
        value=params(request['op'],request['params']);rid=request['id'];op=request['op'];deadline=request['deadline']
        reply={**common,'kind':'response','id':rid,'ok':False}
        try:
            before=source_stamp(root,op,value,full=True)
            if op in ('timeline','prewarm'):
                guard_cache(cache,value['recording_id'])
                guard_cache(config['recovery_cache'],value['recording_id'],recovery_only=True)
                provider._archive_cache_budget=cache_budget(cache,output)
            identity=value.get('recording_id',op)
            if previous.get(identity) not in (None,before):
                provider._timeline_cache.clear();provider._light_timeline_cache.clear()
            previous[identity]=before
            previous.move_to_end(identity)
            while len(previous)>8:previous.popitem(last=False)
            data=dispatch(provider,op,value)
            metadata=encode_body(output,rid,data,request['gzip'],deadline)
            need(source_stamp(root,op,value,full=True)==before,'archive_source_changed',409)
            reply.update(ok=True,body=metadata,source_fingerprint=before)
        except BaseException as error:
            reply.update(error=error.code if isinstance(error,ArchiveProcessError) else 'archive_worker_request_failed',
              status=error.status if isinstance(error,ArchiveProcessError) else 503)
        sys.stdout.buffer.write(encode(reply));sys.stdout.buffer.flush()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--worker',action='store_true')
    parser.add_argument('--config',type=Path);args=parser.parse_args()
    if not args.worker:
        print(json.dumps({'default_OFF':True,'archive_or_device_reads':False}));return 0
    need(args.config is not None,'archive_explicit_owned_config')
    return run(args.config)

if __name__=='__main__':raise SystemExit(main())
