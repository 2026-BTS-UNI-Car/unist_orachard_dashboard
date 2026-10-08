"""Explicit bounded15s native original-CDR shadow observer. Default no ROS/IO.

Only this process's own C++ observer can be terminated. No motor/control socket,
publisher, map API or driver restart exists. Root owns runtime preparation.
"""
import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import stat
import subprocess
import sys
import threading
import time

MAX_LINE = 530000
MAX_STDOUT = 32*1024*1024
MAX_STDERR = 256*1024
SECONDS = 20.
MAX_DIAGNOSTICS = 200


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha_file(path, max_bytes=32*1024*1024):
    before=path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_size <= max_bytes, 'bounded_regular_input_required')
    h=hashlib.sha256()
    with path.open('rb') as f:
        while True:
            chunk=f.read(65536)
            if not chunk:break
            h.update(chunk)
    after=path.lstat()
    require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
            (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns), 'input_changed_during_SHA')
    return h.hexdigest()


def load_package(path, expected_manifest_sha):
    path=Path(path).resolve(strict=True)
    manifest=path/'HANDOFF.json'
    require(sha_file(manifest)==expected_manifest_sha, 'reviewed_physical_package_manifest_required')
    m=json.loads(manifest.read_text())
    require(m['schema']=='ubts.private.physical_native_shadow_factory.v2' and m['owned_count']==22 and
            len(m['files_sha256'])==22 and m['deployment_closure_count']==13 and
            m['runtime_authority'] is False and m['after_last_association_final_scalar_fence'] is True,
            'explicit_physical_observer_only_package_required')
    closure_path=path/'DEPLOY_CLOSURE.json'
    require(sha_file(closure_path)==m['files_sha256']['DEPLOY_CLOSURE.json'],
            'reviewed_deployment_closure_required')
    closure=json.loads(closure_path.read_text())
    require(closure['schema']=='ubts.private.physical_native_shadow_deployment_closure.v1' and
            closure['count']==13 and len(closure['files_sha256'])==13,
            'exact_minimal13_deployment_files_required')
    for name,sha in closure['files_sha256'].items():
        p=path/name
        require(not Path(name).is_absolute() and '..' not in Path(name).parts and
                sha==m['files_sha256'].get(name) and sha_file(p)==sha,
                'physical_package_file_changed:'+name)
    sys.path.insert(0,str(path))
    return path


class BoundedReaders:
    """Concurrent stdout/stderr draining; full queue/capacity fails, never drops."""
    def __init__(self, stdout, stderr):
        self.rows=queue.Queue(maxsize=16)
        self.error=None
        self.cancel=threading.Event()
        self.stdout_done=threading.Event()
        self.stderr_done=threading.Event()
        self.stdout_bytes=0
        self.stderr_bytes=0
        self.stderr=[]
        self.threads=[threading.Thread(target=self._stdout,args=(stdout,),daemon=True),
                      threading.Thread(target=self._stderr,args=(stderr,),daemon=True)]

    def start(self):
        for t in self.threads:t.start()

    def _failure(self,error):
        self.error=self.error or str(error)[:256]
        self.cancel.set()

    def _stdout(self,pipe):
        try:
            while not self.cancel.is_set():
                raw=pipe.readline(MAX_LINE+1)
                if not raw:break
                require(len(raw)<=MAX_LINE and raw.endswith(b'\n'),'native_stdout_line_capacity_or_unterminated')
                self.stdout_bytes+=len(raw)
                require(self.stdout_bytes<=MAX_STDOUT,'native_stdout_total_capacity')
                try:self.rows.put_nowait(raw)
                except queue.Full:raise ValueError('native_stdout_processing_capacity_exceeded')
        except Exception as error:self._failure(error)
        finally:self.stdout_done.set()

    def _stderr(self,pipe):
        try:
            while not self.cancel.is_set():
                chunk=pipe.read(4096)
                if not chunk:break
                self.stderr_bytes+=len(chunk)
                require(self.stderr_bytes<=MAX_STDERR,'native_stderr_capacity')
                self.stderr.append(chunk)
        except Exception as error:self._failure(error)
        finally:self.stderr_done.set()

    def finish(self,timeout=1.):
        end=time.monotonic()+timeout
        for t in self.threads:t.join(max(0.,end-time.monotonic()))
        return all(not t.is_alive() for t in self.threads)


def make_receiver():
    """No rclpy.init/Node/publisher; explicit actual RCL system clock handle.

    Source clock must exist before C++ launch. Defaultplan never imports ROS.
    """
    from rclpy.clock import Clock,ClockType
    from common.native_ownership import LinuxRawClock
    from physical_ingress import PhysicalOriginalIngress
    from physical_factory import PhysicalShadowContract,PhysicalNativeShadowFactory,revision
    clock=Clock(clock_type=ClockType.SYSTEM_TIME)
    require(clock.clock_type==ClockType.SYSTEM_TIME,'actual_explicit_RCL_SYSTEM_TIME_required')
    return clock,LinuxRawClock(),PhysicalOriginalIngress,PhysicalShadowContract,PhysicalNativeShadowFactory,revision


def completed_interval(row):
    require(row.get('kind')=='done' and row.get('schema')=='ubts.private.actual_native_cdr_observer.v1'
            and type(row.get('observed_elapsed_ns')) is int and
            15_000_000_000<=row['observed_elapsed_ns']<19_000_000_000,
            'actual_native_fixed15s_completion_required')


def append_diagnostic(records, diagnostic):
    require(len(records) < MAX_DIAGNOSTICS, 'bounded_native_diagnostic_records')
    records.append(asdict(diagnostic))


def own_evidence(directory):
    path=Path(directory).resolve(strict=True)
    s=path.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700,
            'explicit_private_evidence_owner_mode0700_required')
    raw=path/'original-native.ndjson'
    require(not raw.exists() and not (path/'shadow-result.json').exists(),'owned_evidence_collision')
    fd=os.open(raw,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    return path,os.fdopen(fd,'wb')


def close_drained_pipes(child, joined):
    # A cross-thread BufferedReader.close can block on its internal lock. If
    # readers did not join, do not turn a bounded failed cleanup into that wait.
    if not joined:
        return False
    for f in (child.stdout,child.stderr):
        if f is not None:f.close()
    return True


def save_result(directory, capture, result, started):
    """Retain causal error if final private-evidence write fails."""
    try:
        capture.flush();os.fsync(capture.fileno());capture.close()
        result['original_native_capture_sha256']=sha_file(directory/'original-native.ndjson',MAX_STDOUT)
        result['elapsed_s']=time.monotonic()-started
        result['receiver_reset_preceded_native_spawn']=True
        target=directory/'shadow-result.json'
        encoded=(json.dumps(result,indent=2,allow_nan=False)+'\n').encode()
        require(len(encoded)<=2*1024*1024,'bounded_shadow_result_required')
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(encoded);f.flush();os.fsync(f.fileno())
        return True
    except Exception as error:
        result['ok']=False
        result['private_evidence_completion_unqualified']=True
        result['private_evidence_error']=str(error)[:256]
        return False


def observe(args):
    require(sys.platform.startswith('linux') and os.geteuid()==0,
            'root_reviewed_Linux_UID0_private_observer_only')
    require(os.environ.get('ROS_DISTRO')=='iron' and os.environ.get('ROS_DOMAIN_ID')=='0'
            and os.environ.get('UBTS_MAP_FIT_WORKERS','1')=='1','reviewed_Iron_domain0_single_fit_environment_required')
    load_package(args.package,args.package_manifest_sha256)
    binary=Path(args.observer_bin).resolve(strict=True)
    require(sha_file(binary)==args.observer_binary_sha256 and os.access(binary,os.X_OK),
            'root_verified_native_observer_binary_SHA_required')
    for gid in (args.scan_gid,args.imu_gid):
        require(re.fullmatch('[0-9a-f]{32}',gid) is not None and gid!='0'*32,
                'explicit_actual_publisher_GID16_required')
    require(args.scan_gid!=args.imu_gid,'separate_scan_and_imu_publisher_GIDs_required')
    clock,raw_clock,Receiver,Contract,Factory,revision=make_receiver()
    contract=Contract('laser','imu_link')
    receiver=Receiver(raw_clock=raw_clock,source_now_ns=lambda:clock.now().nanoseconds,
        source_clock_kind=lambda:'ROS_SYSTEM_TIME' if clock.clock_type.name=='SYSTEM_TIME' else 'invalid',
        scan_frame='laser',imu_frame='imu_link',profile_revision=revision(contract),
        scan_publisher_gid=bytes.fromhex(args.scan_gid),imu_publisher_gid=bytes.fromhex(args.imu_gid),
        observer_contract_sha256='93cbe9221f7d0b3d06079c04e35c4633cfa454c2c80233cb893629907af97563')
    receiver.reset()  # MUST precede subprocess creation/native callback delivery.
    unit=Factory(receiver,contract)
    from observer_rows import OriginalObserverRows
    directory,capture=own_evidence(args.evidence_dir)
    child=readers=None
    result=dict(schema='ubts.private.physical_native_live_shadow.v1',ok=False,
        state='HOLD',last_row='UNKNOWN',runtime_authority=False,motor_command_sent=False,
        autonomy_output_enabled=False,proposed_linear_mps=0.,proposed_yaw_rate_rad_s=0.,
        applied_linear_mps=0.,applied_yaw_rate_rad_s=0.,body_velocity_mps=None,
        physical_braking_extrinsics_REP145_qualified=False,physical_autonomy_qualified=False,
        original_callback_live_ingestion_qualified=False,error=None,diagnostic_records=[])
    start=time.monotonic()
    try:
        child=subprocess.Popen([str(binary),'--observe','--expected-scan-gid',args.scan_gid,
            '--expected-imu-gid',args.imu_gid],stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False)
        adapter=OriginalObserverRows(receiver,expected_observer_pid=child.pid,
            expected_time_namespace=os.readlink('/proc/self/ns/time'))
        readers=BoundedReaders(child.stdout,child.stderr);readers.start()
        while True:
            require(time.monotonic()-start<SECONDS,'native_shadow_total_deadline')
            require(readers.error is None,readers.error or 'native_reader_error')
            try:line=readers.rows.get(timeout=.02)
            except queue.Empty:
                if child.poll() is not None and readers.stdout_done.is_set() and readers.rows.empty():break
                continue
            capture.write(line)  # exact source byte line, before interpretation.
            row=json.loads(line.decode('utf8',errors='strict'))
            if row.get('kind')=='done':completed_interval(row)
            adapter.receive(row)
            if receiver.pending is not None:
                prepared=receiver.prepare()
                require(prepared.prepared is not None,'physical_original_pair_rejected:'+prepared.reason)
                owned=unit.prepare(prepared.prepared)
                require(not isinstance(owned,dict),'physical_internal_fit_rejected:'+str(owned))
                answer=unit.commit(owned)
                require(answer['diagnostic'] is not None and answer['runtime_authority'] is False
                        and answer['proposed_linear_mps']==0 and answer['proposed_yaw_rate_rad_s']==0,
                        'physical_shadow_final_fence_failed:'+answer['reason'])
                append_diagnostic(result['diagnostic_records'], answer['diagnostic'])
        require(child.wait(timeout=1.)==0,'native_observer_exit_not_successful')
        require(readers.finish() and readers.error is None,'native_readers_not_fully_drained')
        require(adapter.ended and adapter.ready and receiver.fault is None and unit.snapshot().version>0,
                'native_original_protocol_incomplete')
        result.update(ok=True,native_observer_delivery_counts=dict(adapter.samples),
            diagnostic_count=unit.snapshot().version,original_callback_live_ingestion_qualified=True)
    except Exception as error:
        result['error']=str(error)[:512]
        unit.stop()
    finally:
        if child is not None and child.poll() is None:
            child.terminate()  # exact own Popen only; no process name/driver kill.
            try:child.wait(timeout=1.)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=1.)
        if readers is not None:
            joined=readers.finish()
            result.update(readers_joined=joined,native_stdout_bytes=readers.stdout_bytes,
                native_stderr_bytes=readers.stderr_bytes,
                native_stderr=b''.join(readers.stderr).decode('utf8',errors='replace'))
            if not joined:result['ok']=False;result['reader_cleanup_unqualified']=True
        if child is not None:
            result['native_observer_exit_code']=child.poll()
            if not close_drained_pipes(child, readers is None or joined):
                result['open_pipe_cleanup_unqualified']=True
        save_result(directory,capture,result,start)
    print(json.dumps({k:v for k,v in result.items() if k not in ('diagnostic_records','native_stderr')},allow_nan=False))
    return 0 if result['ok'] else 1


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--observe-shadow',action='store_true')
    p.add_argument('--package')
    p.add_argument('--package-manifest-sha256')
    p.add_argument('--observer-bin')
    p.add_argument('--observer-binary-sha256')
    p.add_argument('--scan-gid')
    p.add_argument('--imu-gid')
    p.add_argument('--evidence-dir')
    args=p.parse_args()
    if not args.observe_shadow:
        print(json.dumps(dict(mode='plan',ROS_initialized=False,child_spawned=False,
            evidence_created=False,seconds=15,outer_deadline_s=SECONDS,
            state='HOLD',runtime_authority=False,motor_transport=False,
            physical_calibration_braking_REP145_qualified=False,
            receiver_before_native_spawn=True)))
        return 0
    require(all(getattr(args,k) for k in ('package','package_manifest_sha256','observer_bin',
        'observer_binary_sha256','scan_gid','imu_gid','evidence_dir')),'all_root_reviewed_explicit_paths_SHA_and_GIDs_required')
    return observe(args)


if __name__=='__main__':raise SystemExit(main())
