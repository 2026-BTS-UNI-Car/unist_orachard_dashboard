# 아키텍처

이 레포는 Jetson Orin Nano Super 위에서 동작할 연구용 대시보드의 초기 프로토타입입니다. 실험 현장에서는 cloudflared가 외부 HTTPS 접속을 받아 Jetson 내부의 Python HTTP 서버로 전달합니다.

## 구성

```text
Browser
  -> Cloudflare Tunnel
  -> cloudflared on Jetson
  -> dashboard_server, localhost:8080
  -> /ws/lidar proxy to ros2-lidar-bridge, localhost:8765
  -> TELEMETRY_FILE JSON
  -> ROS2 telemetry writer, future
```

대시보드 서버는 Python 표준 라이브러리 중심으로 동작합니다. Jetson 운영 환경에서는 Docker Compose가 대시보드와 ROS2 Iron 기반 `ros2-lidar-bridge` 컨테이너를 함께 실행합니다.

## 데이터 흐름

1. `dashboard_server.telemetry.TelemetryProvider`가 Jetson의 `/proc`, `/sys`, Docker socket을 읽습니다.
2. `TELEMETRY_FILE`이 있으면 ROS2 bridge가 기록한 로버/센서/실험 상태를 함께 표시합니다.
3. `/api/telemetry`, `/api/connection`, `/api/system`, `/api/network`, `/api/docker`가 최신 상태를 JSON으로 제공합니다.
4. `static/app.js`가 1초마다 API를 조회하고 브라우저 안에서 실시간 그래프 history를 갱신합니다.

이 방식은 ROS2와 웹 서버를 느슨하게 분리합니다. ROS2 bridge가 죽어도 웹 서버는 살아 있고, 웹 서버가 재시작되어도 bridge는 파일만 계속 쓰면 됩니다.

## API

- `GET /`: 대시보드 화면
- `GET /api/health`: 서버 상태
- `GET /api/plan`: 수행계획 기반 연구 정보
- `GET /api/site-map`: 대시보드 섹션 구조
- `GET /api/telemetry`: 시스템, 네트워크, Docker, 로버, 센서, 융합, 실험 데이터
- `GET /api/connection`: LiDAR, ROS2 bridge, sensor link 상태
- `GET /api/system`: CPU, Memory, GPU, Thermal, Disk
- `GET /api/network`: interface, IP, RX/TX throughput
- `GET /api/docker`: Docker engine, container resource
- `POST /api/lidar/test`: serial port를 열지 않는 LiDAR 장치/소유 상태 확인

`POST /api/lidar/test`는 Dashboard가 LiDAR serial port를 열지 않는다는 운영 원칙을 지킵니다. 이 API는 `/dev/ttyUSB*`, `/dev/serial/by-id/*`, `/proc/*/fd` 기반 holder 정보만 갱신합니다. 실제 scan은 ROS2 driver가 `/scan`으로 publish하고, Dashboard는 WebSocket bridge를 통해 구독합니다.

Connection 페이지의 지도는 `/ws/lidar` WebSocket만 사용합니다. 이 경로는 dashboard server가 `ros2-lidar-bridge`의 `ws://127.0.0.1:8765/ws/lidar`로 프록시합니다.

## 추후 확장

- ROS2 Python bridge 노드가 `/scan`, `/camera/*`, `/imu/data`, `/cmd_vel`, `/odom`, `/battery_state` 등을 구독
- bridge 노드가 `runtime/telemetry_snapshot.json`을 atomic write로 갱신
- 실험 회차별 로그를 SQLite 또는 CSV로 저장
- Cloudflare Access를 붙여 연구팀 계정만 접속 허용
- 현장 네트워크가 불안정할 때를 위한 로컬 전용 fallback 모드 제공
