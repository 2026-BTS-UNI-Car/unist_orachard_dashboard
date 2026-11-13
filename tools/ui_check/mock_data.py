"""화면 점검용 모의 응답 자료.

모든 값은 지어낸 것이다. 실제 장치 경로, 주소, 위치 좌표를 쓰지 않는다.
- 주소는 문서 예시용 대역(192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32)만 쓴다.
- 위치는 (0.000100, 0.000200) 한 점만 쓴다.

자료 묶음은 두 가지다.
- empty : 뒤쪽 서비스가 아무것도 연결되지 않은 상태. 응답은 오지만 내용이 비어 있다.
- stress: 공백 없는 80자 이상 장치 경로, 긴 이름, 많은 목록 줄, 큰 수.
"""
from __future__ import annotations

import base64
import math
import time

SCHEMA = "ubts.ui_check.mock_data"
SCHEMA_VERSION = 1
DATASETS = ("empty", "stress")

# 공백 없는 80자 이상 문자열. 줄바꿈 규칙이 없는 칸을 찾아내기 위한 값이다.
LONG_MOTOR_DEVICE = (
    "/dev/serial/by-id/usb-MOCK_VENDOR_FAKE_USB_UART_BRIDGE_FOR_UI_LAYOUT_CHECK_SERIAL_000000000001-if00-port0"
)
LONG_LIDAR_DEVICE = (
    "/dev/serial/by-id/usb-MOCK_VENDOR_FAKE_LIDAR_SERIAL_ADAPTER_FOR_UI_LAYOUT_CHECK_SERIAL_000000000002-if00-port0"
)
LONG_CAMERA_DEVICE = (
    "/dev/v4l/by-id/usb-MOCK_VENDOR_FAKE_UVC_CAMERA_FOR_UI_LAYOUT_CHECK_SERIAL_000000000003-video-index0"
)
LONG_HOSTNAME = "mock-rover-host-with-a-very-long-hostname-for-ui-layout-check-0001"
LONG_SSID = "MOCK_FIELD_ROUTER_SSID_32_CHARS__"[:32]
FAKE_LATITUDE = 0.000100
FAKE_LONGITUDE = 0.000200

# 웹소켓과 지도 묶음을 흉내 내지 않아 자료 없이 남는 패널. 실행 결과 JSON에 그대로 적는다.
PANELS_WITHOUT_DATA = {
    "empty": [
        "LiDAR 극좌표(/sensors, /rover-control): 웹소켓은 연결만 받고 자료를 보내지 않음(스캔 대기 화면)",
        "카메라 실시간(/sensors, /rover-control): 영상 프레임 없음",
        "IMU 자세(/rover-control): 표본 없음",
        "GPS 수치와 지도(/rover-control): 표본 없음, 오프라인 지도 묶음 없음",
        "실시간 점유 격자 지도(/rover-control): 유휴 상태, 셀 없음",
        "기록 목록과 재생 패널(/rover-control): 기록 0개",
        "컨테이너 목록·상세·로그(/docker): Docker 사용 불가 응답",
        "문서 목록과 본문(/docs): 문서 0개",
            ],
    "stress": [
        "LiDAR 극좌표(/sensors, /rover-control): 스캔을 보내지 않음(스캔 대기 화면). 장치 경로 같은 글자 칸은 채워짐",
        "카메라 실시간(/sensors, /rover-control): 이진 프레임 규약을 흉내 내지 않아 영상 없음",
        "GPS 지도(/rover-control): 오프라인 지도 묶음(/maps/*)이 없어 지도 타일 없음. 수치는 채워짐",
        "실시간 점유 격자 지도(/rover-control): 유휴 상태, 셀 없음",
        "기록 재생 패널(/rover-control): 기록을 고르지 않아 재생 LiDAR·영상·기록 지도·IMU·GPS·모터 표 비어 있음",
        "관찰용 센서 누적 지도(/rover-control): 닫힌 접힘 상자 안, 자료 없음",
        "매핑 작업 관리·영상 변환 작업(/rover-control): 닫힌 접힘 상자라 요청하지 않음",
    ],
}

# 자료 묶음마다 표본을 보내는 웹소켓 경로와 보내는 간격(초).
# LiDAR는 기본으로 보내지 않는다. 스캔이 오지 않는 '스캔 대기' 화면을 재기 위해서다.
# 스캔이 계속 오면 캔버스가 그때마다 다시 그려져, 틀 크기를 따라가지 않는 문제가 가려진다.
WEBSOCKET_FEEDS = {"empty": (), "stress": ("imu", "gps")}
WEBSOCKET_PERIOD_S = {"lidar": 1.0, "imu": 0.2, "gps": 1.0}
WEBSOCKET_CHANNELS = ("lidar", "camera", "imu", "gps")

WEBSOCKET_BEHAVIOR = {
    "empty": "네 경로(/ws/lidar, /ws/camera, /ws/imu, /ws/gps) 모두 연결만 받고 아무것도 보내지 않음",
    "stress": "/ws/imu는 0.2초마다, /ws/gps는 1초마다 지어낸 표본 하나를 다시 보냄. /ws/lidar와 /ws/camera는 연결만 받고 보내지 않음",
}


def _stress_services() -> dict:
    services = {
        "dashboard": {"ok": True, "state": "running", "host": "127.0.0.1", "port": 8080},
        "cloudflared": {"ok": False, "state": "waiting_for_tunnel_registration_retry_scheduled", "host": LONG_HOSTNAME},
        "ros2_bridge": {"ok": True, "state": "running", "host": "127.0.0.1", "port": 8765},
        "lidar_bridge": {"ok": True, "state": "scan_topic_alive", "host": "127.0.0.1", "port": 8765},
        "camera_bridge": {"ok": True, "state": "frame_topic_alive", "host": "127.0.0.1", "port": 8770},
        "imu_bridge": {"ok": True, "state": "topic_alive", "host": "127.0.0.1", "port": 8775},
        "rover_bridge": {
            "ok": True,
            "state": "ready",
            "serial_open": True,
            "serial_port": LONG_MOTOR_DEVICE,
            "armed": False,
            "mode": "esp_manual",
            "automatic_input_enabled": False,
        },
        "data_recorder": {"ok": True, "state": "idle", "host": "127.0.0.1", "port": 8790},
    }
    for index in range(1, 5):
        services[f"mock_extra_service_with_long_name_{index:02d}"] = {
            "ok": index % 2 == 0,
            "state": "waiting_for_upstream_connection_and_retrying",
            "host": f"mock-service-host-{index:02d}.example.invalid",
            "port": 60000 + index,
        }
    return services


def _stress_sensors() -> dict:
    sensors = {
        "lidar": {
            "ok": True,
            "topic": "/scan",
            "frame_id": "laser",
            "state": "scan_topic_alive",
            "rate_hz": 10.0,
            "owner": "ros2-lidar-driver",
            "dashboard_access": "subscribe_only",
        },
        "camera": {
            "ok": True,
            "topic": "/camera/image_raw/compressed",
            "frame_id": "camera_link_optical",
            "state": "frame_topic_alive",
            "rate_hz": 29.7,
            "dashboard_access": "subscribe_only",
        },
        "imu": {"ok": True, "topic": "/imu/data_raw", "frame_id": "imu_link", "state": "topic_alive", "rate_hz": 32.9},
        "gps": {"ok": False, "topic": "/gps/fix", "state": "no_fix", "severity": "waiting"},
        "encoder": {"ok": False, "topic": "/mock/encoder/not_installed", "state": "not_found", "severity": "error"},
    }
    for index in range(1, 5):
        sensors[f"mock_sensor_{index:02d}"] = {
            "ok": index % 2 == 1,
            "topic": f"/mock/namespace/with/a/long/topic/name/for/layout/check/sensor_{index:02d}/data_raw",
            "frame_id": f"mock_sensor_{index:02d}_optical_frame_link",
            "link": f"mock_link_{index:02d}",
            "owner": f"mock-driver-container-with-long-name-{index:02d}",
            "state": "waiting_for_ros2",
            "severity": "waiting",
            "rate_hz": 1234.5 if index % 2 == 1 else 0,
        }
    return sensors


def _stress_containers() -> list:
    names = [
        "ubts-dashboard",
        "ros2-lidar-driver",
        "ros2-camera-driver",
        "ros2-lidar-bridge",
        "ros2-camera-bridge",
        "ros2-imu-driver",
        "ros2-imu-bridge",
        "ros2-esp-rover-bridge",
        "ros2-gps-driver",
        "ros2-gps-bridge",
        "ros2-data-recorder",
    ]
    names += [f"mock-container-with-a-very-long-name-for-layout-check-{index:02d}" for index in range(1, 30)]
    rows = []
    for index, name in enumerate(names):
        running = index % 7 != 6
        rows.append(
            {
                "id": f"mock{index:04d}",
                "name": name,
                "image": f"registry.example.invalid/mock-organization/mock-project/{name}:20990101-build-{index:04d}",
                "state": "running" if running else "exited",
                "status": "Up 999 days (healthy)" if running else "Exited (137) 999 days ago",
                "cpu_percent": round(3.5 + index * 31.7, 1),
                "memory_usage": (120 + 4096 * index) * 1048576,
            }
        )
    return rows


def _stress_telemetry(now: float) -> dict:
    services = _stress_services()
    sensors = _stress_sensors()
    containers = _stress_containers()
    cores = [{"name": f"cpu{index}", "percent": round(8.0 + index * 7.9, 1)} for index in range(12)]
    disks = [
        {
            "mount": "/",
            "source": "/dev/mock-nvme0n1p1",
            "fstype": "ext4",
            "used": 9.87e14,
            "free": 1.3e11,
            "total": 9.9e14,
            "percent": 99.7,
        }
    ]
    for index in range(1, 6):
        disks.append(
            {
                "mount": f"/mnt/mock-volume-with-a-long-mount-point-name/recordings/archive-{index:04d}",
                "source": f"/dev/disk/by-id/usb-MOCK_VENDOR_FAKE_STORAGE_FOR_UI_LAYOUT_CHECK_SERIAL_{index:012d}-part1",
                "fstype": "ext4",
                "used": 4.5e11 * index,
                "free": 1.0e9,
                "total": 4.6e11 * index,
                "percent": 97.8,
            }
        )
    interfaces = []
    for index in range(6):
        interfaces.append(
            {
                "name": f"mockiface{index}withlongname",
                "state": "up",
                "ipv4": [f"192.0.2.{10 + index}", f"198.51.100.{10 + index}", f"203.0.113.{10 + index}"],
                "speed_mbps": 100000,
                "rx_bps": 8.5e11 + index,
                "tx_bps": 9.5e11 + index,
            }
        )
    return {
        "meta": {"generated_at": now},
        "system": {
            "hostname": LONG_HOSTNAME,
            "os_uptime_s": 86399999,
            "temperature_c": 104.9,
            "loadavg": [123.45, 98.76, 54.32],
            "cpu": {"total_percent": 100.0, "cores": cores},
            "memory": {"used": 9.87e14, "total": 9.99e14, "available": 1.2e13, "percent": 98.8},
            "swap": {"percent": 100.0, "used": 9.9e13, "total": 9.9e13, "free": 0},
            "power": {"total_w": 1234.56, "source": "mock_power_monitor_with_long_source_name", "available": True, "rails": [{}] * 12},
            "temperatures": [],
            "disks": disks,
        },
        "network": {
            "external_ip": "2001:db8:ffff:ffff:ffff:ffff:ffff:fff1",
            "primary_ipv4": "192.0.2.123",
            "primary_interface": "mockiface0withlongname",
            "hostname": LONG_HOSTNAME,
            "total_rx_bps": 8.5e12,
            "total_tx_bps": 9.5e12,
            "interfaces": interfaces,
            "control": {
                "available": True,
                "ok": True,
                "ap_active": False,
                "wifi_connected": True,
                "ssid": LONG_SSID,
                "connection": LONG_SSID,
                "ap_ssid": "MOCK_AP_SSID_FOR_LAYOUT_CHECK_32",
                "ap_address": "192.0.2.1",
                "ap_clients": 250,
                "seconds_until_auto_switch": 86399,
                "can_request_ap": True,
                "can_request_wifi": True,
                "last_error": "mock: 저장된 Wi-Fi를 찾지 못해 AP 방식으로 되돌아갔습니다(점검용으로 지어낸 긴 오류 문장)",
            },
        },
        "docker": {
            "available": True,
            "summary": {
                "running": sum(1 for row in containers if row["state"] == "running"),
                "total": len(containers),
                "stopped": sum(1 for row in containers if row["state"] != "running"),
                "cpu_percent": 1234.5,
                "memory_usage": 9.5e11,
                "memory_limit": 9.99e11,
            },
            "engine": {"images": 12345, "server_version": "99.99.99-mock+build.20990101"},
            "containers": containers,
        },
        "fan": {
            "available": True,
            "pwm": 255,
            "temperature_c": 104.9,
            "mode": "manual",
            "safety": {"level": "critical"},
            "desired": {"mode": "manual", "pwm": 255},
        },
        "connection": {
            "lidar": {
                "present": True,
                "state": "in_use",
                "device": LONG_LIDAR_DEVICE,
                "by_id": LONG_LIDAR_DEVICE,
                "driver_hint": "cp210x",
                "ok": True,
                "last_test": {
                    "ok": True,
                    "state": "ros2_owned",
                    "message": "mock: 장치 표시 확인 · ROS2 드라이버가 포트를 쓰고 있음 · " + LONG_LIDAR_DEVICE,
                },
            },
            # 실제 서버(telemetry.camera_status)는 캡처 설정을 늘 함께 보낸다. 화면은 이 값을 받은 뒤에만
            # 해상도·프레임 수를 사실로 보여 준다. empty 묶음에는 일부러 넣지 않는다('--' 상태를 보기 위해).
            "camera": {
                "present": True,
                "state": "detected",
                "device": LONG_CAMERA_DEVICE,
                "ok": True,
                "settings": {"resolution": "1280x720", "width": 1280, "height": 720, "fps": 60.0, "fourcc": "MJPG", "source": "mock"},
            },
            "lidar_bridge": {"ok": True, "scan_topic_alive": True},
            "camera_bridge": {"ok": True, "frame_topic_alive": True, "source_width": 1920, "source_height": 1080, "source_fps": 29.97},
            "lidar_websocket": {"active": 99},
            "camera_websocket": {"active": 99},
            "services": services,
            "sensors": sensors,
            "rover": {"state": "disarmed", "mode": "esp_manual"},
        },
        "rover": {"state": "disarmed", "mode": "esp_manual"},
        "sensors": sensors,
        "services": services,
        "experiment": {"state": "idle"},
        "alerts": [],
    }


def _empty_telemetry(now: float) -> dict:
    # 서버 자신은 살아 있으므로 system/network의 틀은 채운다. 연결된 장치와 서비스만 없다.
    return {
        "meta": {"generated_at": now},
        "system": {
            "hostname": "mock-host",
            "os_uptime_s": 60,
            "temperature_c": None,
            "loadavg": [0.0, 0.0, 0.0],
            "cpu": {"total_percent": 0.0, "cores": []},
            "memory": {"used": 0, "total": 0, "available": 0, "percent": 0.0},
            "swap": {"percent": 0.0, "used": 0, "total": 0, "free": 0},
            "power": {},
            "temperatures": [],
            "disks": [],
        },
        "network": {"interfaces": [], "control": {}},
        "docker": {"available": False, "error": "mock: Docker 소켓 없음"},
        "fan": {"available": False},
        "connection": {},
        "rover": {},
        "sensors": {},
        "services": {},
        "experiment": {},
        "alerts": [],
    }


PREPARATION_CHECK_IDS = [
    "profile",
    "native_sources",
    "body_dimensions",
    "sensor_extrinsics",
    "planar_envelope",
    "body_velocity_feedback",
    "physical_emergency_stop",
    "braking",
    "october_source_review_complete",
    "september_orchard_review_complete",
    "current_vehicle_localization_validated",
    "live_latency_under_load_validated",
    "obstacle_and_unknown_space_stop_validated",
    "row_end_and_turn_space_validated",
    "manual_takeover_verified",
    "work_boundary_defined",
    "live_mapping_fresh",
]


def _stress_recordings() -> list:
    rows = []
    for index in range(30):
        suffix = "_mock_long_suffix_for_layout_check" if index % 2 else ""
        rows.append(
            {
                "id": f"20990101T{index:02d}0000Z_both{suffix}",
                "kind": "both",
                "state": "archived",
                "duration_s": 99999.9 - index,
                "started_at": f"2099-01-01T{index % 24:02d}:00:00Z",
                "clock_quality": "gnss_estimated" if index % 3 == 0 else "unverified",
                "timeline": {
                    "lidar_frames": 999999 - index,
                    "camera_frames": 2999999 - index,
                    "imu_frames": 3299999 - index,
                    "gps_frames": 399999 - index,
                },
            }
        )
    return rows


STRESS_DOCS = [
    {
        "slug": f"mock-document-{index:02d}",
        "title": f"점검용 문서 {index:02d} · 제목이 아주 길어서 목록 칸에서 줄이 바뀌는지 보는 문서",
        "category": ["Control", "Sensors", "Deployment", "Operations"][index % 4],
        "summary": "지어낸 요약 문장이다. 목록에서 둘째 줄로 넘어갈 만큼 길게 적는다. " + "가나다라마바사 " * 4,
        "source": f"docs/mock/long/path/for/layout/check/mock_document_{index:02d}.md",
        "url": f"/docs/mock-document-{index:02d}",
    }
    for index in range(1, 15)
]

STRESS_MARKDOWN = "\n".join(
    [
        "# 점검용 문서",
        "",
        "이 글은 화면 점검용으로 지어낸 것이다. 문단이 여러 줄로 넘어가도록 길게 적는다. " * 6,
        "",
        "## 긴 주소와 긴 낱말",
        "",
        "공백 없는 긴 낱말: " + "MOCK_WORD_WITHOUT_SPACES_" * 6,
        "",
        "[아주 긴 연결 글자](https://example.invalid/" + "long-path-segment/" * 8 + "index.html)",
        "",
        "## 코드",
        "",
        "```bash",
        "mock-command --device " + LONG_MOTOR_DEVICE + " --option-with-a-long-name=value --another-option=another-value",
        "```",
        "",
        "## 표",
        "",
        "| 항목 | 값 | 단위 | 설명 | 출처 | 비고 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    + [
        f"| mock_item_{index:02d} | {123456.789 * (index + 1):.3f} | mock_unit | 지어낸 설명 문장 {index:02d} | mock/source/path/{index:02d} | {LONG_HOSTNAME} |"
        for index in range(12)
    ]
    + ["", "- 목록 첫째 줄", "- 목록 둘째 줄: " + "긴 설명 " * 20, ""]
)


def _steer_rover_status(dataset: str, now: float) -> dict:
    """GET /api/rover/status of a front-steer bridge (V3 snapshot with the steering block, docs/STEERING_SERVER_KO.md 4).
    empty: no bridge (the dashboard's reply when the control socket is missing)."""
    if dataset == "empty":
        return {"ok": False, "connected": False, "state": "unavailable", "error": "motor_bridge_unavailable"}
    return {
        "ok": True, "state": "fault", "connected": True, "armed": False, "moving": False, "reason": "mock_steering_not_following",
        "input_ttl_s": 0.75, "automatic_input_enabled": False, "last_error": "mock: 점검용으로 지어낸 아주 긴 오류 문장",
        "updated_at": now, "vehicle_profile": "front_steer", "steer_limit_mrad": {"left": 409, "right": 380},
        "requested": {"throttle": 0, "steer_mrad": 0, "left": 0, "right": 0},
        "outputs": {"D8": 0, "D5": 0, "D3": 0, "D0": 0, "D1": 0, "D2": 0, "D6": 0, "D7": 0},
        "firmware": "UBTS_STEER_V3", "protocol": 3, "boot_id": "89025CC1", "device_id": "5A1B2C", "reset_reason": "power_on",
        "hw_profile": "front_steer_v0", "simulated": True, "profile": "front_steer", "cal_id": 1,
        "steering": {"measured_mrad": -152, "measured_permille": -347, "raw": 401, "target_permille": -320, "cmd_permille": -320,
                     "pwm": 0, "mcu_ms": 123456, "seq": 789, "applied_q": 12, "age_ms": 20, "ttl_remaining_ms": 0,
                     "drive_en": False, "steer_en": False, "hold_remaining_ms": None, "stop_mode": "brake",
                     "faults": ["SOFTLIMIT", "POT_NOISE", "steering_not_following", "mock_unknown_fault_with_a_long_name"],
                     "servo_state": "off", "saturated": True, "calibrated": True},
        "steering_host": {"arm_state": "none", "stop_state": "confirmed", "mcu_state": "DISARMED", "fl": "0110", "stale": False,
                          "target_mrad": None, "latches": {}},
        "transport": "simulation", "serial_port": "mock", "serial_open": True, "mode": "manual",
        "control_page": "/rover-control",
    }


def _executor_status(dataset: str, now: float) -> dict:
    """GET /api/executor/status of the dashboard's executor relay (docs/STEERING_SERVER_KO.md 6).
    empty: no executor configured; stress: a paused run with every go blocker and long values."""
    if dataset == "empty":
        return {"ok": False, "configured": False, "reachable": False, "error": "executor_not_configured", "received_at": now}
    return {"ok": True, "configured": True, "reachable": True, "received_at": now, "status": {
        "run_state": "paused", "start_mode": "one_press", "mode": "MOTOR", "state": "PAUSED",
        "stop_reason": "OPERATOR_LINK_LOST", "stop_detail": "operator_link_lost_mock_reason_with_a_long_identifier_and_no_spaces",
        "go_ready": False, "go_blockers": ["session_conflict", "operator_link_not_fresh", "no_run", "already_running", "latched",
                                           "arm_first", "inputs_not_fresh", "steering_feedback", "vehicle_moving",
                                           "corridor_not_acquired", "mock_new_blocker_with_a_long_identifier"],
        "operator_link": {"fresh": False, "age_s": 12345.678, "timeout_s": 0.5},
        "speed": {"target_mps": 0.4, "limit_mps": 0.123, "limit_reason": "mock_limit_reason_with_a_long_identifier"},
        "tuning": {"preset": "mock_preset_with_a_long_name*", "revision": 12345, "editable": False,
                   "not_editable_because": "tuning_locked_while_running", "presets": ["default"], "writable": False},
        "local_grid_map": _local_grid(),
        "single_row": {"phase": "WAIT", "lateral_error_m": -0.1234, "heading_error_deg": 12.345, "distance_m": 98765.4,
                       "speed_m_s": 0.0, "target_speed_m_s": 0.4, "stop_category": "OPERATOR_OR_BACKSTOP",
                       "confidence": {"level": "LOW"}},
        "gnss": {"state": "no_fix", "fix": False, "age_s": 3.2, "h_acc_m": 123.4, "used": False},
        "bridge_detail": {"state": "LATCHED", "latch_reason": "mock_latch_reason_with_a_long_identifier"},
    }}


def _tuning_view(dataset: str) -> dict:
    """GET /api/executor/tuning (docs/STEERING_SERVER_KO.md 7, the executor's field tuning bundle).
    empty: no executor; stress: every group, a speed table, an unknown long key, long preset names."""
    if dataset == "empty":
        return {"ok": True, "schema": "ubts.executor_tuning.v1", "configured": False, "reachable": False,
                "error": "executor_not_configured", "run_state": None, "available": False, "applicable": False,
                "why_not": "executor_not_configured", "tuning": {}, "params": []}
    basic = [("speed_scale", "속도 배율(k_field)", "", 0.5, 1.3, 1.0), ("target_speed_m_s", "목표 속도", "m/s", 0.2, 0.5, 0.4),
             ("lookahead_base_m", "주시거리 바닥", "m", 0.8, 3.0, 1.5), ("lookahead_per_speed_s", "속도당 주시거리", "s", 0.0, 3.0, 1.25),
             ("lookahead_max_m", "주시거리 상한", "m", 1.0, 5.0, 3.0), ("k_d", "횡오차 이득(k_d)", "", 0.0, 3.0, 1.0),
             ("k_psi", "방향 이득(k_psi)", "", 0.0, 3.0, 1.0), ("steer_rate_fraction", "조향 변화 속도 비율", "", 0.2, 1.0, 0.75),
             ("mock_new_basic_key_with_a_long_name", "mock_new_basic_key_with_a_long_name", "rad/(m·s)", 0, 12345, 6789)]
    params = [{"name": name, "group": "basic", "label": label, "unit": unit, "min": low, "max": high,
               "step": 1 if high - low > 20 else 0.01, "value": value, "editable": True, "editable_when_stopped": True}
              for name, label, unit, low, high, value in basic]
    fixed = [("speed_table", "advanced", "duty→속도 표", "[duty %, m/s]",
              [[25.0, 0.239], [40.0, 0.41], [55.0, 0.55], [70.0, 0.66], [80.0, 0.709], [90.0, 0.715], [100.0, 0.899]]),
             ("speed_kp_pct_s_per_m", "advanced", "속도 PI 비례 이득", "%/(m/s)", 30.0),
             ("lateral_accel_max_m_s2", "advanced", "횡가속도 상한", "m/s²", 0.3),
             ("trim_gain_rad_per_m_s", "advanced", "조향 trim 이득", "rad/(m·s)", 0.05),
             ("mock_unknown_advanced_key_with_a_long_name_and_no_label", "advanced",
              "mock_unknown_advanced_key_with_a_long_name_and_no_label", "", 12345.678),
             ("max_speed_m_s", "locked", "최고 속도", "m/s", 0.5), ("duty_cap_pct", "locked", "출력 가드 duty 상한", "%", 75),
             ("operator_timeout_s", "locked", "화면 연결 끊김 정지", "s", 0.5), ("guard_heading_deg", "locked", "방향 가드", "°", 25.0)]
    params += [{"name": name, "group": group, "label": label, "unit": unit, "min": None, "max": None, "step": None, "value": value,
                "editable": False, "editable_when_stopped": False} for name, group, label, unit, value in fixed]
    return {"ok": True, "schema": "ubts.executor_tuning.v1", "configured": True, "reachable": True, "error": None,
            "run_state": "idle", "available": True, "applicable": True, "why_not": None,
            "tuning": {"preset": "mock_preset_with_a_long_name_for_layout_check*", "revision": 12345, "modified": True,
                       "presets": ["default", "slow_030", "soft_ground", "mock_preset_with_a_long_name_for_layout_check"],
                       "writable": True, "unsaved": True},
            "params": params}


def _local_grid() -> dict:
    """status.local_grid_map of the executor (ubts.row_executor.local_grid.v1, LOCAL_GRID_KO.md): two tree rows along the
    grid axis, 200 x 100 cells of 0.1 m, a bitmap. Synthetic, run odometry frame (not a map, no GNSS)."""
    ni, nj = 200, 100
    occupied = set()
    for i in range(4, ni - 4, 25):                    # trunks every 2.5 m, rows 3.4 m apart
        for di in (0, 1):
            for j in (32, 33, 66, 67):
                occupied.add((i + di, j))
    for i in range(0, ni, 7):                          # leaves and branches beside the rows
        occupied.add((i, 30 + i % 3))
        occupied.add((i, 69 - i % 3))
    bits = bytearray((ni * nj + 7) // 8)
    for i, j in occupied:
        index = i * nj + j
        bits[index // 8] |= 0x80 >> (index % 8)     # numpy.packbits order: big end first
    axis = 0.10472
    return {"schema": "ubts.row_executor.local_grid.v1", "frame": "run_odometry", "updates": 98765, "axis_rad": axis,
            "origin_uv": [-4.0, -4.9], "occupied": len(occupied), "encoding": "bitmap", "age_s": 0.12, "cell_m": 0.1,
            "shape": [ni, nj], "bits": base64.b64encode(bytes(bits)).decode("ascii"), "truncated": False,
            "lines": {"left": [[-1.751, 1.66], [12.216, 2.633]], "right": [[-1.505, -1.872], [12.462, -0.899]],
                      "centre": [[-1.628, -0.106], [12.339, 0.867]], "quality": 1.0, "stamp": 5.9},
            "base_pose": [2.4, 0.3, 0.09]}


def _tuning_log(dataset: str, now: float) -> dict:
    if dataset == "empty":
        return {"ok": True, "entries": []}
    return {"ok": True, "entries": [
        {"at": now - index * 60, "by": "abcdefgh", "ok": index % 3 != 0,
         "error": None if index % 3 else "out_of_range_mock_reason_with_a_long_identifier",
         "action": "set" if index % 4 else "load_preset", "target": None if index % 4 else "mock_preset_with_a_long_name",
         "changes": {"target_speed_m_s": [0.4, 0.35], "lookahead_base_m": [1.5, 2.5], "mock_unknown_parameter_with_a_long_name": [1, 2]},
         "revision_before": 12345 - index, "revision_after": 12346 - index}
        for index in range(50)]}


def http_payloads(dataset: str, now: float | None = None) -> dict:
    """정확히 일치하는 GET 경로 -> JSON 응답."""
    if dataset not in DATASETS:
        raise ValueError(f"unknown dataset: {dataset}")
    now = time.time() if now is None else now
    if dataset == "empty":
        return {
            "/api/health": {"ok": True, "mock": True},
            "/api/telemetry": _empty_telemetry(now),
            "/api/plan": {"milestones": []},
            "/api/docs": {"documents": []},
            "/api/recordings": {"recordings": []},
            "/api/recordings/status": {"ok": False, "state": "unavailable", "error": "mock: 기록 서비스 연결 없음"},
            "/api/recordings/media-jobs": {"ok": True, "jobs": []},
            "/api/live/map": {"ok": True, "state": "idle", "active": False, "map_epoch": "mock-empty", "version": 0, "scan_count": 0},
            "/api/live/mapping-jobs": {"ok": True, "resources": {}, "jobs": []},
            "/api/autonomy": {"ok": True, "speed_percent": 0, "message": "mock: 실차 자동 구동 보류", "preparation": None},
            "/api/autonomy/observe": {
                "ok": True,
                "runtime_authority": False,
                "motor_command_sent": False,
                "autonomy_output_enabled": False,
                "active": False,
                "state": "idle",
                "current_decision": {},
            },
            "/api/maps/status": {"ok": False, "error": "mock: 오프라인 지도 파일 없음"},
            "/api/rover/status": _steer_rover_status(dataset, now),
            "/api/executor/status": _executor_status(dataset, now),
            "/api/executor/tuning": _tuning_view(dataset),
            "/api/executor/tuning/log": _tuning_log(dataset, now),
        }
    checks = [{"id": key, "passed": index % 3 == 0} for index, key in enumerate(PREPARATION_CHECK_IDS)]
    return {
        "/api/health": {"ok": True, "mock": True},
        "/api/telemetry": _stress_telemetry(now),
        "/api/plan": {
            "milestones": [
                {
                    "month": f"2099-{index:02d}",
                    "label": f"점검용 수행계획 항목 {index:02d} · " + "아주 긴 설명이 이어진다 " * 5,
                    "status": ["done", "in_progress", "planned"][index % 3],
                }
                for index in range(1, 13)
            ]
        },
        "/api/docs": {"documents": STRESS_DOCS},
        "/api/recordings": {"recordings": _stress_recordings()},
        "/api/recordings/status": {"ok": True, "status_fresh": True, "active": False, "state": "idle", "archive_count": 123456},
        "/api/recordings/media-jobs": {
            "ok": True,
            "jobs": [
                {
                    "recording_id": f"20990101T{index:02d}0000Z_both_mock_long_suffix_for_layout_check",
                    "state": ["running", "queued", "paused", "failed"][index % 4],
                    "stage": "encoding_adaptive_video",
                    "reason": "recorded_mapping_active",
                }
                for index in range(8)
            ],
        },
        "/api/live/map": {
            "ok": True,
            "state": "idle",
            "active": False,
            "map_epoch": "mock-stress",
            "version": 0,
            "scan_count": 0,
            "reason": "mock: 점검용으로 지어낸 긴 사유 문장 · 센서 동기화 대기 · 마지막 지도 유지 · 다시 시작하려면 새 지도를 누르세요",
        },
        "/api/live/mapping-jobs": {
            "ok": True,
            "resources": {"cpu_percent_one_core": 187.5, "rss_bytes": 3.2e9},
            "jobs": [
                {
                    "id": f"mock-job-{index}",
                    "kind": "archive",
                    "recording_id": f"20990101T{index:02d}0000Z_both_mock_long_suffix_for_layout_check",
                    "state": "localizing",
                    "active": index == 0,
                    "processed_t_s": 12345.6,
                    "requested_t_s": 99999.9,
                    "epoch": "mock",
                }
                for index in range(4)
            ],
        },
        "/api/autonomy": {
            "ok": True,
            "speed_percent": 30,
            "message": "mock: 실차 자동 구동 보류 · 점검용으로 지어낸 긴 안내 문장이 여기에 이어진다",
            "preparation": {"schema": "ubts.field_preflight.v1", "checks": checks},
        },
        "/api/autonomy/observe": {
            "ok": True,
            "runtime_authority": False,
            "motor_command_sent": False,
            "autonomy_output_enabled": False,
            "active": False,
            "state": "idle",
            "speed_percent": 30,
            "cruise_percent": 20,
            "first_failure_reason": "mock_reason_with_a_long_identifier_for_layout_check_and_no_spaces_inside_it",
            "current_decision": {"decision": "HOLD", "proposed_left_percent": 0, "proposed_right_percent": 0},
        },
        "/api/maps/status": {"ok": False, "error": "mock: 오프라인 지도 파일 없음"},
        "/api/rover/status": _steer_rover_status(dataset, now),
        "/api/executor/status": _executor_status(dataset, now),
        "/api/executor/tuning": _tuning_view(dataset),
        "/api/executor/tuning/log": _tuning_log(dataset, now),
    }


def doc_payload(dataset: str, slug: str) -> dict | None:
    if dataset != "stress":
        return None
    for item in STRESS_DOCS:
        if item["slug"] == slug:
            return {"ok": True, **item, "markdown": STRESS_MARKDOWN}
    return None


def container_detail(dataset: str, container_id: str) -> dict | None:
    if dataset != "stress":
        return None
    for row in _stress_containers():
        if row["id"] == container_id:
            return {
                "ok": True,
                "id": row["id"],
                "full_id": "0" * 52 + f"{int(container_id[4:]):012d}",
                "name": row["name"],
                "image": row["image"] + "@sha256:" + "0" * 64,
                "self": False,
                "network_mode": "host",
                "state": {"Running": row["state"] == "running", "Status": row["state"], "StartedAt": "2099-01-01T00:00:00Z", "ExitCode": 137},
                "restart_policy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "stats": {
                    "cpu_percent": row["cpu_percent"],
                    "memory_usage": row["memory_usage"],
                    "memory_limit": 9.99e11,
                    "memory_percent": 99.9,
                    "rx_bytes": 9.87e13,
                    "tx_bytes": 8.76e13,
                },
            }
    return None


def container_logs(dataset: str, container_id: str) -> dict | None:
    if container_detail(dataset, container_id) is None:
        return None
    lines = [
        f"2099-01-01T00:{index // 60:02d}:{index % 60:02d}Z mock log line {index:03d} "
        + ("MOCK_LOG_WORD_WITHOUT_SPACES_" * 6 if index % 10 == 0 else "지어낸 로그 문장이다 " * 3)
        for index in range(180)
    ]
    return {"ok": True, "lines": lines}


def websocket_message(channel: str, sequence: int, now: float | None = None) -> dict | None:
    """웹소켓 경로의 지어낸 표본 하나. 카메라처럼 흉내 내지 않는 경로는 None."""
    now = time.time() if now is None else now
    if channel == "lidar":
        points = []
        for step in range(720):
            angle = step * 0.5
            distance = 4.0 + 2.0 * math.sin(math.radians(angle * 3.0)) + 0.5 * math.cos(math.radians(angle * 11.0))
            points.append([round(angle, 1), round(distance, 3), 47])
        return {
            "type": "scan",
            "sequence": sequence,
            "source": "ros2",
            "frame_id": "laser",
            "point_encoding": "polar-v1",
            "points": points,
            "point_count": len(points),
            "source_point_count": 3240,
            "requested_max_points": 900,
            "coverage_deg": 360.0,
        }
    if channel == "imu":
        return {
            "type": "imu",
            "sequence": sequence,
            "ack_required": False,
            "imu_topic_alive": True,
            "last_imu_age_s": 0.01,
            "raw": {
                "frame_id": "imu_link",
                "euler_deg": {"roll": -179.9, "pitch": 89.9, "yaw": 359.9},
                "angular_velocity": {"x": -99.9999, "y": 99.9999, "z": -99.9999},
                "linear_acceleration": {"x": -156.9064, "y": 156.9064, "z": -156.9064},
            },
            "magnetic": {"magnetic_field_uT": {"x": -4912.5, "y": 4912.5, "z": -4912.5}},
        }
    if channel == "gps":
        return {
            "type": "gps",
            "sequence": sequence,
            "received_at": now,
            "age_s": 0.0,
            "valid": True,
            "connected": True,
            "state": "fix",
            "fix_type": "rtk_float",
            "latitude": FAKE_LATITUDE,
            "longitude": FAKE_LONGITUDE,
            "satellites": 255,
            "h_acc_m": 99999.999,
            "speed_mps": 277.7,
            "course_deg": 359.9,
            "altitude_msl_m": 8848.9,
            "hdop": 99.9,
            "reception": {
                "nav_sig": {"state": "fresh", "age_s": 0.1, "detected_count": 120, "tracked_count": 118},
                "nav_sat": {"state": "fresh", "age_s": 0.1, "detected_count": 255, "tracked_count": 250},
            },
            "driver": {
                "serial_open": True,
                "baud": 921600,
                "bytes_received": 9876543210,
                "valid_packets": 123456789,
                "uart_counters": {"frame": 1234, "parity": 2345, "overrun": 3456, "buf_overrun": 4567},
            },
        }
    return None
