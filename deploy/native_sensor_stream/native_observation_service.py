"""Explicit local native SENSOR owner. It has no motor/device command API.

Default is a plan. --serve exposes start/stop/status/input on one local Unix
socket; it does not start sensor subscriptions until an explicit start request.
Input is the latest native-derived planning snapshot, never wheel odometry.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import threading
import time
from types import SimpleNamespace


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--enable-native-map',action='store_true')
    for name in ('socket-path','scan-gid','imu-gid'):
        parser.add_argument('--'+name)
    args=parser.parse_args(argv)
    if not args.serve:
        print(json.dumps(dict(mode='plan',source_started=False,socket_created=False,
            runtime_authority=False,motor_command_sent=False,autonomy_output_enabled=False)))
        return 0
    import stream_runner
    root=Path(__file__).resolve().parent
    stream_runner.require(os.geteuid()==0 and os.environ.get('ROS_DISTRO')=='iron' and
        os.environ.get('ROS_DOMAIN_ID')=='0','actual_native_environment_required')
    stream_runner.require(args.socket_path and args.scan_gid and args.imu_gid,'explicit_service_inputs_required')
    target=Path(args.socket_path)
    stream_runner.require(target.parent==Path('/run/ubts-native-stream') and
        target.name=='observe.sock','owned_local_sensor_socket_required')
    manifest=root/'BUILD_PROVENANCE.json'
    m=json.loads(manifest.read_text())
    bound=SimpleNamespace(package_manifest_sha256=m['physical_package_manifest_sha256'],
        observer_contract_sha256=m['observer_contract_sha256'],observer_binary_sha256=m['observer_binary_sha256'],
        scan_gid=args.scan_gid,imu_gid=args.imu_gid)
    stream_runner.verify_stream_manifest(manifest,hashlib.sha256(manifest.read_bytes()).hexdigest(),bound)
    bound.physical_reviewed_features = {}
    bound.physical_surface_cache_enabled = stream_runner.load_physical_package(
        '/opt/ubts-shadow/package',bound.package_manifest_sha256,
        reviewed_features=bound.physical_reviewed_features)
    bound.native_map_enabled = args.enable_native_map
    bound.native_map_manifest_sha256 = '682391b127ce637755e7f2dfde2b732d07fc04ac995b85d0a7d281040aab54d5'
    if args.enable_native_map:
        stream_runner.load_native_map_sources(root, bound.native_map_manifest_sha256)
        from committed_native_map import CommittedNativeMapAdapter
        from native_map_transport import NativeMapSession
        import uuid
        bound.native_map_session = NativeMapSession(CommittedNativeMapAdapter, map_epoch=uuid.uuid4().hex)
    retained_map = getattr(bound, 'native_map_session', None)
    binary=root/'bin/actual_cdr_stream'
    stream_runner.require(stream_runner.sha_file(binary)==bound.observer_binary_sha256,'compiled_observer_SHA_required')
    # Warm imports before accepting requests; no ROS context/node or receiver.
    import stream_rows
    from rclpy.clock import Clock,ClockType
    from common.native_ownership import LinuxRawClock
    from physical_factory import PhysicalNativeShadowFactory
    assert Clock and ClockType and LinuxRawClock and PhysicalNativeShadowFactory and stream_rows
    runner=stream_runner.NativeStreamRunner(lambda:stream_runner.make_adapter(bound),lambda:subprocess.Popen(
        [str(binary),'--observe-until-stop','--expected-scan-gid',args.scan_gid,'--expected-imu-gid',args.imu_gid],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False),
        map_session=getattr(bound, 'native_map_session', None))
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda _sig,_frame:stop.set())
    server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    inode=None
    try:
        server.bind(str(target))  # Never unlink another owner's existing socket.
        os.chmod(target,0o666)  # RPC is observation-only; no motor capability.
        inode=target.lstat().st_ino
        server.listen(4)
        server.settimeout(.2)
        print(json.dumps(dict(state='idle',socket_created=True,source_started=False,
            runtime_authority=False,motor_command_sent=False)),flush=True)
        while not stop.is_set():
            try:
                conn,_=server.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(.6)
                try:
                    deadline=time.monotonic()+.6
                    request=b''
                    while b'\n' not in request and len(request)<=4096:
                        remaining=deadline-time.monotonic()
                        if remaining<=0:raise TimeoutError('sensor_owner_request_deadline')
                        conn.settimeout(remaining)
                        chunk=conn.recv(min(4097-len(request),4096))
                        if not chunk:raise ValueError('request_incomplete')
                        request+=chunk
                    stream_runner.require(len(request)<=4096 and request.count(b'\n')==1 and request.endswith(b'\n'),
                        'bounded_sensor_owner_request')
                    value=stream_runner.strict_json(request)
                    map_request = type(value) is dict and value.get('op') in ('map_status','map_tiles','map_release')
                    map_new = type(value) is dict and value.get('op') == 'map_new'
                    stream_runner.require(map_request or map_new or (type(value) is dict and set(value)=={'op'} and
                        value['op'] in ('start','stop','status','input')), 'sensor_owner_operation_required')
                    if map_new:
                        stream_runner.require(args.enable_native_map and set(value) == {'op','map_epoch'} and
                            not runner.lifecycle.status().owned, 'native_new_map_requires_closed_owner')
                        result = retained_map.new_map(value['map_epoch'])
                        conn.sendall((json.dumps(result,allow_nan=False,separators=(',',':'))+'\n').encode())
                        continue
                    if map_request:
                        if args.enable_native_map:
                            from native_map_transport import dispatch_map_request
                            result = dispatch_map_request(runner, value, retained_map)
                        else:
                            stream_runner.require(set(value) <= {'op','cursor','lease'}, 'native_map_get_request_required')
                            result = dict(ok=True, available=False, status=dict(enabled=False, reason='native_map_default_OFF'),
                                runtime_authority=False,motor_command_sent=False,autonomy_output_enabled=False,
                                execution_eligible=False,physical_accuracy_verified=False,live_input_eligible=False)
                        raw=(json.dumps(result,allow_nan=False,separators=(',',':'))+'\n').encode()
                        stream_runner.require(len(raw)<=131072,'bounded_native_map_reply')
                        conn.sendall(raw)
                        continue
                    if value['op']=='start':
                        runner.start()
                    elif value['op']=='stop':
                        runner.stop(3)
                    owner=runner.lifecycle.status()
                    okay=(owner.owned and not owner.cancelled and owner.first_failure_reason is None) if value['op']=='start' else (not owner.owned if value['op']=='stop' else True)
                    conn.settimeout(.6)
                    result=dict(ok=okay,schema='ubts.native_observation.local_reply.v1',
                        state=runner.lifecycle.status().state,runtime_authority=False,
                        motor_command_sent=False,autonomy_output_enabled=False)
                    if value['op']=='input':
                        with runner.lock:
                            resource=runner.resource
                            adapter=None if resource is None else resource.adapter
                        owner=runner.lifecycle.status()
                        result['input']=(adapter.proposal_input() if adapter is not None and
                            owner.owned and not owner.cancelled and owner.first_failure_reason is None else None)
                    else:
                        result['status']=runner.status()
                    raw=(json.dumps(result,allow_nan=False,separators=(',',':'))+'\n').encode()
                    stream_runner.require(len(raw)<=262144,'bounded_sensor_owner_reply')
                    conn.sendall(raw)
                except (OSError,ValueError,TypeError) as error:
                    try:
                        conn.sendall((json.dumps(dict(ok=False,error=str(error)[:256],runtime_authority=False,
                            motor_command_sent=False,autonomy_output_enabled=False))+'\n').encode())
                    except OSError:
                        pass
    finally:
        runner.stop(3)
        server.close()
        if inode is not None:
            current=target.lstat()
            if stat.S_ISSOCK(current.st_mode) and current.st_ino==inode:
                target.unlink()
        print(json.dumps(dict(state='stopped',status=runner.status(),runtime_authority=False,
            motor_command_sent=False,autonomy_output_enabled=False)),flush=True)
    return 0 if not runner.lifecycle.status().owned else 1


if __name__=='__main__':
    raise SystemExit(main())
