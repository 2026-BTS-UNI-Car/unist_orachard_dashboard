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

RPLIDAR `GET_INFO` 응답은 `1,000,000 bps`에서 확인했습니다. Docker Compose는 `/dev`를 `/host-dev`로 마운트하고 serial 장치 cgroup rule을 열어 대시보드의 수동 테스트 버튼이 장치를 열 수 있게 합니다.

대시보드는 LiDAR를 상시 읽지 않습니다. ROS2 LiDAR driver와 충돌하지 않도록 Connection 페이지의 테스트 버튼을 눌렀을 때만 다음 순서로 동작합니다.

1. `/dev/ttyUSB*`, `/dev/serial/by-id/*` 후보 확인
2. `/proc/*/fd`를 스캔해 포트 점유 프로세스 확인
3. 점유 중이면 테스트 중단
4. 점유 중이 아니면 RPLIDAR `GET_INFO`/`GET_HEALTH`만 요청
5. 즉시 포트를 닫고 결과를 화면에 표시

추후 ROS2에서 `/scan`을 실제로 구독하는 세부 LiDAR 대시보드는 별도 ROS2 bridge가 topic rate, scan range 요약, frame id, scan health를 `runtime/telemetry_snapshot.json`으로 써주는 방식이 안전합니다. 이 웹 서버가 직접 scan stream을 계속 읽는 구조는 ROS2 driver와 포트 경쟁을 만들 수 있으므로 사용하지 않습니다.
