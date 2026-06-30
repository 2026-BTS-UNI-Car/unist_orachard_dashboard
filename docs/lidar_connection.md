# LiDAR 연결과 테스트

## 운영 원칙

Jetson 실제 운영에서는 ROS2 LiDAR driver만 RPLIDAR S2 serial device(`/dev/ttyUSB0`)를 소유합니다. Dashboard backend는 실시간 운영 중 serial device를 직접 열지 않고, ROS2 `/scan` 토픽을 WebSocket bridge로 받아 2D 지도에 표시합니다.

금지 구조:

- Dashboard backend와 ROS2 driver가 동시에 `/dev/ttyUSB0`를 여는 구조
- 두 개 이상의 LiDAR driver가 동일 serial device를 여는 구조
- 두 개 이상의 driver가 동일 `/scan` topic에 publish하는 구조

권장 구조:

```text
RPLIDAR S2 /dev/ttyUSB0
  -> ROS2 RPLIDAR driver
  -> /scan
  -> scripts/lidar_ws_bridge.py
  -> ws://0.0.0.0:8765/ws/lidar
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

`실시간 연결` 버튼은 브라우저가 LiDAR WebSocket bridge에 접속합니다.

기본 URL:

- HTTP 페이지: `ws://${location.hostname}:8765/ws/lidar`
- HTTPS 페이지: `wss://${location.hostname}:8765/ws/lidar`

`window.UBTS_LIDAR_WS_URL`이 정의되어 있으면 그 값을 우선 사용합니다.

수신 메시지:

- `socket`: WebSocket 연결 확인
- `status`: `/scan` topic alive, 마지막 scan age, point count
- `scan`: 2D point 배열

### 360 스캔 테스트

이 버튼은 ROS2 driver가 꺼져 있고 serial port가 비어 있을 때만 쓰는 진단 기능입니다. 실시간 WebSocket 연결 중에는 serial 단발 스캔을 실행하지 않습니다.

절차:

1. `/dev/ttyUSB*`, `/dev/serial/by-id/*` 후보 확인
2. `/proc/*/fd`로 포트 점유 프로세스 확인
3. 점유 중이면 `in_use` JSON을 반환하고 중단
4. 점유 중이 아니면 짧은 normal scan 수행
5. STOP 명령 후 DTR/RTS를 끄고 포트를 닫음

## 2D LiDAR 지도

Dashboard 지도 좌표계:

- 중앙: LiDAR 위치
- 화면 위쪽: `0° / FRONT / +y_m`
- 화면 오른쪽: `90° / +x_m`
- 화면 아래쪽: `180°`
- 화면 왼쪽: `270°`

원형 grid는 거리(m)를 나타냅니다. 점은 glow 없이 일반 원으로 표시하며, 정면 화살표는 LiDAR 0° 방향입니다.

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

WebSocket bridge:

```bash
python3 /home/unist/unist_orachard_dashboard/scripts/lidar_ws_bridge.py --host 0.0.0.0 --port 8765 --topic /scan
```
