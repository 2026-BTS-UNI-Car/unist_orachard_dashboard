"""Autonomy resource priority signal (S06, plan section 7).

While autonomy runs (the field row executor container, or the in-dashboard
native autonomy supervisor) the dashboard must not take CPU or link capacity
from it. This module only decides the state; it never sends rover commands and
never changes manual drive, STOP, lease or deadman behaviour.

States
  ACTIVE   positive evidence (supervisor active, executor container running,
           or the test override file). Entered immediately.
  IDLE     negative evidence for idle_hold_s (15 s) after the last positive one.
  UNKNOWN  the Docker check failed twice in a row (and no positive evidence),
           or ACTIVE ended without a successful negative check.

Policy by layer (plan 7.3)
  background work (media, recorded mapping, map windows, docker stats):
      yields while ACTIVE or UNKNOWN
  previews (camera, LiDAR): strong cap while ACTIVE, medium cap while UNKNOWN,
      except for the session that holds the manual-drive lease
  dashboard live mapping: stopped (map kept) and start refused while ACTIVE;
      start refused while UNKNOWN
  manual drive / STOP / lease / deadman: never changed
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

ACTIVE, IDLE, UNKNOWN, DISABLED = 'active', 'idle', 'unknown', 'disabled'
PROFILE_ORDER = ('tiny', 'slow', 'balanced', 'detail')


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__('localhost', timeout=timeout)
        self._path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def docker_running_containers(socket_path: str, timeout_s: float = 0.5) -> list[dict[str, Any]]:
    """GET /containers/json for running containers with a hard socket timeout."""
    filters = quote(json.dumps({'status': ['running']}, separators=(',', ':')))
    connection = _UnixHTTPConnection(socket_path, timeout_s)
    try:
        connection.request('GET', f'/containers/json?filters={filters}', headers={'Host': 'docker'})
        response = connection.getresponse()
        body = response.read(1024 * 1024)
        if response.status != 200:
            raise OSError(f'docker_status_{response.status}')
        rows = json.loads(body)
        if not isinstance(rows, list):
            raise ValueError('docker_reply_not_list')
        return rows
    finally:
        connection.close()


class AutonomyPriority:
    def __init__(self, *, supervisor_status: Callable[[], dict] | None = None,
                 docker_socket: str | None = '/var/run/docker.sock',
                 container_names: tuple[str, ...] = ('row-executor',), label: str = 'ubts.autonomy=1',
                 override_file: str | None = None, clock: Callable[[], float] = time.monotonic,
                 idle_hold_s: float = 15.0, period_s: float = 2.0, timeout_s: float = 0.5,
                 docker_query: Callable[[str, float], list] | None = None, start: bool = True):
        self.supervisor_status = supervisor_status
        self.docker_socket = docker_socket
        self.container_names = tuple(n.strip().lstrip('/') for n in container_names if n.strip())
        key, _, value = label.partition('=')
        self.label = (key.strip(), value.strip()) if key.strip() else None
        self.override_file = override_file
        self.clock = clock
        self.idle_hold_s = float(idle_hold_s)
        self.period_s = float(period_s)
        self.timeout_s = float(timeout_s)
        self.docker_query = docker_query or docker_running_containers
        self.state = UNKNOWN
        self.since = clock()
        self.updated = None
        self.last_positive = None
        self.docker_failures = 0
        self.sources: dict[str, Any] = {}
        self._snapshot = self._make_snapshot()
        self._listeners: list[Callable[[str, str], None]] = []
        self._stop = threading.Event()
        self._thread = None
        self.evaluate()
        if start:
            self._thread = threading.Thread(target=self._run, daemon=True, name='autonomy-priority')
            self._thread.start()

    @classmethod
    def from_env(cls, supervisor_status=None, start=True):
        """Production factory. Disabled (never yields) when neither the Docker
        socket nor a test override file exists, e.g. developer machines and the
        unit tests that construct the server."""
        mode = os.environ.get('UBTS_AUTONOMY_PRIORITY', 'auto').strip().lower()
        override = os.environ.get('UBTS_AUTONOMY_PRIORITY_OVERRIDE_FILE') or None
        socket_path = os.environ.get('DOCKER_SOCKET', '/var/run/docker.sock')
        if mode == 'off' or (mode == 'auto' and not override and not Path(socket_path).exists()):
            return DisabledPriority()
        names = tuple(os.environ.get('UBTS_AUTONOMY_CONTAINERS', 'row-executor').split(','))
        return cls(supervisor_status=supervisor_status, docker_socket=socket_path, container_names=names,
                   label=os.environ.get('UBTS_AUTONOMY_LABEL', 'ubts.autonomy=1'), override_file=override,
                   idle_hold_s=float(os.environ.get('UBTS_AUTONOMY_IDLE_HOLD_S', '15')), start=start)

    # -- evaluation -----------------------------------------------------
    def _override(self):
        if not self.override_file:
            return None
        try:
            value = Path(self.override_file).read_text(encoding='utf-8').strip().lower()
        except OSError:
            return None
        return value if value in (ACTIVE, IDLE, UNKNOWN) else None

    def _supervisor_active(self):
        if self.supervisor_status is None:
            return None
        try:
            status = self.supervisor_status() or {}
        except Exception:
            return None
        return bool(status.get('active') or status.get('loop_cleanup_pending'))

    def _docker(self):
        if not self.docker_socket:
            return dict(ok=False, error='docker_socket_not_configured')
        try:
            rows = self.docker_query(self.docker_socket, self.timeout_s)
        except Exception as error:  # OSError, timeout, JSON errors
            return dict(ok=False, error=str(error)[:120] or type(error).__name__)
        matched = []
        for row in rows:
            names = [str(n).lstrip('/') for n in (row.get('Names') or [])]
            labels = row.get('Labels') or {}
            by_name = any(n in self.container_names for n in names)
            by_label = bool(self.label) and str(labels.get(self.label[0], '')) == self.label[1]
            if by_name or by_label:
                matched.append(names[0] if names else str(row.get('Id', ''))[:12])
        return dict(ok=True, running=matched)

    def evaluate(self):
        now = self.clock()
        override = self._override()
        supervisor = self._supervisor_active()
        docker = self._docker() if override is None else dict(ok=None, skipped='override')
        previous = self.state
        if override is not None:
            state = override
            if state == ACTIVE:
                self.last_positive = now
        else:
            positive = bool(supervisor) or bool(docker.get('ok') and docker.get('running'))
            if docker.get('ok') is False:
                self.docker_failures += 1
            elif docker.get('ok'):
                self.docker_failures = 0
            if positive:
                state = ACTIVE
                self.last_positive = now
            else:
                held = self.last_positive is not None and now - self.last_positive < self.idle_hold_s
                negative = docker.get('ok') is True
                if held:
                    state = ACTIVE
                elif negative:
                    state = IDLE
                elif self.docker_failures >= 2 or previous == ACTIVE:
                    state = UNKNOWN
                else:
                    state = previous
        if state != previous:
            self.since = now
        self.state = state
        self.updated = now
        self.sources = dict(supervisor=supervisor, docker=docker, override=override,
                            docker_failures=self.docker_failures)
        self._snapshot = self._make_snapshot()
        if state != previous:
            for listener in list(self._listeners):
                try:
                    listener(previous, state)
                except Exception:
                    pass
        return self._snapshot

    def _make_snapshot(self):
        now = self.clock()
        return dict(state=self.state, background_yield=self.state in (ACTIVE, UNKNOWN),
                    preview_cap=self.state if self.state in (ACTIVE, UNKNOWN) else None,
                    since_s=round(max(0.0, now - self.since), 1),
                    age_s=None if self.updated is None else round(max(0.0, now - self.updated), 1),
                    sources=dict(self.sources), idle_hold_s=self.idle_hold_s,
                    executor_page='http://10.77.0.1:8097/', control_path_changed=False)

    def _run(self):
        while not self._stop.wait(self.period_s):
            self.evaluate()

    # -- public ---------------------------------------------------------
    def snapshot(self):
        value = dict(self._snapshot)
        if self.updated is not None:
            value['age_s'] = round(max(0.0, self.clock() - self.updated), 1)
        return value

    def background_yield(self) -> bool:
        return self.state in (ACTIVE, UNKNOWN)

    def add_listener(self, listener: Callable[[str, str], None]):
        self._listeners.append(listener)

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)


class DisabledPriority:
    """Used when autonomy cannot be detected here (no Docker socket, no override)."""
    state = DISABLED

    def snapshot(self):
        return dict(state=DISABLED, background_yield=False, preview_cap=None, since_s=None, age_s=None,
                    sources={}, control_path_changed=False)

    def background_yield(self):
        return False

    def add_listener(self, listener):
        pass

    def evaluate(self):
        return self.snapshot()

    def close(self):
        pass


# -- preview caps (applied by the dashboard WebSocket proxy) -------------
def _cap_profile(value, limit):
    value = (value or 'auto').strip().lower()
    if value not in PROFILE_ORDER:
        # 'auto' (or unknown) cannot exceed the cap: choose the cap itself.
        return limit
    return value if PROFILE_ORDER.index(value) <= PROFILE_ORDER.index(limit) else limit


def _int(value, default):
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def capped_stream_query(stream: str, state: str, query: dict[str, str]) -> dict[str, str]:
    """Return the upstream query for a preview stream under an autonomy state.

    query maps parameter -> single value (as parsed from the browser request).
    IMU/GPS and IDLE/DISABLED states are returned unchanged."""
    result = dict(query)
    if state not in (ACTIVE, UNKNOWN) or stream not in ('camera', 'lidar'):
        return result
    if stream == 'camera':
        if state == ACTIVE:
            result['profile'] = _cap_profile(result.get('profile'), 'slow')
            result['fps'] = str(min(10, max(1, _int(result.get('fps'), 30))))
            width, height = 1280, 720
            try:
                width, height = (int(v) for v in str(result.get('resolution', '1280x720')).lower().split('x', 1))
            except ValueError:
                pass
            if width * height > 640 * 360:
                result['resolution'] = '640x360' if width * 9 >= height * 16 - 1 else '320x240'
        else:
            if (result.get('profile') or 'auto').lower() != 'auto':
                result['profile'] = _cap_profile(result.get('profile'), 'balanced')
            result['fps'] = str(min(15, max(1, _int(result.get('fps'), 30))))
    else:
        if state == ACTIVE:
            result['profile'] = _cap_profile(result.get('profile'), 'slow')
            result['max_points'] = str(min(300, max(50, _int(result.get('max_points'), 900))))
        elif (result.get('profile') or 'auto').lower() != 'auto':
            result['profile'] = _cap_profile(result.get('profile'), 'balanced')
    return result
