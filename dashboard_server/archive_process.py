"""One bounded archive subprocess; the control process only streams pinned files."""
from __future__ import annotations
import os
from pathlib import Path
import queue
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
from .archive_protocol import (SCHEMA,MAX_MESSAGE,MAX_WAITERS,RPC_SECONDS,START_SECONDS,
    ArchiveProcessError,decode,encode,hex_id,need,no_link,open_response,output_path,params,
    root_paths,source_stamp)

class EncodedArchiveResponse:
    def __init__(self,owner,rid,stream,metadata,deadline):
        self.owner=owner;self.id=rid;self.stream=stream;self.metadata=metadata
        self.deadline=deadline;self.closed=False
        self._close_lock=threading.Lock()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    def chunks(self):
        while True:
            need(not self.owner.closed and time.monotonic()<self.deadline,'archive_response_deadline',504)
            data=self.stream.read(65536)
            if not data:break
            yield data
    def close(self):
        with self._close_lock:
            if self.closed:return
            self.closed=True
        try:self.stream.close()
        finally:self.owner._finish_response(self)

class ArchiveProcess:
    """Default caller feature gate belongs to app.create_server; never opens devices."""
    def __init__(self,archive,cache):
        self.archive,self.original_cache=root_paths(archive,cache)
        self.cache=no_link(self.original_cache/'archive-process-v1').resolve()
        self.cache.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.directory=Path(tempfile.mkdtemp(prefix='archive-http-',dir=self.cache)).resolve()
        self._slots=threading.BoundedSemaphore(MAX_WAITERS);self._serial=threading.Lock();self._state=threading.Lock()
        self._retire_lock=threading.Lock()
        self._process=None;self._inbox=None;self._reader=None;self._log=None
        self._generation=0;self._nonce=None;self._responses=set();self.closed=False
        self._fault=False;self._cleanup_unproven=False;self.cleanup_complete=False

    @staticmethod
    def _environment():
        # Never copy motor/ROS/bridge/config/recorder credentials or endpoints.
        allowed=('PATH','PATHEXT','SYSTEMROOT','SystemRoot','WINDIR','TEMP','TMP','TMPDIR',
                 'HOME','LANG','LC_ALL','PYTHONPATH','LD_LIBRARY_PATH')
        env={k:os.environ[k] for k in allowed if k in os.environ}
        env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
        return env

    def _check(self,deadline,cancelled):
        need(not self.closed,'archive_process_closed')
        need(time.monotonic()<deadline,'archive_process_timeout',504)
        need(not cancelled or not cancelled(),'archive_request_cancelled',499)

    def _receive(self,process,deadline,cancelled):
        while True:
            self._check(deadline,cancelled)
            need(not self._fault,'archive_worker_protocol_failed')
            need(process is self._process and process.poll() is None,'archive_worker_exited')
            log=self._log
            if log is not None:
                need(os.fstat(log.fileno()).st_size<=1<<20,'archive_worker_log_limit')
            try:
                raw=self._inbox.get(timeout=min(.05,max(.001,deadline-time.monotonic())))
            except queue.Empty:continue
            need(raw is not None,'archive_worker_pipe_closed')
            return decode(raw)

    def _start(self,deadline,cancelled):
        need(not self._cleanup_unproven,'archive_worker_cleanup_unproven')
        if self._process is not None:
            if self._process.poll() is None:return self._process
            self._retire(self._process)
        self._check(deadline,cancelled)
        need(self._generation<3,'archive_worker_restart_limit')
        self._generation+=1;self._nonce=secrets.token_hex(32);self._fault=False
        config={'schema':SCHEMA,'nonce':self._nonce,'generation':self._generation,'parent_PID':os.getpid(),
                'archive':str(self.archive),'cache':str(self.cache),'recovery_cache':str(self.original_cache),
                'output':str(self.directory)}
        config_path=self.directory/('worker-'+str(self._generation)+'.json')
        fd=os.open(config_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as output:output.write(encode(config))
        log_path=self.directory/('worker-'+str(self._generation)+'.stderr')
        self._log=os.fdopen(os.open(log_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb',buffering=0)
        # Windows venv executables can be launchers; direct base exe pins PID to
        # our retained Popen handle. Target deployment remains Linux/ARM.
        executable=getattr(sys,'_base_executable',sys.executable) if os.name=='nt' else sys.executable
        worker=Path(__file__).with_name('archive_worker.py').resolve()
        inbox=queue.Queue(maxsize=2)
        try:
            with self._state:
                need(not self.closed,'archive_process_closed')
                process=subprocess.Popen([executable,'-B','-u',str(worker),'--worker','--config',str(config_path)],
                  stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self._log,bufsize=0,
                  env=self._environment(),cwd=str(worker.parent.parent),close_fds=True)
                self._process=process;self._inbox=inbox
        except BaseException:
            self._log.close();self._log=None;raise
        def reader():
            try:
                while True:
                    raw=process.stdout.readline(MAX_MESSAGE+1)
                    if not raw:
                        inbox.put_nowait(None);return
                    if len(raw)>MAX_MESSAGE or not raw.endswith(b'\n'):self._fault=True;return
                    inbox.put_nowait(raw)
            except BaseException:self._fault=True
        self._reader=threading.Thread(target=reader,daemon=True,name='archive-metadata-only')
        self._reader.start()
        ready=self._receive(process,min(deadline,time.monotonic()+START_SECONDS),cancelled)
        need(ready=={'schema':SCHEMA,'nonce':self._nonce,'generation':self._generation,
                    'kind':'ready','PID':process.pid,'archive_only':True},'archive_worker_identity_failed')
        return process

    def _retire(self,process):
        # Retained Popen OS handle/never an arbitrary PID or discovered service.
        try:self._retire_owned(process)
        except BaseException:
            self._cleanup_unproven=True
            raise

    def _retire_owned(self,process):
        with self._retire_lock:
            with self._state:
                if process is not self._process:return
            if process.poll() is None:
                try:process.terminate()
                except OSError:
                    if process.poll() is None:raise
                try:process.wait(timeout=1)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=1)
            for pipe in (process.stdin,process.stdout):
                if pipe is not None:pipe.close()
            if self._reader is not None:
                self._reader.join(timeout=.5)
                need(not self._reader.is_alive(),'archive_metadata_reader_cleanup_unproven')
            if self._log is not None:self._log.close();self._log=None
            # Keep the retained handle reachable on every uncertain/error path.
            with self._state:
                if self._process is process:self._process=None

    def _discard(self,rid):
        for suffix in ('.body','.plain.tmp','.wire.tmp'):
            path=output_path(self.directory,rid,suffix)
            try:
                info=path.lstat()
                need(stat.S_ISREG(info.st_mode),'archive_cleanup_nonregular')
                path.unlink()
            except FileNotFoundError:pass

    def request(self,op,value,*,gzip=False,cancelled=None):
        value=params(op,value)
        need(type(gzip) is bool,'archive_encoding_flag',400)
        self._check(time.monotonic()+RPC_SECONDS,cancelled)
        need(self._slots.acquire(blocking=False),'archive_process_busy',429)
        deadline=time.monotonic()+RPC_SECONDS;locked=False;response=None;rid=secrets.token_hex(16);process=None
        try:
            while not self._serial.acquire(timeout=.05):self._check(deadline,cancelled)
            locked=True;self._check(deadline,cancelled)
            before=source_stamp(self.archive,op,value,full=True)
            process=self._start(deadline,cancelled)
            generation,nonce=self._generation,self._nonce
            message={'schema':SCHEMA,'nonce':nonce,'generation':generation,'id':rid,'op':op,
                     'params':value,'gzip':gzip,'deadline':deadline}
            # One <=4096-byte command, only after the preceding reply/ready.
            process.stdin.write(encode(message));process.stdin.flush()
            reply=self._receive(process,deadline,cancelled)
            need(reply.get('schema')==SCHEMA and reply.get('nonce')==nonce
                 and reply.get('generation')==generation and reply.get('id')==rid
                 and reply.get('kind')=='response' and self._generation==generation,
                 'archive_response_epoch_mismatch')
            if reply.get('ok') is not True:
                code=reply.get('error');need(isinstance(code,str) and len(code)<=80,'archive_worker_error_invalid')
                raise ArchiveProcessError(code,reply.get('status') if reply.get('status') in (400,409,413,503,504) else 503)
            need(hex_id(reply.get('source_fingerprint'),64) and reply['source_fingerprint']==before,
                 'archive_source_fingerprint')
            need(source_stamp(self.archive,op,value,full=True)==before,'archive_source_changed',409)
            self._check(deadline,cancelled)
            stream=open_response(self.directory,rid,reply['body'],lambda:self._check(deadline,cancelled))
            try:
                self._check(deadline,cancelled)
                need(source_stamp(self.archive,op,value,full=True)==before,'archive_source_changed',409)
                response=EncodedArchiveResponse(self,rid,stream,reply['body'],deadline)
                with self._state:self._responses.add(response)
            except BaseException:stream.close();raise
            return response
        except BaseException:
            if process is not None:self._retire(process)
            elif locked and self._process is not None:self._retire(self._process)
            raise
        finally:
            if locked:self._serial.release()
            if response is None:
                try:self._discard(rid)
                finally:self._slots.release()

    def _finish_response(self,response):
        try:self._discard(response.id)
        finally:
            with self._state:self._responses.discard(response)
            self._slots.release()

    def close(self):
        with self._state:
            if self.cleanup_complete:return
            self.closed=True;process=self._process
        if process is not None:self._retire(process)
        need(self._serial.acquire(timeout=1),'archive_request_cleanup_unproven')
        try:
            with self._state:responses=list(self._responses)
            for response in responses:response.close()
            # Only our fresh direct child directory, never recursive deletion.
            root=no_link(self.directory).resolve()
            need(root.parent==self.cache and root.name.startswith('archive-http-'),'archive_cleanup_directory')
            allowed={f'worker-{n}{s}' for n in (1,2,3) for s in ('.json','.stderr')}
            for path in root.iterdir():
                need(path.name in allowed and stat.S_ISREG(path.lstat().st_mode)
                     and not path.is_symlink(),'archive_unexpected_cleanup_file')
                path.unlink()
            root.rmdir();self.cleanup_complete=True
        finally:self._serial.release()
