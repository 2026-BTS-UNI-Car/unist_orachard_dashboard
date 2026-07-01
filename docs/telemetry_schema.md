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

CPU, 메모리, swap, 네트워크, 디스크, 온도, Jetson `ina3221` 전력 값은 서버가 Linux `/proc`, `/sys`, Docker socket에서 직접 읽습니다. `TELEMETRY_FILE`의 `system`은 추후 ROS2 bridge가 `nvpmodel`, throttle 상태 같은 보강 정보를 전달할 때 사용합니다.

## connection

`connection`은 대시보드 서버가 직접 확인한 연결 상태입니다. 현재 LiDAR는 Jetson에서 CP2102N USB-UART 장치로 인식되며, 운영 모드에서는 ROS2 driver만 serial port를 열고 `/scan`을 publish합니다.

```json
{
  "lidar": {
    "present": true,
    "state": "detected",
    "device": "/dev/ttyUSB0",
    "by_id": "/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_...",
    "test_mode": "ros2_subscribe_only",
    "conflict_policy": "Dashboard does not open the LiDAR serial port; ROS2 driver owns it and publishes /scan",
    "last_test": {
      "ok": true,
      "state": "device_ready",
      "message": "LiDAR device is visible; waiting for ROS2 /scan"
    },
    "last_scan": {
      "ok": false,
      "state": "disabled",
      "message": "Serial scan is disabled; use ROS2 /scan WebSocket"
    }
  }
}
```

`ros2-lidar-bridge`는 `/scan` 수신 상태를 `runtime/lidar_bridge_status.json`에 씁니다. 대시보드 서버는 이 파일을 읽어서 `connection.lidar_bridge`, `services.ros2_bridge`, `sensors.lidar` 상태를 갱신합니다.

```json
{
  "lidar_bridge": {
    "alive": true,
    "scan_topic_alive": true,
    "last_scan_age_sec": 0.08,
    "point_count": 900,
    "source": "ros2",
    "frame_id": "laser"
  }
}
```

브라우저 LiDAR WebSocket은 dashboard session cookie가 있는 경우만 허용됩니다. 현재 활성 연결 수는 `connection.lidar_websocket`에 표시됩니다.

```json
{
  "lidar_websocket": {
    "active": 1,
    "session_ttl_s": 3600,
    "known_sessions": 2,
    "sessions": [
      {
        "id": "a1b2c3d4",
        "session": "u9v8w7x6",
        "age_sec": 12.4,
        "remote": "127.0.0.1"
      }
    ]
  }
}
```

대시보드는 LiDAR 포트를 열지 않습니다. 상태 새로고침 버튼은 장치 표시와 holder만 확인합니다. 실시간 지도는 `ros2-lidar-bridge` 컨테이너가 제공하는 WebSocket을 사용하며, bridge는 ROS2 `/scan`만 구독합니다.

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
