# Jetson 배포

초기 운영 위치는 `/home/unist/workspace/unist_orachard_dashboard`를 기준으로 합니다.

## 1. 코드 받기

```bash
cd /home/unist
mkdir -p workspace
cd workspace
git clone https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard.git
cd unist_orachard_dashboard
```

## 2. Docker 실행 확인

```bash
docker compose down --remove-orphans 2>/dev/null || docker-compose down --remove-orphans 2>/dev/null || true
docker ps -aq --filter "name=unist-orachard-dashboard" | xargs -r docker rm -f 2>/dev/null || true
docker compose up -d --build
# docker compose 플러그인이 없으면: docker-compose up -d --build
curl http://127.0.0.1:8080/api/health
```

Cloudflare Tunnel을 통해 `https://bts.sarihub.com`으로 접속합니다. Docker Compose는 호스트의 `127.0.0.1:8080`에만 포트를 노출합니다.

## 3. 자동 재시작

`docker-compose.yml`의 `restart: unless-stopped` 설정으로 Docker 서비스 재시작 또는 Jetson 재부팅 후 컨테이너가 자동으로 다시 올라옵니다.

로그 확인:

```bash
docker compose logs -f
# docker compose 플러그인이 없으면: docker-compose logs -f
```

## 4. ROS2 연동 예정 방식

ROS2 bridge 노드는 아래 파일을 주기적으로 갱신합니다.

```text
/home/unist/workspace/unist_orachard_dashboard/runtime/telemetry_snapshot.json
```

서버는 이 파일을 매 요청마다 읽기 때문에 별도 재시작 없이 화면이 갱신됩니다.

권장 방식은 임시 파일에 먼저 쓰고 `rename`으로 교체하는 atomic write입니다.

```python
tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
tmp_path.replace(target_path)
```

## 5. Jetson 상태 확장 후보

- `tegrastats` 로그 파싱: GPU, RAM, EMC, 온도, 전력
- `/sys/class/thermal` 기반 온도 모니터링
- `nvpmodel` 전원 모드
- 배터리 BMS 또는 전압 센서
- 모터 드라이버 fault pin
- ROS2 node liveliness와 topic rate

현재 프로토타입은 CPU, 메모리, 디스크, 온도처럼 컨테이너/일반 Linux에서도 읽을 수 있는 값부터 표시합니다.

## 6. LiDAR 연결

현재 Jetson에서는 LiDAR가 아래 USB-serial 장치로 인식되었습니다.

```text
/dev/ttyUSB0
/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_...-if00-port0
```

RPLIDAR `GET_INFO` 응답은 `1,000,000 bps`에서 확인했습니다. 실제 운영에서는 ROS2 LiDAR driver만 `/dev/ttyUSB0`를 열고 `/scan`을 publish합니다. Dashboard는 serial을 직접 열지 않고 `scripts/lidar_ws_bridge.py`가 제공하는 `/scan` WebSocket을 구독합니다.

Silicon Labs CP210x VCP 릴리즈 노트는 구형 Linux용 out-of-tree 드라이버 안내에 가깝습니다. 현재 Jetson은 CP2102N USB-UART를 커널 내장 `cp210x`로 인식하므로, 별도 드라이버 설치 없이 `/dev/ttyUSB0` 장치 접근과 Docker 권한이 핵심입니다.

대시보드는 LiDAR를 상시 읽지 않습니다. ROS2 LiDAR driver와 충돌하지 않도록 Connection 페이지의 serial 테스트 버튼은 ROS2 driver가 꺼져 있을 때 진단용으로만 사용합니다.

1. `/dev/ttyUSB*`, `/dev/serial/by-id/*` 후보 확인
2. `/proc/*/fd`를 스캔해 포트 점유 프로세스 확인
3. 점유 중이면 테스트 중단
4. 정보 확인은 RPLIDAR `GET_INFO`/`GET_HEALTH`만 요청
5. 360 스캔 지도는 DTR motor line을 켜고 normal scan을 약 3초 수집
6. STOP 명령 후 DTR/RTS를 끄고 즉시 포트를 닫아 결과를 화면에 표시

실시간 LiDAR 지도 실행:

```bash
source /opt/ros/iron/setup.bash
source /home/unist/unist_orachard_ros2/install/setup.bash
ros2 launch orchard_bringup orchard_full.launch.py mode:=real use_rviz:=false use_lidar_driver:=true
python3 /home/unist/unist_orachard_dashboard/scripts/lidar_ws_bridge.py --host 0.0.0.0 --port 8765 --topic /scan
python3 -m dashboard_server --host 0.0.0.0 --port 8080
```

## 7. Fan Control

기본 Docker Compose는 안전을 위해 sysfs write 권한을 열지 않습니다. Jetson에서 팬 제어까지 사용할 때만 override compose를 명시적으로 추가합니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.jetson-fan.yml up -d --build
```

수동 검증:

```bash
cat /sys/class/hwmon/hwmon*/name
systemctl is-active nvfancontrol
sudo systemctl stop nvfancontrol
echo 1 | sudo tee /sys/class/hwmon/hwmonX/pwm1_enable
echo 160 | sudo tee /sys/class/hwmon/hwmonX/pwm1
cat /sys/class/hwmon/hwmonX/fan1_input
```
