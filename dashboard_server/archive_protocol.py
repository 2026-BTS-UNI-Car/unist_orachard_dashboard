"""Small archive-only IPC contract. No sensors, sockets or Provider imports."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat

SCHEMA='ubts.archive_http_process.v1'
MAX_MESSAGE=4096
MAX_BODY=128 << 20
MAX_WAITERS=4
RPC_SECONDS=30.0
START_SECONDS=3.0
MAX_RECORDINGS=2048
MAX_CACHE_BYTES=512 << 20
MAX_CACHE_FILES=8192
OPS=frozenset(('recordings','detail','timeline','prewarm'))
SOURCE_FILES=('metadata.json','timeline.json','preview_scan.json','gps.jsonl','motor.jsonl',
              'recording_clock.json','video_preparation.json','adaptive_video/adaptive_video.json',
              'mapping_source/manifest.json','mapping_source/sensors.jsonl.gz')

class ArchiveProcessError(ValueError):
    def __init__(self,code,status=503):
        super().__init__(code);self.code=code;self.status=status

def need(ok,code,status=503):
    if not ok:raise ArchiveProcessError(code,status)

def hex_id(value,size=32):
    return isinstance(value,str) and len(value)==size and all(c in '0123456789abcdef' for c in value)

def encode(value):
    raw=json.dumps(value,separators=(',',':'),allow_nan=False).encode()+b'\n'
    need(len(raw)<=MAX_MESSAGE,'archive_rpc_message_limit',400)
    return raw

def decode(raw):
    need(type(raw) is bytes and len(raw)<=MAX_MESSAGE and raw.endswith(b'\n'),'archive_rpc_invalid_frame')
    def pairs(items):
        result={}
        for key,value in items:
            need(key not in result,'archive_rpc_duplicate_key');result[key]=value
        return result
    value=json.loads(raw,object_pairs_hook=pairs,
      parse_constant=lambda _: (_ for _ in ()).throw(ArchiveProcessError('archive_rpc_nonfinite')))
    need(type(value) is dict,'archive_rpc_object_required')
    return dict(value)

def record_id(value):
    need(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,160}',value)
         and value not in ('.','..'),'invalid_recording_id',400)
    return value

def params(op,value):
    need(op in OPS and type(value) is dict,'archive_operation_not_allowed',400)
    if op=='recordings':need(not value,'archive_unexpected_parameters',400);return {}
    wanted={'recording_id','start_s','duration_s','profile'} if op=='timeline' else {'recording_id'}
    need(set(value)==wanted,'archive_unexpected_parameters',400)
    record_id(value['recording_id'])
    if op=='timeline':
        for key in ('start_s','duration_s','profile'):
            item=value[key]
            need(item is None or (isinstance(item,str) and len(item)<=128),
                 'archive_parameter_not_small_text',400)
    return dict(value)

def no_link(path):
    """Reject symlink/reparse components before archive/cache/output operations."""
    path=Path(path).absolute()
    for part in (path,*path.parents):
        try:info=part.lstat()
        except FileNotFoundError:continue
        need(not stat.S_ISLNK(info.st_mode) and not (getattr(info,'st_file_attributes',0)&0x400),
             'archive_link_path_refused',400)
    return path

def root_paths(archive,cache):
    archive=no_link(archive).resolve();cache=no_link(cache).resolve()
    need(archive!=cache and archive not in cache.parents and cache not in archive.parents,
         'archive_and_cache_must_be_separate',400)
    need(not archive.exists() or archive.is_dir(),'archive_root_not_directory',400)
    return archive,cache

def recording_path(root,rid):
    record_id(rid);root=Path(root).resolve();path=no_link(root/rid)
    need(path.parent.resolve()==root and path.resolve().parent==root,'archive_recording_escape',400)
    return path

def signature(path):
    no_link(path)
    try:info=Path(path).stat()
    except FileNotFoundError:return None
    return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns]

def source_stamp(root,op,value,*,full=False):
    """Only metadata/stat: never decodes or hashes recorded sensor payloads."""
    root=Path(root);rows=[['root',signature(root)]]
    if op=='recordings':
        if full and root.is_dir():
            dirs=[]
            for item in root.iterdir():
                if item.is_dir():dirs.append(item)
                need(len(dirs)<=MAX_RECORDINGS,'archive_recording_count_limit')
            for item in sorted(dirs):
                record_id(item.name);recording_path(root,item.name)
                rows.append([item.name,signature(item)])
                for name in SOURCE_FILES:rows.append([item.name+'/'+name,signature(item/name)])
    else:
        path=recording_path(root,value['recording_id'])
        rows.append(['record',signature(path)])
        for name in SOURCE_FILES:rows.append([name,signature(path/name)])
    return hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()

def output_path(root,rid,suffix='.body'):
    need(hex_id(rid),'archive_response_id')
    root=no_link(root).resolve();path=no_link(root/(rid+suffix))
    need(path.parent==root,'archive_response_escape')
    return path

def cache_write_path(root,path):
    root=no_link(root).resolve();path=no_link(path)
    need(root in path.parents,'archive_cache_path_escape',400)
    try:
        info=path.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'archive_cache_nonregular_or_hardlink',400)
    except FileNotFoundError:pass
    return path

def guard_cache(root,rid,*,recovery_only=False):
    """Existing derived data is untrusted for writes, including nested links."""
    record_id(rid);root=no_link(root).resolve()
    selected=[root/'recovered-lidar'/rid] if recovery_only else [root/rid]
    count=0
    for directory in selected:
        no_link(directory)
        if not directory.exists():continue
        need(directory.is_dir(),'archive_cache_not_directory')
        for path in directory.rglob('*'):
            no_link(path);info=path.lstat();count+=1
            need(count<=8192,'archive_cache_file_limit')
            need(stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink==1),
                 'archive_cache_nonregular_or_hardlink',400)

def cache_budget(root,output):
    """Bound only this feature's namespace, excluding its bounded response dir."""
    root=no_link(root).resolve();output=no_link(output).resolve()
    need(output.parent==root,'archive_cache_output_escape')
    files=0;size=0
    for path in root.rglob('*'):
        if path==output or output in path.parents:continue
        no_link(path);info=path.lstat()
        need(stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink==1),
             'archive_cache_nonregular_or_hardlink',400)
        files+=1
        if stat.S_ISREG(info.st_mode):size+=info.st_size
        need(files<=MAX_CACHE_FILES and size<=MAX_CACHE_BYTES,'archive_cache_budget_exceeded',503)
    return {'bytes_left':MAX_CACHE_BYTES-size,'files_left':MAX_CACHE_FILES-files}

def open_response(root,rid,metadata,check=None):
    """Pin the actual regular descriptor; never accept an arbitrary worker path."""
    need(type(metadata) is dict and set(metadata)=={'file','bytes','sha256','encoding','decoded_bytes'},
         'archive_response_metadata')
    need(metadata.get('file')==rid+'.body','archive_response_filename')
    size=metadata.get('bytes');need(type(size) is int and 0<size<=MAX_BODY,'archive_response_size')
    need(hex_id(metadata.get('sha256'),64),'archive_response_digest')
    need(metadata.get('encoding') in ('identity','gzip'),'archive_response_encoding')
    decoded=metadata.get('decoded_bytes')
    need(type(decoded) is int and 0<decoded<=MAX_BODY,'archive_response_decoded_size')
    need(metadata['encoding']!='identity' or decoded==size,'archive_response_decoded_size')
    path=output_path(root,rid)
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    stream=os.fdopen(fd,'rb')
    try:
        before=os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size==size,
             'archive_response_not_regular')
        digest=hashlib.sha256()
        for block in iter(lambda:stream.read(1<<20),b''):
            if check is not None:check()
            digest.update(block)
        if check is not None:check()
        after=os.fstat(stream.fileno())
        need((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
             (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
             and digest.hexdigest()==metadata['sha256'],'archive_response_changed')
        stream.seek(0)
        return stream
    except BaseException:stream.close();raise
