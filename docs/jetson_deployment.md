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
- `unist-orachard-ros2-lidar-bridge`: ROS2 Iron `/scan` WebSocket bridge, subscribe-only

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

## 5. Jetson 상태

System 페이지는 CPU, Memory, Swap, Network, Power를 실시간 그래프로 표시합니다. Network 상세 정보는 Network 탭에 유지합니다.

Jetson Orin Nano Super의 GPU 사용률은 일반 Linux GPU처럼 안정적인 percent 값으로 노출되지 않아 대시보드 리소스 그래프에서 제외합니다. 대신 `/sys/class/hwmon`의 `ina3221` 전류/전압 rail을 읽을 수 있으면 실시간 소비 전력을 표시합니다. 현재 총 전력은 `VDD_IN` rail을 우선 사용하고, 없으면 읽힌 rail의 합계를 fallback으로 사용합니다.

추후 확장 후보:

- `nvpmodel` 전원 모드
- 배터리 BMS 또는 전압 센서
- 모터 드라이버 fault pin
- ROS2 node liveliness와 topic rate

## 6. LiDAR 연결

현재 Jetson에서는 LiDAR가 아래 USB-serial 장치로 인식되었습니다.

```text
/dev/ttyUSB0
/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_...-if00-port0
```

RPLIDAR `GET_INFO` 응답은 `1,000,000 bps`에서 확인했습니다. 실제 운영에서는 ROS2 LiDAR driver가 `/dev/ttyUSB0`를 열고 `/scan`을 publish합니다. Dashboard는 serial을 직접 열지 않고 `ros2-lidar-bridge` 컨테이너가 제공하는 WebSocket을 구독합니다.

LiDAR bridge는 Jetson 내부에서 `ws://127.0.0.1:8765/ws/lidar`를 열고, 브라우저는 dashboard와 같은 origin의 `/ws/lidar`로 접속합니다. 로컬에서는 `ws://127.0.0.1:8080/ws/lidar`, 공개 주소에서는 `wss://bts.sarihub.com/ws/lidar`입니다.

Silicon Labs CP210x VCP 릴리즈 노트는 구형 Linux용 out-of-tree 드라이버 안내에 가깝습니다. 현재 Jetson은 CP2102N USB-UART를 커널 내장 `cp210x`로 인식하므로, 별도 드라이버 설치 없이 `/dev/ttyUSB0` 장치 접근과 Docker 권한이 핵심입니다.

`ros2-lidar-bridge`는 ROS2 `/scan`만 구독합니다. ROS2 driver가 꺼져 있으면 Dashboard 지도는 대기 상태가 정상이며, bridge나 dashboard가 `/dev/ttyUSB0`을 대신 열지 않습니다.

1. `/dev/ttyUSB*`, `/dev/serial/by-id/*` 후보 확인
2. `/proc/*/fd`를 스캔해 포트 점유 프로세스 확인
3. ROS2 driver가 serial port를 단독 소유
4. ROS2 driver가 `/scan` publish
5. bridge가 `/scan`을 WebSocket으로 변환
6. Dashboard가 같은 origin의 `/ws/lidar`로 수신

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

수동 PWM은 같은 Jetson 부팅 세션 안에서만 유지합니다. Jetson이 재부팅되면 Dashboard는 저장된 수동 상태를 자동 상태로 되돌리고 `nvfancontrol`이 기본 제어자가 되도록 설계합니다.

수동 검증:

```bash
cat /sys/class/hwmon/hwmon*/name
systemctl is-active nvfancontrol
sudo systemctl stop nvfancontrol
echo 1 | sudo tee /sys/class/hwmon/hwmonX/pwm1_enable
echo 160 | sudo tee /sys/class/hwmon/hwmonX/pwm1
cat /sys/class/hwmon/hwmonX/fan1_input
```
