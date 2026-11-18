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
| [slow_link.md](slow_link.md) | 느린 2.4 GHz 연결: 시험대, 기준 측정, 원인, 목표 32개, 고친 뒤와 최종 측정(12절) | 현재 |
| [WIFI_AP_CHECKLIST_KO.md](WIFI_AP_CHECKLIST_KO.md) | 차의 무선 AP 현장 점검표(채널·대역·출력·안테나·거리), 현장 연결 기록(`?link_log=1`)과 등급 경계 다시 맞추기 | 현재 |
| [SCREENS_KO.md](SCREENS_KO.md) | 메뉴 8쪽(수정 전 구성으로 되돌림, UI-16), Rover Control 안의 새 기능 자리, 화면 6개 주소의 별칭 | 현재 |

## 조종 (차동 V2)

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| [esp_rover_control.md](esp_rover_control.md) | ESP32-S3(BTS7960 3개) 조종 브리지: 부호, 조향 목표 규칙, 구동 안전과 남는 위험, 설치. 옛 ESP8266 연결, 조종권, 정지, 저속 통신 | 현재(2026-11-16, 가짜 보드로 확인. 차에서 확인 전) |
| [rover_control_dashboard.md](rover_control_dashboard.md) | 화면의 조종 사용법 요약 | 현재(차동) |

## 조향 차량 (V3)

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| [STEERING_SERVER_KO.md](STEERING_SERVER_KO.md) | 조향 차량의 서버 사양: 구동·조향 조종 API, 조향 상태, 자율주행 시작·정지·heartbeat 중계, 조정 값, 정지 규칙, 차동과의 공존 | 현재(실차 펌웨어 V3 전) |
| [STEERING_HOST_PORT_KO.md](STEERING_HOST_PORT_KO.md) | 시뮬레이션 레포에서 옮긴 조향 V3 호스트 브리지의 출처와 시험 | 현재 |
| [FIELD_STEER_AUTONOMY_TEST_KO.md](FIELD_STEER_AUTONOMY_TEST_KO.md) | ESP32-S3 차량의 현장 첫 조향 시험(바퀴 든 상태, 잴 값 표), 그림자 주행, 구동 측정, 결정할 것, 첫 자율 시험 순서 | 현재(2026-11-16, 차에서 하기 전) |
| [CORRIDOR_TEST_KO.md](CORRIDOR_TEST_KO.md) | 건물 복도에서 처음 땅에 놓고 하는 시험의 준비물과 당일 순서: 채울 값(조향 극성·범위, 출발 duty, 속도, 정지 거리, 직진 휨, 거리 명령 보정), 안전 규칙 | 현재(2026-11-17, 시험 전 점검표) |
| [ORCHARD_DAY_20261120_KO.md](ORCHARD_DAY_20261120_KO.md) | 건물 복도에서 처음 땅에 놓고 하는 시험의 준비물과 당일 순서: 채울 값(조향 극성·범위, 출발 duty, 속도, 정지 거리, 직진 휨, 거리 명령 보정), 안전 규칙 | 현재(2026-11-17, 시험 전 점검표) |

## 설치

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| [../deploy/jetson_dashboard/README_KO.md](../deploy/jetson_dashboard/README_KO.md) | Jetson 대시보드 설치 묶음: 점검, 설치, 확인, 되돌리기. 4-1절: 이 묶음만으로 현장에 가는 변경과 브리지를 바꿔야 하는 변경 | 현재 |
| [../deploy/jetson_row_executor/README_KO.md](../deploy/jetson_row_executor/README_KO.md) | Jetson 자율 실행기 설치 묶음: SHADOW 기본(조종 소켓 없음), 점검, 설치, 대시보드 연결 파일, 되돌리기, 현장 날의 MOTOR 모드 | 현재(2026-11-16) |

## 접속과 운영

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| [jetson_offline_access.md](jetson_offline_access.md) | 무선랜 Wi-Fi/AP 자동 전환, AP 주소 `http://10.77.0.1`, 로컬 DNS | 현재 |
| [cloudflared.md](cloudflared.md) | Cloudflare Tunnel 설정 | 일부 낡음(`docker compose up --build` 안내는 쓰지 않는다. 아래 주의 참고) |
| [fan_control.md](fan_control.md) | Jetson 팬 자동/수동 PWM | 확인하지 않음(2026-06 작성) |

## 차동 시절 관찰 묶음 (조향 실행기로 바뀔 때까지 둠)

결정 나5(D-14): 조향 펌웨어가 차에 올라가 조향 실행기로 바뀔 때까지 둔다. 그때까지 지금 차(차동 V2)의 수동 주행과 설치된 native 관찰 이미지가 이 코드에 기댄다. 내리는 조건과 내릴 파일 목록은 [STEERING_SERVER_KO.md](STEERING_SERVER_KO.md) 9절.

결정 나5(D-14): 조향 펌웨어가 차에 올라가 조향 실행기로 바뀔 때까지 둔다. 그때까지 지금 차(차동 V2)의 수동 주행과 설치된 native 관찰 이미지가 이 코드에 기댄다. 내리는 조건과 내릴 파일 목록은 [STEERING_SERVER_KO.md](STEERING_SERVER_KO.md) 9절.

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
| [../tools/ui_check/README_KO.md](../tools/ui_check/README_KO.md) | 화면 점검 도구(11경로 × 5화면 × 자료 2종 = 110쪽) |
| [../tools/slow_link/README_KO.md](../tools/slow_link/README_KO.md) | 느린 통신 시험대(속도 제한 중계기, 센서 재생원, 측정기, 받아들인 값과 견주기). 현장 연결 기록 보고 `link_log_report.py` |
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
