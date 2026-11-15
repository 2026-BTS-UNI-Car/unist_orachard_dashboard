#!/usr/bin/env python3
"""UBTS row executor (autonomous row following) installer for the Jetson (Python 3 standard library only).

Default: check only (dry-run). Nothing is changed unless --apply is given. --rollback undoes the latest install.

The executor is the simulation repository's tools/row_executor at one commit (docs/steering/EXECUTOR_ESP32_KO.md there);
the kit carries that package byte-exact and the vehicle configuration. What --apply does:
  1. checks: kit files (SHA-256, the package's own SHA256SUMS), Docker, the runtime image (an existing ROS Iron image
     with Python 3.10, numpy and scipy: no build, no pull), the container name, the dashboard container (its runtime
     folder is where the dashboard link file and the operator token go), the ROS domain of the sensor containers,
     the operator port on 127.0.0.1 is free
  2. host folders: ~/ubts-row-executor/releases/<stamp>_<commit>/ (the package and config.json, read-only in the
     container), ~/ubts-row-executor/state/field_tuning.json (copied from the package default only when missing; the
     field tuning of earlier runs is never overwritten), ~/ubts-row-executor/log/ (JSONL logs), <dashboard runtime>/
     row_executor/ (the operator token and the link file)
  3. preflight: a short container from the runtime image with the release folder read-only, network none: imports the
     executor, checks its SHA256SUMS and vendor manifest, loads the configuration, builds the loop in the requested mode
     and imports rclpy; kept (exited), never removed
  4. a new container '<name>' (network and IPC host like the sensor containers, restart unless-stopped, 1 GiB) that
     runs 'python3 -m row_executor.executor_main' with the configuration. Mode 'shadow' (the default): no
     --motor-output and NO mount of /run/ubts-rover, so the executor cannot reach the control bridge at all; it reads
     /scan and /imu/data_raw and computes proposals only. Mode 'motor' (field day, after the steering and drive
     measurements, a person at the vehicle): --motor-output and /run/ubts-rover; still nothing moves before an operator
     Start, Arm and go on the dashboard, and the executor's own GO rules refuse the configuration while a measurement is
     missing. A container of an earlier install is stopped, renamed '<name>-kept-<stamp>' (restart no) and kept.
  5. ready: the container runs, the operator API on 127.0.0.1 answers (the token is read through the Docker API and
     never printed), run_state idle, the mode is the requested one, /scan and IMU arrive (the executor's message
     counters grow by >= 5 Hz scans and >= 15 Hz IMU over 3 s; the sensor ages exist only inside a run);
     then the link file <dashboard runtime>/row_executor/dashboard_link.json is written (the dashboard relay reads it
     when its code has the link-file reader, dashboard_server/executor_proxy.LinkedExecutor)
  Any failure after step 4 began stops the new container (renamed '<name>-failed-<stamp>', restart no, kept), puts
  the link file aside and restores the earlier container. Never used: docker compose, build, pull, prune, container or
  image removal, the control socket, any motor or arm command, firmware writes. The receipt (paths, SHA-256, container
  and image IDs, times, environment variable names only) goes to --state-dir (default ~/ubts-releases/row-executor-kit).
Exit codes: 0 check passed / installed / rolled back, 1 failed but the old state is back, 2 refused before any change,
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
import posixpath
import socket
import sys
import tarfile
import time
import urllib.parse
from pathlib import Path

MANIFEST_SCHEMA = 'ubts.row_executor_kit.manifest.v1'
RECEIPT_SCHEMA = 'ubts.row_executor_kit.receipt.v1'
LINK_SCHEMA = 'ubts.row_executor.dashboard_link.v1'
NAME = 'unist-orachard-row-executor'
DASHBOARD_NAME = 'unist-orachard-dashboard'
ROS_REFERENCE = 'unist-orachard-ros2-lidar-driver'        # the ROS domain of the sensor containers
# Runtime image: an existing ROS Iron arm64 image on the Jetson with Python 3.10.12, numpy 1.21.5, scipy 1.8.0, rclpy
# (checked read-only 2026-11-16; the native sensor observation service runs on it). No build, no pull.
IMAGE = 'sha256:a59689051b2e1529206f9e9003b83f8721930f6e2b868ecae427438e658694b0'
BIND_HOST, BIND_PORT = '127.0.0.1', 8097
PKG_IN = '/opt/ubts/row_executor'
CONFIG_IN = '/etc/row_executor/config.json'
STATE_IN = '/var/lib/row_executor'
LOG_IN = '/var/log/row_executor'
LINK_IN = '/run/ubts-row-executor-link'
CONTROL_DIR = '/run/ubts-rover'
TOKEN_NAME = 'operator_token.txt'
LINK_NAME = 'dashboard_link.json'
DASH_RUNTIME_IN = '/app/runtime'
MODES = ('shadow', 'motor')
MEMORY_BYTES = 1024 * 1024 * 1024
MAX_LOG_MB = 1000
DEFAULT_SOCKET = '/var/run/docker.sock'
STOP_GRACE_S = 10
DOCKER_TIMEOUT_S = 20.0
PREFLIGHT_TIMEOUT_S = 120.0
READY_TIMEOUT_S = 60.0
READY_RATE_WINDOW_S = 3.0      # the sensor message rates are taken over this window (RPLIDAR S2 10 Hz, BNO085 ~33 Hz)
SCAN_HZ_MIN, IMU_HZ_MIN = 5.0, 15.0
MARKER = 'UBTS_ROW_EXECUTOR_PREFLIGHT_OK'
PREFLIGHT_CODE = (
    "import json, os, sys\n"
    "sys.path.insert(0, '/opt/ubts')\n"
    "from dataclasses import replace\n"
    "import numpy, scipy\n"
    "from row_executor import config as C, executor_main as M\n"
    "from row_executor.core import verify_vendor\n"
    "digest = M.verify_sources()\n"
    "verify_vendor()\n"
    "cfg = C.load_config('" + CONFIG_IN + "')\n"
    "motor = os.environ.get('UBTS_KIT_MODE') == 'motor'\n"
    "loop = M.ExecutorLoop(cfg, motor_output_flag=motor)\n"
    "import rclpy, sensor_msgs.msg, std_msgs.msg\n"
    "out = dict(python=sys.version.split()[0], numpy=numpy.__version__, scipy=scipy.__version__,\n"
    "           sums_sha256=digest, config_digest=cfg.digest(), vehicle_profile=cfg.vehicle_profile,\n"
    "           bridge_kind=cfg.steer_bridge_kind, motor_allowed=loop.motor_allowed,\n"
    "           feasibility=cfg.feasibility()['status'], bind='%s:%d' % (cfg.bind_host, cfg.bind_port),\n"
    "           socket=cfg.socket_path, tuning=loop.tuning.brief() if loop.tuning else None)\n"
    "print('" + MARKER + " ' + json.dumps(out, default=str))\n"
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
# Docker Engine API over the Unix socket (only inspect, create, start, stop, wait, rename, update, logs, archive read)
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

    def _call(self, method, path, body=None, *, timeout=DOCKER_TIMEOUT_S, ok=(200, 201, 204, 304), missing_ok=False):
        headers, data = {}, None
        if body is not None:
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

    def read_file(self, cid, path, max_bytes=64 * 1024):
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
# Host access (separate so tests give a fake host)
# --------------------------------------------------------------------------------------------------
class Host:
    def home(self):
        user = os.environ.get('SUDO_USER')
        return os.path.expanduser('~' + user) if user else os.path.expanduser('~')

    def exists(self, path):
        return os.path.lexists(path)

    def isdir(self, path):
        return os.path.isdir(path)

    def makedirs(self, path):
        os.makedirs(path, exist_ok=True)

    def write_bytes(self, path, data, mode=0o644):
        tmp = '%s.tmp-%d' % (path, os.getpid())
        with open(tmp, 'wb') as handle:
            handle.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)

    def read_bytes(self, path, limit=1024 * 1024):
        try:
            with open(path, 'rb') as handle:
                return handle.read(limit)
        except OSError:
            return None

    def rename(self, source, target):
        os.replace(source, target)

    def port_free(self, host, port):
        """True when nothing listens on host:port (a connect is refused)."""
        try:
            with socket.create_connection((host, port), timeout=.5):
                return False
        except OSError:
            return True

    def http_status(self, port, token, timeout=2.0):
        """GET /api/status of the executor's operator API with its token (never printed); (code, dict) or None."""
        conn = http.client.HTTPConnection(BIND_HOST, port, timeout=timeout)
        try:
            conn.request('GET', '/api/status', headers={'X-Row-Token': token, 'Host': '%s:%d' % (BIND_HOST, port),
                                                        'Accept': 'application/json'})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read(1024 * 1024))
        except (OSError, ValueError, http.client.HTTPException):
            return None
        finally:
            conn.close()


# --------------------------------------------------------------------------------------------------
# Kit
# --------------------------------------------------------------------------------------------------
class Kit:
    def __init__(self, kit_dir):
        self.dir = Path(kit_dir)
        path = self.dir / 'MANIFEST.json'
        need(path.is_file(), 'kit_manifest_missing', str(path))
        self.manifest_bytes = path.read_bytes()
        self.manifest = json.loads(self.manifest_bytes)
        need(self.manifest.get('schema') == MANIFEST_SCHEMA, 'kit_manifest_schema', str(self.manifest.get('schema')))
        self.commit = str(self.manifest.get('sim_commit', ''))
        need(len(self.commit) == 40, 'kit_manifest_commit', self.commit)
        self.files = {}
        for rel, digest in sorted(self.manifest.get('files', {}).items()):
            need(rel.startswith(('row_executor/', 'config/')) and '..' not in rel.split('/') and not rel.startswith('/'),
                 'kit_bad_path', rel)
            data = (self.dir / 'payload' / rel).read_bytes()
            need(sha256(data) == digest, 'kit_file_sha256', rel)
            self.files[rel] = data
        need('row_executor/executor_main.py' in self.files and 'row_executor/SHA256SUMS' in self.files,
             'kit_incomplete', 'executor package missing')
        self._check_package_sums()

    def _check_package_sums(self):
        """The package's own SHA256SUMS lists exactly the package files with these bytes (executor_main re-checks)."""
        listed = {}
        for line in self.files['row_executor/SHA256SUMS'].decode('utf-8').splitlines():
            digest, _, rel = line.partition('  ')
            listed['row_executor/' + rel] = digest
        package = {rel for rel in self.files if rel.startswith('row_executor/') and rel != 'row_executor/SHA256SUMS'}
        need(set(listed) == package, 'kit_package_sums_files', ', '.join(sorted(set(listed) ^ package))[:200])
        for rel in package:
            need(sha256(self.files[rel]) == listed[rel], 'kit_package_sums', rel)

    def config(self, name):
        rel = 'config/' + name
        need(rel in self.files, 'kit_config_missing', name)
        return self.files[rel]


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


def paths(host, options, dash_runtime, stamp, commit):
    base = options.base_dir or posixpath.join(host.home(), 'ubts-row-executor')
    release = posixpath.join(base, 'releases', '%s_%s' % (stamp, commit[:12]))
    return {'base': base, 'release': release, 'package': posixpath.join(release, 'row_executor'),
            'config': posixpath.join(release, 'config.json'), 'state': posixpath.join(base, 'state'),
            'tuning': posixpath.join(base, 'state', 'field_tuning.json'), 'log': posixpath.join(base, 'log'),
            'link_dir': posixpath.join(dash_runtime, 'row_executor') if dash_runtime else None}


def container_spec(image_id, options, p, ros_domain, commit, config_digest):
    motor = options.mode == 'motor'
    args = ['python3', '-B', '-s', '-m', 'row_executor.executor_main', '--config', CONFIG_IN, '--log-dir', LOG_IN,
            '--token-file', LINK_IN + '/' + TOKEN_NAME, '--no-print-token', '--max-log-mb', str(MAX_LOG_MB)]
    if motor:
        args.append('--motor-output')
    binds = ['%s:%s:ro' % (p['package'], PKG_IN), '%s:%s:ro' % (p['config'], CONFIG_IN),
             '%s:%s:rw' % (p['state'], STATE_IN), '%s:%s:rw' % (p['log'], LOG_IN),
             '%s:%s:rw' % (p['link_dir'], LINK_IN)]
    if motor:
        binds.append('%s:%s:rw' % (CONTROL_DIR, CONTROL_DIR))        # the control socket (shadow: not reachable)
    return {
        'Image': image_id,
        'Entrypoint': ['/ros_entrypoint.sh'],
        'Cmd': ['/bin/bash', '-lc', 'source /opt/ros/iron/setup.bash && exec ' + ' '.join(args)],
        'WorkingDir': '/opt/ubts',
        'User': '0:0',
        'Env': ['PYTHONPATH=/opt/ubts', 'PYTHONDONTWRITEBYTECODE=1', 'PYTHONUNBUFFERED=1', 'ROS_DOMAIN_ID=%s' % ros_domain,
                'ROS_LOG_DIR=%s/ros' % LOG_IN, 'OMP_NUM_THREADS=1', 'OPENBLAS_NUM_THREADS=1'],
        'Labels': {'ubts.kit': 'row-executor', 'ubts.kit.sim_commit': commit, 'ubts.kit.mode': options.mode,
                   'ubts.kit.config_digest': config_digest},
        'StopSignal': 'SIGTERM',
        'StopTimeout': STOP_GRACE_S,
        'HostConfig': {
            'Binds': binds,
            'NetworkMode': 'host',
            'IpcMode': 'host',
            'SecurityOpt': ['no-new-privileges:true'],
            'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
            'Memory': MEMORY_BYTES,
            'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '10m', 'max-file': '3'}},
            'Privileged': False,
        },
    }


def summarize_spec(spec):
    hc = spec['HostConfig']
    return {'image': spec['Image'], 'cmd': spec['Cmd'], 'env_names': sorted(i.split('=', 1)[0] for i in spec['Env']),
            'binds': hc['Binds'], 'network': hc['NetworkMode'], 'ipc': hc['IpcMode'], 'restart': hc['RestartPolicy'],
            'memory': hc['Memory'], 'labels': spec['Labels']}


def run_checks(docker, host, kit, options):
    """Read-only. Returns (report, blockers, context)."""
    report = {'kit': {'sim_commit': kit.commit, 'manifest_sha256': sha256(kit.manifest_bytes), 'files': len(kit.files),
                      'config': options.config_file or options.config_name, 'mode': options.mode}}
    blockers, ctx = [], {}

    def check(name, fn):
        try:
            report[name] = fn()
        except KitError as error:
            report[name] = {'error': error.code, 'detail': error.detail}
            blockers.append('%s:%s' % (error.code, error.detail) if error.detail else error.code)

    def config():
        if options.config_file:
            data = host.read_bytes(options.config_file)
            need(data is not None, 'config_file_unreadable', options.config_file)
        else:
            data = kit.config(options.config_name)
        try:
            value = json.loads(data)
        except ValueError:
            raise KitError('config_not_json', options.config_file or options.config_name)
        need(value.get('vehicle_profile') == 'front_steer' and value.get('steer_bridge_kind') == 'esp32_abs_hold',
             'config_not_esp32_front_steer', options.config_name)
        need(value.get('bind_host') == BIND_HOST, 'config_bind_not_loopback', str(value.get('bind_host')))
        need(value.get('socket_path') == CONTROL_DIR + '/control.sock', 'config_socket', str(value.get('socket_path')))
        if options.mode == 'shadow':
            need(value.get('motor_output_enabled') is False, 'config_enables_motor', 'shadow mode needs a SHADOW config')
        else:
            need(value.get('motor_output_enabled') is True, 'config_not_motor', 'motor mode needs motor_output_enabled')
        ctx['config'] = data
        ctx['port'] = int(value.get('bind_port'))
        ctx['tuning_file'] = value.get('tuning_file')
        need(ctx['tuning_file'] == STATE_IN + '/field_tuning.json', 'config_tuning_file', str(ctx['tuning_file']))
        return {'sha256': sha256(data), 'port': ctx['port'], 'motor_output_enabled': value.get('motor_output_enabled')}
    check('config', config)

    def image():
        info = docker.image(options.image)
        need(info is not None, 'image_missing', options.image)
        need(info.get('Architecture') == 'arm64', 'image_arch', str(info.get('Architecture')))
        env = {k: v for k, v in (item.partition('=')[::2] for item in (info.get('Config') or {}).get('Env') or [])}
        need(env.get('ROS_DISTRO') == 'iron', 'image_not_ros_iron', str(env.get('ROS_DISTRO')))
        ctx['image'] = info['Id']
        return {'id': info['Id'], 'tags': info.get('RepoTags'), 'ros': env.get('ROS_DISTRO')}
    check('image', image)

    def name():
        info = docker.inspect(options.name)
        if info is None:
            return 'free'
        st = info.get('State') or {}
        ctx['old'] = info
        return {'existing': info['Id'][:12], 'state': st.get('Status'),
                'becomes': options.name + '-kept-<stamp> (restart no, kept)'}
    check('name', name)

    def dashboard():
        info = docker.inspect(options.dashboard_name)
        need(info is not None, 'dashboard_missing', options.dashboard_name)
        runtime = bind_source(info, DASH_RUNTIME_IN)
        need(runtime, 'dashboard_runtime_bind_missing', DASH_RUNTIME_IN)
        ctx['dash_runtime'] = runtime
        env = env_dict(info)
        return {'runtime_bind': runtime, 'executor_env_set': 'UBTS_EXECUTOR_URL' in env,
                'link_env_set': 'UBTS_EXECUTOR_LINK_FILE' in env, 'running': bool((info.get('State') or {}).get('Running'))}
    check('dashboard', dashboard)

    def ros():
        info = docker.inspect(options.ros_reference)
        domain = env_dict(info).get('ROS_DOMAIN_ID', '0') if info else '0'
        need(domain.isdigit(), 'ros_domain', domain)
        ctx['ros_domain'] = domain
        return {'reference': options.ros_reference, 'found': info is not None, 'domain': domain,
                'network': ((info or {}).get('HostConfig') or {}).get('NetworkMode'),
                'ipc': ((info or {}).get('HostConfig') or {}).get('IpcMode')}
    check('ros', ros)

    def port():
        if 'port' not in ctx:
            return 'not_tested'
        old = ctx.get('old')
        old_running = bool(old and (old.get('State') or {}).get('Running'))
        free = host.port_free(BIND_HOST, ctx['port'])
        need(free or old_running, 'port_in_use', '%s:%d' % (BIND_HOST, ctx['port']))
        return 'free' if free else 'used_by_the_existing_executor'
    check('port', port)

    if options.mode == 'motor':
        def control():
            need(host.isdir(CONTROL_DIR), 'control_dir_missing', CONTROL_DIR)
            return {'dir': CONTROL_DIR}
        check('control_dir', control)
    if 'image' in ctx and 'dash_runtime' in ctx and 'config' in ctx:
        p = paths(host, options, ctx['dash_runtime'], '<stamp>', kit.commit)
        report['plan'] = summarize_spec(container_spec(ctx['image'], options, p, ctx.get('ros_domain', '0'), kit.commit,
                                                       sha256(ctx['config'])))
        report['plan']['paths'] = p
        report['plan']['link_file'] = posixpath.join(p['link_dir'], LINK_NAME)
    return report, blockers, ctx


# --------------------------------------------------------------------------------------------------
# Apply and rollback
# --------------------------------------------------------------------------------------------------
def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(json.dumps(value, ensure_ascii=False, indent=1, default=str).encode('utf-8'))
    os.replace(tmp, path)


def default_state_dir(host):
    return os.path.join(host.home(), 'ubts-releases', 'row-executor-kit')


def link_bytes(port):
    body = {'schema': LINK_SCHEMA, 'url': 'http://%s:%d' % (BIND_HOST, port),
            'token_file': '%s/row_executor/%s' % (DASH_RUNTIME_IN, TOKEN_NAME),
            'note': 'written by deploy/jetson_row_executor/install_row_executor.py; the dashboard reads it'}
    return (json.dumps(body, indent=1, sort_keys=True) + '\n').encode('utf-8')


class Installer:
    def __init__(self, docker, host, kit, options, *, clock=time.monotonic, sleep=time.sleep, out=None):
        self.docker, self.host, self.kit, self.options = docker, host, kit, options
        self.clock, self.sleep = clock, sleep
        self.out = out or sys.stdout
        self.stamp = utc_stamp()
        self.receipt = {'schema': RECEIPT_SCHEMA, 'stamp': self.stamp, 'sim_commit': kit.commit, 'steps': [],
                        'options': {k: v for k, v in vars(options).items() if k not in ('state_dir',)}}

    def log(self, text):
        self.receipt['steps'].append({'t': round(self.clock(), 3), 'step': text})
        print(text, file=self.out)

    def stage_files(self, p, config):
        """Release folder (new, never overwritten), state and log folders, the field tuning copy when missing."""
        host = self.host
        need(not host.exists(p['release']), 'release_exists', p['release'])
        for rel, data in sorted(self.kit.files.items()):
            if rel.startswith('row_executor/'):
                target = posixpath.join(p['release'], *rel.split('/'))
                host.makedirs(posixpath.dirname(target))
                host.write_bytes(target, data, 0o644)
        host.write_bytes(p['config'], config, 0o644)
        for folder in (p['state'], p['log'], p['link_dir']):
            host.makedirs(folder)
        tuning = 'kept'
        if not host.exists(p['tuning']):
            host.write_bytes(p['tuning'], self.kit.files['row_executor/field_tuning.default.json'], 0o644)
            tuning = 'copied_from_package_default'
        return {'release': p['release'], 'files': sum(1 for r in self.kit.files if r.startswith('row_executor/')),
                'config_sha256': sha256(config), 'field_tuning': tuning}

    def preflight(self, image_id, p):
        name = '%s-kit-preflight-%s' % (self.options.name, self.stamp)
        spec = {'Image': image_id, 'Entrypoint': ['/ros_entrypoint.sh'],
                'Cmd': ['/bin/bash', '-lc', 'source /opt/ros/iron/setup.bash && exec python3 -B -c "$0"', PREFLIGHT_CODE],
                'Env': ['UBTS_KIT_MODE=' + self.options.mode, 'PYTHONDONTWRITEBYTECODE=1'],
                'WorkingDir': '/opt/ubts', 'User': '0:0', 'Labels': {'ubts.kit': 'row-executor-preflight'},
                'HostConfig': {'NetworkMode': 'none', 'RestartPolicy': {'Name': 'no'},
                               'Binds': ['%s:%s:ro' % (p['package'], PKG_IN), '%s:%s:ro' % (p['config'], CONFIG_IN),
                                         '%s:%s:ro' % (p['state'], STATE_IN)]}}
        cid = self.docker.create(name, spec)
        self.receipt['preflight_container'] = {'name': name, 'id': cid}
        self.docker.start(cid)
        code = self.docker.wait(cid, PREFLIGHT_TIMEOUT_S)
        logs = self.docker.logs(cid)
        self.receipt['preflight_log_tail'] = logs[-2000:]
        need(code == 0 and MARKER in logs, 'preflight_failed', 'exit %s' % code)
        line = [l for l in logs.splitlines() if MARKER in l][-1]
        result = json.loads(line.split(MARKER, 1)[1])
        need(result.get('motor_allowed') is (self.options.mode == 'motor'), 'preflight_mode',
             'motor_allowed %s' % result.get('motor_allowed'))
        return result

    def wait_ready(self, cid, port):
        """The executor answers on its operator API, idle, in the requested mode, and /scan and IMU arrive: while no run
        is open the executor reports no sensor ages, so its message counters (counts scans_processed, imu_fed) must grow
        by at least SCAN_HZ_MIN / IMU_HZ_MIN over READY_RATE_WINDOW_S."""
        deadline = self.clock() + self.options.ready_timeout
        last, first = None, None
        while self.clock() < deadline:
            info = self.docker.inspect(cid) or {}
            st = info.get('State') or {}
            need(st.get('Running'), 'executor_not_running', '%s exit=%s' % (st.get('Status'), st.get('ExitCode')))
            raw = self.docker.read_file(cid, LINK_IN + '/' + TOKEN_NAME)
            token = raw.decode('utf-8', 'replace').strip() if raw else ''
            reply = self.host.http_status(port, token) if token else None
            if reply is not None:
                code, status = reply
                need(code == 200, 'operator_api_http_%s' % code)
                last = {'run_state': status.get('run_state'), 'mode': status.get('mode'),
                        'lidar_age': status.get('lidar_age'), 'imu_age': status.get('imu_age'),
                        'vehicle_control': status.get('vehicle_control'), 'start_mode': status.get('start_mode'),
                        'feasibility': (status.get('feasibility') or {}).get('status'),
                        'go_blockers': status.get('go_blockers')}
                need(status.get('run_state') == 'idle', 'not_idle', str(status.get('run_state')))
                want = 'MOTOR' if self.options.mode == 'motor' else 'SHADOW'
                need(status.get('mode') == want, 'mode_mismatch', str(status.get('mode')))
                counts = status.get('counts') if isinstance(status.get('counts'), dict) else {}
                sample = (self.clock(), counts.get('scans_processed'), counts.get('imu_fed'))
                if self.options.allow_sensors_absent:
                    return last
                if all(isinstance(v, int) for v in sample[1:]):
                    if first is None:
                        first = sample
                    elif sample[0] - first[0] >= READY_RATE_WINDOW_S:
                        span = sample[0] - first[0]
                        last['scan_hz'] = round((sample[1] - first[1]) / span, 2)
                        last['imu_hz'] = round((sample[2] - first[2]) / span, 2)
                        if last['scan_hz'] >= SCAN_HZ_MIN and last['imu_hz'] >= IMU_HZ_MIN:
                            return last
            self.sleep(1.0)
        raise KitError('ready_timeout', json.dumps(last, default=str) if last else 'operator API did not answer')

    def apply(self):
        o = self.options
        report, blockers, ctx = run_checks(self.docker, self.host, self.kit, o)
        self.receipt['checks'] = report
        if blockers:
            self.receipt['result'] = 'refused'
            self.receipt['blockers'] = blockers
            self.log('설치 불가(바꾼 것 없음): %s' % ', '.join(blockers))
            return 2
        p = paths(self.host, o, ctx['dash_runtime'], self.stamp, self.kit.commit)
        self.receipt['paths'] = p
        link_path = posixpath.join(p['link_dir'], LINK_NAME)
        old = ctx.get('old')
        self.receipt['target'] = {'name': o.name, 'image': ctx['image'], 'mode': o.mode,
                                  'old_container_id': old['Id'] if old else None,
                                  'old_restart_policy': ((old or {}).get('HostConfig') or {}).get('RestartPolicy'),
                                  'old_was_running': bool(old and (old.get('State') or {}).get('Running')),
                                  'link_file': link_path, 'link_existed': self.host.exists(link_path)}
        try:
            self.receipt['staged'] = self.stage_files(p, ctx['config'])
            self.log('files staged: %s' % p['release'])
            self.receipt['preflight'] = self.preflight(ctx['image'], p)
            self.log('preflight ok: %s' % self.receipt['preflight'])
            spec = container_spec(ctx['image'], o, p, ctx['ros_domain'], self.kit.commit, sha256(ctx['config']))
            self.receipt['new_spec'] = summarize_spec(spec)
            staged = '%s-kit-new-%s' % (o.name, self.stamp)
            new_id = self.docker.create(staged, spec)
            self.receipt['target']['new_container_id'] = new_id
            self.log('new container created: %s' % new_id[:12])
        except KitError as error:
            self.receipt['result'] = 'failed_before_change'
            self.receipt['error'] = {'code': error.code, 'detail': error.detail}
            self.log('failed before any change of a running container: %s' % error)
            return 1
        changed = []
        try:
            if old:
                if (old.get('State') or {}).get('Running'):
                    self.docker.stop(old['Id'], STOP_GRACE_S)
                    changed.append('old_stopped')
                self.docker.set_restart(old['Id'], {'Name': 'no', 'MaximumRetryCount': 0})
                changed.append('old_restart_no')
                self.docker.rename(old['Id'], '%s-kept-%s' % (o.name, self.stamp))
                changed.append('old_renamed')
            self.docker.rename(new_id, o.name)
            changed.append('new_renamed')
            self.docker.start(new_id)
            changed.append('new_started')
            self.log('executor started (%s)' % o.mode)
            self.receipt['ready'] = self.wait_ready(new_id, ctx['port'])
            if self.host.exists(link_path):
                self.host.rename(link_path, '%s.before-%s' % (link_path, self.stamp))
                changed.append('link_set_aside')
            self.host.write_bytes(link_path, link_bytes(ctx['port']), 0o644)
            changed.append('link_written')
            self.receipt['link'] = {'path': link_path, 'sha256': sha256(link_bytes(ctx['port']))}
            self.receipt['result'] = 'installed'
            self.log('ready: %s' % self.receipt['ready'])
            return 0
        except KitError as error:
            self.receipt['error'] = {'code': error.code, 'detail': error.detail}
            self.log('failed: %s -> restoring' % error)
            try:
                self.receipt['new_log_tail'] = self.docker.logs(new_id)[-2000:]
            except KitError:
                pass
            ok = self.restore(old, new_id, changed, link_path)
            self.receipt['result'] = 'failed_rolled_back' if ok else 'failed_rollback_failed'
            return 1 if ok else 3

    def restore(self, old, new_id, changed, link_path):
        o, problems = self.options, []
        steps = [('new_started', lambda: self.docker.stop(new_id, STOP_GRACE_S)),
                 ('new_restart', lambda: self.docker.set_restart(new_id, {'Name': 'no', 'MaximumRetryCount': 0})),
                 ('new_renamed', lambda: self.docker.rename(new_id, '%s-failed-%s' % (o.name, self.stamp))),
                 ('link_written', lambda: self.host.rename(link_path, '%s.failed-%s' % (link_path, self.stamp))),
                 ('link_set_aside', lambda: self.host.rename('%s.before-%s' % (link_path, self.stamp), link_path))]
        if old:
            policy = (old.get('HostConfig') or {}).get('RestartPolicy') or {'Name': 'no'}
            steps += [('old_renamed', lambda: self.docker.rename(old['Id'], o.name)),
                      ('old_restart_no', lambda: self.docker.set_restart(old['Id'], policy)),
                      ('old_stopped', lambda: self.docker.start(old['Id']))]
        for step, action in steps:
            if step == 'new_restart' or step in changed:
                try:
                    action()
                except (KitError, OSError) as error:
                    problems.append('%s: %s' % (step, error))
        self.receipt['rollback'] = {'problems': problems}
        self.log('rollback %s' % ('ok' if not problems else 'problems: %s' % problems))
        return not problems


def rollback(docker, host, options, out):
    """Undo the latest install of the receipt: stop the executor (renamed '-rolledback-<stamp>', restart no, kept), put
    the link file aside, and restore the container an install kept (name, restart policy, running state)."""
    state = Path(options.state_dir or default_state_dir(host))
    latest = options.receipt or (host.read_bytes(str(state / 'LATEST_RECEIPT')) or b'').decode('utf-8').strip()
    need(latest, 'no_receipt', str(state))
    receipt = json.loads(host.read_bytes(latest) or b'{}')
    need(receipt.get('schema') == RECEIPT_SCHEMA and receipt.get('result') == 'installed', 'receipt_not_installed', latest)
    target = receipt['target']
    stamp = utc_stamp()
    done, problems = [], []

    def step(name, action):
        try:
            action()
            done.append(name)
        except (KitError, OSError) as error:
            problems.append('%s: %s' % (name, error))
    new = docker.inspect(target['new_container_id'])
    if new is not None:
        if (new.get('State') or {}).get('Running'):
            step('executor_stopped', lambda: docker.stop(new['Id'], STOP_GRACE_S))
        step('executor_restart_no', lambda: docker.set_restart(new['Id'], {'Name': 'no', 'MaximumRetryCount': 0}))
        step('executor_renamed', lambda: docker.rename(new['Id'], '%s-rolledback-%s' % (target['name'], stamp)))
    link = target.get('link_file')
    if link and host.exists(link):
        step('link_set_aside', lambda: host.rename(link, '%s.rolledback-%s' % (link, stamp)))
    old_id = target.get('old_container_id')
    if old_id and docker.inspect(old_id) is not None:
        step('old_renamed', lambda: docker.rename(old_id, target['name']))
        step('old_restart', lambda: docker.set_restart(old_id, target.get('old_restart_policy') or {'Name': 'no'}))
        if target.get('old_was_running'):
            step('old_started', lambda: docker.start(old_id))
    result = {'schema': RECEIPT_SCHEMA, 'rollback_of': latest, 'stamp': stamp, 'done': done, 'problems': problems,
              'result': 'rolled_back' if not problems else 'rollback_problems'}
    write_json(Path(latest).parent / ('ROLLBACK_%s.json' % stamp), result)
    print(json.dumps(result, ensure_ascii=False, indent=1), file=out)
    return 0 if not problems else 3


def build_parser():
    p = argparse.ArgumentParser(description='UBTS row executor installer (check by default; --apply to install)')
    action = p.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true')
    action.add_argument('--rollback', action='store_true', help='undo the latest install (receipt)')
    p.add_argument('--mode', choices=MODES, default='shadow',
                   help='shadow (default): no motor output, the control socket is not even mounted; motor: field day only')
    p.add_argument('--config-name', default='config.vehicle_esp32_shadow.json',
                   help='a configuration of the kit (config/<name>); motor mode needs a motor configuration')
    p.add_argument('--config-file', default=None,
                   help='a configuration file on this computer instead of the kit one (motor mode: the field values)')
    p.add_argument('--kit-dir', default=os.path.dirname(os.path.abspath(__file__)))
    p.add_argument('--name', default=NAME)
    p.add_argument('--image', default=IMAGE)
    p.add_argument('--dashboard-name', default=DASHBOARD_NAME)
    p.add_argument('--ros-reference', default=ROS_REFERENCE)
    p.add_argument('--base-dir', default=None, help='default ~/ubts-row-executor')
    p.add_argument('--allow-sensors-absent', action='store_true', help='ready without /scan and IMU (bench only)')
    p.add_argument('--ready-timeout', type=float, default=READY_TIMEOUT_S)
    p.add_argument('--receipt', default=None, help='rollback: a receipt other than the latest')
    p.add_argument('--docker-socket', default=DEFAULT_SOCKET)
    p.add_argument('--state-dir', default=None)
    return p


def main(argv=None, docker=None, host=None, out=None):
    options = build_parser().parse_args(argv)
    out = out or sys.stdout
    docker = docker or DockerAPI(options.docker_socket)
    host = host or Host()
    if options.rollback:
        try:
            return rollback(docker, host, options, out)
        except KitError as error:
            print('되돌리기 불가: %s' % error, file=out)
            return 2
    try:
        kit = Kit(options.kit_dir)
    except (KitError, OSError, ValueError) as error:
        print('묶음 오류: %s' % error, file=out)
        return 2
    if not options.apply:
        try:
            report, blockers, _ = run_checks(docker, host, kit, options)
        except KitError as error:
            print('점검 실패: %s' % error, file=out)
            return 2
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str), file=out)
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
    state = Path(options.state_dir or default_state_dir(host)) / ('%s_%s' % (installer.stamp, kit.commit[:12]))
    try:
        write_json(state / 'RECEIPT.json', installer.receipt)
        (state.parent / 'LAST_ATTEMPT').write_bytes(str(state / 'RECEIPT.json').encode('utf-8'))
        if installer.receipt.get('result') == 'installed':           # what --rollback undoes
            (state.parent / 'LATEST_RECEIPT').write_bytes(str(state / 'RECEIPT.json').encode('utf-8'))
        print('영수증: %s' % (state / 'RECEIPT.json'), file=out)
    except OSError as error:
        print('영수증을 쓰지 못함: %s' % error, file=out)
    print('결과: %s' % installer.receipt.get('result'), file=out)
    return code


if __name__ == '__main__':
    sys.exit(main())
