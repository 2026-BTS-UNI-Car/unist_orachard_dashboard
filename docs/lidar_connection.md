# LiDAR 연결과 테스트

## 운영 원칙

Jetson 실제 운영에서는 ROS2 LiDAR driver가 RPLIDAR S2 serial device(`/dev/ttyUSB0`)를 소유하고 `/scan`을 publish합니다. Dashboard backend와 `ros2-lidar-bridge`는 serial device를 열지 않고, ROS2 `/scan`을 WebSocket으로 변환한 데이터만 받아 2D 지도에 표시합니다.

ROS2 driver가 꺼져 있거나 `/scan`이 없으면 Dashboard 지도는 대기 상태가 정상입니다. Dashboard가 대신 LiDAR port를 열어 scan하지 않습니다.

금지 구조:

- Dashboard backend와 ROS2 driver가 동시에 `/dev/ttyUSB0`를 여는 구조
- 두 개 이상의 LiDAR driver가 동일 serial device를 여는 구조
- 두 개 이상의 driver가 동일 `/scan` topic에 publish하는 구조

권장 구조:

```text
RPLIDAR S2 /dev/ttyUSB0
  -> ROS2 RPLIDAR driver -> /scan
  -> ros2-lidar-bridge container
  -> scripts/lidar_ws_bridge.py
  -> ws://0.0.0.0:8765/ws/lidar
  -> runtime/lidar_bridge_status.json
  -> Dashboard browser 2D LiDAR 지도
```

## 장치 정보

RPLIDAR S2 제품 스펙 기준:

- Interface: TTL UART via USB adapter
- Baudrate: `1,000,000 bps`
- Scan range: 360°
- Scan frequency: 10 Hz
- Measuring radius: 최대 30 m
- Angular resolution: 약 0.12°

Jetson에서는 CP2102N USB-UART가 커널 내장 `cp210x` 드라이버로 `/dev/ttyUSB0`에 노출됩니다. 구형 Silicon Labs VCP out-of-tree 드라이버 설치보다 장치 권한, udev, ROS2 driver 설정이 중요합니다.

## Dashboard Connection 탭

Connection 탭은 두 가지 경로를 구분합니다.

### 실시간 연결

Connection 페이지에 들어가면 브라우저가 LiDAR WebSocket bridge에 자동 접속합니다. `실시간 연결` 버튼은 사용자가 직접 중지하거나 다시 연결할 때 사용합니다.

기본 URL:

- HTTP 페이지: `ws://${location.host}/ws/lidar`
- HTTPS 페이지: `wss://${location.host}/ws/lidar`

`window.UBTS_LIDAR_WS_URL`이 정의되어 있으면 그 값을 우선 사용합니다.

브라우저는 `:8765` 포트로 직접 접속하지 않습니다. dashboard server가 `/ws/lidar`를 내부 bridge(`ws://127.0.0.1:8765/ws/lidar`)로 프록시하거나, cloudflared ingress가 `/ws/lidar` path를 bridge로 직접 라우팅합니다.

수신 메시지:

- `socket`: WebSocket 연결 확인
- `status`: live source, 마지막 scan age, point count
- `scan`: 2D point 배열

### 상태 확인

`상태 새로고침` 버튼은 serial port를 열지 않습니다. Dashboard가 볼 수 있는 `/dev/ttyUSB*` 표시, 안정적인 by-id 경로, 현재 포트 holder만 확인합니다.

## 2D LiDAR 지도

Dashboard 지도 좌표계:

- 중앙: LiDAR 위치
- 화면 위쪽: `0° / +y_m`
- 화면 오른쪽: `90° / +x_m`
- 화면 아래쪽: `180°`
- 화면 왼쪽: `270°`

원형 grid는 거리(m)를 나타냅니다. Dashboard 표시는 현재 장착 방향에 맞춰 ROS2 `/scan` point를 180° 회전한 좌표를 사용합니다. ROS2 원본 `/scan`과 TF는 변경하지 않습니다.

지도 range는 기본적으로 첫 live scan의 point 밀도와 외곽 분포를 보고 자동으로 정수 m 단위로 맞춥니다. 사용자가 확대/축소 버튼이나 range slider를 조작하면 수동 range로 전환되며, slider는 0.1 m 단위로 조정할 수 있습니다. `Auto` 버튼을 누르면 다음 live scan 기준으로 다시 자동 산정합니다.

## 현재 지도 다운로드

Connection 탭의 `현재 지도 다운로드`는 대시보드가 표시 중인 2D point data를 저장합니다.

지원 형식:

- JSON: point data, 좌표계 설명, grid/range metadata
- CSV: `angle_deg,distance_m,quality,x_m,y_m`
- PNG: 현재 canvas 시각화 스냅샷

이 다운로드는 ROS2 SLAM 공식 map 저장(`map.pgm`, `map.yaml`)과 다릅니다. Nav2 재사용 지도는 `nav2_map_server map_saver_cli` 또는 `/map` OccupancyGrid 저장 기능으로 별도 구현해야 합니다.

## Jetson 검증 명령

```bash
source /opt/ros/iron/setup.bash
source /home/unist/unist_orachard_ros2/install/setup.bash
ros2 launch orchard_bringup orchard_full.launch.py mode:=real use_rviz:=false use_lidar_driver:=true
```

다른 터미널:

```bash
ros2 topic list
ros2 topic echo /scan --once
ros2 topic hz /scan
ros2 run tf2_ros tf2_echo base_link laser
```

Docker Compose bridge:

```bash
cd /home/unist/workspace/unist_orachard_dashboard
docker-compose up -d --build
docker-compose logs -f ros2-lidar-bridge
```

브라우저 경로 검증:

```bash
curl -i http://127.0.0.1:8080/ws/lidar
```

일반 HTTP 요청이면 `426 WebSocket upgrade required`가 정상입니다. 브라우저에서는 `ws://127.0.0.1:8080/ws/lidar` 또는 `wss://bts.sarihub.com/ws/lidar`로 연결됩니다.
