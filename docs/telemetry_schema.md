# 텔레메트리 스키마

`TELEMETRY_FILE`은 JSON 객체 하나입니다. 모든 필드는 선택 사항이며, 없는 로버/센서/실험 값은 대시보드에서 `연결 전`으로 표시합니다.

## 최상위 구조

```json
{
  "system": {},
  "connection": {},
  "rover": {},
  "sensors": {},
  "fusion": {},
  "experiment": {},
  "alerts": []
}
```

## system

```json
{
  "power_mode": "15W",
  "throttle_state": "normal"
}
```

CPU, 메모리, 디스크, 온도는 서버가 Linux 시스템에서 직접 읽습니다. Jetson 전용 값은 추후 ROS2 bridge 또는 tegrastats parser가 넣을 수 있습니다.

## connection

`connection`은 대시보드 서버가 직접 확인한 연결 상태입니다. 현재 LiDAR는 Jetson에서 CP2102N USB-UART 장치로 인식되었고, RPLIDAR `GET_INFO` 응답은 `1,000,000 bps`에서 확인되었습니다.

```json
{
  "lidar": {
    "present": true,
    "state": "detected",
    "device": "/dev/ttyUSB0",
    "by_id": "/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_...",
    "test_mode": "manual_short_probe",
    "last_test": {
      "ok": true,
      "protocol": "RPLIDAR",
      "baud": 1000000,
      "info": {"model_id": 113, "firmware": "1.2", "hardware": 18},
      "health": {"status": "good", "error_code": 0}
    },
    "last_scan": {
      "ok": true,
      "protocol": "RPLIDAR normal scan",
      "baud": 1000000,
      "duration_s": 3.0,
      "metrics": {
        "points": 476,
        "rendered_points": 476,
        "coverage_deg": 360.0,
        "min_distance_m": 0.12,
        "max_distance_m": 4.8
      }
    }
  }
}
```

대시보드는 LiDAR 포트를 직접 상시 점유하지 않습니다. 정보 확인 버튼은 포트가 비어 있을 때만 `GET_INFO`/`GET_HEALTH`를 수행합니다. 실시간 지도는 `ros2-lidar-bridge` 컨테이너가 제공하는 WebSocket을 사용하며, bridge는 ROS2 `/scan`을 우선 사용하고 ROS2가 아직 없을 때만 serial live fallback을 수행합니다.

## rover

```json
{
  "mode": "AUTONOMY",
  "autonomy_enabled": true,
  "state": "DRIVING",
  "pose": {"x": 1.2, "y": -3.0, "yaw_deg": 2.1},
  "speed_mps": 0.34,
  "battery_percent": 82.5,
  "motor_state": "enabled",
  "estop": false
}
```

## sensors

```json
{
  "lidar": {"ok": true, "topic": "/scan", "rate_hz": 10.0, "model": "RPLIDAR S2"},
  "camera": {"ok": true, "topic": "/camera/color/image_raw", "rate_hz": 15.0},
  "imu": {"ok": true, "topic": "/imu/data", "rate_hz": 50.0},
  "encoder": {"ok": true, "topic": "/wheel/encoder", "rate_hz": 30.0},
  "mavlink": {"ok": true, "mode": "long_range_telemetry"}
}
```

## fusion

```json
{
  "lidar_reliability": 0.82,
  "vision_reliability": 0.76,
  "lidar_weight": 0.52,
  "vision_weight": 0.48,
  "centerline_error_m": 0.08,
  "path_tracking_error_m": 0.12,
  "row_detection_count": 4
}
```

신뢰도와 가중치는 `0.0`부터 `1.0`까지를 권장합니다.

## experiment

```json
{
  "session_id": "field-2026-09-01-a",
  "runtime_s": 323.4,
  "distance_m": 93.2,
  "rows_completed": 2,
  "map_coverage_percent": 64.5,
  "weather_tag": "sunny_shadow",
  "gps_dependency": "low"
}
```

## alerts

```json
[
  {"level": "info", "message": "실험 세션 시작"},
  {"level": "warn", "message": "Vision 신뢰도 저하"}
]
```

`level`은 `info`, `warn`, `bad`를 사용합니다.
