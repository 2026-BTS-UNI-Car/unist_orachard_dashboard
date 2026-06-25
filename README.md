# UNIST Orchard Rover Dashboard

Jetson Orin Nano Super에서 실행할 과수원 자율주행 연구용 웹 대시보드 프로토타입입니다. 현재는 실사용 전 단계라 ROS2 토픽에 직접 붙지 않고, 표준 라이브러리 기반 Python 서버가 mock 텔레메트리와 일부 시스템 상태를 보여줍니다.

대시보드는 수행계획서의 연구 방향인 Vision-LiDAR 신뢰도 동적 조절, 행간 중심선 추정, 경로추종 안정화, 과수원 주행 성능 검증을 기준으로 구성했습니다.

## 주요 화면

- Jetson Orin Nano Super 상태: CPU, 메모리, 디스크, 온도, 호스트 정보
- 로버 상태: 주행 모드, 위치, 속도, 배터리, E-stop, 모터 상태
- 센서 상태: LiDAR, Camera, IMU, Encoder, MAVLink 준비 상태
- Vision-LiDAR 융합: 센서별 신뢰도, 동적 가중치, 중심선 오차, 경로추종 오차
- LiDAR 지도: 과수열, 오검출 요소, 로버 위치, 중심선, 목표 경로 canvas 시각화
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

## 텔레메트리 연결 방식

서버는 기본적으로 `sample_data/telemetry_snapshot.json` 파일을 읽어 mock 데이터 위에 덮어씁니다. 나중에 ROS2 bridge 노드가 이 JSON 파일을 주기적으로 갱신하면, 대시보드는 별도 수정 없이 실제 상태를 표시할 수 있습니다.

```bash
export TELEMETRY_FILE=/home/uni/unist_orachard_dashboard/runtime/telemetry_snapshot.json
python3 -m dashboard_server --host 0.0.0.0 --port 8080
```

스키마는 [docs/telemetry_schema.md](docs/telemetry_schema.md)에 정리했습니다.

## 로컬 mock 업데이트

```bash
python3 scripts/update_sample_telemetry.py --output runtime/telemetry_snapshot.json --loop
TELEMETRY_FILE=runtime/telemetry_snapshot.json python3 -m dashboard_server --host 0.0.0.0 --port 8080
```

## 검증

```bash
python3 -m py_compile dashboard_server/*.py scripts/*.py
python3 -m unittest discover -s tests
```

## 배포 문서

- [docs/architecture.md](docs/architecture.md): 서버와 데이터 흐름
- [docs/jetson_deployment.md](docs/jetson_deployment.md): Jetson 배포 절차
- [docs/cloudflared.md](docs/cloudflared.md): Cloudflare Tunnel 설정
- [docs/research_metrics.md](docs/research_metrics.md): 연구 성과 지표
- [docs/telemetry_schema.md](docs/telemetry_schema.md): JSON 텔레메트리 스키마
