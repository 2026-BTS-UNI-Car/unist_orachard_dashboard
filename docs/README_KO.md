# 문서 색인

`docs/`와 레포 곳곳에 있는 문서가 무엇을 다루는지, 지금 내용과 맞는지 적은 목록이다. 지난 보고 문서와 검증 영수증은 [HISTORY.md](HISTORY.md)를 본다.

표시: **현재** = 지금 코드와 맞는 계약·사용법. **일부 낡음** = 쓸 내용이 있지만 옛 안내가 섞여 있음. **낡음** = 2026-07 무렵 내용이라 지금과 다름. 차량은 2026-11에 앞바퀴 조향으로 바뀌었고, 아래 조종 문서는 아직 좌우 PWM(차동) 펌웨어 V2 기준이다.

## 센서와 녹화

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| [imu_ros2_recording.md](imu_ros2_recording.md) | BNO085 원본 토픽 계약, 소프트웨어 보정 없는 표시·기록, `/imu/data` 호환 전달 | 현재 |
| [usb_camera_connection.md](usb_camera_connection.md) | UVC 카메라 단독 소유, ROS 토픽, 대시보드 표시 | 현재 |
| [lidar_connection.md](lidar_connection.md) | RPLIDAR S2 `/scan` 소유 구조, WebSocket 표시, 시리얼 충돌 방지 | 일부 낡음(장치 이름 `/dev/ttyUSB0` 예시, 지금 compose 는 by-id 고정) |
| [gps_offline_map.md](gps_offline_map.md) | GPS 드라이버, 오프라인 지도, 동기 기록, 진단 이력 | 현재(앞부분은 2026-09 진단 기록) |
| [gps_usb_reconnect.md](gps_usb_reconnect.md) | USB GPS 분리·재연결 처리 | 현재 |
| [gps_reception_status.md](gps_reception_status.md) | GPS 신호·녹화 상태 표시 | 현재 |
| [motor_pwm_recording.md](motor_pwm_recording.md) | 녹화에 남기는 요청·보고 PWM 핀 신호와 그 한계 | 현재(차동 핀 기준) |
| [raw_sensor_dashboard.md](raw_sensor_dashboard.md) | 영상 인식(AI)을 걷어 낸 원본 센서 화면의 범위 | 현재 |

## 전송과 화면

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| [stream_latency.md](stream_latency.md) | 카메라·LiDAR·IMU 실시간 전송, 프레임 간격, ACK | 현재 |
| [camera_ack_window_v2.md](camera_ack_window_v2.md) | 카메라 전송창 V2 적용과 검증(2026-10-25 설치) | 현재 |
| [CAMERA_ORIGINAL1080_PREVIEW.md](CAMERA_ORIGINAL1080_PREVIEW.md) | 원본 1080 JPEG 실시간 표시(2026-10-25 설치), 끄는 주소 옵션 | 현재 |
| [grid_cell_transport.md](grid_cell_transport.md) | 격자 지도를 셀 숫자로 보내고 브라우저가 그리는 형식 | 현재 |
| [slow_link.md](slow_link.md) | 느린 2.4 GHz 연결의 기준 측정, 원인, 목표 | 현재(작업 중) |

## 조종 (차동 V2)

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| [esp_rover_control.md](esp_rover_control.md) | ESP8266 연결, 조종권, 정지, 저속 통신 설정 | 현재(차동). 조향 V3 는 아직 없음 |
| [rover_control_dashboard.md](rover_control_dashboard.md) | 화면의 조종 사용법 요약 | 현재(차동) |
| [rover_control_todo.md](rover_control_todo.md) | 남은 실물 확인 항목 | 낡음 |

## 접속과 운영

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| [jetson_offline_access_20260827.md](jetson_offline_access_20260827.md) | 무선랜 Wi-Fi/AP 자동 전환, AP 주소 `http://10.77.0.1`, 로컬 DNS | 현재. 이름의 날짜는 대시보드 Docs 화면의 주소에 묶여 있어 그대로 둠 |
| [cloudflared.md](cloudflared.md) | Cloudflare Tunnel 설정 | 일부 낡음(`docker compose up --build` 안내는 쓰지 않는다. 아래 주의 참고) |
| [fan_control.md](fan_control.md) | Jetson 팬 자동/수동 PWM | 확인하지 않음(2026-06 작성) |

## 낡은 문서 (대시보드 Docs 화면 목록에 묶여 있어 남겨 둠)

대시보드 서버의 문서 목록(`dashboard_server/app.py`의 `DOCS_CATALOG`)과 `tests/test_api.py`가 이 문서들의 주소를 확인한다. 목록을 고칠 때 같이 내리거나 새로 쓴다.

| 문서 | 무엇이 낡았나 |
| --- | --- |
| [architecture.md](architecture.md) | 2026-07 "초기 프로토타입" 설명 |
| [jetson_deployment.md](jetson_deployment.md) | `docker compose up -d --build` 로 띄우는 안내. 지금 차의 대시보드는 compose 로 다시 만들면 옛 이미지로 돌아간다 |
| [jetson_runtime_architecture.md](jetson_runtime_architecture.md) | 2026-07 서비스 구성 |
| [telemetry_schema.md](telemetry_schema.md) | 2026-07 텔레메트리 파일 형식. 지금 `/api/telemetry` 응답과 대조하지 않았다 |
| [simulation_run.md](simulation_run.md) | 다른 저장소의 옛 RViz 시뮬레이션. 지금 시뮬레이션은 별도 레포 |
| [lidar_drive_test_recording.md](lidar_drive_test_recording.md) | 2026-07 rosbag 기록 절차(지금은 대시보드 녹화기) |
| [research_metrics.md](research_metrics.md) | 짧은 점검 항목 목록 |

## 차동 시절 관찰 묶음 (조향 실행기로 바뀔 때까지 둠)

| 위치 | 내용 |
| --- | --- |
| [C0_SOURCE_CONSOLIDATION.md](C0_SOURCE_CONSOLIDATION.md), [C0_SOURCE_SHA256.json](C0_SOURCE_SHA256.json) | 2026-10-26 관찰 소스 묶음(진행 중이던 정리)의 설명과 파일 92개의 SHA-256 |
| [../deploy/native_sensor_stream/README.md](../deploy/native_sensor_stream/README.md) | native 관찰 서비스(LiDAR·IMU·GNSS 원본 CDR, 지도). Jetson 에 compose 밖 별도 컨테이너로 설치돼 있다(2026-10 기록) |
| [../deploy/native_sensor_shadow/README.md](../deploy/native_sensor_shadow/README.md) | 물리 관찰기(항상 HOLD, 모터 경로 없음) |
| `../dashboard_server/_field10/row_executor/` (`README_FIELD.txt`, `OPERATIONS.txt`, `TURN_ASSIST.txt`, `NATIVE_NAVIGATION_KO.md`) | 차동 구동 열 주행 실행기의 사본. 파일마다 `SHA256SUMS` 로 고정되어 있어, 고치면 `write_sha256sums.py` 로 다시 만든다 |
| [../tools/native_overlay_v2/README.md](../tools/native_overlay_v2/README.md), [../tools/cartographer_incremental_capture/README.md](../tools/cartographer_incremental_capture/README.md), [../tools/gnss_receiver_utc/README.md](../tools/gnss_receiver_utc/README.md) | 관찰 묶음의 설치 도구와 실험 소스 |

## 레포의 다른 문서와 도구

| 위치 | 내용 |
| --- | --- |
| [../README.md](../README.md) | 레포 소개와 빠른 실행 |
| [../tools/ui_check/README_KO.md](../tools/ui_check/README_KO.md) | 화면 점검 도구(9경로 × 5화면 × 자료 2종 = 90쪽) |
| [../tools/slow_link/README_KO.md](../tools/slow_link/README_KO.md) | 느린 통신 시험대(속도 제한 중계기, 센서 재생원, 측정기) |
| [../deploy/offline_ap/README.md](../deploy/offline_ap/README.md) | 오프라인 AP 설정 파일과 회귀 검사 |
| `../deploy/systemd/` | systemd 단위 파일(cloudflared 터널, 대시보드 직접 실행). Jetson 에서 지금 쓰는지는 확인하지 않았다 |
| `../tools/fonts/` | 화면 글꼴(UBTS Sans)을 다시 만드는 스크립트 |

## compose 밖에서 도는 것

`docker-compose.yml` 은 대시보드와 ROS 센서·브리지·녹화기 서비스를 정의한다. 기록상(2026-10-13) Jetson 에는 그 밖에 다음이 따로 설치돼 있다. 이 레포의 compose 로는 만들어지지 않는다.

- GNSS 시각 동기: `scripts/gnss_time_sync.py`(시계가 동기화되지 않았고 녹화·조종·지도가 쉬는 동안에만 GNSS UTC 로 한 번 맞춤)
- native 관찰 서비스: `deploy/native_sensor_stream/`

주의: 차의 대시보드 컨테이너는 compose 로 다시 만들지 않는다. compose 파일이 가리키는 이미지가 설치된 이미지보다 오래되어, 다시 만들면 옛 화면으로 돌아간다.

## 시험

```bash
python3 -m compileall dashboard_server scripts
python3 -m unittest discover -s tests          # Linux. Windows 에서는 fcntl 을 쓰는 시험이 모이지 않는다
for f in tests/test_*.js tests/test_*.mjs; do node "$f" >/dev/null 2>&1 || echo "FAIL $f"; done
```

- ROS 가 있어야 하는 시험은 ROS 없이 건너뛴다. CI 는 ROS 이미지 안에서 따로 돌린다(`.github/workflows/smoke.yml`).
- `dashboard_server/_field10/row_executor/tests/` 는 실행기 묶음의 시험이다(`python3 -m pytest dashboard_server/_field10/row_executor/tests`, pytest 필요). 그중 `test_field1010_closed_loop.py`, `test_navigation_observation.py` 는 시뮬레이션 레포의 배치에서만 돌고 여기서는 건너뛴다.
- `tests/test_image_copies.py` 는 compose 의 각 서비스가 import 하는 파일이 그 이미지에 복사되는지 본다. `Dockerfile.ros2` 에 스크립트를 더하면 COPY 줄도 같이 고친다.
