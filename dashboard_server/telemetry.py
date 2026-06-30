from __future__ import annotations

import http.client
import ipaddress
import json
import fcntl
import math
import os
import platform
import shutil
import socket
import struct
import termios
import time
from pathlib import Path
from typing import Any
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "dashboard.json"
DEFAULT_TELEMETRY_FILE = ROOT / "runtime" / "telemetry_snapshot.json"
DEFAULT_MEMORY_DISPLAY_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_LIDAR_BAUDS = [1_000_000, 460_800, 256_000, 115_200]
DEFAULT_LIDAR_SCAN_DURATION_S = 3.0
DEFAULT_LIDAR_SCAN_MAX_POINTS = 1800
RPLIDAR_STOP = bytes([0xA5, 0x25])
RPLIDAR_SCAN = bytes([0xA5, 0x20])
RPLIDAR_SCAN_DESCRIPTOR = bytes([0xA5, 0x5A, 0x05, 0x00, 0x00, 0x40, 0x81])
TIOCMGET = 0x5415
TIOCMSET = 0x5418
TIOCM_DTR = 0x002
TIOCM_RTS = 0x004


def _read_text(path: str | Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, TypeError, UnicodeDecodeError):
        return None


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _pct(used: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round((used / total) * 100.0, 1)


def _cpu_display_label(name: str) -> str:
    if name.startswith("cpu") and name[3:].isdigit():
        return f"CPU {int(name[3:]) + 1}"
    return name.upper()


class DockerSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


class TelemetryProvider:
    def __init__(self, config_path: str | Path | None = None):
        self.started_at = time.time()
        self.config_path = Path(config_path or os.environ.get("DASHBOARD_CONFIG", DEFAULT_CONFIG))
        self.config = _load_json(self.config_path)
        self.mode = os.environ.get("DASHBOARD_MODE", self.config.get("mode", "live"))
        telemetry_file = Path(
            os.environ.get("TELEMETRY_FILE", self.config.get("telemetry_file", str(DEFAULT_TELEMETRY_FILE)))
        )
        self.telemetry_file = telemetry_file if telemetry_file.is_absolute() else ROOT / telemetry_file
        self.host_root = Path(os.environ.get("HOST_ROOT", "/host-root"))
        if not self.host_root.exists():
            self.host_root = Path("/")
        self.proc_root = Path(os.environ.get("HOST_PROC", "/proc"))
        self.sys_root = Path(os.environ.get("HOST_SYS", "/sys"))
        self.docker_socket = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
        self._last_cpu = self._read_cpu_times()
        self._last_net = self._read_net_counters()
        self._last_sample_at = time.time()
        self._external_ip_value: str | None = None
        self._external_ip_error: str | None = None
        self._external_ip_next_refresh = 0.0
        self._last_lidar_test: dict[str, Any] | None = None
        self._last_lidar_scan: dict[str, Any] | None = None

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service": "unist_orachard_dashboard",
            "mode": self.mode,
            "uptime_s": round(time.time() - self.started_at, 1),
            "host": self._hostname(),
        }

    def telemetry(self) -> dict[str, Any]:
        external = _load_json(self.telemetry_file) if self.telemetry_file.exists() else {}
        payload = {
            "meta": self._meta(),
            "system": self.system(),
            "network": self.network(),
            "docker": self.docker(),
            "connection": self.connection(external),
            "rover": external.get("rover", self._not_connected("rover")),
            "sensors": external.get("sensors", self._default_sensors()),
            "fusion": external.get("fusion", self._not_connected("fusion")),
            "experiment": external.get("experiment", self._not_connected("experiment")),
            "mission": external.get("mission", self._mission_status()),
            "services": self._services(external),
            "alerts": self._alerts(external),
        }
        return payload

    def connection(self, external: dict[str, Any] | None = None) -> dict[str, Any]:
        external = external or (_load_json(self.telemetry_file) if self.telemetry_file.exists() else {})
        return {
            "lidar": self.lidar_status(),
            "services": self._services(external),
            "sensors": external.get("sensors", self._default_sensors()),
        }

    def system(self) -> dict[str, Any]:
        memory = self._memory()
        swap = self._swap()
        disks = self._disks()
        cpu = self._cpu_percent()
        return {
            "hostname": self._hostname(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "jetson_release": _read_text(os.environ.get("JETSON_RELEASE_FILE", "/etc/nv_tegra_release")),
            "service_uptime_s": round(time.time() - self.started_at, 1),
            "os_uptime_s": self._os_uptime_s(),
            "loadavg": os.getloadavg() if hasattr(os, "getloadavg") else [0.0, 0.0, 0.0],
            "cpu": cpu,
            "memory": memory,
            "swap": swap,
            "gpu_percent": self._gpu_percent(),
            "temperatures": self._temperatures(),
            "temperature_c": self._max_temperature(),
            "disks": disks,
        }

    def network(self) -> dict[str, Any]:
        now = time.time()
        counters = self._read_net_counters()
        elapsed = max(0.001, now - self._last_sample_at)
        interfaces = []
        for name, values in counters.items():
            previous = self._last_net.get(name, values)
            rx_bps = max(0.0, (values["rx_bytes"] - previous["rx_bytes"]) / elapsed)
            tx_bps = max(0.0, (values["tx_bytes"] - previous["tx_bytes"]) / elapsed)
            interfaces.append(
                {
                    "name": name,
                    "state": self._iface_text(name, "operstate"),
                    "carrier": self._iface_text(name, "carrier"),
                    "mac": self._iface_text(name, "address"),
                    "speed_mbps": _safe_int(self._iface_text(name, "speed")),
                    "mtu": _safe_int(self._iface_text(name, "mtu")),
                    "ipv4": self._ipv4_for_interface(name),
                    "rx_bytes": values["rx_bytes"],
                    "tx_bytes": values["tx_bytes"],
                    "rx_bps": round(rx_bps, 1),
                    "tx_bps": round(tx_bps, 1),
                    "rx_packets": values["rx_packets"],
                    "tx_packets": values["tx_packets"],
                }
            )
        self._last_net = counters
        self._last_sample_at = now
        active = [iface for iface in interfaces if iface.get("state") == "up" and iface["name"] != "lo"]
        primary_ipv4 = self._primary_ipv4(interfaces)
        primary_interface = self._primary_interface(primary_ipv4, interfaces)
        traffic_interfaces = [iface for iface in interfaces if iface["name"] != "lo"]
        return {
            "hostname": self._hostname(),
            "interfaces": sorted(interfaces, key=lambda item: (item["name"] == "lo", item["name"])),
            "active_interfaces": [iface["name"] for iface in active],
            "primary_ipv4": primary_ipv4,
            "primary_interface": primary_interface,
            "external_ip": self._cached_external_ip(),
            "external_ip_error": self._external_ip_error,
            "total_rx_bps": round(sum(iface["rx_bps"] for iface in traffic_interfaces), 1),
            "total_tx_bps": round(sum(iface["tx_bps"] for iface in traffic_interfaces), 1),
        }

    def docker(self) -> dict[str, Any]:
        if not Path(self.docker_socket).exists():
            return {"available": False, "error": "docker socket not mounted", "containers": []}
        try:
            info = self._docker_get_json("/info")
            version = self._docker_get_json("/version")
            containers = self._docker_get_json("/containers/json?all=1")
        except OSError as exc:
            return {"available": False, "error": str(exc), "containers": []}
        rows = []
        for container in containers:
            container_id = container.get("Id", "")
            stats = self._docker_container_stats(container_id) if container_id else {}
            rows.append(
                {
                    "id": container_id[:12],
                    "name": (container.get("Names") or [""])[0].lstrip("/"),
                    "image": container.get("Image"),
                    "state": container.get("State"),
                    "status": container.get("Status"),
                    "ports": container.get("Ports", []),
                    "cpu_percent": stats.get("cpu_percent"),
                    "memory_percent": stats.get("memory_percent"),
                    "memory_usage": stats.get("memory_usage"),
                    "memory_limit": stats.get("memory_limit"),
                    "rx_bytes": stats.get("rx_bytes"),
                    "tx_bytes": stats.get("tx_bytes"),
                }
            )
        running = [row for row in rows if row.get("state") == "running"]
        return {
            "available": True,
            "engine": {
                "server_version": info.get("ServerVersion"),
                "docker_root_dir": info.get("DockerRootDir"),
                "driver": info.get("Driver"),
                "operating_system": info.get("OperatingSystem"),
                "architecture": info.get("Architecture"),
                "containers": info.get("Containers"),
                "containers_running": info.get("ContainersRunning"),
                "images": info.get("Images"),
                "version": version.get("Version"),
                "api_version": version.get("ApiVersion"),
            },
            "summary": {
                "total": len(rows),
                "running": len(running),
                "stopped": len(rows) - len(running),
                "cpu_percent": round(sum(row.get("cpu_percent") or 0.0 for row in rows), 1),
                "memory_usage": sum(row.get("memory_usage") or 0 for row in rows),
                "memory_limit": max([row.get("memory_limit") or 0 for row in rows] or [0]),
            },
            "containers": rows,
        }

    def lidar_status(self) -> dict[str, Any]:
        candidates = self._lidar_candidates()
        present = [item for item in candidates if item["exists"]]
        selected = present[0] if present else (candidates[0] if candidates else None)
        holders = self._device_holders(selected["open_path"]) if selected and selected["exists"] else []
        state = "detected" if present else "not_found"
        if holders:
            state = "in_use"
        return {
            "present": bool(present),
            "state": state,
            "device": selected["display_path"] if selected else None,
            "open_path": selected["open_path"] if selected else None,
            "by_id": selected["by_id"] if selected else None,
            "driver_hint": self._lidar_driver_hint(selected["display_path"] if selected else ""),
            "holders": holders,
            "test_supported": bool(present),
            "test_mode": "manual_short_probe",
            "conflict_policy": "포트가 점유 중이면 테스트를 실행하지 않고, 버튼 테스트 중에만 잠깐 포트를 엽니다.",
            "last_test": self._last_lidar_test,
            "last_scan": self._last_lidar_scan,
        }

    def lidar_test(self) -> dict[str, Any]:
        status = self.lidar_status()
        if not status["present"]:
            return {"ok": False, "state": "not_found", "message": "LiDAR serial device was not found.", "status": status}
        if status["holders"]:
            return {
                "ok": False,
                "state": "in_use",
                "message": "LiDAR device is currently held by another process; skipped to avoid ROS2/driver conflicts.",
                "status": status,
            }
        lock_path = Path(os.environ.get("LIDAR_TEST_LOCK", "/tmp/ubts-dashboard-lidar-test.lock"))
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"ok": False, "state": "busy", "message": "Another LiDAR test is already running.", "status": status}
            result = self._rplidar_probe(status["open_path"])
        self._last_lidar_test = result
        return result

    def lidar_scan(self) -> dict[str, Any]:
        status = self.lidar_status()
        if not status["present"]:
            return {"ok": False, "state": "not_found", "message": "LiDAR serial device was not found.", "status": status}
        if status["holders"]:
            return {
                "ok": False,
                "state": "in_use",
                "message": "LiDAR device is currently held by another process; skipped to avoid ROS2/driver conflicts.",
                "status": status,
            }
        lock_path = Path(os.environ.get("LIDAR_TEST_LOCK", "/tmp/ubts-dashboard-lidar-test.lock"))
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"ok": False, "state": "busy", "message": "Another LiDAR test is already running.", "status": status}
            result = self._rplidar_scan(status["open_path"])
        self._last_lidar_scan = result
        return result

    def _lidar_device_root(self) -> Path:
        root = Path(os.environ.get("LIDAR_DEV_ROOT", "/host-dev"))
        return root if root.exists() else Path("/dev")

    def _host_device_label(self, path: Path, resolve: bool = True) -> str:
        root = self._lidar_device_root()
        try:
            target = path.resolve() if resolve else path.absolute()
            relative = target.relative_to(root.resolve())
            return str(Path("/dev") / relative)
        except (OSError, ValueError):
            return str(path)

    def _open_device_path(self, path: str | Path) -> Path:
        raw = Path(path)
        root = self._lidar_device_root()
        if raw.is_absolute() and str(raw).startswith("/dev/") and root != Path("/dev"):
            return root / raw.relative_to("/dev")
        return raw

    def _lidar_candidates(self) -> list[dict[str, Any]]:
        root = self._lidar_device_root()
        paths: list[Path] = []
        configured = os.environ.get("LIDAR_DEVICE")
        if configured:
            paths.append(self._open_device_path(configured))
        by_id_root = Path(os.environ.get("LIDAR_BY_ID_ROOT", str(root / "serial/by-id")))
        if by_id_root.exists():
            paths.extend(sorted(by_id_root.glob("*")))
        for pattern in ("ttyUSB*", "ttyACM*"):
            paths.extend(sorted(root.glob(pattern)))

        rows = []
        seen: set[str] = set()
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "display_path": self._host_device_label(resolved),
                    "open_path": str(resolved),
                    "by_id": self._host_device_label(path, resolve=False) if "/serial/by-id/" in str(path) else None,
                    "exists": resolved.exists(),
                }
            )
        return rows

    def _lidar_driver_hint(self, path: str) -> str | None:
        lower = path.lower()
        if "cp210" in lower or "ttyusb" in lower:
            return "USB-UART serial LiDAR candidate, CP210x 계열"
        if "ttyacm" in lower:
            return "USB CDC ACM LiDAR candidate"
        return None

    def _device_holders(self, device_path: str | None) -> list[dict[str, Any]]:
        if not device_path:
            return []
        try:
            device_name = Path(device_path).resolve().name
        except OSError:
            device_name = Path(device_path).name
        holders = []
        proc_root = Path("/proc")
        for pid_dir in proc_root.iterdir():
            if not pid_dir.name.isdigit():
                continue
            fd_dir = pid_dir / "fd"
            try:
                fds = list(fd_dir.iterdir())
            except OSError:
                continue
            matched = False
            for fd in fds:
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if target == device_path or target.endswith(f"/{device_name}"):
                    matched = True
                    break
            if not matched:
                continue
            cmdline = _read_text(pid_dir / "cmdline") or _read_text(pid_dir / "comm") or ""
            holders.append({"pid": int(pid_dir.name), "cmd": cmdline.replace("\x00", " ").strip()})
        return holders

    def _rplidar_probe(self, path: str) -> dict[str, Any]:
        bauds = self._lidar_bauds()
        errors = []
        for baud in bauds:
            speed = getattr(termios, f"B{baud}", None)
            if speed is None:
                errors.append({"baud": baud, "error": "baud_not_supported_by_termios"})
                continue
            try:
                response = self._rplidar_probe_at_baud(path, baud, speed)
            except OSError as exc:
                errors.append({"baud": baud, "error": str(exc)})
                continue
            if response["ok"]:
                return response
            errors.append({"baud": baud, "error": response.get("message", "no_response")})
        return {
            "ok": False,
            "state": "no_protocol_response",
            "message": "Serial device exists, but RPLIDAR info/health response was not received.",
            "device": self._host_device_label(Path(path)),
            "tried_baud": bauds,
            "errors": errors,
        }

    def _rplidar_scan(self, path: str) -> dict[str, Any]:
        bauds = self._lidar_bauds()
        duration_s = self._lidar_scan_duration()
        max_points = self._lidar_scan_max_points()
        errors = []
        for baud in bauds:
            speed = getattr(termios, f"B{baud}", None)
            if speed is None:
                errors.append({"baud": baud, "error": "baud_not_supported_by_termios"})
                continue
            try:
                response = self._rplidar_normal_scan_at_baud(path, baud, speed, duration_s, max_points)
            except OSError as exc:
                errors.append({"baud": baud, "error": str(exc)})
                continue
            if response["ok"]:
                return response
            error = {
                "baud": baud,
                "error": response.get("message", "no_scan_data"),
                "state": response.get("state"),
                "raw_bytes": response.get("raw_bytes"),
                "motor_line": response.get("motor_line"),
            }
            errors.append({key: value for key, value in error.items() if value is not None})
        return {
            "ok": False,
            "state": "no_scan_data",
            "message": "Serial device exists, but RPLIDAR normal scan points were not received.",
            "device": self._host_device_label(Path(path)),
            "tried_baud": bauds,
            "duration_s": duration_s,
            "errors": errors,
            "points": [],
        }

    def _rplidar_normal_scan_at_baud(
        self,
        path: str,
        baud: int,
        speed: int,
        duration_s: float,
        max_points: int,
    ) -> dict[str, Any]:
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        raw = bytearray()
        motor_line = os.environ.get("LIDAR_MOTOR_LINE", "dtr").strip().lower() or "dtr"
        line_report: dict[str, Any] | None = None
        try:
            attrs = termios.tcgetattr(fd)
            attrs[0] = 0
            attrs[1] = 0
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attrs[3] = 0
            attrs[4] = speed
            attrs[5] = speed
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIOFLUSH)
            line_report = self._set_lidar_modem_lines(fd, motor_line)
            os.write(fd, RPLIDAR_STOP)
            time.sleep(0.08)
            termios.tcflush(fd, termios.TCIFLUSH)
            os.write(fd, RPLIDAR_SCAN)
            deadline = time.time() + duration_s
            while time.time() < deadline and len(raw) < 240_000:
                try:
                    chunk = os.read(fd, 8192)
                except BlockingIOError:
                    time.sleep(0.01)
                    continue
                if chunk:
                    raw.extend(chunk)
                else:
                    time.sleep(0.01)
        finally:
            try:
                os.write(fd, RPLIDAR_STOP)
                time.sleep(0.03)
            except OSError:
                pass
            self._set_lidar_modem_lines(fd, "off")
            os.close(fd)

        payload = self._scan_payload(bytes(raw))
        nodes = self._parse_normal_scan_nodes(payload)
        if not nodes:
            return {
                "ok": False,
                "state": "no_scan_points",
                "message": f"RPLIDAR scan descriptor or valid points were not received at {baud} bps.",
                "device": self._host_device_label(Path(path)),
                "baud": baud,
                "protocol": "RPLIDAR normal scan",
                "duration_s": duration_s,
                "raw_bytes": len(raw),
                "motor_line": line_report,
                "points": [],
            }

        rendered = self._decimate_points(nodes, max_points)
        distances = [point["distance_m"] for point in nodes]
        angles = [point["angle_deg"] for point in nodes]
        metrics = {
            "points": len(nodes),
            "rendered_points": len(rendered),
            "raw_bytes": len(raw),
            "min_distance_m": round(min(distances), 3),
            "max_distance_m": round(max(distances), 3),
            "angle_min_deg": round(min(angles), 2),
            "angle_max_deg": round(max(angles), 2),
            "coverage_deg": self._angle_coverage(nodes),
            "range_limit_m": 30.0,
        }
        return {
            "ok": True,
            "state": "scan_ready",
            "message": "RPLIDAR produced normal scan points; port was closed after STOP.",
            "device": self._host_device_label(Path(path)),
            "baud": baud,
            "protocol": "RPLIDAR normal scan",
            "duration_s": duration_s,
            "points": rendered,
            "metrics": metrics,
            "tested_at": int(time.time()),
            "motor_line": line_report,
            "conflict_policy": "Short manual scan only; DTR/RTS motor line is disabled after STOP and the serial port is closed.",
        }

    def _scan_payload(self, raw: bytes) -> bytes:
        exact = raw.find(RPLIDAR_SCAN_DESCRIPTOR)
        if exact >= 0:
            return raw[exact + len(RPLIDAR_SCAN_DESCRIPTOR) :]
        marker = raw.find(b"\xA5\x5A")
        if marker >= 0 and len(raw) >= marker + 7:
            return raw[marker + 7 :]
        return raw

    def _parse_normal_scan_nodes(self, data: bytes) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        i = 0
        while i <= len(data) - 5:
            b0, b1, b2, b3, b4 = data[i : i + 5]
            start_flag = b0 & 0x01
            inverse_start_flag = (b0 >> 1) & 0x01
            check_bit = b1 & 0x01
            if (start_flag ^ inverse_start_flag) != 1 or check_bit != 1:
                i += 1
                continue
            angle_deg = ((b1 >> 1) | (b2 << 7)) / 64.0
            distance_m = ((b3 | (b4 << 8)) / 4.0) / 1000.0
            if 0.05 <= distance_m <= 30.0 and 0.0 <= angle_deg < 360.0:
                rad = math.radians(angle_deg)
                nodes.append(
                    {
                        "angle_deg": round(angle_deg, 3),
                        "distance_m": round(distance_m, 3),
                        "quality": b0 >> 2,
                        "x_m": round(math.sin(rad) * distance_m, 3),
                        "y_m": round(math.cos(rad) * distance_m, 3),
                    }
                )
                i += 5
                continue
            i += 1
        return nodes

    def _decimate_points(self, points: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
        if len(points) <= max_points:
            return points
        step = len(points) / max_points
        return [points[min(len(points) - 1, int(index * step))] for index in range(max_points)]

    def _angle_coverage(self, points: list[dict[str, Any]]) -> float:
        if not points:
            return 0.0
        bins = {int(point["angle_deg"]) % 360 for point in points}
        return round(float(len(bins)), 1)

    def _set_lidar_modem_lines(self, fd: int, mode: str) -> dict[str, Any]:
        try:
            flags = struct.unpack("I", fcntl.ioctl(fd, TIOCMGET, struct.pack("I", 0)))[0]
            if mode == "dtr_rts":
                flags |= TIOCM_DTR | TIOCM_RTS
            elif mode == "rts":
                flags = (flags | TIOCM_RTS) & ~TIOCM_DTR
            elif mode == "off":
                flags &= ~(TIOCM_DTR | TIOCM_RTS)
            else:
                mode = "dtr"
                flags = (flags | TIOCM_DTR) & ~TIOCM_RTS
            fcntl.ioctl(fd, TIOCMSET, struct.pack("I", flags))
        except OSError as exc:
            return {"ok": False, "mode": mode, "error": str(exc)}
        return {"ok": True, "mode": mode}

    def _lidar_scan_duration(self) -> float:
        try:
            value = float(os.environ.get("LIDAR_SCAN_DURATION_S", DEFAULT_LIDAR_SCAN_DURATION_S))
        except ValueError:
            value = DEFAULT_LIDAR_SCAN_DURATION_S
        return round(max(0.3, min(4.0, value)), 2)

    def _lidar_scan_max_points(self) -> int:
        try:
            value = int(os.environ.get("LIDAR_SCAN_MAX_POINTS", DEFAULT_LIDAR_SCAN_MAX_POINTS))
        except ValueError:
            value = DEFAULT_LIDAR_SCAN_MAX_POINTS
        return max(120, min(5000, value))

    def _lidar_bauds(self) -> list[int]:
        raw = os.environ.get("LIDAR_BAUDS")
        if not raw:
            return DEFAULT_LIDAR_BAUDS
        values = []
        for item in raw.split(","):
            try:
                values.append(int(item.strip()))
            except ValueError:
                continue
        return values or DEFAULT_LIDAR_BAUDS

    def _rplidar_probe_at_baud(self, path: str, baud: int, speed: int) -> dict[str, Any]:
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attrs = termios.tcgetattr(fd)
            attrs[0] = 0
            attrs[1] = 0
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attrs[3] = 0
            attrs[4] = speed
            attrs[5] = speed
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIOFLUSH)
            os.write(fd, bytes([0xA5, 0x25]))
            time.sleep(0.05)
            info_raw = self._serial_command(fd, bytes([0xA5, 0x50]), 27, 0.9)
            health_raw = self._serial_command(fd, bytes([0xA5, 0x52]), 10, 0.7)
        finally:
            os.close(fd)
        if not info_raw.startswith(b"\xA5\x5A"):
            return {"ok": False, "message": f"no RPLIDAR response at {baud} bps"}
        info = self._parse_rplidar_info(info_raw)
        health = self._parse_rplidar_health(health_raw)
        return {
            "ok": True,
            "state": "responding",
            "message": "RPLIDAR responded to GET_INFO without starting scan.",
            "device": self._host_device_label(Path(path)),
            "baud": baud,
            "protocol": "RPLIDAR",
            "info": info,
            "health": health,
            "raw_info": info_raw.hex(" "),
            "raw_health": health_raw.hex(" "),
            "tested_at": int(time.time()),
            "conflict_policy": "GET_INFO/GET_HEALTH only; no scan command; serial port closed immediately after test.",
        }

    def _serial_command(self, fd: int, command: bytes, expected: int, timeout_s: float) -> bytes:
        termios.tcflush(fd, termios.TCIOFLUSH)
        os.write(fd, command)
        deadline = time.time() + timeout_s
        data = bytearray()
        while time.time() < deadline and len(data) < expected:
            try:
                chunk = os.read(fd, expected - len(data))
            except BlockingIOError:
                time.sleep(0.02)
                continue
            if chunk:
                data.extend(chunk)
            else:
                time.sleep(0.02)
        return bytes(data)

    def _parse_rplidar_info(self, data: bytes) -> dict[str, Any]:
        payload = data[7:27]
        if len(payload) < 20:
            return {"raw_payload": payload.hex(" ")}
        return {
            "model_id": payload[0],
            "firmware": f"{payload[2]}.{payload[1]}",
            "hardware": payload[3],
            "serial": payload[4:20].hex(),
        }

    def _parse_rplidar_health(self, data: bytes) -> dict[str, Any] | None:
        if not data.startswith(b"\xA5\x5A") or len(data) < 10:
            return None
        payload = data[7:10]
        status = {0: "good", 1: "warning", 2: "error"}.get(payload[0], f"unknown_{payload[0]}")
        return {"status": status, "error_code": payload[1] | (payload[2] << 8)}

    def plan(self) -> dict[str, Any]:
        return {
            "research_topic": "과수원 환경에서 Vision-LiDAR 센서 기반 신뢰도 동적 조절을 이용한 행간 중심선 추정 및 경로추종 안정화",
            "team": "유니주행차",
            "partner": "한국자동차연구원",
            "focus": [
                "ROS2 기반 자율주행 아키텍처",
                "LiDAR 기반 과수 인식 및 과수열 검출",
                "RGB 카메라 기반 주행 가능 영역 보완",
                "IMU 기반 자세 및 주행 상태 추정",
                "Vision-LiDAR 신뢰도 동적 가중치 조절",
                "과수원 주행 테스트 및 성능 검증",
            ],
            "milestones": [
                {"month": "5월", "label": "선행연구 분석, 자율주행 이론 스터디", "status": "done"},
                {"month": "6월", "label": "구동 시스템 설계, 센서 요구사항 정의", "status": "done"},
                {"month": "7월", "label": "자율주행 자동차 제작, 회로 제작 및 센서 보정", "status": "planned"},
                {"month": "8월", "label": "예측 모델 구축, 추가 개선 장비 구매", "status": "planned"},
                {"month": "9월", "label": "주행 테스트 및 성능 검증", "status": "planned"},
                {"month": "10월", "label": "실험 데이터 분석, 전문가 피드백", "status": "planned"},
                {"month": "11월", "label": "논문 작성, 학회 참가", "status": "planned"},
                {"month": "12월", "label": "결과물 제출 및 최종성과발표회", "status": "planned"},
            ],
        }

    def site_map(self) -> dict[str, Any]:
        return {
            "routes": [
                {"path": "/", "label": "Overview", "summary": "핵심 상태"},
                {"path": "/connection", "label": "Connection", "summary": "LiDAR, ROS2 bridge, sensor links"},
                {"path": "/system", "label": "System", "summary": "CPU, Memory, GPU, Thermal, Disk"},
                {"path": "/network", "label": "Network", "summary": "IP, interface, RX/TX throughput"},
                {"path": "/docker", "label": "Docker", "summary": "Docker engine and containers"},
                {"path": "/research", "label": "Research", "summary": "연구 일정과 ROS2 연결 상태"},
                {"path": "/docs", "label": "Docs", "summary": "운영 문서와 Markdown viewer"},
            ]
        }

    def _meta(self) -> dict[str, Any]:
        return {
            "generated_at": int(time.time()),
            "mode": self.mode,
            "project": "unist_orachard_dashboard",
            "telemetry_file": str(self.telemetry_file),
        }

    def _not_connected(self, name: str) -> dict[str, Any]:
        return {"connected": False, "state": "not_connected", "source": name}

    def _default_sensors(self) -> dict[str, Any]:
        return {
            "lidar": {"ok": False, "state": "not_connected", "topic": "/scan"},
            "camera": {"ok": False, "state": "not_connected", "topic": "/camera/color/image_raw"},
            "imu": {"ok": False, "state": "not_connected", "topic": "/imu/data"},
            "encoder": {"ok": False, "state": "not_connected", "topic": "/wheel/encoder"},
            "mavlink": {"ok": False, "state": "not_connected"},
        }

    def _mission_status(self) -> dict[str, Any]:
        return {
            "name": "Orchard rover dashboard",
            "stage": "infrastructure",
            "operator": "unist",
            "connected": False,
        }

    def _services(self, external: dict[str, Any]) -> dict[str, Any]:
        services = {
            "dashboard": {"ok": True, "state": "serving", "port": 8080},
            "cloudflared": {"ok": True, "state": "tunnel_configured", "host": "bts.sarihub.com"},
            "ros2_bridge": {"ok": False, "state": "not_connected"},
            "data_recorder": {"ok": False, "state": "not_connected"},
        }
        services.update(external.get("services", {}))
        return services

    def _alerts(self, external: dict[str, Any]) -> list[dict[str, str]]:
        alerts = list(external.get("alerts", []))
        if not external.get("rover"):
            alerts.append({"level": "info", "message": "ROS2/로버 텔레메트리 파일이 아직 연결되지 않았습니다."})
        if not Path(self.docker_socket).exists():
            alerts.append({"level": "warn", "message": "Docker socket이 컨테이너에 마운트되지 않았습니다."})
        return alerts

    def _hostname(self) -> str:
        return _read_text(os.environ.get("DASHBOARD_HOSTNAME_FILE", str(self.host_root / "etc/hostname"))) or socket.gethostname()

    def _cpu_percent(self) -> dict[str, Any]:
        current = self._read_cpu_times()
        if not current or not self._last_cpu:
            return {"total_percent": None, "cores": []}
        result = {"total_percent": None, "cores": []}
        for name, values in current.items():
            previous = self._last_cpu.get(name)
            if not previous:
                continue
            idle_delta = values["idle"] - previous["idle"]
            total_delta = values["total"] - previous["total"]
            percent = None if total_delta <= 0 else round(max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta))), 1)
            if name == "cpu":
                result["total_percent"] = percent
            else:
                index = int(name[3:]) if name.startswith("cpu") and name[3:].isdigit() else len(result["cores"])
                result["cores"].append(
                    {
                        "name": name,
                        "label": _cpu_display_label(name),
                        "index": index,
                        "percent": percent,
                    }
                )
        self._last_cpu = current
        result["cores"].sort(key=lambda row: row.get("index", 0))
        return result

    def _read_cpu_times(self) -> dict[str, dict[str, int]] | None:
        text = _read_text(self.proc_root / "stat")
        if not text:
            return None
        rows = {}
        for line in text.splitlines():
            parts = line.split()
            if not parts or not parts[0].startswith("cpu"):
                continue
            try:
                values = [int(value) for value in parts[1:]]
            except ValueError:
                continue
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            rows[parts[0]] = {"idle": idle, "total": sum(values)}
        return rows

    def _memory(self) -> dict[str, Any]:
        values = self._meminfo()
        total = values.get("MemTotal", 0) * 1024
        available = values.get("MemAvailable", 0) * 1024
        used = max(0, total - available)
        display_total = int(os.environ.get("MEMORY_DISPLAY_TOTAL_BYTES", DEFAULT_MEMORY_DISPLAY_TOTAL_BYTES))
        return {
            "total": total,
            "available": available,
            "used": used,
            "percent": _pct(used, total),
            "display_total": display_total,
            "display_percent": _pct(used, display_total),
            "display_label": "8.0 GB 기준",
        }

    def _swap(self) -> dict[str, Any]:
        values = self._meminfo()
        total = values.get("SwapTotal", 0) * 1024
        free = values.get("SwapFree", 0) * 1024
        used = max(0, total - free)
        return {"total": total, "free": free, "used": used, "percent": _pct(used, total)}

    def _meminfo(self) -> dict[str, int]:
        text = _read_text(self.proc_root / "meminfo")
        values = {}
        if not text:
            return values
        for line in text.splitlines():
            key, raw = line.split(":", 1)
            try:
                values[key] = int(raw.strip().split()[0])
            except (IndexError, ValueError):
                continue
        return values

    def _disks(self) -> list[dict[str, Any]]:
        targets = [("/", self.host_root)]
        workspace = self.host_root / "home/unist/workspace"
        if workspace.exists():
            targets.append(("/home/unist/workspace", workspace))
        disks = []
        for label, path in targets:
            try:
                usage = shutil.disk_usage(path)
            except OSError:
                continue
            disks.append(
                {
                    "mount": label,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": _pct(usage.used, usage.total),
                }
            )
        return disks

    def _os_uptime_s(self) -> float | None:
        text = _read_text(self.proc_root / "uptime")
        if not text:
            return None
        try:
            return round(float(text.split()[0]), 1)
        except (IndexError, ValueError):
            return None

    def _gpu_percent(self) -> float | None:
        candidates = [self.sys_root / "devices/gpu.0/load"]
        candidates.extend((self.sys_root / "devices/platform").glob("*gpu*/load"))
        candidates.extend((self.sys_root / "devices/platform").glob("*.gpu/load"))
        for path in candidates:
            raw = _read_text(path)
            if raw and raw.lstrip("-").isdigit():
                value = int(raw)
                return round(value / 10.0 if value > 100 else float(value), 1)
        return None

    def _temperatures(self) -> list[dict[str, Any]]:
        thermal_root = self.sys_root / "class/thermal"
        temps = []
        if not thermal_root.exists():
            return temps
        for zone in thermal_root.glob("thermal_zone*"):
            raw = _read_text(zone / "temp")
            value = _safe_int(raw)
            if value is None:
                continue
            label = _read_text(zone / "type") or zone.name
            temp = value / 1000.0 if abs(value) > 200 else float(value)
            temps.append({"name": label, "temperature_c": round(temp, 1)})
        return temps

    def _max_temperature(self) -> float | None:
        temps = [row["temperature_c"] for row in self._temperatures()]
        return round(max(temps), 1) if temps else None

    def _read_net_counters(self) -> dict[str, dict[str, int]]:
        text = _read_text(self.proc_root / "net/dev")
        counters = {}
        if not text:
            return counters
        for line in text.splitlines()[2:]:
            if ":" not in line:
                continue
            name, raw = line.split(":", 1)
            fields = raw.split()
            if len(fields) < 16:
                continue
            counters[name.strip()] = {
                "rx_bytes": int(fields[0]),
                "rx_packets": int(fields[1]),
                "tx_bytes": int(fields[8]),
                "tx_packets": int(fields[9]),
            }
        return counters

    def _cached_external_ip(self) -> str | None:
        now = time.time()
        if now < self._external_ip_next_refresh:
            return self._external_ip_value
        url = os.environ.get("EXTERNAL_IP_URL", "https://api64.ipify.org")
        timeout = float(os.environ.get("EXTERNAL_IP_TIMEOUT_S", "0.8"))
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                value = response.read(96).decode("utf-8").strip().splitlines()[0]
            ipaddress.ip_address(value)
            self._external_ip_value = value
            self._external_ip_error = None
            self._external_ip_next_refresh = now + int(os.environ.get("EXTERNAL_IP_CACHE_S", "300"))
        except (OSError, UnicodeDecodeError, ValueError, IndexError) as exc:
            self._external_ip_error = str(exc)
            self._external_ip_next_refresh = now + int(os.environ.get("EXTERNAL_IP_RETRY_S", "60"))
        return self._external_ip_value

    def _primary_ipv4(self, interfaces: list[dict[str, Any]]) -> str | None:
        target = os.environ.get("PRIMARY_IP_ROUTE_TARGET", "1.1.1.1")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.2)
                sock.connect((target, 80))
                value = sock.getsockname()[0]
                if value and not value.startswith("127."):
                    return value
        except OSError:
            pass
        for iface in interfaces:
            if iface["name"] == "lo":
                continue
            for value in iface.get("ipv4") or []:
                if value and not value.startswith("127."):
                    return value
        return None

    def _primary_interface(self, primary_ipv4: str | None, interfaces: list[dict[str, Any]]) -> str | None:
        if not primary_ipv4:
            return None
        for iface in interfaces:
            if primary_ipv4 in (iface.get("ipv4") or []):
                return iface["name"]
        return None

    def _iface_text(self, name: str, field: str) -> str | None:
        return _read_text(self.sys_root / "class/net" / name / field)

    def _ipv4_for_interface(self, name: str) -> list[str]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            request = struct.pack("256s", name[:15].encode("utf-8"))
            response = fcntl_ioctl(sock, 0x8915, request)
            return [socket.inet_ntoa(response[20:24])]
        except OSError:
            return []
        finally:
            if "sock" in locals():
                sock.close()

    def _docker_get_json(self, path: str) -> Any:
        conn = DockerSocketHTTPConnection(self.docker_socket)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read()
            if response.status >= 400:
                raise OSError(f"Docker API {path} returned {response.status}")
            return json.loads(body.decode("utf-8"))
        finally:
            conn.close()

    def _docker_container_stats(self, container_id: str) -> dict[str, Any]:
        try:
            stats = self._docker_get_json(f"/containers/{container_id}/stats?stream=false")
        except (OSError, json.JSONDecodeError):
            return {}
        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})
        cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
        system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
        online_cpus = cpu_stats.get("online_cpus") or len(cpu_stats.get("cpu_usage", {}).get("percpu_usage") or []) or 1
        cpu_percent = 0.0
        if cpu_delta > 0 and system_delta > 0:
            cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0
        memory = stats.get("memory_stats", {})
        memory_usage = max(0, memory.get("usage", 0) - memory.get("stats", {}).get("cache", 0))
        memory_limit = memory.get("limit", 0)
        networks = stats.get("networks", {})
        rx_bytes = sum(item.get("rx_bytes", 0) for item in networks.values())
        tx_bytes = sum(item.get("tx_bytes", 0) for item in networks.values())
        return {
            "cpu_percent": round(cpu_percent, 1),
            "memory_usage": memory_usage,
            "memory_limit": memory_limit,
            "memory_percent": _pct(memory_usage, memory_limit),
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
        }


def fcntl_ioctl(sock: socket.socket, request: int, args: bytes) -> bytes:
    import fcntl

    return fcntl.ioctl(sock.fileno(), request, args)
