"""Bounded read-only inspection. No ROS, bag repair, reindex, or archive writes."""
from __future__ import annotations
import argparse
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import time
import zlib


class InspectionRefused(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    metadata_bytes: int = 256 * 1024
    input_bytes_per_sidecar: int = 8 * 1024 * 1024
    decoded_bytes_per_sidecar: int = 16 * 1024 * 1024
    line_bytes: int = 512 * 1024
    bag_entries: int = 128
    seconds: float = 5.0

    def __post_init__(self):
        for name, maximum in (('metadata_bytes', 1024*1024),
                              ('input_bytes_per_sidecar', 32*1024*1024),
                              ('decoded_bytes_per_sidecar', 64*1024*1024),
                              ('line_bytes', 1024*1024), ('bag_entries', 256)):
            value=getattr(self,name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('invalid_inspection_limit:'+name)
        if type(self.seconds) not in (int,float) or not .01 <= self.seconds <= 10:
            raise ValueError('invalid_inspection_limit:seconds')


def _relative(name):
    parts=PurePosixPath(name).parts
    if not parts or len(parts)>4 or PurePosixPath(name).is_absolute():
        raise InspectionRefused('invalid_relative_path')
    if any(part in ('.','..') or ':' in part or '\\' in part or len(part)>255 for part in parts):
        raise InspectionRefused('invalid_relative_path')
    return parts


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not (getattr(info,'st_file_attributes',0)&0x400)


def _stamp(info):
    # Reading may change access time. It is deliberately not a mutation proof.
    return info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns


def _windows_open(path, directory):
    """Open existing entry itself; deny write/delete sharing while inspecting.

    All ancestor directory handles remain held. Reparse entries are refused
    before reading or descending, including junctions and symbolic links.
    """
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
                                wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
    kernel.CloseHandle.restype=wintypes.BOOL
    class Info(ctypes.Structure):
        _fields_=[('attributes',wintypes.DWORD),('created',wintypes.FILETIME),
                  ('accessed',wintypes.FILETIME),('written',wintypes.FILETIME),
                  ('volume',wintypes.DWORD),('size_high',wintypes.DWORD),
                  ('size_low',wintypes.DWORD),('links',wintypes.DWORD),
                  ('index_high',wintypes.DWORD),('index_low',wintypes.DWORD)]
    kernel.GetFileInformationByHandle.argtypes=(wintypes.HANDLE,ctypes.POINTER(Info))
    kernel.GetFileInformationByHandle.restype=wintypes.BOOL
    flags=0x00200000 | (0x02000000 if directory else 0x08000000)
    handle=kernel.CreateFileW(str(path),0 if directory else 0x80000000,1,None,3,flags,None)
    if handle==ctypes.c_void_p(-1).value:
        code=ctypes.get_last_error()
        if code in (2,3):raise FileNotFoundError(code,'entry_missing',str(path))
        raise OSError(code,'readonly_open_refused',str(path))
    try:
        info=Info()
        if not kernel.GetFileInformationByHandle(handle,ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(),'handle_information_refused')
        if info.attributes&0x400:raise InspectionRefused('reparse_point_refused')
        if bool(info.attributes&0x10)!=directory:raise InspectionRefused('unexpected_entry_type')
        if directory:return handle,kernel.CloseHandle
        import msvcrt
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
        handle=None  # fd now owns the handle
        return fd,os.close
    finally:
        if handle is not None and not directory:kernel.CloseHandle(handle)
        # Directory handle ownership is transferred only by the return above.
        if handle is not None and directory and ('info' not in locals() or info.attributes&0x400 or not info.attributes&0x10):
            kernel.CloseHandle(handle)


class ReadOnlyTree:
    def __init__(self,path):
        raw=Path(path)
        if '..' in raw.parts:raise InspectionRefused('parent_traversal_refused')
        self.path=Path(os.path.abspath(raw))  # lexical only; never resolve links
        self.stack=ExitStack()
        try:
            if os.name=='posix':
                flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
                fd=os.open(self.path.anchor,flags);self.stack.callback(os.close,fd)
                for part in self.path.parts[1:]:
                    fd=os.open(part,flags,dir_fd=fd);self.stack.callback(os.close,fd)
                self.root_fd=fd
            elif os.name=='nt':
                current=Path(self.path.anchor)
                handle,close=_windows_open(current,True);self.stack.callback(close,handle)
                for part in self.path.parts[1:]:
                    current=current/part
                    handle,close=_windows_open(current,True);self.stack.callback(close,handle)
            else:raise InspectionRefused('secure_read_platform_unavailable')
        except BaseException:
            self.stack.close();raise

    def __enter__(self):return self
    def __exit__(self,*args):self.stack.close()

    @contextmanager
    def directory(self,name):
        parts=_relative(name)
        with ExitStack() as temporary:
            if os.name=='posix':
                fd=self.root_fd
                for part in parts:
                    fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                    temporary.callback(os.close,fd)
                yield fd
            else:
                path=self.path
                for part in parts:
                    path=path/part;handle,close=_windows_open(path,True);temporary.callback(close,handle)
                yield str(path)

    @contextmanager
    def file(self,name):
        parts=_relative(name)
        with ExitStack() as temporary:
            if len(parts)>1:
                parent=temporary.enter_context(self.directory('/'.join(parts[:-1])))
            else:parent=self.root_fd if os.name=='posix' else str(self.path)
            if os.name=='posix':
                fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=parent)
                close=os.close
            else:fd,close=_windows_open(Path(parent)/parts[-1],False)
            temporary.callback(close,fd)
            info=os.fstat(fd)
            if not _regular(info):raise InspectionRefused('non_regular_file_refused')
            yield fd,info


def _strict_json(data):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('duplicate_json_key')
            value[key]=item
        return value
    return json.loads(data.decode('utf-8'),object_pairs_hook=pairs,
                      parse_constant=lambda value:(_ for _ in ()).throw(ValueError('nonfinite_json')))


class Lines:
    def __init__(self,maximum):
        self.maximum=maximum;self.tail=bytearray();self.oversize=False
        self.complete_json_rows=0;self.invalid_complete_rows=0;self.oversize_rows=0

    def feed(self,data):
        while data:
            end=data.find(b'\n');piece=data if end<0 else data[:end]
            if not self.oversize:
                if len(self.tail)+len(piece)>self.maximum:
                    self.tail.clear();self.oversize=True
                else:self.tail.extend(piece)
            if end<0:return
            if self.oversize:self.oversize_rows+=1
            else:
                try:_strict_json(bytes(self.tail));self.complete_json_rows+=1
                except (ValueError,UnicodeError,RecursionError):self.invalid_complete_rows+=1
            self.tail.clear();self.oversize=False;data=data[end+1:]

    def status(self,full):
        partial_valid=None
        if full and self.tail:
            try:_strict_json(bytes(self.tail));partial_valid=True
            except (ValueError,UnicodeError,RecursionError):partial_valid=False
        return dict(complete_json_rows=self.complete_json_rows,invalid_complete_rows=self.invalid_complete_rows,
                    oversize_rows=self.oversize_rows,unterminated_tail_bytes=len(self.tail),
                    unterminated_tail_exceeds_limit=self.oversize,
                    final_unterminated_json_valid=partial_valid,
                    tail_is_artifact_end=full)


def _scan_sidecar(tree,name,limits,deadline,compressed=False):
    result=dict(present=False,rosbag_readproof=False)
    try:
        with tree.file(name) as (fd,before):
            result.update(present=True,size_bytes=before.st_size)
            digest=hashlib.sha256();lines=Lines(limits.line_bytes)
            used=decoded=0;decoder=zlib.decompressobj(16+zlib.MAX_WBITS) if compressed else None
            members=0;error=None;stop=None
            while used<before.st_size:
                if time.monotonic()>=deadline:stop='time_limit';break
                if used>=limits.input_bytes_per_sidecar:stop='input_byte_limit';break
                if decoded>=limits.decoded_bytes_per_sidecar:stop='decoded_byte_limit';break
                data=os.read(fd,min(32768,limits.input_bytes_per_sidecar-used,before.st_size-used))
                if not data:break
                used+=len(data);digest.update(data)
                if not compressed:
                    remaining=limits.decoded_bytes_per_sidecar-decoded
                    output=data[:remaining];decoded+=len(output);lines.feed(output)
                    if len(output)!=len(data):stop='decoded_byte_limit';break
                    continue
                while data:
                    if time.monotonic()>=deadline:stop='time_limit';break
                    if decoded>=limits.decoded_bytes_per_sidecar:stop='decoded_byte_limit';break
                    if decoder.eof:decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
                    try:output=decoder.decompress(data,min(32768,limits.decoded_bytes_per_sidecar-decoded))
                    except zlib.error: error='gzip_stream_or_checksum_invalid';break
                    decoded+=len(output);lines.feed(output)
                    if decoder.eof:
                        members+=1;data=decoder.unused_data
                    else:data=decoder.unconsumed_tail
                if error or stop:break
            after=os.fstat(fd);stable=_stamp(before)==_stamp(after)
            all_input_read=used==before.st_size
            read_full=all_input_read and not stop and not error
            footer=bool(compressed and read_full and decoder.eof and members>0)
            if compressed and read_full and not footer:error='gzip_footer_missing_or_incomplete'
            full=read_full and (not compressed or footer)
            result.update(lines.status(full))
            result.update(input_bytes_read=used,decoded_bytes_read=decoded,
                          input_prefix_sha256=digest.hexdigest(),entire_file_bytes_read=all_input_read,
                          input_hash_scope='entire_file' if all_input_read and stable else 'bytes_read_prefix',
                          inspection_complete=full and stable,read_limit=stop,error=error,
                          snapshot_stable=stable,row_semantics_validated=False,content_integrity_verified=False)
            if compressed:result.update(gzip_footer_valid=footer,gzip_members_completed=members)
    except FileNotFoundError:pass
    except (OSError,InspectionRefused) as error:
        result.update(present=None,inspection_complete=False,error=type(error).__name__+':'+str(error.args[1] if isinstance(error,OSError) and len(error.args)>1 else error)[:96])
    return result


def _metadata(tree,limits,deadline):
    value=dict(present=False,valid_json=False,state=None)
    try:
        with tree.file('metadata.json') as (fd,before):
            value.update(present=True,size_bytes=before.st_size)
            data=bytearray()
            while len(data)<min(before.st_size,limits.metadata_bytes):
                if time.monotonic()>=deadline:
                    value['read_limit']='time_limit';break
                block=os.read(fd,min(32768,limits.metadata_bytes-len(data),before.st_size-len(data)))
                if not block:break
                data.extend(block)
            value.update(bytes_read=len(data),input_prefix_sha256=hashlib.sha256(data).hexdigest(),
                         snapshot_stable=_stamp(before)==_stamp(os.fstat(fd)))
            if len(data)!=before.st_size:value['read_limit']=value.get('read_limit','metadata_byte_limit')
            else:
                try:
                    parsed=_strict_json(bytes(data))
                    if type(parsed) is not dict:raise ValueError('metadata_not_object')
                    value.update(valid_json=True,state=parsed.get('state') if type(parsed.get('state')) is str else None)
                    if value['state'] is not None:value['state']=value['state'][:128]
                except (ValueError,UnicodeError,RecursionError):value['error']='invalid_metadata_json'
    except FileNotFoundError:pass
    except (OSError,InspectionRefused) as error:value.update(present=None,error=type(error).__name__+':'+str(error)[:96])
    return value


def _bag(tree,limits,deadline):
    value=dict(directory_present=False,components=[],inventory_complete=False,
               metadata_yaml_present=False,rosbag_readproof=False,bag_content_read=False)
    try:
        with tree.directory('rosbag2') as folder:
            value['directory_present']=True
            report_bytes=0
            with os.scandir(folder) as entries:
                for entry in entries:
                    if time.monotonic()>=deadline:value['read_limit']='time_limit';break
                    if len(value['components'])>=limits.bag_entries:value['read_limit']='entry_limit';break
                    info=entry.stat(follow_symlinks=False)
                    refused=stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0)&0x400)
                    name=entry.name[:96]
                    item=dict(name=name,name_display_truncated=len(entry.name)>96,
                        original_name_sha256=hashlib.sha256(os.fsencode(entry.name)).hexdigest(),size_bytes=info.st_size,
                        kind='refused_link' if refused else 'regular' if stat.S_ISREG(info.st_mode) else 'unread_directory_or_special',
                        original_content_read=False)
                    item_bytes=len(json.dumps(item,ensure_ascii=True).encode())
                    if report_bytes+item_bytes>24*1024:value['read_limit']='report_byte_limit';break
                    report_bytes+=item_bytes;value['components'].append(item)
                    if not refused and stat.S_ISREG(info.st_mode) and entry.name=='metadata.yaml':value['metadata_yaml_present']=True
                else:value['inventory_complete']=True
    except FileNotFoundError:pass
    except (OSError,InspectionRefused) as error:value.update(directory_present=None,error=type(error).__name__+':'+str(error)[:96])
    return value


def inspect_recording(path,*,limits=None):
    limits=Limits() if limits is None else limits
    if type(limits) is not Limits:raise ValueError('typed_inspection_limits_required')
    deadline=time.monotonic()+limits.seconds
    with ReadOnlyTree(path) as tree:
        meta=_metadata(tree,limits,deadline)
        sidecars={name:_scan_sidecar(tree,name,limits,deadline,name.endswith('.gz'))
                  for name in ('gps.jsonl','motor.jsonl','mapping_source/sensors.jsonl.gz')}
        bag=_bag(tree,limits,deadline)
    reasons=[]
    if meta['state'] in ('recording','finalizing','stopping'):reasons.append('metadata_reports_unfinished_recording')
    if meta.get('error'):reasons.append('metadata_inspection_error')
    if not meta['present']:reasons.append('metadata_missing')
    for name,result in sidecars.items():
        if result.get('error'):reasons.append(name+':'+result['error'])
        if result.get('tail_is_artifact_end') and (result.get('unterminated_tail_bytes') or result.get('unterminated_tail_exceeds_limit')):
            reasons.append(name+':unterminated_final_line')
        if result.get('invalid_complete_rows') or result.get('oversize_rows'):reasons.append(name+':invalid_or_oversize_rows')
    limited=bool(meta.get('read_limit') or any(row.get('read_limit') for row in sidecars.values()) or bag.get('read_limit'))
    changed=meta.get('snapshot_stable') is False or any(row.get('snapshot_stable') is False for row in sidecars.values())
    return dict(schema='ubts.recorder.interrupted_inspection.v1',inspection_performed=True,
        metadata=meta,sidecars=sidecars,bag_components=bag,
        recovery_needed=True if reasons else None,recovery_reasons=reasons,
        recovery_assessment='preserve_original_and_inspect_before_copy_repair' if reasons else 'not_established_by_bounded_inspection',
        bounded_inspection_limited=limited,snapshot_requires_recheck=changed,rosbag_readproof=False,recording_integrity_verified=False,
        artifact_state_changed=False,repair_or_reindex_performed=False,automatic_resume=False,
        original_data_written=False,inspection_may_update_access_time=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect',action='store_true')
    parser.add_argument('--recording-dir')
    args=parser.parse_args(argv)
    if not args.inspect:
        print(json.dumps(dict(mode='inert',inspection_performed=False,original_data_written=False)))
        return 0
    if not args.recording_dir:parser.error('--recording-dir is required with --inspect')
    try:report=inspect_recording(args.recording_dir)
    except (OSError,InspectionRefused) as error:
        report=dict(schema='ubts.recorder.interrupted_inspection.v1',inspection_performed=False,
                    error=type(error).__name__+':'+str(error)[:160],rosbag_readproof=False,
                    original_data_written=False,repair_or_reindex_performed=False)
    encoded=json.dumps(report,ensure_ascii=True,allow_nan=False)
    if len(encoded.encode('utf-8'))>64*1024:raise RuntimeError('bounded_report_limit')
    print(encoded)
    return 0 if report.get('inspection_performed') else 2


if __name__=='__main__':raise SystemExit(main())
