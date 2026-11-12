#!/usr/bin/env python3
"""UBTS dashboard installer for the Jetson (Python 3 standard library only).

Default: check only (dry-run). Nothing is changed unless --apply is given.

With --apply the running dashboard container is replaced like this:
  1. idle check (rover disarmed and not moving, recorder idle, no mapping job active)
  2. snapshot: commit the running container (its writable layer is kept) as a local image
  3. stage: a container from the snapshot gets only the changed/added files (and the files the
     repository deleted); it is committed as the new local image (no build, no pull, no network)
  4. preflight: a short container from the new image imports the dashboard modules (network none)
  5. a new container with the same specification (Env, mounts, host network, restart policy, ...)
     is created; its /app files are compared with the bundle before anything is stopped
  6. idle check again, the old container is stopped, renamed '<name>-kept-<stamp>' and kept
     (restart policy 'no'); the new container takes the name and starts
  7. readiness: /api/health, Docker health, every static file served equal to the bundle,
     a few API answers, idle check on the new dashboard
  Any failure after step 6 began restores the old container automatically.

Never used: docker compose, prune, pull, image or container removal, motor/arm/firmware commands.
Every Docker call and HTTP request has a timeout; the whole install has a deadline and a separate
rollback reserve. The receipt (file SHA-256, image and container IDs, times) is written under
--state-dir (default ~/ubts-releases/dashboard-kit).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import http.client
import http.cookiejar
import io
import json
import os
import posixpath
import re
import signal
import socket
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MANIFEST_SCHEMA = 'ubts.dashboard_kit.manifest.v1'
RECEIPT_SCHEMA = 'ubts.dashboard_kit.receipt.v1'
DEFAULT_NAME = 'unist-orachard-dashboard'
DEFAULT_SOCKET = '/var/run/docker.sock'
DEFAULT_IMAGE_REPO = 'ubts-dashboard-kit'
APP_ROOT = '/app'
GENERATED_IN_IMAGE = ('dashboard_server/_native_raster.so',)
REBUILD_SOURCES = {
    'requirements.txt': 'requirements_changed',
    'dashboard_server/native_raster.cc': 'native_raster_source_changed',
}
INSTALL_DEADLINE_S = 480.0
ROLLBACK_RESERVE_S = 150.0
READY_TIMEOUT_S = 120.0
STOP_TIMEOUT_S = 20
STAGE_TIMEOUT_S = 60.0
PREFLIGHT_TIMEOUT_S = 90.0
DOCKER_CALL_TIMEOUT_S = 20.0
COMMIT_TIMEOUT_S = 120.0
HTTP_TIMEOUT_S = 5.0
MAX_JSON_REPLY = 8 * 1024 * 1024
MAX_TREE_BYTES = 1024 * 1024 * 1024
MAX_TREE_MEMBERS = 200000
MAX_STATIC_BYTES = 32 * 1024 * 1024
VERSION_QUERY = re.compile(rb'\?v=[0-9a-f]{10}(?=["\'\s)])')
PREFLIGHT_MODULES = ('dashboard_server.app',)

REMOVE_CODE = (
    "import os,sys\n"
    "n=0\n"
    "for p in sys.argv[1:]:\n"
    "    if os.path.lexists(p) and not os.path.isdir(p):\n"
    "        os.remove(p); n+=1\n"
    "print('UBTS_KIT_REMOVE_OK', n)\n"
)
IMPORT_CODE = (
    "import importlib,json,sys\n"
    "sys.path.insert(0,'/app')\n"
    "for m in sys.argv[1:]:\n"
    "    importlib.import_module(m)\n"
    "print('UBTS_KIT_IMPORT_OK '+json.dumps(sys.argv[1:]))\n"
)


class KitError(Exception):
    """A reason to stop. code is short and stable; detail is for people."""

    def __init__(self, code, detail=''):
        super().__init__(code if not detail else code + ': ' + str(detail))
        self.code = code
        self.detail = str(detail)


def need(value, code, detail=''):
    if not value:
        raise KitError(code, detail)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def utc_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


class Deadline:
    def __init__(self, seconds, clock=time.monotonic):
        self.clock = clock
        self.end = clock() + float(seconds)

    def remaining(self):
        return self.end - self.clock()

    def need(self, seconds, step):
        if self.remaining() < seconds:
            raise KitError('deadline', step)

    def timeout(self, cap):
        return max(1.0, min(float(cap), self.remaining()))


# --------------------------------------------------------------------------------------------------
# Docker Engine API over the Unix socket
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


def _q(value):
    return urllib.parse.quote(str(value), safe='')


def tar_digest(stream, parent, max_bytes=MAX_TREE_BYTES, max_members=MAX_TREE_MEMBERS):
    """Hash every regular file of a tar stream. Names are returned relative to /app.

    parent is the directory that holds the archived path (for /app/static it is /app)."""
    base = posixpath.relpath(parent, APP_ROOT) if parent != APP_ROOT else ''
    need(not base.startswith('..'), 'path_outside_app', parent)
    out = {}
    total = 0
    count = 0
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            count += 1
            need(count <= max_members, 'archive_too_many_members', parent)
            name = member.name[2:] if member.name.startswith('./') else member.name
            name = posixpath.normpath(name or '.')
            need(not name.startswith('..') and not name.startswith('/'), 'archive_bad_name', member.name)
            rel = posixpath.normpath(posixpath.join(base, name)) if base else name
            if member.isdir():
                out[rel] = ('dir', None, 0)
            elif member.isfile():
                handle = archive.extractfile(member)
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    total += len(chunk)
                    need(total <= max_bytes, 'archive_too_large', parent)
                    digest.update(chunk)
                out[rel] = ('file', digest.hexdigest(), size)
            elif member.issym() or member.islnk():
                out[rel] = ('link', member.linkname, 0)
            else:
                out[rel] = ('other', None, 0)
    return out


def demux_logs(raw):
    """Docker log stream without TTY: 8-byte frame headers. Falls back to raw text."""
    out = []
    pos = 0
    while pos + 8 <= len(raw) and raw[pos] in (0, 1, 2) and raw[pos + 1:pos + 4] == b'\0\0\0':
        size = int.from_bytes(raw[pos + 4:pos + 8], 'big')
        out.append(raw[pos + 8:pos + 8 + size])
        pos += 8 + size
    if pos != len(raw):
        return raw.decode('utf-8', 'replace')
    return b''.join(out).decode('utf-8', 'replace')


class DockerAPI:
    """The few Docker Engine API calls the kit uses. There is no remove, prune or pull call."""

    def __init__(self, socket_path=DEFAULT_SOCKET):
        self.socket_path = socket_path

    def _call(self, method, path, body=None, *, raw=None, content_type='application/json',
              timeout=DOCKER_CALL_TIMEOUT_S, ok=(200, 201, 204, 304), missing_ok=False, max_bytes=MAX_JSON_REPLY):
        headers = {}
        data = None
        if raw is not None:
            data = raw
            headers['Content-Type'] = content_type
        elif body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        conn = _UnixConnection(self.socket_path, timeout)
        try:
            try:
                conn.request(method, path, body=data, headers=headers)
                resp = conn.getresponse()
                payload = resp.read(max_bytes + 1)
            except FileNotFoundError:
                raise KitError('docker_socket_missing', self.socket_path)
            except PermissionError:
                raise KitError('docker_socket_permission', 'sudo 로 실행하거나 docker 그룹이 필요합니다')
            except socket.timeout:
                raise KitError('docker_timeout', method + ' ' + path.split('?')[0])
            except OSError as error:
                raise KitError('docker_unreachable', type(error).__name__)
            need(len(payload) <= max_bytes, 'docker_reply_too_large', path.split('?')[0])
            if missing_ok and resp.status == 404:
                return None
            if resp.status not in ok:
                message = ''
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

    def containers(self, include_stopped=False):
        return self._call('GET', '/containers/json?all=' + ('1' if include_stopped else '0'))

    def inspect(self, ref):
        return self._call('GET', '/containers/' + _q(ref) + '/json', missing_ok=True)

    def image(self, ref):
        return self._call('GET', '/images/' + _q(ref) + '/json', missing_ok=True)

    def read_tree(self, cid, path, timeout=60.0, max_bytes=MAX_TREE_BYTES):
        conn = _UnixConnection(self.socket_path, timeout)
        try:
            try:
                conn.request('GET', '/containers/' + _q(cid) + '/archive?path=' + _q(path))
                resp = conn.getresponse()
                if resp.status == 404:
                    resp.read(65536)
                    return None
                if resp.status != 200:
                    raise KitError('docker_api', 'GET archive %s -> %s' % (path, resp.status))
                return tar_digest(resp, posixpath.dirname(path.rstrip('/')) or '/', max_bytes)
            except socket.timeout:
                raise KitError('docker_timeout', 'GET archive ' + path)
            except tarfile.TarError as error:
                raise KitError('archive_unreadable', path + ' ' + type(error).__name__)
        finally:
            conn.close()

    def read_file(self, cid, path, max_bytes=65536, timeout=10.0):
        conn = _UnixConnection(self.socket_path, timeout)
        try:
            try:
                conn.request('GET', '/containers/' + _q(cid) + '/archive?path=' + _q(path))
                resp = conn.getresponse()
                raw = resp.read(max_bytes + 4096)
            except socket.timeout:
                raise KitError('docker_timeout', 'GET archive ' + path)
            if resp.status != 200 or len(raw) > max_bytes + 2048:
                return None
            try:
                with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
                    for member in archive:
                        if member.isfile():
                            return archive.extractfile(member).read(max_bytes + 1)[:max_bytes]
            except tarfile.TarError:
                return None
            return None
        finally:
            conn.close()

    def commit(self, cid, repo, tag, pause, config=None, comment='', timeout=COMMIT_TIMEOUT_S):
        query = 'container=%s&repo=%s&tag=%s&pause=%s&comment=%s' % (
            _q(cid), _q(repo), _q(tag), '1' if pause else '0', _q(comment))
        reply = self._call('POST', '/commit?' + query, config, timeout=timeout)
        return reply['Id']

    def create(self, name, spec):
        return self._call('POST', '/containers/create?name=' + _q(name), spec)['Id']

    def put_archive(self, cid, path, data, timeout=60.0):
        self._call('PUT', '/containers/%s/archive?path=%s&noOverwriteDirNonDir=1' % (_q(cid), _q(path)),
                   raw=data, content_type='application/x-tar', timeout=timeout)

    def start(self, cid):
        self._call('POST', '/containers/' + _q(cid) + '/start', timeout=30.0)

    def stop(self, cid, grace=STOP_TIMEOUT_S):
        self._call('POST', '/containers/%s/stop?t=%d' % (_q(cid), int(grace)), timeout=grace + 20.0)

    def kill(self, cid):
        self._call('POST', '/containers/' + _q(cid) + '/kill', ok=(204, 304, 409), timeout=15.0)

    def wait(self, cid, timeout):
        reply = self._call('POST', '/containers/' + _q(cid) + '/wait', timeout=timeout)
        return int(reply.get('StatusCode', -1))

    def rename(self, cid, name):
        self._call('POST', '/containers/%s/rename?name=%s' % (_q(cid), _q(name)))

    def set_restart(self, cid, policy):
        self._call('POST', '/containers/' + _q(cid) + '/update', {'RestartPolicy': policy})

    def logs(self, cid, tail=60):
        raw = self._call('GET', '/containers/%s/logs?stdout=1&stderr=1&tail=%d' % (_q(cid), tail),
                         max_bytes=256 * 1024)
        if isinstance(raw, (bytes, bytearray)):
            return demux_logs(bytes(raw))
        return json.dumps(raw)


# --------------------------------------------------------------------------------------------------
# HTTP to the dashboard. Proxies from the environment are not used. A cookie jar keeps one dashboard
# session for all checks (each request without a cookie would open a new viewer session).
# --------------------------------------------------------------------------------------------------
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                      urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def http_get(url, timeout=HTTP_TIMEOUT_S, max_bytes=MAX_STATIC_BYTES):
    request = urllib.request.Request(url, headers={'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache'})
    try:
        with _OPENER.open(request, timeout=timeout) as reply:
            body = reply.read(max_bytes + 1)
            need(len(body) <= max_bytes, 'http_reply_too_large', url)
            return reply.status, body
    except urllib.error.HTTPError as error:
        return error.code, error.read(65536)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        raise KitError('http_unreachable', '%s (%s)' % (url, type(error).__name__))


def get_json(http, url, timeout=HTTP_TIMEOUT_S):
    status, body = http(url, timeout)
    need(status == 200, 'http_status', '%s -> %s' % (url, status))
    try:
        value = json.loads(body)
    except ValueError:
        raise KitError('http_not_json', url)
    need(isinstance(value, dict), 'http_not_object', url)
    return value


# --------------------------------------------------------------------------------------------------
# Bundle
# --------------------------------------------------------------------------------------------------
def _clean_rel(name):
    rel = posixpath.normpath(name)
    need(rel == name and not rel.startswith('/') and not rel.startswith('..') and rel not in ('', '.'),
         'bundle_bad_path', name)
    return rel


class Bundle:
    def __init__(self, kit_dir):
        self.dir = Path(kit_dir)
        manifest_raw = (self.dir / 'MANIFEST.json').read_bytes()
        self.manifest_sha256 = sha256(manifest_raw)
        manifest = json.loads(manifest_raw)
        need(manifest.get('schema') == MANIFEST_SCHEMA, 'bundle_schema', manifest.get('schema'))
        need(manifest.get('app_root') == APP_ROOT, 'bundle_app_root')
        self.manifest = manifest
        self.bundle_id = str(manifest['bundle_id'])
        need(re.fullmatch(r'[0-9a-f]{7,40}', self.bundle_id) is not None, 'bundle_id')
        payload_info = manifest['payload']
        payload = (self.dir / payload_info['name']).read_bytes()
        need(sha256(payload) == payload_info['sha256'], 'bundle_payload_sha256')
        self.files = {}
        for rel, meta in manifest['files'].items():
            self.files[_clean_rel(rel)] = dict(sha256=meta['sha256'], size=int(meta['size']), mode=int(meta['mode']))
        self.data = {}
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
            for member in archive:
                need(member.isfile(), 'bundle_member_not_file', member.name)
                rel = _clean_rel(member.name)
                need(rel in self.files and rel not in self.data, 'bundle_member_unexpected', rel)
                body = archive.extractfile(member).read()
                need(sha256(body) == self.files[rel]['sha256'] and len(body) == self.files[rel]['size'],
                     'bundle_member_sha256', rel)
                self.data[rel] = body
        need(set(self.data) == set(self.files), 'bundle_members_missing')
        self.remove = [_clean_rel(p) for p in manifest.get('remove', [])]
        need(not set(self.remove) & set(self.files), 'bundle_remove_overlap')
        self.roots = []
        for root in manifest['managed_roots']:
            need(root['kind'] in ('dir', 'file'), 'bundle_root_kind')
            self.roots.append((_clean_rel(root['path']), root['kind']))
        for rel in list(self.files) + self.remove:
            need(any(rel == r or (k == 'dir' and rel.startswith(r + '/')) for r, k in self.roots),
                 'bundle_path_outside_roots', rel)
        self.tool_mismatch = []
        for name, digest in manifest.get('tools', {}).items():
            path = self.dir / name
            if path.is_file() and sha256(path.read_bytes()) != digest:
                self.tool_mismatch.append(name)

    def in_dir_root(self, rel):
        return any(k == 'dir' and rel.startswith(r + '/') for r, k in self.roots)


def protected(rel):
    return rel in GENERATED_IN_IMAGE or '/__pycache__/' in '/' + rel or rel.endswith('.pyc')


def compute_plan(bundle, current, allow=()):
    changed, added, same = [], [], []
    for rel in sorted(bundle.files):
        cur = current.get(rel)
        if cur is None:
            added.append(rel)
        elif cur[0] != 'file' or cur[1] != bundle.files[rel]['sha256']:
            changed.append(rel)
        else:
            same.append(rel)
    remove = sorted(rel for rel in bundle.remove if rel in current and current[rel][0] != 'dir')
    stale = sorted(rel for rel, cur in current.items()
                   if cur[0] != 'dir' and rel not in bundle.files and rel not in remove
                   and bundle.in_dir_root(rel) and not protected(rel))
    blockers = []
    rebuild = dict(REBUILD_SOURCES)
    for rel in bundle.manifest.get('rebuild_sources') or []:
        rebuild.setdefault(rel, 'rebuild_source_changed:' + rel)
    for rel, code in sorted(rebuild.items()):
        if rel in bundle.files and rel in changed + added and code not in allow:
            blockers.append(code)
    warnings = []
    if 'dashboard_server/native_raster.cc' in bundle.files and 'dashboard_server/_native_raster.so' not in current:
        warnings.append('native_raster_library_missing_python_fallback')
    dirs = set(rel for rel, cur in current.items() if cur[0] == 'dir')
    new_dirs = set()
    for rel in added + changed:
        parent = posixpath.dirname(rel)
        while parent and parent not in dirs:
            new_dirs.add(parent)
            parent = posixpath.dirname(parent)
    return dict(changed=changed, added=added, unchanged_count=len(same), remove=remove, stale=stale,
                blockers=blockers, warnings=warnings, new_dirs=sorted(new_dirs))


def overlay_tar(bundle, paths, new_dirs, mtime):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for rel in sorted(new_dirs):
            info = tarfile.TarInfo(rel)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.mtime = mtime
            info.uname = info.gname = 'root'
            archive.addfile(info)
        for rel in sorted(paths):
            body = bundle.data[rel]
            info = tarfile.TarInfo(rel)
            info.size = len(body)
            info.mode = bundle.files[rel]['mode'] & 0o777 or 0o644
            info.mtime = mtime
            info.uname = info.gname = 'root'
            archive.addfile(info, io.BytesIO(body))
    return raw.getvalue()


# --------------------------------------------------------------------------------------------------
# Target container
# --------------------------------------------------------------------------------------------------
def _cmd_text(inspect):
    config = inspect.get('Config') or {}
    return ' '.join(str(x) for x in (config.get('Entrypoint') or []) + (config.get('Cmd') or []))


def env_value(inspect, key, default=None):
    for item in (inspect.get('Config') or {}).get('Env') or []:
        if item.startswith(key + '='):
            return item[len(key) + 1:]
    return default


def looks_like_dashboard(inspect):
    config = inspect.get('Config') or {}
    return config.get('WorkingDir') == APP_ROOT and 'dashboard_server' in _cmd_text(inspect)


def find_target(docker, name, explicit):
    x = docker.inspect(name)
    if x is not None:
        need(looks_like_dashboard(x), 'target_not_dashboard', name)
        return x
    need(not explicit, 'target_missing', name)
    found = []
    for row in docker.containers(False):
        x = docker.inspect(row['Id'])
        if x is None or not looks_like_dashboard(x):
            continue
        port = env_value(x, 'DASHBOARD_PORT', '8080')
        exposed = (x.get('Config') or {}).get('ExposedPorts') or {}
        if port == '8080' or '8080/tcp' in exposed:
            found.append(x)
    need(len(found) == 1, 'target_not_unique', '%d candidates' % len(found))
    return found[0]


def dashboard_url(inspect, override=None):
    if override:
        return override.rstrip('/')
    port = env_value(inspect, 'DASHBOARD_PORT', '8080')
    need(re.fullmatch(r'\d{1,5}', port or '') is not None, 'dashboard_port')
    host = inspect.get('HostConfig') or {}
    if host.get('NetworkMode') == 'host':
        return 'http://127.0.0.1:' + port
    bindings = (host.get('PortBindings') or {}).get(port + '/tcp') or []
    need(bindings and bindings[0].get('HostPort'), 'dashboard_port_unreachable')
    ip = bindings[0].get('HostIp') or '127.0.0.1'
    return 'http://%s:%s' % ('127.0.0.1' if ip in ('0.0.0.0', '::', '') else ip, bindings[0]['HostPort'])


def running_state(inspect):
    state = inspect.get('State') or {}
    return bool(state.get('Running')) and not state.get('Paused') and not state.get('Restarting')


def read_current(docker, cid, bundle):
    current = {}
    for root, kind in bundle.roots:
        tree = docker.read_tree(cid, APP_ROOT + '/' + root)
        if tree:
            current.update(tree)
    return current


SPEC_HOST_KEYS = ('Binds', 'Mounts', 'NetworkMode', 'IpcMode', 'PidMode', 'UTSMode', 'UsernsMode', 'RestartPolicy',
                  'Memory', 'NanoCpus', 'CpuShares', 'CpusetCpus', 'ReadonlyRootfs', 'SecurityOpt', 'CapAdd',
                  'CapDrop', 'Privileged', 'Devices', 'DeviceRequests', 'DeviceCgroupRules', 'PortBindings', 'Dns',
                  'ExtraHosts', 'GroupAdd', 'Runtime', 'LogConfig', 'ShmSize', 'Tmpfs', 'Ulimits', 'Init')
SPEC_CONFIG_KEYS = ('Env', 'Cmd', 'Entrypoint', 'User', 'WorkingDir', 'Healthcheck', 'ExposedPorts', 'Volumes',
                    'StopSignal', 'StopTimeout', 'Tty', 'OpenStdin')


def mounts_key(inspect):
    keys = ('Type', 'Source', 'Destination', 'Mode', 'RW', 'Propagation')
    return sorted(tuple(str(m.get(k)) for k in keys) for m in inspect.get('Mounts') or [])


def _norm(value):
    """Docker shows an unset list or map as null or as empty depending on how it was made."""
    return None if value in (None, [], {}, '') else value


def spec_diff(old, new):
    """Spec fields that differ (values are not reported, Env stays private)."""
    oh, nh = old.get('HostConfig') or {}, new.get('HostConfig') or {}
    oc, nc = old.get('Config') or {}, new.get('Config') or {}
    diff = [k for k in SPEC_HOST_KEYS if _norm(oh.get(k)) != _norm(nh.get(k))]
    diff += [k for k in SPEC_CONFIG_KEYS if _norm(oc.get(k)) != _norm(nc.get(k))]
    if mounts_key(old) != mounts_key(new):
        diff.append('Mounts(effective)')
    old_nets = sorted(((old.get('NetworkSettings') or {}).get('Networks') or {}).keys())
    new_nets = sorted(((new.get('NetworkSettings') or {}).get('Networks') or {}).keys())
    if old_nets != new_nets:
        diff.append('Networks')
    return diff


IMAGE_CONFIG_KEYS = ('Cmd', 'Entrypoint', 'User', 'WorkingDir', 'Env', 'Labels', 'Healthcheck', 'ExposedPorts',
                     'Volumes', 'StopSignal', 'OnBuild', 'Shell')


def commit_config(snapshot_config):
    """Body for committing the stage container.

    The commit API fills every empty field of the given config from the container that is
    committed. The stage container runs the remover as its entrypoint, so an unset entrypoint
    must be sent as an explicit empty list, or the new image would start the remover."""
    config = json.loads(json.dumps(snapshot_config or {}))
    if not config.get('Entrypoint'):
        config['Entrypoint'] = []
    return config


def image_config_diff(snapshot_config, new_config):
    return [k for k in IMAGE_CONFIG_KEYS
            if _norm((snapshot_config or {}).get(k)) != _norm((new_config or {}).get(k))]


def new_container_spec(old, image_id, labels):
    config = json.loads(json.dumps(old['Config']))
    config.pop('Hostname', None)
    config.pop('Domainname', None)
    config['Image'] = image_id
    config['Labels'] = dict(config.get('Labels') or {}, **labels)
    host = json.loads(json.dumps(old['HostConfig']))
    spec = dict(config, HostConfig=host)
    networks = (old.get('NetworkSettings') or {}).get('Networks') or {}
    if host.get('NetworkMode') not in ('host', 'none', 'default', 'bridge') and len(networks) > 1:
        raise KitError('target_multiple_networks_unsupported')
    return spec


# --------------------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------------------
def idle_check(http, base, *, recorder_unavailable_ok=False, bridge_status=None):
    """Read-only. Raises KitError('not_idle', ...) unless the car is safe to leave alone."""
    out = {}
    rover = get_json(http, base + '/api/rover/status')
    if rover.get('ok') is True:
        need(rover.get('armed') is False and rover.get('moving') is False, 'not_idle',
             'rover armed=%s moving=%s' % (rover.get('armed'), rover.get('moving')))
        out['rover'] = 'disarmed_stopped'
    elif rover.get('ok') is False and rover.get('connected') is False and rover.get('state') == 'unavailable':
        out['rover'] = 'bridge_unavailable_no_control_path'
    else:
        raise KitError('not_idle', 'rover status unknown')
    rec = get_json(http, base + '/api/recordings/status')
    if rec.get('state') == 'idle' and rec.get('active') is False and rec.get('finalizing') in (False, None):
        out['recorder'] = 'idle'
    elif recorder_unavailable_ok and rec.get('active') is not True and rec.get('state') in ('not_connected', 'unknown'):
        out['recorder'] = 'unavailable_allowed_by_option'
    else:
        raise KitError('not_idle', 'recorder state=%s active=%s finalizing=%s' % (
            rec.get('state'), rec.get('active'), rec.get('finalizing')))
    jobs = get_json(http, base + '/api/live/mapping-jobs')
    rows = jobs.get('jobs')
    need(isinstance(rows, list), 'not_idle', 'mapping jobs unknown')
    busy = [str(j.get('id')) for j in rows if not isinstance(j, dict) or j.get('active') is not False]
    need(not busy, 'not_idle', 'mapping job active: ' + ','.join(busy))
    out['mapping_jobs'] = 'idle(%d)' % len(rows)
    status, body = http(base + '/api/autonomy/supervisor', HTTP_TIMEOUT_S)
    if status == 200:
        try:
            sup = json.loads(body)
        except ValueError:
            sup = None
        if isinstance(sup, dict):
            need(sup.get('active') is not True and sup.get('motor_lease_acquired') is not True
                 and sup.get('motor_command_sent') is not True, 'not_idle', 'autonomy supervisor active')
            out['autonomy_supervisor'] = 'inactive'
    else:
        out['autonomy_supervisor'] = 'absent(%s)' % status
    if bridge_status is not None:
        try:
            value = json.loads(bridge_status)
        except ValueError:
            value = None
        if isinstance(value, dict):
            need(value.get('armed') is not True and value.get('moving') is not True, 'not_idle',
                 'rover bridge status file armed/moving')
            updated = value.get('updated_at')
            age = time.time() - updated if isinstance(updated, (int, float)) else None
            out['rover_bridge_file'] = 'disarmed_stopped(age_s=%s)' % (None if age is None else round(age, 1))
    else:
        out['rover_bridge_file'] = 'not_present'
    return out


def bridge_status_bytes(docker, inspect):
    path = env_value(inspect, 'ROVER_BRIDGE_STATUS_FILE', APP_ROOT + '/runtime/rover_bridge_status.json')
    if not path.startswith('/'):
        return None
    return docker.read_file(inspect['Id'], path)


def strip_versions(body):
    return VERSION_QUERY.sub(b'', body)


def check_static(http, base, bundle):
    """Every static file in the bundle must be served with the bundle's bytes (versions removed)."""
    checked = 0
    versioned = 0
    bad = []
    status, index = http(base + '/', HTTP_TIMEOUT_S)
    need(status == 200, 'static_index_status', status)
    expected_index = bundle.data.get('static/index.html')
    if expected_index is not None and strip_versions(index) != expected_index:
        bad.append('static/index.html(/)')
    versions = dict((m.group(1).decode(), m.group(2).decode())
                    for m in re.finditer(rb'/static/([^"\'?#\s]+)\?v=([0-9a-f]{10})', index))
    for rel in sorted(bundle.files):
        if not rel.startswith('static/'):
            continue
        path = rel[len('static/'):]
        url = base + '/static/' + urllib.parse.quote(path)
        if path in versions:
            url += '?v=' + versions[path]
        status, body = http(url, HTTP_TIMEOUT_S)
        if status != 200:
            bad.append('%s(status %s)' % (rel, status))
            continue
        if strip_versions(body) != bundle.data[rel]:
            bad.append(rel)
            continue
        if path in versions:
            versioned += 1
            if sha256(body)[:10] != versions[path]:
                bad.append(rel + '(version)')
                continue
        checked += 1
    need(not bad, 'static_mismatch', ', '.join(bad[:8]) + (' ...' if len(bad) > 8 else ''))
    return dict(files=checked, index_versioned_refs=len(versions), versioned_checked=versioned)


API_PROBES = ('/api/health', '/api/telemetry', '/api/rover/status', '/api/recordings/status', '/api/live/mapping-jobs')


def check_apis(http, base):
    out = {}
    for path in API_PROBES:
        status, body = http(base + path, 10.0)
        need(status == 200, 'api_probe', '%s -> %s' % (path, status))
        try:
            value = json.loads(body)
        except ValueError:
            raise KitError('api_probe', path + ' not JSON')
        need(isinstance(value, dict), 'api_probe', path + ' not object')
        out[path] = 'ok(%d B)' % len(body)
    return out


def wait_ready(docker, http, cid, image_id, base, deadline, timeout, sleep=time.sleep, need_docker_health=True):
    """Running with the expected image, /api/health ok and (if configured) Docker health 'healthy'."""
    limit = Deadline(min(timeout, max(1.0, deadline.remaining())), deadline.clock)
    health_ok = False
    last = 'starting'
    while limit.remaining() > 0:
        x = docker.inspect(cid)
        need(x is not None, 'ready_container_missing')
        need(x.get('Image') == image_id, 'ready_image_mismatch')
        state = x.get('State') or {}
        if not state.get('Running') and state.get('Status') in ('exited', 'dead'):
            raise KitError('ready_container_exited', 'exit %s' % state.get('ExitCode'))
        if state.get('Running') and not state.get('Restarting'):
            if not health_ok:
                try:
                    value = get_json(http, base + '/api/health', 3.0)
                    health_ok = value.get('ok') is True
                except KitError as error:
                    last = error.code
            if health_ok:
                docker_health = (state.get('Health') or {}).get('Status')
                if docker_health in (None, 'healthy') or not need_docker_health:
                    return dict(http_health=True, docker_health=docker_health or 'not_configured',
                                started_at=state.get('StartedAt'))
                if docker_health == 'unhealthy':
                    raise KitError('ready_docker_unhealthy')
                last = 'docker_health_' + str(docker_health)
        sleep(0.5)
    raise KitError('ready_timeout', last)


def snapshot_running(docker, x):
    return [(r['Id'], r.get('ImageID'), (docker.inspect(r['Id']) or {}).get('State', {}).get('StartedAt'))
            for r in docker.containers(False) if r['Id'] != x['Id']]


# --------------------------------------------------------------------------------------------------
# Receipt and state directory
# --------------------------------------------------------------------------------------------------
def default_state_dir():
    user = os.environ.get('SUDO_USER')
    home = Path(os.path.expanduser('~' + user)) if user and user != 'root' else Path.home()
    return home / 'ubts-releases' / 'dashboard-kit'


def _chown_like_user(path):
    uid, gid = os.environ.get('SUDO_UID'), os.environ.get('SUDO_GID')
    if hasattr(os, 'geteuid') and os.geteuid() == 0 and uid and gid:
        try:
            os.chown(path, int(uid), int(gid))
        except (OSError, ValueError):
            pass


def write_json(path, value):
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
    need(len(raw) <= 4 * 1024 * 1024, 'receipt_too_large')
    temp = path.with_name(path.name + '.tmp')
    temp.write_bytes(raw)
    os.replace(temp, path)
    _chown_like_user(path)


class StateLock:
    def __init__(self, directory):
        self.path = Path(directory) / '.kit.lock'
        self.handle = None

    def __enter__(self):
        self.handle = open(self.path, 'a+')
        try:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass
        except OSError:
            self.handle.close()
            raise KitError('another_kit_run_active', str(self.path))
        return self

    def __exit__(self, *exc):
        self.handle.close()


# --------------------------------------------------------------------------------------------------
# Restore (used by the automatic rollback and by rollback_dashboard.py)
# --------------------------------------------------------------------------------------------------
def restore_old(docker, http, *, name, old_id, old_image, old_policy, new_id, new_label, stamp, deadline, log,
                sleep=time.sleep, base_override=None):
    steps = []
    if new_id:
        x = docker.inspect(new_id)
        if x is not None:
            docker.set_restart(new_id, {'Name': 'no', 'MaximumRetryCount': 0})
            steps.append('new_restart_no')
            if (x.get('State') or {}).get('Running'):
                docker.stop(new_id)
                steps.append('new_stopped')
            if x.get('Name') == '/' + name:
                docker.rename(new_id, '%s-%s-%s' % (name, new_label, stamp))
                steps.append('new_renamed_' + new_label)
    old = docker.inspect(old_id)
    need(old is not None, 'restore_old_missing', old_id[:12])
    need(old.get('Image') == old_image, 'restore_old_image_changed')
    if old.get('Name') != '/' + name:
        holder = docker.inspect(name)
        need(holder is None, 'restore_name_taken', name)
        docker.rename(old_id, name)
        steps.append('old_renamed_back')
    docker.set_restart(old_id, old_policy)
    steps.append('old_restart_policy_restored')
    if not running_state(docker.inspect(old_id)):
        docker.start(old_id)
        steps.append('old_started')
    log('되돌림: 이전 컨테이너 %s 시작, 준비 확인 중' % old_id[:12])
    base = dashboard_url(old, base_override)
    ready = wait_ready(docker, http, old_id, old_image, base, deadline, min(90.0, deadline.remaining()),
                       sleep=sleep, need_docker_health=False)
    final = docker.inspect(old_id)
    need(final.get('Name') == '/' + name and running_state(final), 'restore_not_running')
    return dict(ok=True, steps=steps, ready=ready, old_id=old_id)


# --------------------------------------------------------------------------------------------------
# Installer
# --------------------------------------------------------------------------------------------------
class Installer:
    def __init__(self, docker, http, bundle, options, *, clock=time.monotonic, sleep=time.sleep, out=None):
        self.docker = docker
        self.http = http
        self.bundle = bundle
        self.opt = options
        self.clock = clock
        self.sleep = sleep
        self.out = out or sys.stdout
        self.receipt = None
        self.receipt_path = None
        self.events = []

    def log(self, text):
        line = '[%s] %s' % (datetime.datetime.now().strftime('%H:%M:%S'), text)
        self.events.append(line)
        print(line, file=self.out, flush=True)

    def allow(self):
        out = []
        if self.opt.allow_requirements_change:
            out.append('requirements_changed')
        if self.opt.allow_native_raster_change:
            out.append('native_raster_source_changed')
        return tuple(out)

    # ---- read-only part shared by dry-run and apply ---------------------------------------------
    def inspect_target(self):
        x = find_target(self.docker, self.opt.container or DEFAULT_NAME, bool(self.opt.container))
        need(running_state(x), 'target_not_running', x.get('Name'))
        return x

    def check(self):
        b = self.bundle
        x = self.inspect_target()
        name = x['Name'].lstrip('/')
        base = dashboard_url(x, self.opt.dashboard_url)
        self.log('대상 컨테이너 %s (%s), 이미지 %s, 주소 %s' % (name, x['Id'][:12], x['Image'][7:19], base))
        current = read_current(self.docker, x['Id'], b)
        plan = compute_plan(b, current, self.allow())
        idle = None
        idle_error = None
        try:
            idle = idle_check(self.http, base, recorder_unavailable_ok=self.opt.recorder_unavailable_ok,
                              bridge_status=bridge_status_bytes(self.docker, x))
        except KitError as error:
            idle_error = error
        return x, name, base, current, plan, idle, idle_error

    def report(self, x, plan, idle, idle_error):
        b = self.bundle
        p = print
        p('', file=self.out)
        p('묶음 %s (커밋 %s, 매니페스트 SHA-256 %s)' % (b.bundle_id, b.manifest.get('commit', '?')[:12],
                                                    b.manifest_sha256[:16]), file=self.out)
        p('묶음 파일 %d개 중 그대로 %d, 바뀜 %d, 새로 %d, 지울 것 %d, 묶음에 없는 옛 파일(그대로 둠) %d' % (
            len(b.files), plan['unchanged_count'], len(plan['changed']), len(plan['added']), len(plan['remove']),
            len(plan['stale'])), file=self.out)
        for title, key in (('바뀌는 파일', 'changed'), ('새 파일', 'added'), ('지우는 파일(레포에서 지운 것)', 'remove'),
                           ('그대로 두는 옛 파일', 'stale')):
            if plan[key]:
                p('- %s:' % title, file=self.out)
                for rel in plan[key]:
                    p('    ' + rel, file=self.out)
        for code in plan['blockers']:
            p('! 설치 불가: %s (이미지를 다시 빌드해야 하는 변경. README 참고)' % code, file=self.out)
        for code in plan['warnings']:
            p('! 알림: %s' % code, file=self.out)
        if b.tool_mismatch:
            p('! 알림: 설치 도구 파일이 매니페스트와 다름: %s' % ', '.join(b.tool_mismatch), file=self.out)
        if idle_error is not None:
            p('! 지금은 설치할 수 없음(차가 쉬는 상태가 아님): %s' % idle_error, file=self.out)
        else:
            p('쉬는 상태 확인: ' + ', '.join('%s=%s' % kv for kv in sorted(idle.items())), file=self.out)

    def dry_run(self):
        x, name, base, current, plan, idle, idle_error = self.check()
        self.report(x, plan, idle, idle_error)
        ok = not plan['blockers'] and idle_error is None
        nothing = not (plan['changed'] or plan['added'] or plan['remove'])
        if nothing:
            print('\n이미 이 묶음과 같습니다. 설치할 것이 없습니다.', file=self.out)
        elif ok:
            print('\n점검 통과. 설치하려면 같은 명령에 --apply 를 붙이세요.', file=self.out)
        return dict(ok=ok, nothing_to_do=nothing, plan=plan, idle=idle,
                    idle_error=None if idle_error is None else str(idle_error), target=name,
                    container_id=x['Id'], image_id=x['Image'])

    # ---- apply ---------------------------------------------------------------------------------------
    def save(self):
        if self.receipt_path is not None:
            self.receipt['events'] = self.events[-200:]
            write_json(self.receipt_path, self.receipt)

    def phase(self, name):
        self.receipt['phase'] = name
        self.receipt.setdefault('phase_times', {})[name] = utc_now()
        self.save()

    def apply(self, state_dir):
        b = self.bundle
        opt = self.opt
        state_dir = Path(state_dir)
        stamp = first = utc_stamp()
        n = 0
        while (state_dir / ('%s_%s' % (stamp, b.bundle_id[:12]))).exists():
            n += 1
            stamp = '%s-%d' % (first, n)
        run_dir = state_dir / ('%s_%s' % (stamp, b.bundle_id[:12]))
        run_dir.mkdir(parents=True, exist_ok=False)
        _chown_like_user(run_dir)
        self.receipt_path = run_dir / 'RECEIPT.json'
        (state_dir / 'LATEST_RECEIPT').write_text(str(self.receipt_path) + '\n', encoding='utf-8')
        _chown_like_user(state_dir / 'LATEST_RECEIPT')
        deadline = Deadline(opt.deadline, self.clock)
        r = self.receipt = dict(
            schema=RECEIPT_SCHEMA, result='running', ok=False, stamp=stamp, started_utc=utc_now(),
            bundle=dict(bundle_id=b.bundle_id, commit=b.manifest.get('commit'), manifest_sha256=b.manifest_sha256,
                        payload_sha256=b.manifest['payload']['sha256'], file_count=len(b.files)),
            host=dict(hostname=socket.gethostname(), machine=os.uname().machine if hasattr(os, 'uname') else None),
            compose_used=False, pull_used=False, prune_used=False, removal_used=False,
            motor_or_arm_command_sent=False, deadline_s=opt.deadline, rollback_reserve_s=opt.rollback_reserve)
        mutations = []
        ctx = {}
        self.log('설치 시작 (영수증 %s)' % self.receipt_path)

        def on_signal(signum, _frame):
            raise KitError('signal' if signum != getattr(signal, 'SIGALRM', -1) else 'deadline', str(signum))

        previous = {}
        for sig in ('SIGTERM', 'SIGINT', 'SIGHUP', 'SIGALRM'):
            if hasattr(signal, sig):
                previous[sig] = signal.signal(getattr(signal, sig), on_signal)
        if hasattr(signal, 'alarm'):
            signal.alarm(int(opt.deadline) + 5)
        try:
            self.phase('PRECHECK')
            x, name, base, current, plan, idle, idle_error = self.check()
            ctx.update(name=name, old_id=x['Id'], old_image=x['Image'],
                       old_policy=(x.get('HostConfig') or {}).get('RestartPolicy') or {'Name': 'no'},
                       old_started=x['State'].get('StartedAt'), base=base)
            r['target'] = dict(name=name, old_container_id=x['Id'], old_image_id=x['Image'],
                               old_started_at=ctx['old_started'], old_restart_policy=ctx['old_policy'],
                               network_mode=(x.get('HostConfig') or {}).get('NetworkMode'), dashboard_url=base)
            r['plan'] = dict(changed=[dict(path=p, before=current[p][1] if current.get(p, ('',))[0] == 'file' else None,
                                           after=b.files[p]['sha256']) for p in plan['changed']],
                             added=[dict(path=p, after=b.files[p]['sha256']) for p in plan['added']],
                             remove=[dict(path=p, before=current[p][1]) for p in plan['remove']],
                             stale_kept=plan['stale'], unchanged_count=plan['unchanged_count'],
                             blockers=plan['blockers'], warnings=plan['warnings'])
            self.report(x, plan, idle, idle_error)
            need(not plan['blockers'], 'blocked', ','.join(plan['blockers']))
            if idle_error is not None:
                raise idle_error
            r['idle_before'] = idle
            if not (plan['changed'] or plan['added'] or plan['remove']):
                r.update(result='nothing_to_do', ok=True)
                self.log('이미 같은 판입니다. 바꾼 것 없음.')
                return r
            unrelated_before = snapshot_running(self.docker, x)

            self.phase('SNAPSHOT')
            deadline.need(opt.ready_timeout + 120, 'snapshot')
            repo = opt.image_repo
            snap_tag = '%s-%s' % (stamp.lower(), b.bundle_id[:12])
            self.log('지금 컨테이너를 그대로 이미지로 보존 (docker commit, 잠깐 일시정지)')
            snapshot = self.docker.commit(x['Id'], repo + '/snapshot', snap_tag, True,
                                          comment='ubts dashboard kit snapshot of ' + x['Id'][:12],
                                          timeout=deadline.timeout(COMMIT_TIMEOUT_S))
            r['images'] = dict(old=x['Image'], snapshot=snapshot)
            after = self.docker.inspect(x['Id'])
            need(after is not None and after['Id'] == x['Id'] and running_state(after)
                 and after['State'].get('StartedAt') == ctx['old_started'] and after['Image'] == x['Image'],
                 'snapshot_changed_running_container')
            snap_image = self.docker.image(snapshot)
            need(snap_image is not None, 'snapshot_image_missing')
            self.save()

            self.phase('STAGE')
            deadline.need(opt.ready_timeout + 90, 'stage')
            stage_name = '%s-kit-stage-%s' % (name, stamp)
            # No User/Labels/WorkingDir here: the commit below would copy them into the new image.
            stage_spec = dict(Image=snapshot, Entrypoint=['python3', '-c', REMOVE_CODE],
                              Cmd=[APP_ROOT + '/' + p for p in plan['remove']], NetworkDisabled=True,
                              HostConfig=dict(NetworkMode='none', AutoRemove=False))
            stage = self.docker.create(stage_name, stage_spec)
            r.setdefault('containers', {})['stage'] = dict(id=stage, name=stage_name)
            if plan['remove']:
                self.log('지울 파일 %d개 제거 (네트워크 없는 준비 컨테이너)' % len(plan['remove']))
                self.docker.start(stage)
                code = self.docker.wait(stage, deadline.timeout(STAGE_TIMEOUT_S))
                need(code == 0, 'stage_remove_failed', 'exit %s' % code)
            paths = plan['changed'] + plan['added']
            self.log('바뀐/새 파일 %d개를 겹침' % len(paths))
            mtime = int(b.manifest.get('commit_time_epoch') or time.time())
            self.docker.put_archive(stage, APP_ROOT, overlay_tar(b, paths, plan['new_dirs'], mtime),
                                    timeout=deadline.timeout(60.0))
            release_tag = '%s-%s' % (b.bundle_id[:12], stamp.lower())
            new_image = self.docker.commit(stage, repo + '/release', release_tag, False,
                                           config=commit_config(snap_image.get('Config')),
                                           comment='ubts dashboard kit %s over %s' % (b.bundle_id[:12], x['Id'][:12]),
                                           timeout=deadline.timeout(COMMIT_TIMEOUT_S))
            r['images']['new'] = new_image
            r['images']['tags'] = dict(snapshot=repo + '/snapshot:' + snap_tag, new=repo + '/release:' + release_tag)
            new_info = self.docker.image(new_image)
            snap_layers = (snap_image.get('RootFS') or {}).get('Layers') or []
            new_layers = (new_info.get('RootFS') or {}).get('Layers') or []
            need(new_layers[:len(snap_layers)] == snap_layers and len(new_layers) == len(snap_layers) + 1,
                 'new_image_layers_not_snapshot_plus_one')
            config_diff = image_config_diff(snap_image.get('Config'), new_info.get('Config'))
            need(not config_diff, 'new_image_config_differs', ','.join(config_diff))
            self.save()

            self.phase('PREFLIGHT')
            deadline.need(opt.ready_timeout + 60, 'preflight')
            pre_name = '%s-kit-preflight-%s' % (name, stamp)
            pre_spec = dict(Image=new_image, Entrypoint=['python3', '-c', IMPORT_CODE], Cmd=list(PREFLIGHT_MODULES),
                            User='0:0', WorkingDir=APP_ROOT, Env=list((x.get('Config') or {}).get('Env') or []),
                            NetworkDisabled=True, Labels={'ubts.dashboard_kit.role': 'preflight'},
                            HostConfig=dict(NetworkMode='none', AutoRemove=False, CapDrop=['ALL'],
                                            SecurityOpt=['no-new-privileges']))
            pre = self.docker.create(pre_name, pre_spec)
            r['containers']['preflight'] = dict(id=pre, name=pre_name)
            self.docker.start(pre)
            try:
                code = self.docker.wait(pre, deadline.timeout(PREFLIGHT_TIMEOUT_S))
            except KitError:
                self.docker.kill(pre)
                raise
            logs = self.docker.logs(pre)
            if code != 0 or 'UBTS_KIT_IMPORT_OK' not in logs:
                r['preflight_log_tail'] = logs[-1500:]
                raise KitError('preflight_import_failed', 'exit %s' % code)
            r['preflight'] = dict(ok=True, modules=list(PREFLIGHT_MODULES))
            self.log('새 이미지에서 대시보드 모듈 import 확인')
            self.save()

            self.phase('CREATE')
            deadline.need(opt.ready_timeout + 45, 'create')
            temp_name = '%s-kit-new-%s' % (name, stamp)
            labels = {'ubts.dashboard_kit.bundle': b.bundle_id, 'ubts.dashboard_kit.replaces': x['Id'],
                      'ubts.dashboard_kit.stamp': stamp}
            new_id = self.docker.create(temp_name, new_container_spec(x, new_image, labels))
            ctx['new_id'] = new_id
            r['containers']['new'] = dict(id=new_id, created_name=temp_name)
            mutations.append('new_created')
            new_inspect = self.docker.inspect(new_id)
            diff = spec_diff(x, new_inspect)
            need(not diff, 'new_spec_differs', ','.join(diff))
            installed = read_current(self.docker, new_id, b)
            wrong = [p for p in b.files if installed.get(p, ('',))[0] != 'file' or installed[p][1] != b.files[p]['sha256']]
            need(not wrong, 'new_files_mismatch', ', '.join(wrong[:8]))
            left = [p for p in plan['remove'] if p in installed]
            need(not left, 'new_removed_files_present', ', '.join(left[:8]))
            lost = [p for p in plan['stale'] if installed.get(p) != current.get(p)]
            need(not lost, 'new_kept_files_changed', ', '.join(lost[:8]))
            r['new_files_verified'] = dict(bundle_files=len(b.files), removed_absent=len(plan['remove']),
                                           stale_kept=len(plan['stale']))
            self.log('새 컨테이너 파일 %d개가 묶음과 같음(SHA-256)' % len(b.files))
            self.save()

            self.phase('SWITCH')
            deadline.need(opt.ready_timeout + 30, 'switch')
            idle_now = idle_check(self.http, base, recorder_unavailable_ok=opt.recorder_unavailable_ok,
                                  bridge_status=bridge_status_bytes(self.docker, x))
            r['idle_before_switch'] = idle_now
            again = self.docker.inspect(x['Id'])
            need(again is not None and running_state(again) and again['State'].get('StartedAt') == ctx['old_started']
                 and again['Image'] == x['Image'], 'old_changed_before_switch')
            kept_name = '%s-kept-%s' % (name, stamp)
            self.log('이전 컨테이너 정지·이름 변경(%s) 후 새 컨테이너 시작' % kept_name)
            mutations.append('old_policy')
            self.docker.set_restart(x['Id'], {'Name': 'no', 'MaximumRetryCount': 0})
            mutations.append('old_stop')
            self.docker.stop(x['Id'], STOP_TIMEOUT_S)
            mutations.append('old_rename')
            self.docker.rename(x['Id'], kept_name)
            mutations.append('new_rename')
            self.docker.rename(new_id, name)
            mutations.append('new_start')
            self.docker.start(new_id)
            r['containers']['old'] = dict(id=x['Id'], kept_name=kept_name, restart_policy_now={'Name': 'no'},
                                          original_restart_policy=ctx['old_policy'])
            self.save()

            self.phase('READY')
            ready = wait_ready(self.docker, self.http, new_id, new_image, base, deadline,
                               min(opt.ready_timeout, max(1.0, deadline.remaining() - 10.0)),
                               sleep=self.sleep)
            r['ready'] = ready
            self.log('새 대시보드 응답 확인, 정적 파일 대조 중')
            r['static_check'] = check_static(self.http, base, b)
            r['api_check'] = check_apis(self.http, base)
            new_now = self.docker.inspect(new_id)
            r['idle_after'] = idle_check(self.http, base, recorder_unavailable_ok=opt.recorder_unavailable_ok,
                                         bridge_status=bridge_status_bytes(self.docker, new_now))
            unrelated_after = snapshot_running(self.docker, new_now)
            before = dict((cid, (img, started)) for cid, img, started in unrelated_before)
            after_map = dict((cid, (img, started)) for cid, img, started in unrelated_after)
            changed_others = sorted(cid[:12] for cid in before if after_map.get(cid) != before[cid])
            r['unrelated_containers'] = dict(checked=len(before), changed=changed_others)
            if changed_others:
                self.log('알림: 다른 컨테이너 상태가 바뀜(설치기는 건드리지 않음): ' + ', '.join(changed_others))
            final = self.docker.inspect(new_id)
            need(final['Name'] == '/' + name and running_state(final) and final['Image'] == new_image,
                 'final_state')
            r['containers']['new'].update(name=name, started_at=final['State'].get('StartedAt'))
            r.update(result='installed', ok=True, finished_utc=utc_now())
            self.log('설치 완료. 이전 컨테이너는 %s 로 보존(멈춤, 재시작 정책 no)' % kept_name)
            return r
        except BaseException as error:  # noqa: BLE001 - every failure must reach the rollback below
            if hasattr(signal, 'alarm'):
                signal.alarm(0)
                signal.alarm(int(opt.rollback_reserve) + 5)
            r['error'] = dict(code=getattr(error, 'code', type(error).__name__), detail=str(error)[:400])
            r['result'] = 'aborted_no_change'
            self.log('중단: %s' % error)
            if any(m in mutations for m in ('old_policy', 'old_stop', 'old_rename', 'new_rename', 'new_start')):
                r['result'] = 'failed_rolling_back'
                self.save()
                rollback_deadline = Deadline(opt.rollback_reserve, self.clock)
                try:
                    r['rollback'] = restore_old(
                        self.docker, self.http, name=ctx['name'], old_id=ctx['old_id'], old_image=ctx['old_image'],
                        old_policy=ctx['old_policy'], new_id=ctx.get('new_id'), new_label='failed', stamp=stamp,
                        deadline=rollback_deadline, log=self.log, sleep=self.sleep,
                        base_override=self.opt.dashboard_url)
                    r['result'] = 'failed_rolled_back'
                    self.log('자동 되돌림 완료: 이전 컨테이너가 다시 돌고 있음')
                except BaseException as recovery:  # noqa: BLE001
                    r['rollback'] = dict(ok=False, error=getattr(recovery, 'code', type(recovery).__name__),
                                         detail=str(recovery)[:400])
                    r['result'] = 'failed_rollback_failed'
                    self.log('!! 자동 되돌림 실패: %s. README 의 "되돌림이 실패했을 때"를 보세요.' % recovery)
            elif 'new_created' in mutations:
                r['result'] = 'aborted_old_untouched'
                try:
                    self.docker.rename(ctx['new_id'], '%s-failed-%s' % (ctx['name'], stamp))
                except KitError:
                    pass
            r['finished_utc'] = utc_now()
            return r
        finally:
            if hasattr(signal, 'alarm'):
                signal.alarm(0)
            for sig, handler in previous.items():
                signal.signal(getattr(signal, sig), handler)
            self.save()


def build_parser():
    p = argparse.ArgumentParser(description='UBTS 대시보드 설치기. 기본은 점검만 한다.')
    p.add_argument('--apply', action='store_true', help='실제로 설치한다(없으면 점검만)')
    p.add_argument('--kit-dir', default=str(Path(__file__).resolve().parent), help='묶음 폴더(MANIFEST.json 위치)')
    p.add_argument('--container', default=None, help='대상 컨테이너 이름 또는 ID (기본: %s, 없으면 /app 과 8080 으로 찾음)' % DEFAULT_NAME)
    p.add_argument('--state-dir', default=None, help='영수증 폴더 (기본: ~/ubts-releases/dashboard-kit)')
    p.add_argument('--docker-socket', default=DEFAULT_SOCKET)
    p.add_argument('--dashboard-url', default=None, help='대시보드 주소를 직접 줄 때(보통 필요 없음)')
    p.add_argument('--image-repo', default=DEFAULT_IMAGE_REPO, help='새로 만드는 이미지 이름 앞부분')
    p.add_argument('--deadline', type=float, default=INSTALL_DEADLINE_S, help='설치 전체 시간 한도(초)')
    p.add_argument('--rollback-reserve', type=float, default=ROLLBACK_RESERVE_S, help='되돌림용 시간(초)')
    p.add_argument('--ready-timeout', type=float, default=READY_TIMEOUT_S, help='새 대시보드 준비 대기(초)')
    p.add_argument('--recorder-unavailable-ok', action='store_true', help='녹화기 상태를 못 읽어도 진행(녹화기가 꺼져 있을 때만)')
    p.add_argument('--allow-requirements-change', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--allow-native-raster-change', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--json', action='store_true', help='끝에 결과 JSON 을 출력')
    return p


def validate_options(opt):
    need(60.0 <= opt.deadline <= 1800.0, 'option_deadline_range')
    need(30.0 <= opt.rollback_reserve <= 600.0, 'option_rollback_reserve_range')
    need(1.0 <= opt.ready_timeout <= 600.0, 'option_ready_timeout_range')
    need(re.fullmatch(r'[a-z0-9]+(?:[._-][a-z0-9]+)*', opt.image_repo) is not None, 'option_image_repo')


def main(argv=None, *, docker=None, http=None, clock=time.monotonic, sleep=time.sleep, out=None):
    opt = build_parser().parse_args(argv)
    out = out or sys.stdout
    try:
        validate_options(opt)
        bundle = Bundle(opt.kit_dir)
        docker = docker or DockerAPI(opt.docker_socket)
        http = http or http_get
        installer = Installer(docker, http, bundle, opt, clock=clock, sleep=sleep, out=out)
        if not opt.apply:
            result = installer.dry_run()
            if opt.json:
                print(json.dumps(result, ensure_ascii=False, indent=1), file=out)
            return 0 if result['ok'] else 2
        state_dir = Path(opt.state_dir) if opt.state_dir else default_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        _chown_like_user(state_dir)
        with StateLock(state_dir):
            result = installer.apply(state_dir)
    except KitError as error:
        print('실패: %s' % error, file=out)
        return 2
    if opt.json:
        print(json.dumps(result, ensure_ascii=False, indent=1), file=out)
    print('결과: %s (영수증 %s)' % (result['result'], installer.receipt_path), file=out)
    if result['result'] in ('installed', 'nothing_to_do'):
        return 0
    if result['result'] == 'failed_rollback_failed':
        return 3
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
