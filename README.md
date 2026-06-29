# UNIST Orchard Rover Dashboard

Jetson Orin Nano Super에서 실행할 과수원 자율주행 연구용 웹 대시보드입니다. 현재 서버는 Jetson의 실제 시스템, 네트워크, Docker 상태를 읽고, ROS2/로버 데이터는 추후 bridge가 제공하는 JSON 텔레메트리 파일을 통해 연결합니다.

대시보드는 수행계획서의 연구 방향인 Vision-LiDAR 신뢰도 동적 조절, 행간 중심선 추정, 경로추종 안정화, 과수원 주행 성능 검증을 기준으로 구성했습니다.

## 주요 화면

- Jetson Orin Nano Super 상태: CPU, 메모리, 디스크, 온도, 호스트 정보
- 로버 상태: 주행 모드, 위치, 속도, 배터리, E-stop, 모터 상태
- 센서 상태: LiDAR, Camera, IMU, Encoder, MAVLink 준비 상태
- Vision-LiDAR 융합: 센서별 신뢰도, 동적 가중치, 중심선 오차, 경로추종 오차
- 네트워크 상태: interface, IP, link speed, RX/TX 처리량 그래프
- Docker 상태: engine 정보, 컨테이너 목록, 컨테이너 CPU/메모리 그래프
- 실험 통계: 주행 시간, 누적 거리, 완료 행간, 지도 커버리지
- 수행계획 진행도: 월별 연구 일정 기반 진행 상태

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

`docker-compose.yml`은 `restart: unless-stopped`를 사용하므로 Jetson이 재부팅되어도 Docker 서비스가 올라오면 대시보드 컨테이너가 다시 시작됩니다. Cloudflare Tunnel은 `bts.sarihub.com`을 `http://localhost:8080`으로 전달합니다.

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
- [docs/research_metrics.md](docs/research_metrics.md): 연구 성과 지표
- [docs/telemetry_schema.md](docs/telemetry_schema.md): JSON 텔레메트리 스키마
