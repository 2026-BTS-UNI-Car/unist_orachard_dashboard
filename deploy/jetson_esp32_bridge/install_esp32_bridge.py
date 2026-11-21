#!/usr/bin/env python3
"""UBTS ESP32-S3 bridge installer for the Jetson (Python 3 standard library only).

Default: check only (dry-run). Nothing is changed unless --apply is given.

What it does with --apply (the role of the old ESP8266 bridge container passes to a new container):
  1. checks: kit files (SHA-256), Docker, the old bridge container and its image, the ESP32 device (by-id symlink ->
     a ttyACM character device whose live udev ID_SERIAL is the expected one), the control socket lock is free (no
     other bridge runs), the dashboard uses the same control socket directory and runtime directory
  2. preflight: a short container from the old bridge image with the kit files, network none, no devices, no
     mounts, imports the new bridge modules; it is kept (exited), never removed
  3. a new container '<new name>' from the same image (no build, no pull): the old container's mounts (runtime,
     /run/ubts-rover, /run/lock, /run/udev read-only), host network and IPC, security options and log settings;
     the command 'exec python3 -m scripts.esp_rover_bridge' with ROVER_ESP_KIND=esp32_abs_hold; the device by its
     by-id path (Docker resolves the symlink at every start; the bridge checks the udev identity at every open);
     the kit files are copied into /app/scripts and read back (SHA-256) before anything else changes
  4. the old container is stopped if it runs, renamed '<old name>-kept-<stamp>' with restart policy 'no' (kept)
  5. the new container starts; ready = running, the status file and (when this user may) the control socket answer
     with esp_kind esp32_abs_hold, connected, not armed
  Any failure after step 4 began restores the old container's name and restart policy and stops the new one
  (renamed '<new name>-failed-<stamp>', restart 'no', kept).

The bridge only writes STOP, STATUS, '2 S' and '3 S' while nobody arms it. Opening the USB port may reset the ESP32
(observed once on 2026-11-16); a reset turns every output off. Never used: docker compose, build, pull, prune,
container or image removal, motor or arm commands, firmware writes. The receipt (file SHA-256, container and image IDs,
times; environment variable names only) goes to --state-dir (default ~/ubts-releases/esp32-bridge-kit).
Exit codes: 0 check passed / installed, 1 failed but the old state is back, 2 refused before any change,
3 failed and the rollback failed too (see the receipt).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import http.client
import io
import json
import os
import socket
import stat
import sys
import tarfile
import time
import urllib.parse
from pathlib import Path

MANIFEST_SCHEMA = 'ubts.esp32_bridge_kit.manifest.v1'
RECEIPT_SCHEMA = 'ubts.esp32_bridge_kit.receipt.v1'
OLD_NAME = 'unist-orachard-ros2-esp-rover-bridge'
CONTROL_CPU_SHARES = 4096          # 2026-11-19: CPU weight of the bridge (default 1024)
NEW_NAME = 'unist-orachard-ros2-esp32-bridge'
DASHBOARD_NAME = 'unist-orachard-dashboard'
# The board's udev ID_SERIAL (Espressif_USB_JTAG_serial_debug_unit_<board serial>) differs per board, so it is a
# setting: --identity or the environment variable UBTS_ESP32_IDENTITY. The by-id path follows from it (--device).
IDENTITY = os.environ.get('UBTS_ESP32_IDENTITY', '').strip()


def device_for(identity):
    return '/dev/serial/by-id/usb-%s-if00' % identity


DEVICE = device_for(IDENTITY) if IDENTITY else ''
CONTAINER_DEVICE = '/dev/ubts-motor'
CONTROL_DIR = '/run/ubts-rover'
STATUS_FILE_IN_CONTAINER = '/app/runtime/rover_bridge_status.json'
APP_ROOT = '/app'
CMD = ['/bin/bash', '-lc', 'source /opt/ros/iron/setup.bash && exec python3 -m scripts.esp_rover_bridge']
DEFAULT_SOCKET = '/var/run/docker.sock'
ACM_MAJOR = 166
STOP_GRACE_S = 10
DOCKER_TIMEOUT_S = 20.0
PREFLIGHT_TIMEOUT_S = 90.0
READY_TIMEOUT_S = 40.0
MARKER = 'UBTS_ESP32_KIT_IMPORT_OK'
PREFLIGHT_CODE = (
    "import json,sys\n"
    "sys.path.insert(0,'/app')\n"
    "import scripts.esp32_abs_hold as e, scripts.esp32_steer_bridge as b, scripts.esp_rover_bridge as s\n"
    "assert s.module_for('esp32_abs_hold')=='scripts.esp32_steer_bridge'\n"
    "assert s.module_for(None)=='scripts.rover_control_bridge'\n"
    "assert e.LIMIT_MRAD==349 and e.angle_line(-20)==b'ANGLE -20.0\\n'\n"
    "print('" + MARKER + " '+json.dumps({'python':sys.version.split()[0],'firmware':e.FIRMWARE_NAME}))\n"
)


class KitError(Exception):
    def __init__(self, code, detail=''):
        super().__init__('%s: %s' % (code, detail) if detail else code)
        self.code, self.detail = code, detail


def need(value, code, detail=''):
    if not value:
        raise KitError(code, detail)
    return value


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def utc_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _q(value):
    return urllib.parse.quote(str(value), safe='')


# --------------------------------------------------------------------------------------------------
# Docker Engine API over the Unix socket (no remove, prune, pull or build call exists here)
# --------------------------------------------------------------------------------------------------
class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__('localhost', timeout=timeout)
        self._socket_path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class DockerAPI:
    def __init__(self, socket_path=DEFAULT_SOCKET):
        self.socket_path = socket_path

    def _call(self, method, path, body=None, *, raw=None, content_type='application/json', timeout=DOCKER_TIMEOUT_S,
              ok=(200, 201, 204, 304), missing_ok=False):
        headers, data = {}, None
        if raw is not None:
            data, headers['Content-Type'] = raw, content_type
        elif body is not None:
            data, headers['Content-Type'] = json.dumps(body).encode('utf-8'), 'application/json'
        conn = _UnixConnection(self.socket_path, timeout)
        try:
            try:
                conn.request(method, path, body=data, headers=headers)
                resp = conn.getresponse()
                payload = resp.read(16 * 1024 * 1024)
            except FileNotFoundError:
                raise KitError('docker_socket_missing', self.socket_path)
            except PermissionError:
                raise KitError('docker_socket_permission', 'sudo 로 실행하거나 docker 그룹이 필요합니다')
            except socket.timeout:
                raise KitError('docker_timeout', method + ' ' + path.split('?')[0])
            except OSError as error:
                raise KitError('docker_unreachable', type(error).__name__)
            if missing_ok and resp.status == 404:
                return None
            if resp.status not in ok:
                try:
                    message = str(json.loads(payload).get('message', ''))[:200]
                except (ValueError, AttributeError):
                    message = payload[:200].decode('utf-8', 'replace')
                raise KitError('docker_api', '%s %s -> %s %s' % (method, path.split('?')[0], resp.status, message))
            if not payload:
                return {}
            try:
                return json.loads(payload)
            except ValueError:
                return payload
        finally:
            conn.close()

    def inspect(self, ref):
        return self._call('GET', '/containers/' + _q(ref) + '/json', missing_ok=True)

    def image(self, ref):
        return self._call('GET', '/images/' + _q(ref) + '/json', missing_ok=True)

    def create(self, name, spec):
        return self._call('POST', '/containers/create?name=' + _q(name), spec)['Id']

    def put_archive(self, cid, path, data):
        self._call('PUT', '/containers/%s/archive?path=%s&noOverwriteDirNonDir=1' % (_q(cid), _q(path)),
                   raw=data, content_type='application/x-tar', timeout=60.0)

    def read_file(self, cid, path, max_bytes=1024 * 1024):
        conn = _UnixConnection(self.socket_path, 20.0)
        try:
            conn.request('GET', '/containers/' + _q(cid) + '/archive?path=' + _q(path))
            resp = conn.getresponse()
            raw = resp.read(max_bytes + 8192)
            if resp.status != 200:
                return None
            with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
                for member in archive:
                    if member.isfile():
                        return archive.extractfile(member).read()
            return None
        except (OSError, tarfile.TarError):
            return None
        finally:
            conn.close()

    def start(self, cid):
        self._call('POST', '/containers/' + _q(cid) + '/start', timeout=30.0)

    def stop(self, cid, grace=STOP_GRACE_S):
        self._call('POST', '/containers/%s/stop?t=%d' % (_q(cid), int(grace)), timeout=grace + 20.0)

    def wait(self, cid, timeout):
        return int(self._call('POST', '/containers/' + _q(cid) + '/wait', timeout=timeout).get('StatusCode', -1))

    def rename(self, cid, name):
        self._call('POST', '/containers/%s/rename?name=%s' % (_q(cid), _q(name)))

    def set_restart(self, cid, policy):
        self._call('POST', '/containers/' + _q(cid) + '/update', {'RestartPolicy': policy})

    def logs(self, cid, tail=60):
        raw = self._call('GET', '/containers/%s/logs?stdout=1&stderr=1&tail=%d' % (_q(cid), tail))
        if isinstance(raw, (bytes, bytearray)):
            out, data = [], bytes(raw)
            while len(data) >= 8 and data[0] in (0, 1, 2) and data[1:4] == b'\0\0\0':
                size = int.from_bytes(data[4:8], 'big')
                out.append(data[8:8 + size])
                data = data[8 + size:]
            return (b''.join(out) if out else bytes(raw)).decode('utf-8', 'replace')
        return json.dumps(raw)


# --------------------------------------------------------------------------------------------------
# Host access (separate so tests can give a fake host)
# --------------------------------------------------------------------------------------------------
class Host:
    def realpath(self, path):
        return os.path.realpath(path)

    def lexists(self, path):
        return os.path.lexists(path)

    def char_device(self, path):
        """(major, minor) of a character device, else None."""
        try:
            info = os.stat(path)
        except OSError:
            return None
        if not stat.S_ISCHR(info.st_mode):
            return None
        return os.major(info.st_rdev), os.minor(info.st_rdev)

    def read_text(self, path, limit=1024 * 1024):
        try:
            with open(path, 'rb') as handle:
                return handle.read(limit).decode('utf-8', 'replace')
        except OSError:
            return None

    def isdir(self, path):
        return os.path.isdir(path)

    def lock_free(self, path):
        """True when nobody holds the flock on path (a missing file is free). None when it cannot be tested."""
        try:
            import fcntl
        except ImportError:
            return None
        if not os.path.exists(path):
            return True
        try:
            with open(path, 'a') as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
                fcntl.flock(handle, fcntl.LOCK_UN)
                return True
        except PermissionError:
            return None

    def process_names(self):
        names = set()
        try:
            for entry in os.listdir('/proc'):
                if entry.isdigit():
                    text = self.read_text('/proc/%s/comm' % entry, 256)
                    if text:
                        names.add(text.strip())
        except OSError:
            pass
        return names

    def socket_status(self, path, timeout=1.0):
        """{'op': 'status'} on the control socket. None when this user may not connect."""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout)
                client.connect(path)
                client.sendall(b'{"op": "status"}\n')
                data = b''
                while b'\n' not in data and len(data) < 1024 * 1024:
                    part = client.recv(65536)
                    if not part:
                        break
                    data += part
            return json.loads(data)
        except PermissionError:
            return None
        except (OSError, ValueError) as error:
            return {'ok': False, 'error': 'socket_%s' % type(error).__name__}


# --------------------------------------------------------------------------------------------------
# Kit
# --------------------------------------------------------------------------------------------------
class Kit:
    def __init__(self, kit_dir):
        self.dir = Path(kit_dir)
        manifest_path = self.dir / 'MANIFEST.json'
        need(manifest_path.is_file(), 'kit_manifest_missing', str(manifest_path))
        self.manifest_bytes = manifest_path.read_bytes()
        self.manifest = json.loads(self.manifest_bytes)
        need(self.manifest.get('schema') == MANIFEST_SCHEMA, 'kit_manifest_schema', str(self.manifest.get('schema')))
        self.commit = str(self.manifest.get('commit', ''))
        self.files = {}
        for rel, digest in sorted(self.manifest.get('files', {}).items()):
            need(rel.startswith('scripts/') and rel.endswith('.py') and '..' not in rel and rel.count('/') == 1,
                 'kit_bad_path', rel)
            data = (self.dir / 'payload' / rel).read_bytes()
            need(sha256(data) == digest, 'kit_file_sha256', rel)
            self.files[rel] = data
        need('scripts/esp32_steer_bridge.py' in self.files and 'scripts/esp_rover_bridge.py' in self.files,
             'kit_incomplete', 'bridge files missing')

    def tar(self, mtime=0):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for rel, data in sorted(self.files.items()):
                info = tarfile.TarInfo(rel)
                info.size, info.mode, info.mtime = len(data), 0o644, int(mtime)
                info.uid = info.gid = 0
                archive.addfile(info, io.BytesIO(data))
        return buffer.getvalue()


# --------------------------------------------------------------------------------------------------
# Checks and plan
# --------------------------------------------------------------------------------------------------
def env_dict(inspect):
    out = {}
    for item in (inspect.get('Config') or {}).get('Env') or []:
        key, _, value = item.partition('=')
        out[key] = value
    return out


def bind_source(inspect, destination):
    for mount in inspect.get('Mounts') or []:
        if mount.get('Destination') == destination and mount.get('Type') == 'bind':
            return mount.get('Source')
    return None


def host_path(inspect, container_path):
    """Host path of a file under a bind mount of the container, else None."""
    best = None
    for mount in inspect.get('Mounts') or []:
        dest = (mount.get('Destination') or '').rstrip('/')
        if mount.get('Type') == 'bind' and dest and (container_path == dest or container_path.startswith(dest + '/')):
            if best is None or len(dest) > len(best[0]):
                best = (dest, mount.get('Source'))
    if best is None:
        return None
    return best[1].rstrip('/') + container_path[len(best[0]):]


def check_device(host, device, identity, udev_root):
    out = {'device': device}
    need(host.lexists(device), 'device_missing', device)
    resolved = host.realpath(device)
    out['resolved'] = resolved
    need(resolved.startswith('/dev/ttyACM'), 'device_not_ttyacm', resolved)
    numbers = host.char_device(resolved)
    need(numbers is not None, 'device_not_char', resolved)
    need(numbers[0] == ACM_MAJOR, 'device_major', '%s %s' % (resolved, numbers))
    out['numbers'] = '%d:%d' % numbers
    props = host.read_text('%s/c%d:%d' % (udev_root, numbers[0], numbers[1]))
    need(props is not None, 'udev_data_missing', '%s/c%d:%d' % (udev_root, numbers[0], numbers[1]))
    lines = props.splitlines()
    need('E:ID_SERIAL=%s' % identity in lines, 'udev_identity_mismatch', resolved)
    out['identity'] = identity
    out['usb'] = [line[2:] for line in lines if line.startswith(('E:ID_VENDOR_ID=', 'E:ID_MODEL_ID='))]
    return out


def new_env(old_inspect, options):
    env = env_dict(old_inspect)
    env.update({
        'ROVER_ESP_KIND': 'esp32_abs_hold',
        'ROVER_SERIAL_PORT': CONTAINER_DEVICE,
        'ROVER_ESP_IDENTITY': options.identity,
        'ROVER_CONTROL_SOCKET': env.get('ROVER_CONTROL_SOCKET') or CONTROL_DIR + '/control.sock',
        'ROVER_BRIDGE_STATUS_FILE': env.get('ROVER_BRIDGE_STATUS_FILE') or STATUS_FILE_IN_CONTAINER,
        'ROVER_ESP32_LEFT_FORWARD': options.left_forward,
        'ROVER_ESP32_RIGHT_FORWARD': options.right_forward,
        'ROVER_ESP32_DIRECTION_VERIFIED': '1' if options.direction_verified else '0',
        'ROVER_ESP32_MAX_DRIVE_PWM': str(options.max_drive_pwm),
        'PYTHONUNBUFFERED': '1',
    })
    return env


def new_spec(old_inspect, image_id, options, device_path, commit):
    hc = old_inspect.get('HostConfig') or {}
    env = new_env(old_inspect, options)
    return {
        'Image': image_id,
        'Entrypoint': (old_inspect.get('Config') or {}).get('Entrypoint'),
        'Cmd': list(CMD),
        'WorkingDir': (old_inspect.get('Config') or {}).get('WorkingDir') or APP_ROOT,
        'Env': ['%s=%s' % (k, v) for k, v in sorted(env.items())],
        'Labels': {'ubts.kit': 'esp32-bridge', 'ubts.kit.commit': commit, 'ubts.kit.replaces': options.old_name},
        'StopSignal': 'SIGTERM',
        'StopTimeout': STOP_GRACE_S,
        'HostConfig': {
            'Binds': list(hc.get('Binds') or []),
            'NetworkMode': hc.get('NetworkMode') or 'host',
            'IpcMode': hc.get('IpcMode') or '',
            'SecurityOpt': list(hc.get('SecurityOpt') or ['no-new-privileges:true']),
            'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
            'Devices': [{'PathOnHost': device_path, 'PathInContainer': CONTAINER_DEVICE, 'CgroupPermissions': 'rwm'}],
            'LogConfig': hc.get('LogConfig') or {'Type': 'json-file', 'Config': {}},
            'Privileged': False,
            # 2026-11-19 field: the drive heartbeat must not starve when the camera / recorder load the CPU
            # (CFS weight; the default is 1024, the camera and the recorder run at 512)
            'CpuShares': CONTROL_CPU_SHARES,
        },
    }


def summarize_spec(spec):
    hc = spec['HostConfig']
    return {'image': spec['Image'], 'cmd': spec['Cmd'], 'entrypoint': spec['Entrypoint'],
            'env_names': sorted(item.split('=', 1)[0] for item in spec['Env']),
            'esp32_settings': {k: v for k, v in (item.split('=', 1) for item in spec['Env'])
                               if k.startswith('ROVER_ESP') or k in ('ROVER_SERIAL_PORT', 'ROVER_CONTROL_SOCKET',
                                                                     'ROVER_BRIDGE_STATUS_FILE')},
            'binds': hc['Binds'], 'network': hc['NetworkMode'], 'ipc': hc['IpcMode'], 'devices': hc['Devices'],
            'restart': hc['RestartPolicy'], 'stop': [spec['StopSignal'], spec['StopTimeout']]}


def run_checks(docker, host, kit, options):
    """Read-only. Returns (report dict, blockers list)."""
    report, blockers = {'kit': {'commit': kit.commit, 'files': {k: sha256(v) for k, v in kit.files.items()},
                                'manifest_sha256': sha256(kit.manifest_bytes)}}, []

    def check(name, fn):
        try:
            report[name] = fn()
        except KitError as error:
            report[name] = {'error': error.code, 'detail': error.detail}
            blockers.append('%s:%s' % (error.code, error.detail) if error.detail else error.code)

    old = {}

    def old_container():
        info = docker.inspect(options.old_name)
        need(info is not None, 'old_container_missing', options.old_name)
        old['inspect'] = info
        image = docker.image(info['Image'])
        need(image is not None, 'old_image_missing', info['Image'])
        old['image'] = image
        st = info.get('State') or {}
        return {'id': info['Id'][:12], 'image': info['Image'], 'image_tag': (info.get('Config') or {}).get('Image'),
                'arch': image.get('Architecture'), 'state': st.get('Status'), 'running': bool(st.get('Running')),
                'exit_code': st.get('ExitCode'), 'error': (st.get('Error') or '')[:160],
                'restart': (info.get('HostConfig') or {}).get('RestartPolicy'),
                'cmd': (info.get('Config') or {}).get('Cmd'), 'runtime_bind': bind_source(info, '/app/runtime'),
                'control_bind': bind_source(info, CONTROL_DIR)}
    check('old_container', old_container)

    def new_name():
        info = docker.inspect(options.new_name)
        need(info is None, 'new_name_in_use', options.new_name)
        return 'free'
    check('new_name', new_name)
    check('device', lambda: check_device(host, options.device, options.identity, options.udev_root))

    def control():
        need(host.isdir(CONTROL_DIR), 'control_dir_missing', CONTROL_DIR)
        free = host.lock_free(CONTROL_DIR + '/bridge.lock')
        need(free is not False, 'control_socket_in_use', 'another bridge holds %s/bridge.lock' % CONTROL_DIR)
        return {'dir': CONTROL_DIR, 'lock': 'free' if free else 'not_tested'}
    check('control_socket', control)

    def old_idle():
        info = old.get('inspect')
        if not info or not (info.get('State') or {}).get('Running'):
            return 'not_running'
        path = host_path(info, env_dict(info).get('ROVER_BRIDGE_STATUS_FILE', STATUS_FILE_IN_CONTAINER))
        text = host.read_text(path) if path else None
        need(text is not None, 'not_idle', 'old bridge runs and its status file is not readable')
        value = json.loads(text)
        need(value.get('armed') is not True and value.get('moving') is not True, 'not_idle', 'old bridge armed/moving')
        return 'running_disarmed'
    check('old_idle', old_idle)

    def dashboard():
        info = docker.inspect(options.dashboard_name)
        if info is None:
            return 'not_found'
        env = env_dict(info)
        socket_path = env.get('ROVER_CONTROL_SOCKET', CONTROL_DIR + '/control.sock')
        need(socket_path.startswith(CONTROL_DIR + '/'), 'dashboard_socket_elsewhere', socket_path)
        need(bind_source(info, CONTROL_DIR) == CONTROL_DIR, 'dashboard_control_bind', str(bind_source(info, CONTROL_DIR)))
        old_runtime = old.get('inspect') and bind_source(old['inspect'], '/app/runtime')
        dash_runtime = bind_source(info, '/app/runtime')
        need(not old_runtime or dash_runtime == old_runtime, 'runtime_differs', '%s != %s' % (dash_runtime, old_runtime))
        return {'control_socket': socket_path, 'runtime_bind': dash_runtime,
                'vehicle_env_set': 'DASHBOARD_VEHICLE' in env}
    check('dashboard', dashboard)
    names = host.process_names()
    report['warnings'] = ['ModemManager runs: it may probe new ttyACM devices'] if 'ModemManager' in names else []
    if old.get('inspect'):
        device_path = options.device if options.device_mode == 'by-id' else host.realpath(options.device)
        report['plan'] = summarize_spec(new_spec(old['inspect'], old['inspect']['Image'], options, device_path,
                                                 kit.commit))
        report['plan']['new_name'] = options.new_name
        report['plan']['old_becomes'] = options.old_name + '-kept-<stamp> (restart no, kept)'
    return report, blockers


# --------------------------------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------------------------------
def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(json.dumps(value, ensure_ascii=False, indent=1, default=str).encode('utf-8'))
    os.replace(tmp, path)


def default_state_dir():
    home = os.path.expanduser('~' + os.environ['SUDO_USER']) if os.environ.get('SUDO_USER') else os.path.expanduser('~')
    return os.path.join(home, 'ubts-releases', 'esp32-bridge-kit')


class Installer:
    def __init__(self, docker, host, kit, options, *, clock=time.monotonic, sleep=time.sleep, out=None):
        self.docker, self.host, self.kit, self.options = docker, host, kit, options
        self.clock, self.sleep = clock, sleep
        self.out = out or sys.stdout
        self.stamp = utc_stamp()
        self.receipt = {'schema': RECEIPT_SCHEMA, 'stamp': self.stamp, 'commit': kit.commit, 'steps': [],
                        'options': {k: v for k, v in vars(options).items() if k not in ('state_dir',)}}

    def log(self, text):
        self.receipt['steps'].append({'t': round(self.clock(), 3), 'step': text})
        print(text, file=self.out)

    def preflight(self, image_id):
        name = '%s-kit-preflight-%s' % (self.options.new_name, self.stamp)
        spec = {'Image': image_id, 'Entrypoint': ['/ros_entrypoint.sh'], 'Cmd': ['python3', '-B', '-c', PREFLIGHT_CODE],
                'WorkingDir': APP_ROOT, 'Labels': {'ubts.kit': 'esp32-bridge-preflight'},
                'HostConfig': {'NetworkMode': 'none', 'RestartPolicy': {'Name': 'no'}}}
        cid = self.docker.create(name, spec)
        self.receipt['preflight_container'] = {'name': name, 'id': cid}
        self.docker.put_archive(cid, APP_ROOT, self.kit.tar())
        self.docker.start(cid)
        code = self.docker.wait(cid, PREFLIGHT_TIMEOUT_S)
        logs = self.docker.logs(cid)
        self.receipt['preflight_log_tail'] = logs[-2000:]
        need(code == 0 and MARKER in logs, 'preflight_import_failed', 'exit %s' % code)
        line = [l for l in logs.splitlines() if MARKER in l][-1]
        return json.loads(line.split(MARKER, 1)[1])

    def verify_files(self, cid):
        out = {}
        for rel, data in self.kit.files.items():
            got = self.docker.read_file(cid, APP_ROOT + '/' + rel)
            need(got is not None and sha256(got) == sha256(data), 'file_verify_failed', rel)
            out[rel] = sha256(got)
        return out

    def wait_ready(self, cid, old_inspect):
        deadline = self.clock() + self.options.ready_timeout
        status_path = host_path(old_inspect, env_dict(old_inspect).get('ROVER_BRIDGE_STATUS_FILE',
                                                                         STATUS_FILE_IN_CONTAINER))
        last = None
        while self.clock() < deadline:
            info = self.docker.inspect(cid) or {}
            st = info.get('State') or {}
            need(st.get('Running') or st.get('Status') in ('created', 'running'), 'bridge_not_running',
                 '%s exit=%s' % (st.get('Status'), st.get('ExitCode')))
            text = self.host.read_text(status_path) if status_path else None
            value = None
            if text:
                try:
                    value = json.loads(text)
                except ValueError:
                    value = None
            if isinstance(value, dict) and value.get('esp_kind') == 'esp32_abs_hold':
                age = time.time() - value.get('updated_at', 0) if isinstance(value.get('updated_at'), (int, float)) \
                    else None
                need(value.get('armed') is not True, 'armed_after_start', 'nobody may arm during the install')
                last = {'connected': value.get('connected'), 'firmware': value.get('firmware'),
                        'state': value.get('state'), 'age_s': None if age is None else round(age, 2),
                        'last_error': value.get('last_error')}
                if age is not None and age < 3 and (value.get('connected') or self.options.allow_esp_absent):
                    sock = self.host.socket_status(CONTROL_DIR + '/control.sock')
                    if sock is None:
                        last['socket'] = 'not_tested_permission'
                        return last
                    if sock.get('vehicle_profile') == 'front_steer' and sock.get('esp_kind') == 'esp32_abs_hold':
                        last['socket'] = 'answered'
                        return last
                    last['socket'] = sock.get('error') or 'unexpected'
            self.sleep(0.5)
        raise KitError('ready_timeout', json.dumps(last, default=str) if last else 'no status file from the new bridge')

    def apply(self):
        o = self.options
        report, blockers = run_checks(self.docker, self.host, self.kit, o)
        self.receipt['checks'] = report
        if blockers:
            self.receipt['result'] = 'refused'
            self.receipt['blockers'] = blockers
            self.log('설치 불가(바꾼 것 없음): %s' % ', '.join(blockers))
            return 2
        old = self.docker.inspect(o.old_name)
        old_id, old_policy = old['Id'], (old.get('HostConfig') or {}).get('RestartPolicy') or {'Name': 'no'}
        old_running = bool((old.get('State') or {}).get('Running'))
        self.receipt['target'] = {'old_name': o.old_name, 'old_container_id': old_id, 'old_image': old['Image'],
                                  'old_restart_policy': old_policy, 'old_was_running': old_running,
                                  'new_name': o.new_name}
        try:
            self.receipt['preflight'] = self.preflight(old['Image'])
            self.log('preflight ok: %s' % self.receipt['preflight'])
            device_path = o.device if o.device_mode == 'by-id' else self.host.realpath(o.device)
            spec = new_spec(old, old['Image'], o, device_path, self.kit.commit)
            self.receipt['new_spec'] = summarize_spec(spec)
            staged = '%s-kit-new-%s' % (o.new_name, self.stamp)
            new_id = self.docker.create(staged, spec)
            self.receipt['target']['new_container_id'] = new_id
            self.docker.put_archive(new_id, APP_ROOT, self.kit.tar())
            self.receipt['files_in_new_container'] = self.verify_files(new_id)
            self.log('new container created and its files verified: %s' % new_id[:12])
        except KitError as error:
            # Nothing of the old container was touched yet.
            self.receipt['result'] = 'failed_before_change'
            self.receipt['error'] = {'code': error.code, 'detail': error.detail}
            self.log('failed before any change: %s' % error)
            return 1
        kept = '%s-kept-%s' % (o.old_name, self.stamp)
        changed = []
        try:
            if old_running:
                self.docker.stop(old_id, STOP_GRACE_S)
                changed.append('old_stopped')
            self.docker.set_restart(old_id, {'Name': 'no', 'MaximumRetryCount': 0})
            changed.append('old_restart_no')
            self.docker.rename(old_id, kept)
            changed.append('old_renamed')
            self.docker.rename(new_id, o.new_name)
            changed.append('new_renamed')
            self.docker.start(new_id)
            changed.append('new_started')
            self.log('old container kept as %s; new bridge started' % kept)
            self.receipt['ready'] = self.wait_ready(new_id, old)
            self.receipt['result'] = 'installed'
            self.log('ready: %s' % self.receipt['ready'])
            return 0
        except KitError as error:
            self.receipt['error'] = {'code': error.code, 'detail': error.detail}
            self.log('failed: %s -> restoring the old container' % error)
            try:
                self.receipt['new_log_tail'] = self.docker.logs(new_id)[-2000:]
            except KitError:
                pass
            ok = self.restore(old_id, old_policy, old_running, new_id, changed)
            self.receipt['result'] = 'failed_rolled_back' if ok else 'failed_rollback_failed'
            return 1 if ok else 3

    def restore(self, old_id, old_policy, old_running, new_id, changed):
        o, problems = self.options, []
        for step, action in (
                ('new_started', lambda: self.docker.stop(new_id, STOP_GRACE_S)),
                ('new_restart', lambda: self.docker.set_restart(new_id, {'Name': 'no', 'MaximumRetryCount': 0})),
                ('new_renamed', lambda: self.docker.rename(new_id, '%s-failed-%s' % (o.new_name, self.stamp))),
                ('old_renamed', lambda: self.docker.rename(old_id, o.old_name)),
                ('old_restart_no', lambda: self.docker.set_restart(old_id, old_policy)),
                ('old_stopped', lambda: self.docker.start(old_id))):
            if step == 'new_restart' or step in changed:
                try:
                    action()
                except KitError as error:
                    problems.append('%s: %s' % (step, error))
        self.receipt['rollback'] = {'problems': problems}
        self.log('rollback %s' % ('ok' if not problems else 'problems: %s' % problems))
        return not problems


def build_parser():
    p = argparse.ArgumentParser(description='UBTS ESP32-S3 bridge installer (check by default; --apply to install)')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--kit-dir', default=os.path.dirname(os.path.abspath(__file__)))
    p.add_argument('--old-name', default=OLD_NAME)
    p.add_argument('--new-name', default=NEW_NAME)
    p.add_argument('--dashboard-name', default=DASHBOARD_NAME)
    p.add_argument('--device', default=None,
                   help='by-id path of the ESP32 (never a ttyACM scan); default: the by-id path of --identity')
    p.add_argument('--identity', default=IDENTITY,
                   help='expected udev ID_SERIAL (default: environment variable UBTS_ESP32_IDENTITY)')
    p.add_argument('--device-mode', choices=('by-id', 'node'), default='by-id',
                   help='by-id: Docker resolves the symlink at every start (default); node: pin the node found now')
    p.add_argument('--max-drive-pwm', type=int, default=255,
                   help='host cap of the drive PWM (255 = 100 %%, since 2026-11-19; 153 = 60 %% was the old default)')
    p.add_argument('--left-forward', choices=('F', 'R'), default='F')
    p.add_argument('--right-forward', choices=('F', 'R'), default='F')
    p.add_argument('--direction-verified', action='store_true', help='only after the wheels-up check on site')
    p.add_argument('--allow-esp-absent', action='store_true',
                   help='ready without a connected ESP32 (the bridge runs and keeps trying to open it)')
    p.add_argument('--ready-timeout', type=float, default=READY_TIMEOUT_S)
    p.add_argument('--udev-root', default='/run/udev/data')
    p.add_argument('--docker-socket', default=DEFAULT_SOCKET)
    p.add_argument('--state-dir', default=None)
    return p


def main(argv=None, docker=None, host=None, out=None):
    options = build_parser().parse_args(argv)
    out = out or sys.stdout
    if not options.identity:
        print('결과: refused (보드 ID_SERIAL 이 없다: --identity 또는 UBTS_ESP32_IDENTITY)', file=out)
        return 2
    if options.device is None:
        options.device = device_for(options.identity)
    if not 0 <= options.max_drive_pwm <= 255:
        print('결과: refused (max-drive-pwm 0..255)', file=out)
        return 2
    try:
        kit = Kit(options.kit_dir)
    except (KitError, OSError, ValueError) as error:
        print('묶음 오류: %s' % error, file=out)
        return 2
    docker = docker or DockerAPI(options.docker_socket)
    host = host or Host()
    if not options.apply:
        try:
            report, blockers = run_checks(docker, host, kit, options)
        except KitError as error:
            print('점검 실패: %s' % error, file=out)
            return 2
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str), file=out)
        for warning in report.get('warnings', []):
            print('경고: %s' % warning, file=out)
        if blockers:
            print('설치 불가: %s' % ', '.join(blockers), file=out)
            return 2
        print('점검 통과 (바꾼 것 없음. 설치는 --apply)', file=out)
        return 0
    installer = Installer(docker, host, kit, options, out=out)
    try:
        code = installer.apply()
    except Exception as error:      # an unexpected error: record it; the receipt says what was changed
        installer.receipt['result'] = 'error'
        installer.receipt['error'] = {'code': getattr(error, 'code', type(error).__name__),
                                      'detail': getattr(error, 'detail', str(error))}
        code = 2 if installer.receipt.get('target') is None else 3
    state = Path(options.state_dir or default_state_dir()) / ('%s_%s' % (installer.stamp, kit.commit[:12]))
    try:
        write_json(state / 'RECEIPT.json', installer.receipt)
        (state.parent / 'LATEST_RECEIPT').write_bytes(str(state / 'RECEIPT.json').encode('utf-8'))
        print('영수증: %s' % (state / 'RECEIPT.json'), file=out)
    except OSError as error:
        print('영수증을 쓰지 못함: %s' % error, file=out)
    print('결과: %s' % installer.receipt.get('result'), file=out)
    return code


if __name__ == '__main__':
    sys.exit(main())
