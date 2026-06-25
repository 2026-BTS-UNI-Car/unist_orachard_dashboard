from __future__ import annotations

import json
import math
import os
import platform
import random
import shutil
import socket
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "dashboard.json"
DEFAULT_TELEMETRY_FILE = ROOT / "sample_data" / "telemetry_snapshot.json"


def _read_text(path: str | Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class TelemetryProvider:
    def __init__(self, config_path: str | Path | None = None):
        self.started_at = time.time()
        self.config_path = Path(config_path or os.environ.get("DASHBOARD_CONFIG", DEFAULT_CONFIG))
        self.config = _load_json(self.config_path)
        self.mode = os.environ.get("DASHBOARD_MODE", self.config.get("mode", "mock"))
        telemetry_file = Path(
            os.environ.get("TELEMETRY_FILE", self.config.get("telemetry_file", str(DEFAULT_TELEMETRY_FILE)))
        )
        self.telemetry_file = telemetry_file if telemetry_file.is_absolute() else ROOT / telemetry_file
        self._last_cpu = self._read_cpu_times()
        self._last_cpu_at = time.time()
        self._rng = random.Random(2026)

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service": "unist_orachard_dashboard",
            "mode": self.mode,
            "uptime_s": round(time.time() - self.started_at, 1),
            "host": socket.gethostname(),
        }

    def telemetry(self) -> dict[str, Any]:
        external = _load_json(self.telemetry_file) if self.telemetry_file.exists() else {}
        now = time.time()
        base = self._mock_telemetry(now)
        if external:
            base = self._deep_merge(base, external)
        base["system"].update(self._system_status())
        base["meta"] = {
            "generated_at": int(now),
            "mode": self.mode,
            "telemetry_file": str(self.telemetry_file),
            "project": "unist_orachard_dashboard",
        }
        return base

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
                {"month": "6월", "label": "구동 시스템 설계, 센서 요구사항 정의", "status": "in_progress"},
                {"month": "7월", "label": "자율주행 자동차 제작, 회로 제작 및 센서 보정", "status": "planned"},
                {"month": "8월", "label": "예측 모델 구축, 추가 개선 장비 구매", "status": "planned"},
                {"month": "9월", "label": "주행 테스트 및 성능 검증", "status": "planned"},
                {"month": "10월", "label": "실험 데이터 분석, 전문가 피드백", "status": "planned"},
                {"month": "11월", "label": "논문 작성, 학회 참가", "status": "planned"},
                {"month": "12월", "label": "결과물 제출 및 최종성과발표회", "status": "planned"},
            ],
            "roles": [
                {"name": "나현준", "role": "연구 총괄", "task": "PM, 일정관리, LiDAR 기반 Detection 모델 설계"},
                {"name": "최영준", "role": "하드웨어 제작", "task": "하드웨어 시스템 설계, 모터·배터리·배선 구성"},
                {"name": "최용오", "role": "ROS/제어", "task": "ROS2 환경 구축, 모터 컨트롤, teleoperation"},
                {"name": "문동욱", "role": "알고리즘", "task": "이미지 Detection 모델, 센서 가중치 알고리즘"},
                {"name": "김성헌", "role": "센서/SLAM", "task": "LiDAR, RealSense, IMU, sensor calibration"},
            ],
        }

    def _mock_telemetry(self, now: float) -> dict[str, Any]:
        t = now - self.started_at
        phase = t / 17.0
        robot_x = -4.0 + (math.sin(phase) + 1.0) * 15.0
        lane_index = int((t / 35.0) % 3)
        lane_y = [-3.0, 0.0, 3.0][lane_index]
        lidar_rel = 0.78 + 0.14 * math.sin(t / 11.0)
        vision_rel = 0.70 + 0.18 * math.sin(t / 13.0 + 0.6)
        center_err = 0.06 + abs(0.12 * math.sin(t / 9.0))
        tracking_err = 0.08 + abs(0.10 * math.cos(t / 12.0))

        return {
            "system": {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "power_mode": "future_jetson_orin_nano",
                "throttle_state": "not_available",
            },
            "rover": {
                "mode": "SIMULATION_READY",
                "autonomy_enabled": False,
                "state": "IDLE",
                "pose": {"x": round(robot_x, 2), "y": lane_y, "yaw_deg": round(math.sin(phase) * 7.0, 1)},
                "speed_mps": round(0.28 + 0.05 * math.sin(t / 7.0), 2),
                "battery_percent": round(82.0 - (t % 2000) / 120.0, 1),
                "motor_state": "standby",
                "estop": False,
            },
            "sensors": {
                "lidar": {"ok": True, "topic": "/scan", "rate_hz": 10.0, "model": "RPLIDAR S2 planned"},
                "camera": {"ok": True, "topic": "/camera/image_raw", "rate_hz": 15.0, "mode": "fake_segmentation"},
                "imu": {"ok": True, "topic": "/imu/data", "rate_hz": 50.0},
                "encoder": {"ok": False, "topic": "/wheel/encoder", "rate_hz": 0.0, "mode": "future"},
                "mavlink": {"ok": False, "mode": "future_long_range_telemetry"},
            },
            "fusion": {
                "lidar_reliability": round(max(0.0, min(1.0, lidar_rel)), 2),
                "vision_reliability": round(max(0.0, min(1.0, vision_rel)), 2),
                "lidar_weight": round(lidar_rel / max(lidar_rel + vision_rel, 0.01), 2),
                "vision_weight": round(vision_rel / max(lidar_rel + vision_rel, 0.01), 2),
                "centerline_error_m": round(center_err, 3),
                "path_tracking_error_m": round(tracking_err, 3),
                "row_detection_count": 4,
            },
            "experiment": {
                "session_id": "mock-session-001",
                "runtime_s": round(t, 1),
                "distance_m": round(t * 0.27, 1),
                "rows_completed": lane_index,
                "map_coverage_percent": round(min(100.0, 12.0 + t / 8.0), 1),
                "weather_tag": "simulation",
                "gps_dependency": "low",
            },
            "map": self._mock_map(robot_x, lane_y),
            "alerts": self._mock_alerts(lidar_rel, vision_rel),
        }

    def _mock_map(self, robot_x: float, robot_y: float) -> dict[str, Any]:
        trees = []
        for y in [-4.5, -1.5, 1.5, 4.5]:
            x = -4.0
            while x <= 26.0:
                trees.append({"x": round(x + 0.08 * math.sin(x + y), 2), "y": round(y + 0.05 * math.cos(x), 2)})
                x += 2.0
        centerline = [{"x": x, "y": robot_y} for x in [robot_x + i * 0.5 for i in range(16)]]
        path = [
            {"x": -4.0, "y": -3.0},
            {"x": 26.0, "y": -3.0},
            {"x": 26.0, "y": 0.0},
            {"x": -4.0, "y": 0.0},
            {"x": -4.0, "y": 3.0},
            {"x": 26.0, "y": 3.0},
        ]
        clutter = [
            {"x": 3.5, "y": -2.7},
            {"x": 9.0, "y": 0.4},
            {"x": 17.5, "y": 3.3},
            {"x": 21.5, "y": -3.2},
        ]
        return {
            "frame": "map",
            "bounds": {"x_min": -8.0, "x_max": 30.0, "y_min": -7.0, "y_max": 7.0},
            "robot": {"x": round(robot_x, 2), "y": robot_y, "yaw_deg": 0.0},
            "trees": trees,
            "clutter": clutter,
            "centerline": centerline,
            "route": path,
        }

    def _mock_alerts(self, lidar_rel: float, vision_rel: float) -> list[dict[str, str]]:
        alerts = []
        if lidar_rel < 0.55:
            alerts.append({"level": "warn", "message": "LiDAR 신뢰도 저하 구간입니다."})
        if vision_rel < 0.55:
            alerts.append({"level": "warn", "message": "Vision 신뢰도 저하 구간입니다."})
        alerts.append({"level": "info", "message": "REAL 모드는 아직 placeholder입니다."})
        return alerts

    def _system_status(self) -> dict[str, Any]:
        memory = self._memory_percent()
        disk = shutil.disk_usage("/")
        status = {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "uptime_s": round(time.time() - self.started_at, 1),
            "os_uptime_s": self._os_uptime_s(),
            "cpu_percent": self._cpu_percent(),
            "gpu_percent": self._gpu_percent(),
            "memory_percent": memory,
            "disk_percent": round((disk.used / disk.total) * 100.0, 1),
            "temperature_c": self._temperature_c(),
            "loadavg": os.getloadavg() if hasattr(os, "getloadavg") else [0.0, 0.0, 0.0],
        }
        jetson_release = _read_text("/etc/nv_tegra_release")
        if jetson_release:
            status["jetson_release"] = jetson_release
        return status

    def _cpu_percent(self) -> float:
        current = self._read_cpu_times()
        now = time.time()
        if not current or not self._last_cpu:
            return 0.0
        idle_delta = current["idle"] - self._last_cpu["idle"]
        total_delta = current["total"] - self._last_cpu["total"]
        self._last_cpu = current
        self._last_cpu_at = now
        if total_delta <= 0:
            return 0.0
        return round(max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta))), 1)

    def _read_cpu_times(self) -> dict[str, int] | None:
        text = _read_text("/proc/stat")
        if not text:
            return None
        first = text.splitlines()[0].split()
        if not first or first[0] != "cpu":
            return None
        values = [int(value) for value in first[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return {"idle": idle, "total": sum(values)}

    def _memory_percent(self) -> float:
        text = _read_text("/proc/meminfo")
        if not text:
            return 0.0
        values = {}
        for line in text.splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0])
        total = values.get("MemTotal", 1)
        available = values.get("MemAvailable", 0)
        return round(100.0 * (1.0 - available / total), 1)

    def _os_uptime_s(self) -> float | None:
        text = _read_text("/proc/uptime")
        if not text:
            return None
        try:
            return round(float(text.split()[0]), 1)
        except (IndexError, ValueError):
            return None

    def _gpu_percent(self) -> float | None:
        candidates = [Path("/sys/devices/gpu.0/load")]
        candidates.extend(Path("/sys/devices/platform").glob("*gpu*/load"))
        candidates.extend(Path("/sys/devices/platform").glob("*.gpu/load"))
        for path in candidates:
            raw = _read_text(path)
            if raw and raw.lstrip("-").isdigit():
                value = int(raw)
                return round(value / 10.0 if value > 100 else float(value), 1)
        return None

    def _temperature_c(self) -> float | None:
        thermal_root = Path("/sys/class/thermal")
        temps = []
        if thermal_root.exists():
            for path in thermal_root.glob("thermal_zone*/temp"):
                raw = _read_text(path)
                if raw and raw.lstrip("-").isdigit():
                    value = int(raw)
                    temps.append(value / 1000.0 if abs(value) > 200 else float(value))
        if not temps:
            return None
        return round(max(temps), 1)

    def _deep_merge(self, base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        result = dict(base)
        for key, value in extra.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
