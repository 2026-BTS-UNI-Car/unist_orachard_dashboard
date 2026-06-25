# Jetson 배포

초기 운영 위치는 `/home/uni/unist_orachard_dashboard`를 기준으로 합니다.

## 1. 코드 받기

```bash
cd /home/uni
git clone https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard.git
cd unist_orachard_dashboard
```

## 2. 로컬 실행 확인

```bash
python3 -m dashboard_server --host 0.0.0.0 --port 8080
```

같은 네트워크에서 `http://JETSON_IP:8080`으로 접속합니다.

## 3. systemd 서비스

```bash
mkdir -p /home/uni/unist_orachard_dashboard/runtime
sudo cp deploy/systemd/unist-orachard-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now unist-orachard-dashboard.service
sudo systemctl status unist-orachard-dashboard.service
```

로그 확인:

```bash
journalctl -u unist-orachard-dashboard.service -f
```

## 4. ROS2 연동 예정 방식

ROS2 bridge 노드는 아래 파일을 주기적으로 갱신합니다.

```text
/home/uni/unist_orachard_dashboard/runtime/telemetry_snapshot.json
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
