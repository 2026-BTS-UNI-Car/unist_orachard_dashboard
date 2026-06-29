from __future__ import annotations

import http.client
import json
import os
import platform
import shutil
import socket
import struct
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "dashboard.json"
DEFAULT_TELEMETRY_FILE = ROOT / "runtime" / "telemetry_snapshot.json"


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
            "rover": external.get("rover", self._not_connected("rover")),
            "sensors": external.get("sensors", self._default_sensors()),
            "fusion": external.get("fusion", self._not_connected("fusion")),
            "experiment": external.get("experiment", self._not_connected("experiment")),
            "mission": external.get("mission", self._mission_status()),
            "services": self._services(external),
            "alerts": self._alerts(external),
        }
        return payload

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
        return {
            "hostname": self._hostname(),
            "interfaces": sorted(interfaces, key=lambda item: (item["name"] == "lo", item["name"])),
            "active_interfaces": [iface["name"] for iface in active],
            "total_rx_bps": round(sum(iface["rx_bps"] for iface in interfaces), 1),
            "total_tx_bps": round(sum(iface["tx_bps"] for iface in interfaces), 1),
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
                {"path": "/system", "label": "System", "summary": "CPU, Memory, GPU, Thermal, Disk"},
                {"path": "/network", "label": "Network", "summary": "IP, interface, RX/TX throughput"},
                {"path": "/docker", "label": "Docker", "summary": "Docker engine and containers"},
                {"path": "/research", "label": "Research", "summary": "연구 일정과 ROS2 연결 상태"},
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
                result["cores"].append({"name": name, "percent": percent})
        self._last_cpu = current
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
        return {"total": total, "available": available, "used": used, "percent": _pct(used, total)}

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

    def _iface_text(self, name: str, field: str) -> str | None:
        return _read_text(self.sys_root / "class/net" / name / field)

    def _ipv4_for_interface(self, name: str) -> list[str]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            request = struct.pack("256s", name[:15].encode("utf-8"))
            response = fcntl_ioctl(sock, 0x8915, request)
            return [socket.inet_ntoa(response[20:24])]
        except OSError:
            return []
        finally:
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
