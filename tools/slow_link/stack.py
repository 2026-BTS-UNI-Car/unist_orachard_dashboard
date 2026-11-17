#!/usr/bin/env python3
"""Start and stop the local bench stack: recording control endpoint, replayed bridges,
the dashboard under test, the link emulator and a CPU sampler.

Everything binds to 127.0.0.1. The dashboard runs from an export of one commit
in a scratch folder, never from the repository work tree.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import link_emulator

TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_PORTS = dict(dashboard=28080, lidar=28765, camera=28770, imu=28775, gps=28778, recorder=28790, control=28795, link=29100)
CHROME_PROFILE_PREFIX = "slow-link-chrome-"
# Variables of this shell that must not leak into the stack (devices, the real control socket, other instances).
STRIP_ENV_PREFIXES = ("ROVER_", "ESP_", "LIDAR_", "CAMERA_", "IMU_", "GPS_", "DASHBOARD_", "UBTS_", "SLOW_LINK_", "RECORD", "OFFLINE_MAP", "PYTHON")


def shifted_ports(offset=0):
    return {name: port + int(offset) for name, port in DEFAULT_PORTS.items()}


def port_is_open(port, timeout=0.3):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        return probe.connect_ex(("127.0.0.1", port)) == 0


class Sampler(threading.Thread):
    """Once per second: CPU (% of one core) and memory of the stack processes and of the bench Chrome."""

    def __init__(self, procs, path):
        super().__init__(daemon=True)
        import psutil
        self.psutil = psutil
        self.path, self.stop = Path(path), threading.Event()
        self.handles, self._chrome, self._chrome_looked = {}, None, 0.0
        for name, process in procs.items():
            handle = psutil.Process(process.pid)
            # A virtual-environment python.exe on Windows is a launcher; measure the interpreter it starts.
            kids = [kid for kid in handle.children() if (kid.name() or "").lower().startswith("python")]
            self.handles[name] = kids[0] if kids else handle

    # Reading every process's command line can take seconds on a busy Windows PC; look for new Chrome processes this often.
    CHROME_LOOKUP_S = 5.0

    def chrome(self):
        now = time.monotonic()
        if self._chrome is None or now - self._chrome_looked >= self.CHROME_LOOKUP_S:
            self._chrome, self._chrome_looked = self.find_chrome(), now
        return self._chrome

    def find_chrome(self):
        psutil, groups = self.psutil, {}
        for process in psutil.process_iter(["name", "cmdline"]):
            try:
                if not (process.info["name"] or "").lower().startswith("chrome"):
                    continue
                command = process.info["cmdline"] or []
                joined = " ".join(command)
                if CHROME_PROFILE_PREFIX not in joined:
                    continue
                role = "browser"
                for part in command:
                    if part.startswith("--type="):
                        role = part[7:]
                        if role == "utility" and "network" in joined:
                            role = "network"
                groups.setdefault(role, []).append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return groups

    def run(self):
        psutil = self.psutil
        for handle in self.handles.values():
            try:
                handle.cpu_percent(None)
            except psutil.Error:
                pass
        chrome_last = {}
        with self.path.open("a", encoding="utf-8", newline="\n") as out:
            while not self.stop.wait(1.0):
                began = time.monotonic()
                row = dict(t=round(time.time(), 3), procs={}, chrome={}, host_cpu_pct=psutil.cpu_percent(None), ncpu=psutil.cpu_count(),
                           host_mem_avail_gb=round(psutil.virtual_memory().available / 2**30, 2))
                for name, handle in self.handles.items():
                    try:
                        with handle.oneshot():
                            row["procs"][name] = dict(cpu_pct=round(handle.cpu_percent(None), 1),
                                                      rss_mb=round(handle.memory_info().rss / 1048576, 1), threads=handle.num_threads())
                    except psutil.Error:
                        row["procs"][name] = None
                now = time.time()
                for role, processes in self.chrome().items():
                    total, rss = 0.0, 0.0
                    for process in processes:
                        try:
                            times = process.cpu_times()
                            cpu = times.user + times.system
                            previous = chrome_last.get(process.pid)
                            chrome_last[process.pid] = (cpu, now)
                            if previous:
                                total += 100 * (cpu - previous[0]) / max(1e-3, now - previous[1])
                            rss += process.memory_info().rss / 1048576
                        except psutil.Error:
                            pass
                    row["chrome"][role] = dict(cpu_pct=round(total, 1), rss_mb=round(rss, 1), n=len(processes))
                row["sample_s"] = round(time.monotonic() - began, 3)   # how long this sample took (a busy PC makes the sampler late)
                out.write(json.dumps(row) + "\n")
                out.flush()


class Stack:
    def __init__(self, *, python, app, scratch, out, inputs, profile=None, clients=1, ports=None, map_publish_mark=False, extra_env=None,
                 client_profiles=None):
        self.python, self.app, self.scratch, self.out = str(python), Path(app), Path(scratch), Path(out)
        # One link profile per client (None: straight to the dashboard). By default every client has `profile`; the
        # two-viewer scenario gives the second client a slower one (NET-11 (6)).
        self.client_profiles = list(client_profiles) if client_profiles is not None else [profile] * int(clients)
        self.inputs, self.profile, self.clients = inputs, profile, len(self.client_profiles)
        self.ports = dict(ports or DEFAULT_PORTS)
        self.map_publish_mark, self.extra_env = map_publish_mark, dict(extra_env or {})
        self.procs, self.logs = {}, []
        self.control_socket_marker = str(self.scratch / "control.sock")  # never created: the shim maps it to the endpoint
        (self.out / "logs").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ addresses
    def link_ports(self):
        return [self.ports["link"] + index for index, profile in enumerate(self.client_profiles) if profile]

    def client_origins(self):
        return [f"http://127.0.0.1:{self.ports['link'] + index if profile else self.ports['dashboard']}"
                for index, profile in enumerate(self.client_profiles)]

    def all_ports(self):
        return [self.ports[name] for name in ("dashboard", "lidar", "camera", "imu", "gps", "recorder", "control")] + self.link_ports()

    # ------------------------------------------------------------ environment
    def env(self, name):
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith(STRIP_ENV_PREFIXES)}
        python_dir = str(Path(self.python).parent)
        if os.name == "nt":
            # A short PATH: every shutil.which() of the dashboard walks the whole PATH, which is slow on a developer PC.
            env["PATH"] = os.pathsep.join([python_dir, os.path.join(os.environ.get("SystemRoot", "C:/Windows"), "System32")])
        shim = [str(TOOL_DIR / "shim")] + ([str(TOOL_DIR / "shim" / "win")] if os.name == "nt" else [])
        runtime = self.app / "runtime"
        ports = self.ports
        env.update(
            PYTHONPATH=os.pathsep.join(shim + [str(self.app)]), PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1",
            DASHBOARD_HOST="127.0.0.1", DASHBOARD_PORT=str(ports["dashboard"]),
            LIDAR_WS_UPSTREAM=f"ws://127.0.0.1:{ports['lidar']}/ws/lidar", CAMERA_WS_UPSTREAM=f"ws://127.0.0.1:{ports['camera']}/ws/camera",
            IMU_WS_UPSTREAM=f"ws://127.0.0.1:{ports['imu']}/ws/imu", GPS_WS_UPSTREAM=f"ws://127.0.0.1:{ports['gps']}/ws/gps",
            RECORDER_API_URL=f"http://127.0.0.1:{ports['recorder']}",
            RECORDING_ARCHIVE_DIR=str(self.scratch / "recordings"), RECORDING_PLAYBACK_CACHE_DIR=str(self.scratch / "playback_cache"),
            OFFLINE_MAP_DIR=str(self.scratch / "maps"),
            # No outside lookups, no Docker, no network control, no host files.
            EXTERNAL_IP_URL="http://127.0.0.1:9/", EXTERNAL_IP_TIMEOUT_S="0.2", FAN_HOLD_INTERVAL_S="0",
            DOCKER_SOCKET=str(self.scratch / "no-docker.sock"), NETWORK_CONTROL_ENABLED="0",
            HOST_PROC=str(self.scratch / "no-proc"), HOST_SYS=str(self.scratch / "no-sys"),
            LIDAR_BRIDGE_STATUS_FILE=str(runtime / "lidar_bridge_status.json"),
            CAMERA_BRIDGE_STATUS_FILE=str(runtime / "camera_bridge_status.json"),
            IMU_BRIDGE_STATUS_FILE=str(runtime / "imu_bridge_status.json"),
            ROVER_CONTROL_SOCKET=self.control_socket_marker, SLOW_LINK_CONTROL_PORT=str(ports["control"]),
            SLOW_LINK_STALL_PROBE=str(self.out / f"stall_{name}.jsonl"))
        if self.map_publish_mark:
            env["SLOW_LINK_MAP_PUBLISH_MARK"] = "1"
        env.update(self.extra_env)
        return env

    def spawn(self, name, args):
        log = (self.out / "logs" / f"{name}.log").open("w", encoding="utf-8")
        self.logs.append(log)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.procs[name] = subprocess.Popen([self.python] + [str(arg) for arg in args], cwd=str(self.scratch), env=self.env(name),
                                            stdout=log, stderr=subprocess.STDOUT, creationflags=flags)

    # ------------------------------------------------------------ lifecycle
    def start(self):
        busy = [port for port in self.all_ports() if port_is_open(port)]
        if busy:
            raise RuntimeError(f"port(s) already in use on 127.0.0.1: {busy}; nothing was started")
        for folder in ("recordings", "playback_cache"):
            (self.scratch / folder).mkdir(parents=True, exist_ok=True)
        runtime = self.app / "runtime"
        runtime.mkdir(exist_ok=True)
        ports = self.ports
        try:
            proof = self.out / "control_endpoint.json"
            self.spawn("control", [TOOL_DIR / "control_endpoint.py", "--app", self.app, "--events", self.out / "control_events.jsonl",
                                   "--proof", proof, "--port", ports["control"], "--status-file", runtime / "rover_bridge_status.json"])
            self.wait_for(proof.is_file, 10, "control endpoint did not start")
            self.epoch = time.time() + 2.5  # one replay origin for all bridges (LiDAR, IMU and GPS stay aligned)
            common = ["--app", self.app, "--out", self.out, "--status-dir", runtime, "--epoch", repr(self.epoch),
                      "--lidar-port", ports["lidar"], "--camera-port", ports["camera"], "--imu-port", ports["imu"],
                      "--gps-port", ports["gps"], "--recorder-port", ports["recorder"]]
            bridge = TOOL_DIR / "replay_bridges.py"
            self.spawn("bridge_lidar", [bridge, "--bridge", "lidar", "--lidar-npz", self.inputs["lidar_npz"]] + common)
            camera = ["--camera-dir", self.inputs["camera_dir"]]
            if self.inputs.get("camera_original_dir"):
                camera += ["--camera-original-dir", self.inputs["camera_original_dir"]]
            self.spawn("bridge_camera", [bridge, "--bridge", "camera"] + camera + common)
            self.spawn("bridge_imugps", [bridge, "--bridge", "imugps", "--drive-json", self.inputs["drive_json"]] + common)
            time.sleep(2.0)
            self.spawn("dashboard", ["-m", "dashboard_server"])
            if any(self.client_profiles):
                specs = sum((["--link", link_emulator.link_spec(f"c{index}", self.ports["link"] + index, profile)]
                             for index, profile in enumerate(self.client_profiles) if profile), [])
                self.spawn("link", [TOOL_DIR / "link_emulator.py", "--upstream", f"127.0.0.1:{ports['dashboard']}",
                                    "--stats", self.out / "link.json"] + specs)
            self.wait_for(self.dashboard_ready, 40, "dashboard did not become ready")
            self.wait_for(lambda: all(port_is_open(port) for port in self.all_ports()), 20, "a bench listener is missing")
            self.verify_owned_listeners()
            time.sleep(1.0)
            self.sampler = Sampler(self.procs, self.out / "cpu.jsonl")
            self.sampler.start()
        except BaseException:
            self.stop()
            raise
        return self

    def wait_for(self, condition, timeout, message):
        end = time.time() + timeout
        while time.time() < end:
            dead = [name for name, process in self.procs.items() if process.poll() is not None]
            if dead:
                raise RuntimeError(f"bench process exited early: {dead}; see {self.out / 'logs'}")
            if condition():
                return
            time.sleep(0.2)
        raise RuntimeError(f"{message}; see {self.out / 'logs'}")

    def dashboard_ready(self):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.ports['dashboard']}/api/health", timeout=2).read()
            return True
        except OSError:
            return False

    def verify_owned_listeners(self):
        """Every expected port must be listened on by a process of this stack, on 127.0.0.1 only."""
        import psutil
        seen = {}
        for name, process in self.procs.items():
            parent = psutil.Process(process.pid)
            for handle in [parent] + parent.children(recursive=True):
                try:
                    connections = handle.net_connections(kind="tcp")
                except psutil.Error:
                    continue
                for connection in connections:
                    # A listening socket has no remote address. (The reported state is not used:
                    # psutil names it wrongly on some Windows builds.)
                    if connection.laddr and not connection.raddr:
                        if connection.laddr.ip != "127.0.0.1":
                            raise RuntimeError(f"{name} listens on {connection.laddr.ip}:{connection.laddr.port}, not on 127.0.0.1")
                        seen[connection.laddr.port] = name
        missing = sorted(set(self.all_ports()) - set(seen))
        if missing:
            raise RuntimeError(f"port(s) {missing} are not owned by this stack (another process answers there)")
        self.listeners = seen
        return seen

    def stop(self):
        """Stop only the processes this stack started; report any port that still answers."""
        import psutil
        if hasattr(self, "sampler"):
            self.sampler.stop.set()
        for process in self.procs.values():
            try:
                for kid in psutil.Process(process.pid).children(recursive=True):
                    kid.terminate()
            except psutil.Error:
                pass
            try:
                process.terminate()
            except OSError:
                pass
        for process in self.procs.values():
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        for log in self.logs:
            try:
                log.close()
            except OSError:
                pass
        end = time.time() + 5
        left = self.all_ports()
        while left and time.time() < end:
            left = [port for port in left if port_is_open(port, 0.2)]
            if left:
                time.sleep(0.3)
        self.left_listening = left
        return left


if __name__ == "__main__":
    sys.exit("use run_bench.py; this module only provides the Stack class")
