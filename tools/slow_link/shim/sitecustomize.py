"""Process setup for the slow-link bench. Loaded through PYTHONPATH; never part of an installation.

What it does, and only when the bench asks for it through SLOW_LINK_* variables:

* Windows: CPython has no socket.AF_UNIX constant, and window-less processes get
  coarse timers. The constant is added and 1 ms timers are requested.
* SLOW_LINK_CONTROL_PORT: dashboard_server.rover_api talks to the rover control
  bridge over a Unix socket. On the bench that one module is given a socket
  factory that connects to the recording control endpoint on 127.0.0.1 instead.
  The request function, its payloads and its 0.6 s limit are untouched.
* SLOW_LINK_MAP_PUBLISH_MARK=1: each published live-map snapshot gets the wall
  time of publication ("_slow_link_published_at"), so the page instrumentation
  can tell "sensor -> published" from "published -> drawn".
* SLOW_LINK_STALL_PROBE=<file>: a thread asks to wake every 20 ms and writes how
  late it ran, once per second (time the process could not run Python code).
"""
import importlib.abc
import importlib.machinery
import json
import os
import socket
import sys
import threading
import time

if not hasattr(socket, "AF_UNIX"):
    socket.AF_UNIX = 1

if os.name == "nt":
    try:
        import ctypes
        from ctypes import wintypes

        class _ThrottlingState(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        _kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        _kernel32.SetProcessInformation.restype = wintypes.BOOL
        _state = _ThrottlingState(1, 0x1 | 0x4, 0)  # execution-speed and timer-resolution throttling: both off
        _kernel32.SetProcessInformation(_kernel32.GetCurrentProcess(), 4, ctypes.byref(_state), ctypes.sizeof(_state))
        ctypes.WinDLL("winmm").timeBeginPeriod(1)
    except Exception:
        pass


class _LoopbackSocket:
    """Stands where rover_api expects a Unix stream socket; connects to the bench endpoint on 127.0.0.1."""

    def __init__(self, family, kind, port, expected_path):
        if family != getattr(socket, "AF_UNIX", 1) or kind != socket.SOCK_STREAM:
            raise OSError("bench control transport: unexpected socket family")
        self.port, self.expected_path = port, expected_path
        self.timeout = None
        self.client = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.client is not None:
            self.client.close()

    def settimeout(self, timeout):
        self.timeout = timeout
        if self.client is not None:
            self.client.settimeout(timeout)

    def connect(self, path):
        if str(path) != self.expected_path:
            raise OSError("bench control transport: unexpected socket path")
        self.client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.client.settimeout(self.timeout)
        self.client.connect(("127.0.0.1", self.port))

    def sendall(self, data):
        self.client.sendall(data)

    def recv(self, count):
        return self.client.recv(count)


class _LoopbackSocketModule:
    AF_UNIX = getattr(socket, "AF_UNIX", 1)
    SOCK_STREAM = socket.SOCK_STREAM

    def __init__(self, port, expected_path):
        self.port, self.expected_path = port, expected_path

    def socket(self, family, kind):
        return _LoopbackSocket(family, kind, self.port, self.expected_path)


def _mark_map_publication(module):
    cls = getattr(module, "SourceLiveMapping", None)
    if cls is None or getattr(cls._publish, "__slow_link__", False):
        return
    original = cls._publish

    def _publish(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        now = time.time()
        for name in ("cell_snapshot", "snapshot"):
            data = getattr(self, name, None)
            if isinstance(data, dict):
                data["_slow_link_published_at"] = now
        return result
    _publish.__slow_link__ = True
    cls._publish = _publish


class _Finder(importlib.abc.MetaPathFinder):
    def __init__(self, patches):
        self.patches = patches

    def find_spec(self, fullname, path, target=None):
        patch = self.patches.get(fullname)
        if patch is None:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return spec
        execute = spec.loader.exec_module

        def exec_module(module):
            execute(module)
            patch(module)
        spec.loader.exec_module = exec_module
        return spec


def _stall_probe(path):
    period = 0.02
    window_start = time.monotonic()
    worst, over100, over500, count = 0.0, 0, 0, 0
    with open(path, "a", encoding="utf-8", newline="\n") as out:
        while True:
            before = time.monotonic()
            time.sleep(period)
            late = time.monotonic() - before - period
            count += 1
            worst = max(worst, late)
            over100 += late > 0.1
            over500 += late > 0.5
            if time.monotonic() - window_start >= 1.0:
                out.write(json.dumps(dict(t=round(time.time(), 3), max_ms=round(worst * 1000, 1), over100=over100, over500=over500, n=count)) + "\n")
                out.flush()
                window_start = time.monotonic()
                worst, over100, over500, count = 0.0, 0, 0, 0


def _install():
    patches = {}
    port = os.environ.get("SLOW_LINK_CONTROL_PORT")
    if port:
        expected_path = os.environ["ROVER_CONTROL_SOCKET"]
        factory = _LoopbackSocketModule(int(port), expected_path)

        def patch_rover_api(module):
            module.socket = factory
        patches["dashboard_server.rover_api"] = patch_rover_api
    if os.environ.get("SLOW_LINK_MAP_PUBLISH_MARK") == "1":
        patches["dashboard_server.source_live_mapping"] = _mark_map_publication
    if patches:
        sys.meta_path.insert(0, _Finder(patches))
    probe = os.environ.get("SLOW_LINK_STALL_PROBE")
    if probe:
        threading.Thread(target=_stall_probe, args=(probe,), daemon=True, name="slow-link-stall-probe").start()


_install()
