# UBTS Raw Sensor Dashboard

Camera, LaserScan, IMU and GPS display with manual ESP control. AI workers, tree/fusion overlays and model downloads have been removed.

See [raw sensor deployment](docs/raw_sensor_dashboard.md). Existing runtime/data and recorded originals are preserved.


GPS USB/UART driver, offline Korea maps and synchronized GPS recording: [GPS setup and operation](docs/gps_offline_map.md)

녹화별 모터 핀 신호 저장·재생: [PWM 기록 형식과 한계](docs/motor_pwm_recording.md)

최신 지도 처리·느린 망 재생·서버 재시작 이어가기: [2026-10-05 실제 배포 및 검증 상태](docs/MAP_DELIVERY_STATUS_20261005.md)

Jetson Orin Nano Super에서 실행하는 과수원 자율주행 연구용 웹 대시보드입니다. Jetson의 실제 시스템·네트워크·Docker 상태와 ROS2 센서 브리지가 제공하는 데이터를 표시합니다.

실차 센서는 RPLIDAR S2 2D LiDAR, BNO085 IMU, UVC USB 카메라, ZED-F9P GPS이며 휠 엔코더는 없습니다. 2026-09-30 Jetson USB 진단 후 Windows PC 창가 관측에서 GNSS 신호 추적을 확인했습니다. 같은 PC·케이블로 보드와 안테나를 다시 실내에 옮긴 최신 관측에서는 추적 잠금이 없었고, 창가와 실내 모두 위치 fix는 미완료입니다. 배치에 따른 차이는 확인했지만 실내 차폐를 유일한 원인이나 Jetson/케이블 고장으로 단정하지 않습니다. [실내 비교](docs/windows_gps_placement_comparison.json) · [앞선 PC 관측](docs/windows_gps_reception_diagnosis.json) · [Jetson 안테나 진단](docs/jetson_gps_antenna_diagnosis.json). 카메라와 LiDAR는 원시 센서 화면으로 표시합니다. 시뮬레이션 지도·자율주행 개발은 별도 orchard-rover-clone 저장소에서 진행합니다.

## 주요 화면

- Jetson Orin Nano Super 상태: CPU, 메모리, 디스크, 온도, 호스트 정보
- Connection: LiDAR USB-serial 인식 상태, ROS2 `/scan` WebSocket 기반 2D LiDAR 지도, ROS2 카메라 실시간 화면
- 로버 상태: 조종 권한, 연결, 요청 출력과 MCU 보고 출력; 출력값은 실측 바퀴 속도가 아닙니다.
- 센서 상태: LiDAR, Camera, IMU, GPS 수신 상태
- 녹화 재생: 센서 데이터와 핀별 요청 PWM·MCU 보고 PWM 확인
- 네트워크 상태: interface, IP, link speed, RX/TX 처리량 그래프
- Docker 상태: engine 정보, 컨테이너 목록, 컨테이너 CPU/메모리 그래프
- 실험 통계: 주행 시간, 누적 거리, 완료 행간, 지도 커버리지
- 수행계획 진행도: 월별 연구 일정 기반 진행 상태
- Docs: 운영 문서 목록과 Markdown viewer

## 빠른 실행

```bash
cd /root/unist_orachard_dashboard
python3 -m dashboard_server --host 0.0.0.0 --port 8080
```

브라우저에서 `http://localhost:8080` 또는 같은 네트워크의 `http://JETSON_IP:8080`으로 접속합니다.

임시 Cloudflare Tunnel 테스트는 다음처럼 열 수 있습니다.

```bash
cloudflared tunnel --url http://localhost:8080
```

운영용 named tunnel 설정은 [docs/cloudflared.md](docs/cloudflared.md)를 참고하세요.

## Jetson Docker 실행

Jetson에서는 `/home/unist/workspace/unist_orachard_dashboard` 경로를 기준으로 Docker Compose를 실행합니다.

```bash
cd /home/unist/workspace/unist_orachard_dashboard
docker compose down --remove-orphans 2>/dev/null || docker-compose down --remove-orphans 2>/dev/null || true
docker ps -aq --filter "name=unist-orachard-dashboard" | xargs -r docker rm -f 2>/dev/null || true
docker compose up -d --build
# docker compose 플러그인이 없으면: docker-compose up -d --build
curl http://127.0.0.1:8080/api/health
```

`docker-compose.yml`은 대시보드, `ros2-lidar-driver`, `ros2-lidar-bridge`, `ros2-camera-driver`, `ros2-camera-bridge`, `ros2-imu-driver`, `ros2-imu-bridge`, `ros2-esp-rover-bridge`, `ros2-data-recorder` 컨테이너를 함께 실행합니다. 컨테이너들은 `restart: unless-stopped`를 사용하므로 Jetson이 재부팅되어도 Docker 서비스가 올라오면 자동으로 다시 시작됩니다. Cloudflare Tunnel은 `bts.sarihub.com`을 `http://localhost:8080`으로 전달합니다.

LiDAR driver는 RPLIDAR serial device를 단독 소유하고 `/scan`을 publish합니다. LiDAR bridge는 ROS2 Iron 컨테이너에서 `/scan`만 구독하며, Dashboard는 WebSocket으로 변환된 `/scan` point data와 `runtime/lidar_bridge_status.json` 상태만 표시합니다.

Camera driver는 UVC `/dev/video*` 장치를 단독 소유하고 `/camera/image_jpeg`, 원본 기록용 `/camera/image_raw`, `/camera/camera_info`를 publish합니다. Camera bridge는 선택한 네트워크 품질에 맞춰 표시용 JPEG를 줄여 binary WebSocket으로 전달합니다. 같은 프레임·품질을 보는 여러 화면은 JPEG 인코딩 결과를 공유합니다. 원본 ROS 데이터와 기록 해상도는 바꾸지 않습니다. 자세한 제한과 측정 결과는 [실시간 센서 전송](docs/stream_latency.md)을 참고하세요.

IMU driver는 BNO085 I2C 장치를 단독 소유하고 sensor-native `/imu/data_raw`, `/imu/mag`를 publish합니다. Recorder는 `/imu/data_raw`의 시각·좌표계·측정값·공분산을 그대로 `/imu/data`에 전달합니다. 기본90도 회전, 시작 자세 재설정과 gyro bias 차감은 적용하지 않습니다. IMU bridge와 대시보드는 원본 센서값을 표시하며 I2C 장치에 직접 접근하지 않습니다. 실제 센서 장착 좌표계 검증은 별도이며 자세한 계약은 [docs/imu_ros2_recording.md](docs/imu_ros2_recording.md)를 참고하세요.


## 텔레메트리 연결 방식

서버는 기본적으로 `runtime/telemetry_snapshot.json` 파일을 읽습니다. ROS2 bridge 노드가 이 JSON 파일을 주기적으로 갱신하면, 대시보드는 별도 수정 없이 실제 로버/센서/실험 상태를 표시합니다. 파일이 없거나 비어 있으면 연결되지 않은 항목은 `연결 전`으로 표시됩니다.

```bash
export TELEMETRY_FILE=/home/unist/workspace/unist_orachard_dashboard/runtime/telemetry_snapshot.json
python3 -m dashboard_server --host 0.0.0.0 --port 8080
```

스키마는 [docs/telemetry_schema.md](docs/telemetry_schema.md)에 정리했습니다.

## 검증

```bash
python3 -m compileall dashboard_server scripts
python3 -m unittest discover -s tests
```

## 배포 문서

- [docs/architecture.md](docs/architecture.md): 서버와 데이터 흐름
- [docs/jetson_deployment.md](docs/jetson_deployment.md): Jetson 배포 절차
- [docs/cloudflared.md](docs/cloudflared.md): Cloudflare Tunnel 설정
- [docs/lidar_connection.md](docs/lidar_connection.md): LiDAR 인식과 테스트 정책
- [docs/usb_camera_connection.md](docs/usb_camera_connection.md): USB 카메라 ROS2 연결
- [docs/imu_ros2_recording.md](docs/imu_ros2_recording.md): BNO085 ROS2 연결, 원본 표시·기록과 호환 relay
- [docs/jetson_offline_access_20260827.md](docs/jetson_offline_access_20260827.md): 별도 USB Wi-Fi AP, 로컬 DNS, 실제 보드 안전 판정과 GPS 확인
- [docs/research_metrics.md](docs/research_metrics.md): 연구 성과 지표
- [docs/telemetry_schema.md](docs/telemetry_schema.md): JSON 텔레메트리 스키마

ESP8266 control and slow-network deployment: [docs/esp_rover_control.md](docs/esp_rover_control.md).
