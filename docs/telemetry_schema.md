# 텔레메트리 스키마

`TELEMETRY_FILE`은 JSON 객체 하나입니다. 모든 필드는 선택 사항이며, 없는 값은 서버의 mock 데이터가 채웁니다.

## 최상위 구조

```json
{
  "system": {},
  "rover": {},
  "sensors": {},
  "fusion": {},
  "experiment": {},
  "map": {},
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

## map

```json
{
  "frame": "map",
  "bounds": {"x_min": -8.0, "x_max": 30.0, "y_min": -7.0, "y_max": 7.0},
  "robot": {"x": 1.0, "y": -3.0, "yaw_deg": 0.0},
  "trees": [{"x": 0.0, "y": -4.5}],
  "clutter": [{"x": 5.0, "y": -2.7}],
  "centerline": [{"x": 0.0, "y": -3.0}, {"x": 2.0, "y": -3.0}],
  "route": [{"x": -4.0, "y": -3.0}, {"x": 26.0, "y": -3.0}]
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
