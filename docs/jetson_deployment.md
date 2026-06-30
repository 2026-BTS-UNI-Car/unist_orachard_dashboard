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

`docker-compose.yml`의 `restart: unless-stopped` 설정으로 Docker 서비스 재시작 또는 Jetson 재부팅 후 컨테이너가 자동으로 다시 올라옵니다. Compose는 다음 컨테이너를 함께 관리합니다.

- `unist-orachard-dashboard`: HTTP/API/UI, `/ws/lidar` proxy, Fan Control
- `unist-orachard-ros2-lidar-bridge`: ROS2 Iron `/scan` WebSocket bridge, serial live fallback

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

RPLIDAR `GET_INFO` 응답은 `1,000,000 bps`에서 확인했습니다. 실제 운영에서는 ROS2 LiDAR driver가 `/dev/ttyUSB0`를 열고 `/scan`을 publish합니다. Dashboard는 serial을 직접 열지 않고 `ros2-lidar-bridge` 컨테이너가 제공하는 WebSocket을 구독합니다.

LiDAR bridge는 Jetson 내부에서 `ws://127.0.0.1:8765/ws/lidar`를 열고, 브라우저는 dashboard와 같은 origin의 `/ws/lidar`로 접속합니다. 로컬에서는 `ws://127.0.0.1:8080/ws/lidar`, 공개 주소에서는 `wss://bts.sarihub.com/ws/lidar`입니다.

Silicon Labs CP210x VCP 릴리즈 노트는 구형 Linux용 out-of-tree 드라이버 안내에 가깝습니다. 현재 Jetson은 CP2102N USB-UART를 커널 내장 `cp210x`로 인식하므로, 별도 드라이버 설치 없이 `/dev/ttyUSB0` 장치 접근과 Docker 권한이 핵심입니다.

`ros2-lidar-bridge`는 ROS2 `/scan`을 우선 사용합니다. 아직 ROS2 driver가 준비되지 않은 개발 단계에서는 `/dev/ttyUSB0` serial live fallback으로 WebSocket 지도 데이터를 표시합니다. ROS2 `/scan`이 살아나면 ROS2 데이터를 우선합니다. 현재 RPLIDAR S2 serial fallback은 기본 2.5초 scan cycle에서 정상 point 수신을 확인했습니다.

1. `/dev/ttyUSB*`, `/dev/serial/by-id/*` 후보 확인
2. `/proc/*/fd`를 스캔해 포트 점유 프로세스 확인
3. 점유 중이면 테스트 중단
4. 정보 확인은 RPLIDAR `GET_INFO`/`GET_HEALTH`만 요청
5. 실시간 지도는 WebSocket bridge를 통해 수신
6. serial fallback은 짧은 scan cycle마다 STOP 명령 후 포트를 닫음

실시간 LiDAR 지도 실행:

```bash
cd /home/unist/workspace/unist_orachard_dashboard
docker-compose up -d --build
docker-compose logs -f ros2-lidar-bridge
```

검증:

```bash
curl -i http://127.0.0.1:8080/ws/lidar
```

일반 HTTP 요청은 `426 WebSocket upgrade required`가 정상입니다.

## 7. Fan Control

기본 Docker Compose는 Jetson 팬 제어를 위해 `/sys/class/hwmon`과 `/sys/devices`를 rw로 마운트하고 privileged mode로 실행합니다. Fan Control 페이지에서 수동 PWM을 조정할 수 있으며, `nvfancontrol` 자동 모드는 컨테이너가 `nsenter`로 호스트 systemd에 접근해 제어합니다.

수동 검증:

```bash
cat /sys/class/hwmon/hwmon*/name
systemctl is-active nvfancontrol
sudo systemctl stop nvfancontrol
echo 1 | sudo tee /sys/class/hwmon/hwmonX/pwm1_enable
echo 160 | sudo tee /sys/class/hwmon/hwmonX/pwm1
cat /sys/class/hwmon/hwmonX/fan1_input
```
