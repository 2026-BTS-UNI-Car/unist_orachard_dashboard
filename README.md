# unist_orachard_dashboard

UNIST BTS 과수원 로버의 웹 대시보드입니다. 차에 실린 Jetson에서 돌며, 휴대폰이나 노트북 브라우저로 센서를 실시간으로 보고,
차를 조종하고 STOP을 누르고, 자율주행을 시작·정지하고, 주행 기록을 남기고 다시 봅니다. 차의 2.4 GHz 무선 AP처럼 느리고 끊기는 연결에서도
조종과 STOP이 먼저 지나가게 만들었습니다.

> **English summary.** Web dashboard of a small orchard rover (a converted ride-on toy car). It runs on the rover's
> Jetson Orin Nano and is opened from a phone or laptop over the rover's own 2.4 GHz Wi-Fi access point. Eight menu
> pages, as before the redesign: Overview, Connection (2D LiDAR RPLIDAR S2, USB camera, link status), System (Jetson
> resources), Network, Docker, Fan Control, Rover Control (LiDAR with the live occupancy grid map, camera, GNSS position,
> BNO085 IMU, manual control with a STOP that always goes first, autonomy start/stop, the row executor's field tuning
> set, recording and synchronized playback) and Docs. A STOP is in the status bar of every page. The server is plain Python
> (standard library HTTP server, no web framework); sensors come from ROS 2 bridge containers over WebSocket, manual
> control goes to the motor bridge over a Unix socket, autonomy goes through the row executor's operator API. Since
> 2026-11-16 the vehicle has front-wheel steering driven by one ESP32-S3 with three BTS7960 motor drivers (steering DC
> motor with a belt-coupled potentiometer, left drive, right drive); A/D steers to full lock and a drag bar sets a fine
> angle that returns to 0 on release. The Network page manages the Jetson's Wi-Fi (scan, connect with a password,
> autoconnect, automatic rollback). This version runs on the rover's Jetson since 2026-11-16; steering has not yet been
> tried on the real vehicle. The slow-link work was measured on a PC link emulator, not yet on the rover's real Wi-Fi.
> The documents are in Korean.

| Rover Control (노트북 1440 px) | 휴대폰 (390 px) |
|---|---|
| ![Rover Control, 노트북](docs/assets/screens/rover_control_1440.png) | ![Rover Control, 휴대폰](docs/assets/screens/rover_control_390.png) |

[화면 · 모의 자료] 2026-11-16 판(조향 차량, ESP32-S3 브리지의 상태를 흉내 냄). 화면 점검 도구의 모의 서버(`tools/ui_check`)가 내는 예시 자료로 찍었다(실제 센서 값이 아니다).
왼쪽은 Rover Control의 첫 화면(위 카드의 ESP32-S3 연결, LiDAR, 카메라, 조향 주행 제어: A/D·가운데·끌기 막대·목표와 측정 조향각), 오른쪽은 같은 페이지의 휴대폰 첫 화면이다.
STOP은 모든 페이지의 상태 막대와 주행 제어에 있다. 메뉴는 2026-11-14에 수정 전 구성(8쪽)으로 되돌린 그대로다.

> **지금 상태.** 화면과 서버는 가짜 센서·가짜 MCU·가짜 실행기와 느린 연결 시험대로 확인했다. 2026-11-16 차의 보드가 ESP32-S3(BTS7960 3개: 조향,
> 왼쪽·오른쪽 구동)로 바뀌었고, 같은 날 이 판(대시보드, ESP32-S3 조종 브리지 `ROVER_ESP_KIND=esp32_abs_hold`, Wi-Fi 관리용 호스트 도우미)을
> Jetson에 설치했다. 설치 뒤 브리지는 보드와 연결된 채 조종 비활성(disarmed)이고, 실제 보드에는 상태 읽기와 정지 명령만 보냈다.
> 같은 날 사용자 승인으로 펌웨어에 구동 감시(300 ms)를 넣어 썼고(모터 전원 끈 채, 전체 백업 뒤), 첫 조향 시험을 위해 브리지 구동 상한을 0 으로 뒀다.
> 자율 실행기도 같은 날 SHADOW(차를 움직일 수 없는 상태)로 설치해 `/scan`·IMU를 받는 것까지 봤다.
> 조향·구동·자율은 아직 실차에서 움직여 보지 않았다(첫 시험 순서: [`docs/FIELD_STEER_AUTONOMY_TEST_KO.md`](docs/FIELD_STEER_AUTONOMY_TEST_KO.md)).
> 자세한 것은 [현재 한계](#현재-한계).

## 주요 기능

**메뉴 8쪽.** 메뉴와 페이지 구성은 화면 개편 전과 같다(2026-11-14 사용자 지시로 되돌림, [`docs/SCREENS_KO.md`](docs/SCREENS_KO.md)).
디자인(색·글꼴·부품)과 상태 막대의 STOP, 느린 연결 최적화는 새 판 그대로다. 그때 없던 기능은 Rover Control 안에 있다.

| 메뉴 | 주소 | 하는 일 |
| --- | --- | --- |
| Overview | `/` | CPU·메모리·외부 IP·Docker 요약, CPU / Memory와 Network RX / TX 그래프, ROS2 / Rover 연결, Jetson 정보 |
| Connection | `/connection` | LiDAR·카메라 연결 상태, RPLIDAR 상태 확인, 2D LiDAR 지도(내려받기), 카메라 실시간 화면, 서비스 연결, ROS2 센서 링크 |
| System | `/system` | Jetson CPU(코어별)·메모리·Swap·네트워크·전력 그래프, 디스크 |
| Network | `/network` | 외부·내부 주소, 오프라인 AP / 저장된 Wi-Fi 전환, Wi-Fi 관리(주변 검색, 비밀번호로 연결, 자동 연결·우선순위, 자동 되돌림), RX / TX, 연결 정보 |
| Docker | `/docker` | 컨테이너 CPU·메모리, 목록, 상세·로그, 시작·중지·재시작(확인 창) |
| Fan Control | `/fan` | 온도, 팬 PWM(자동·수동) |
| Rover Control | `/rover-control` | LiDAR(안에 실시간 점유 격자 지도와 실행기 국소 격자), 카메라, GPS 위치(오프라인 지도), IMU, 주행 제어(차동 패드 또는 조향 패널, 조향 가변저항, 조향 보정(접힘), 자율 시작·정지), 현장 조정 값(접힘), 기록 제어, 연동 상태, 기록 데이터(동기 재생, 기록 지도, 내려받기) |
| Docs | `/docs` | 운영 문서 |

화면 6개 판의 주소(`/map`, `/records`, `/tuning`, `/sensors`, `/system/*` 등)는 별칭으로 남아 그 기능이 있는 페이지로 간다.

**센서 실시간.** LiDAR(RPLIDAR S2 `/scan`), 카메라(UVC, 녹화는 원본 1080p), IMU(BNO085 원본, 소프트웨어 보정 없음), GPS(ZED-F9P)를
ROS 2 브리지 컨테이너가 WebSocket으로 보내고 대시보드 서버가 중계한다. 화면이 받은 것을 확인한 뒤에만 다음 것을 보내서 느린 연결에서 밀리지 않는다.
화면에 보이지 않는 패널과 숨긴 탭은 받지 않는다(Rover Control처럼 패널이 많은 페이지에서도 화면에 들어온 패널의 스트림만 연다).

**조종과 STOP.** 조종 활성화 뒤 키보드(W/A/S/D, Q/E로 속도)나 화면 버튼으로 몬다. 손을 떼면 감속하고, 입력이 0.75초 끊기면 차가 스스로 선다.
STOP·Esc·창 이탈은 즉시 정지와 조종 해제다. 조종 요청은 다른 요청과 따로, 가장 먼저 보낸다(`controlRequest`, NET-05).
PWM 값은 요청한 출력이지 실제 바퀴 속도가 아니다(차에 휠 엔코더가 없다).

**조향 차량(수동·자율·조정).** 차는 앞바퀴 조향이다. `DASHBOARD_VEHICLE=front_steer`이거나 조종 브리지가 `vehicle_profile: front_steer`를 내면
Rover Control의 주행 제어 패널에 조향 패널(구동 W/S, 조향 A/D·가운데, 끌어서 조향 각, 목표·측정 조향각)과 조향 가변저항 값이 보인다. 그 아래 접힌 칸 '조향 보정'에서 좌우 끝을 다시 찾아 저장한다(펌웨어 `CAL_RUNTIME=1` 이 있어야 함, [docs/esp_rover_control.md](docs/esp_rover_control.md) 1-4절).
ESP32-S3 브리지에서는 A/D를 누르는 동안 왼쪽·오른쪽 끝(±20 명령 도)까지 가고 놓으면 가운데로 오며, 막대를 끌면 그 각으로 가고 놓으면 0°로 돌아온다
(키와 막대는 먼저 잡은 쪽만 듣고, STOP이 언제나 이긴다). SMOOTH 펌웨어(FW 줄 `SMOOTH=1`, 2026-11-16 저녁)에서는 A/D 한 번이 끊김 없는 한 번의 구동이고
막대는 0.25° 단위로 따라간다. W/S 는 화면이 막지 않고, 브리지 구동 상한(설치 값)을 '구동 duty' 칸 아래에 보인다(1-5절). 접힌 칸 '거리 이동'에서는 지형·거리(앞으로 0.2~3.0 m)를 고르고 누르고 있는 동안 그만큼 간다: 2026-10 조향 장치 전 차의 지형별 duty 표로 시간을 정하고, 줄자로 잰 값으로 지형별 배율을 고친다(1-6절). 브리지와 구동 안전: [`docs/esp_rover_control.md`](docs/esp_rover_control.md). 그 아래 자율 칸에서 "준비 → Arm → 자율 주행 시작"을 누르면
실행기가 열 끝까지 간다(시뮬레이션에서 확인, 실차 전). Rover Control을 떠나거나 탭을 숨기면 화면이 운영자 연결 신호를 멈추고, 실행기의 운영자 연결 기한
(기본 1.0 s, 시뮬레이션 저장소 결정 R60. 화면에 그 값이 보인다)이 지나면 차가 선다(페이지 안에서 스크롤하는 것은 괜찮다),
다시 출발은 사람이 누른다. 현장 조정 값은 같은 페이지의 접힌 칸에 있다. 서버 사양: [`docs/STEERING_SERVER_KO.md`](docs/STEERING_SERVER_KO.md).

| 조향 수동 조종 (휴대폰) | 자율 주행 칸 (실행기 상태 자세히) |
|---|---|
| ![조향 패널](docs/assets/screens/steer_drive_390.png) | ![자율 칸](docs/assets/screens/steer_autonomy_1440.png) |

| 현장 조정 값(펼친 칸) | 실시간 지도 아래 실행기 국소 격자 |
|---|---|
| ![조정 화면](docs/assets/screens/tuning_1440.png) | ![국소 격자](docs/assets/screens/map_local_grid_1440.png) |

[화면 · 모의 자료] 위 네 장은 `front_steer`(조향 차량)의 Rover Control을 모의 서버의 예시 자료로 찍은 것이다(그 부분으로 스크롤한 화면). 휴대폰 그림(2026-11-16)은 ESP32-S3 브리지 응답을 흉내 내어 조종 활성화 뒤 막대를 왼쪽 10°까지 끌고 있는 순간이다(파란 막대가 목표, 검은 막대가 측정, 측정값은 지어낸 값). 실행기 상태·조정 값·격자는 가짜 실행기의 값이고
현장 값이 아니다. 조정 묶음의 이름·범위·단위는 실행기가 알려 준 것을 그대로 보인다.

**느린 연결 최적화.** PC에서 2.4 GHz 연결을 흉내 내는 시험대(`tools/slow_link`)로 재며 고쳤다. 아래는 그 측정의 일부다(주행 화면 2분, LiDAR는 주행 화면의
LiDAR 큰 보기, 지도는 지도 화면 10분. 기준은 화면 개편 전의 한 화면에서 잰 값). 정한 목표 32개 중 20개를 만족한다(기준 5개). 못 미친 것의 대부분은 조종 꼬리 지연으로,
잰 PC가 자주 잠깐씩 멈춘 탓으로 판정했다(같은 PC에서 화면 개편 전 판도 같았다). 모두 PC 한 대에서 연결을 흉내 낸 값이고, 차의 실제 무선 연결 값이 아니다. 목표표, 못 미친 것과 까닭, 한계: [`docs/slow_link.md`](docs/slow_link.md) 12절.
메뉴를 수정 전 구성으로 되돌린 뒤(2026-11-14) Rover Control을 다시 쟀다: 첫 화면에 LiDAR가 다시 있어 나쁨 연결 사용이 같은 때의 화면 6개 판보다 7.9 %p 높고(65.3 %, 목표 60 %),
조종·STOP·카메라는 같았다([`docs/slow_link.md`](docs/slow_link.md) 13절).

<!-- slow-link-summary:begin -->
| 수치 | 연결 | 기준 d962013 (2026-11-12) | 최종 90094fd (2026-11-13) |
| --- | --- | --- | --- |
| 조종 명령 왕복 p50 | 보통 2 Mbit/s | 382 ms | 82.7 ms |
| STOP이 조종 끝점에 닿기까지 p50 | 나쁨 500 kbit/s | 252 ms | 102 ms |
| 카메라 지연 p95 | 나쁨 500 kbit/s | 8295 ms | 691 ms |
| LiDAR 다시 그리는 수 | 나쁨 500 kbit/s | 0.98 Hz | 4.68 Hz |
| 연결 사용률(받는 양 ÷ 속도) | 나쁨 500 kbit/s | 88.3 % | 44.7 % |
| 2분 동안 닫힌 스트림 | 나쁨 500 kbit/s | 6개 | 1개 |
| 처음 열기에 받는 양 | 보통 2 Mbit/s | 1501 kB | 297 kB |
| 다시 열기에서 연결에 나간 페이지 파일 요청 | 보통 2 Mbit/s | 49개 | 1개 |
| 숨긴 탭이 받는 스트림 | 보통 2 Mbit/s | 250 kB/s | 0 kB/s |
| 지도 응답 크기 10분째 ÷ 첫 1분 | 나쁨, 10분 | 8.65배 | 0.16배 |
<!-- slow-link-summary:end -->

**기록·재생.** 녹화는 ROS bag(LiDAR, 원본 IMU, GPS, 조종 요청·MCU 보고 PWM)과 원본 1080p 카메라를 함께 남긴다. 기록 화면에서 센서와 영상을 같은 시각으로
되감아 보고, 기록으로 지도를 다시 만들고, 느린 연결용 영상 화질(HLS)을 고른다([`docs/motor_pwm_recording.md`](docs/motor_pwm_recording.md), [`docs/imu_ros2_recording.md`](docs/imu_ros2_recording.md)).

**Wi-Fi 관리 (Network).** Network 쪽의 "오프라인 AP / 저장된 Wi-Fi" 패널에서 AP 전환(이전과 같음)과 함께 Jetson의 Wi-Fi를 고른다.
주변 검색 목록(신호 dBm·%, 대역·채널, 보안), 비밀번호로 연결, 자동 연결 여부와 우선순위, 저장된 Wi-Fi의 자동 연결·우선순위 바꾸기.
원격에서 연결을 바꾸면 조작자가 끊길 수 있어서 모든 연결 변경은 시험 연결이다: 새 연결이 45초 안에 주소를 못 받거나 실패하면 곧바로,
연결된 뒤 150초 안에 화면의 '이 연결 유지'를 누르지 않으면 Jetson이 이전 연결(Wi-Fi 또는 AP)로 스스로 돌아간다. 비밀번호는 Jetson의 root 전용
NetworkManager 파일에만 들어가고 명령행·로그·응답에는 나오지 않는다. 바꾸는 요청은 세션과 같은 Origin이 있어야 받는다. 권한이 필요한 일은 대시보드가 아니라
호스트 도우미(`deploy/offline_ap/ubts_network_mode.py`, 정해진 명령만)가 한다. 계약과 한계: [`docs/jetson_offline_access.md`](docs/jetson_offline_access.md) "Wi-Fi 관리",
도우미 교체: [`deploy/network_helper_update/README_KO.md`](deploy/network_helper_update/README_KO.md).

| 노트북 1440 px: 주변 Wi-Fi에서 고르고 비밀번호 넣기 | 휴대폰 390 px: 시험 연결 중 남은 시간과 확인 |
|---|---|
| ![Wi-Fi 관리, 노트북](docs/assets/screens/network_wifi_1440.png) | ![Wi-Fi 시험 연결, 휴대폰](docs/assets/screens/network_wifi_390.png) |

[화면 · 모의 자료] 2026-11-16. 화면 점검 도구의 모의 서버에 지어낸 SSID·주소(문서 예시 대역)를 넣어 찍었다. 실제 Jetson의 Wi-Fi 목록이 아니다.
이 기능은 가짜 nmcli 시험과 모의 서버로 확인했고, 2026-11-16 Jetson에 설치했다(대시보드와 호스트 도우미). 같은 날 Jetson의 Wi-Fi를 5 GHz에서
2.4 GHz로 옮겨 자동 연결 우선순위를 2.4 GHz 먼저로 두었다(그 전환은 화면이 아니라 명령으로 했다). 화면의 시험 연결 흐름은 차에서 아직 눌러 보지 않았다.

## 구성

```mermaid
flowchart LR
  subgraph PHONE["휴대폰 · 노트북"]
    B["브라우저<br/>메뉴 8쪽"]
  end
  subgraph JET["Jetson (차)"]
    D["대시보드 서버<br/>dashboard_server<br/>HTTP :8080"]
    subgraph ROS["ROS 2 컨테이너"]
      DRV["센서 드라이버<br/>/scan · 카메라 · IMU · GPS"]
      SB["센서 브리지<br/>LiDAR · 카메라 · IMU · GPS"]
      REC["녹화기<br/>bag · 1080p 원본"]
    end
    MB["조종 브리지<br/>ESP32-S3 ABS-HOLD<br/>(옛 차동 V2·조향 V3)"]
    EX["자율 실행기<br/>row_executor"]
  end
  subgraph ESP["ESP32-S3 (ABS-HOLD 펌웨어)"]
    M1["BTS7960 1<br/>조향 DC 모터<br/>+ 벨트 연결 가변저항"]
    M2["BTS7960 2<br/>왼쪽 구동 모터"]
    M3["BTS7960 3<br/>오른쪽 구동 모터"]
  end
  B <-->|"HTTP /api/*<br/>WebSocket /ws/*"| D
  D <-->|"WebSocket 중계"| SB
  DRV --> SB
  DRV --> REC
  D <-->|"HTTP"| REC
  D <-->|"Unix 소켓<br/>/run/ubts-rover/control.sock"| MB
  D <-->|"HTTP + 토큰<br/>/api/executor/* → 127.0.0.1:8097"| EX
  DRV -->|"/scan · /imu/data_raw"| EX
  EX -->|"lease 보유자로 구동<br/>(ESP32 어댑터)"| MB
  MB <-->|"USB 직렬(네이티브 USB)"| ESP
```

[도식] ESP32-S3 한 장이 모터 드라이버(BTS7960) 세 개를 돌린다. 조향은 보드 안에서 가변저항 값이 목표에 오도록 위치를 잡고(약 ±20°),
구동 둘은 브리지가 보낸 PWM을 그대로 낸다. 화면은 상태를 짧은 HTTP 요청으로, 센서는 WebSocket으로 받는다. 조종 요청은 대시보드 서버가 조종 브리지 하나에 전한다(브리지만 직렬 장치를 연다).
자율주행은 실행기가 브리지 조종권(lease)을 잡고 몰며, 대시보드는 실행기 운영자 API를 대신 부르고 토큰은 화면에 내보내지 않는다.
실행기와 조향 브리지 V3는 시뮬레이션 저장소([`unist_orchard_simulation`](https://github.com/2026-BTS-UNI-Car/unist_orchard_simulation), 팀 비공개)에서 만들었고, 조향 브리지는 이 레포로 옮겨 왔다([`docs/STEERING_HOST_PORT_KO.md`](docs/STEERING_HOST_PORT_KO.md)).

펌웨어 소스(`BTS7960_Serial_Control.ino`, 개발자 제공)에 적힌 배선이다. 차에서 배선을 따로 확인하지는 않았고 보드 사진은 아직 없다.

| BTS7960 | 맡은 일 | R_EN | RPWM | L_EN | LPWM |
| --- | --- | --- | --- | --- | --- |
| 1 | 조향 DC 모터(F = 오른쪽) | 16 | 17 | 18 | 8 |
| 2 | 왼쪽 구동 | 46 | 3 | 9 | 10 |
| 3 | 오른쪽 구동 | 12 | 11 | 13 | 14 |

가변저항은 바깥 단자를 3.3 V·GND에, 가운데 단자를 GPIO6에 잇는다. PWM은 20 kHz, 8비트(0~255). GPIO3·GPIO46은 부팅 설정 핀이라
외부 배선이 부팅에 영향을 줄 수 있다(소스 주석). 명령과 보정 값: [`docs/esp_rover_control.md`](docs/esp_rover_control.md) 1절.

## 빠른 시작

### 1. 화면만 보기 (모의 서버, 표준 라이브러리만)

```
python tools/ui_check/mock_server.py --static static --dataset stress --port 8099
# 브라우저: http://127.0.0.1:8099/rover-control
# 조향 차량 화면: --vehicle front_steer 를 붙인다
```

Windows·Linux 어디서나 된다. 센서·조종 값은 지어낸 예시이고 버튼은 읽기 전용 답만 받는다.

### 2. 개발 실행 (Linux 또는 WSL)

```
python3 -m pip install -r requirements.txt
scripts/run_local.sh            # = python3 -m dashboard_server --host 127.0.0.1 --port 8080
# 브라우저: http://127.0.0.1:8080
```

센서 브리지가 없으면 센서 칸은 "연결 전"으로 보인다. 자주 쓰는 환경 변수:

| 변수 | 뜻 |
| --- | --- |
| `DASHBOARD_VEHICLE` | `differential`(기본, 옛 차동 차) / `front_steer`(조향 차량 화면). 지금 차는 조향 차량이고, 조종 브리지가 `vehicle_profile: front_steer`를 내면 이 값과 상관없이 조향 화면이 된다 |
| `UBTS_EXECUTOR_URL`, `UBTS_EXECUTOR_TOKEN_FILE` | 자율 실행기 운영자 API 주소와 토큰 파일 |
| `UBTS_EXECUTOR_LINK_FILE` | 위 둘이 없을 때 읽는 실행기 연결 파일(기본 `runtime/row_executor/dashboard_link.json`, Jetson 실행기 묶음이 쓴다, 바뀌면 다시 읽음). 셋 다 없으면 자율·조정 칸은 "설정 없음" |
| `LIDAR_WS_UPSTREAM`, `CAMERA_WS_UPSTREAM`, `IMU_WS_UPSTREAM`, `GPS_WS_UPSTREAM` | 센서 브리지 WebSocket 주소 |
| `DASHBOARD_MODE` | `live`(기본) / `simulation`(시뮬레이션 확장을 꽂을 때, [`docs/STEERING_SERVER_KO.md`](docs/STEERING_SERVER_KO.md) 2절) |

시뮬레이션 스택(Gazebo, 가상 센서, 조향 브리지, 실행기)과 함께 보려면 시뮬레이션 저장소 [`unist_orchard_simulation`](https://github.com/2026-BTS-UNI-Car/unist_orchard_simulation)(팀 비공개)의 안내를 따른다. 그 스택은 2026-11-16부터 실제 치수 차로 뜬다.
그 저장소는 이 레포의 한 커밋을 사본으로 가져가 쓴다.

### 3. 차(Jetson)에 설치

> **주의: 차의 대시보드를 `docker compose`로 다시 만들지 않는다.** Jetson의 compose 파일은 옛 이미지(10월 7일)를 가리켜서,
> `docker compose up`(`--build`, `down` 포함)을 하면 옛 대시보드로 돌아간다. 설치는 아래 설치 묶음으로 한다.

설치 묶음은 지금 컨테이너를 보존한 뒤 바뀐 파일만 겹치고, 실패하면 자동으로 되돌린다:
[`deploy/jetson_dashboard/README_KO.md`](deploy/jetson_dashboard/README_KO.md) (점검 → 설치 → 확인 → 되돌리기. 4-1절: 이 묶음만으로 현장에 가는 변경과 브리지를 바꿔야 하는 변경).
차의 무선 AP와 접속 주소(`http://10.77.0.1`)는 [`docs/jetson_offline_access.md`](docs/jetson_offline_access.md), 현장 연결 점검은
[`docs/WIFI_AP_CHECKLIST_KO.md`](docs/WIFI_AP_CHECKLIST_KO.md).

`docker-compose.yml`은 서비스 구성(대시보드, LiDAR·카메라·IMU·GPS 드라이버와 브리지, ESP 조종 브리지, 녹화기, 프로필 `steer`의 조향 브리지)을 적은 것이다.
빈 PC에서 전체를 처음 띄울 때의 참고로 쓰고, 이미 돌고 있는 차에서는 위 주의를 따른다.

## 레포 구조

| 위치 | 내용 |
| --- | --- |
| `dashboard_server/` | 대시보드 서버(Python 표준 라이브러리 HTTP 서버): 화면·정적 파일, 상태 API, WebSocket 중계, 조종 중계(`rover_api.py`), 실행기 중계(`executor_proxy.py`), 조정(`steer_tuning.py`), Wi-Fi 관리 중계(`wifi_manager.py`), 기록·지도, 확장 지점(`extensions.py`) |
| `static/` | 화면: `index.html`, `app/`(화면 스크립트 17개), `steer/`(Rover Control의 조향·자율·조정 값·국소 격자), `network/`(Network 쪽 Wi-Fi 관리), `css/`, `vendor/`(지도·영상 라이브러리, 글꼴) |
| `scripts/` | Jetson에서 도는 ROS 2 드라이버·브리지(`*_ros2_driver.py`, `*_ws_bridge.py`), 조종 브리지(ESP32-S3 `esp32_steer_bridge.py`·`esp32_abs_hold.py`, 고르기 `esp_rover_bridge.py`, 옛 차동 `rover_control_bridge.py`, 조향 V3 `steer_rover_bridge.py`), 녹화기, 기록 후처리 |
| `deploy/` | `jetson_dashboard/`(대시보드 설치 묶음), `jetson_esp32_bridge/`(ESP32-S3 조종 브리지 설치 묶음), `offline_ap/`(무선 AP 전환과 Wi-Fi 관리 호스트 도우미), `network_helper_update/`(호스트 도우미 교체, 점검·자동 되돌림), `systemd/`, `native_sensor_stream/`·`native_sensor_shadow/`(차동 시절 관찰 서비스) |
| `tools/` | `ui_check/`(화면 점검), `slow_link/`(느린 통신 시험대, 현장 연결 기록 보고), `fonts/`(화면 글꼴 분할), 차동 시절 관찰 도구 |
| `tests/` | Python 시험(`test_*.py`)과 화면 시험(`test_*.js`, `test_*.mjs`) |
| `config/` | 대시보드 설정 |
| `docs/` | 문서([색인](docs/README_KO.md)), `assets/`(README 그림) |
| `Dockerfile`, `Dockerfile.ros2`, `Dockerfile.lidar_driver`, `docker-compose.yml` | 대시보드 이미지, ROS 2 드라이버·브리지 이미지, 서비스 구성 |

## 문서

전체 목록과 각 문서가 지금 코드와 맞는지는 [`docs/README_KO.md`](docs/README_KO.md)에 있다. 자주 보는 것:

| 문서 | 내용 |
| --- | --- |
| [`docs/SCREENS_KO.md`](docs/SCREENS_KO.md) | 메뉴 8쪽(수정 전 구성), Rover Control 안의 새 기능 자리, 화면 6개 주소의 별칭 |
| [`docs/STEERING_SERVER_KO.md`](docs/STEERING_SERVER_KO.md) | 조향 차량 서버 사양: 조종 API, 조향 상태, 자율 시작·정지 중계, 조정 값, 정지 규칙, 차동 판과의 공존 |
| [`docs/slow_link.md`](docs/slow_link.md) | 느린 연결: 시험대, 기준 측정, 목표 32개, 고친 뒤와 최종 측정 |
| [`docs/WIFI_AP_CHECKLIST_KO.md`](docs/WIFI_AP_CHECKLIST_KO.md) | 차의 무선 AP 현장 점검표, 연결 기록(`?link_log=1`) |
| [`deploy/jetson_dashboard/README_KO.md`](deploy/jetson_dashboard/README_KO.md) | Jetson 설치·되돌리기 |
| [`docs/esp_rover_control.md`](docs/esp_rover_control.md) | ESP32-S3 조종 브리지(BTS7960 3개, 부호, 구동 안전과 남는 위험), 옛 ESP8266 조종(차동 V2) |
| [`deploy/jetson_esp32_bridge/README_KO.md`](deploy/jetson_esp32_bridge/README_KO.md) | Jetson ESP32-S3 브리지 설치·되돌리기, 현장 첫 시험 순서 |
| [`docs/stream_latency.md`](docs/stream_latency.md) | 센서 실시간 전송과 확인 방식 |
| [`docs/HISTORY.md`](docs/HISTORY.md) | 지난 보고 문서와 검증 기록이 있는 곳(보존 태그) |

## 시험

```
# 화면 시험 (Node 22 이상)
for f in tests/test_*.js tests/test_*.mjs; do node "$f" >/dev/null 2>&1 || echo "FAIL $f"; done

# Python 시험 (Linux. Windows 에서는 fcntl 을 쓰는 시험이 모이지 않는다)
python3 -m unittest discover -s tests

# ESP32-S3 브리지 (가짜 보드. pty 끝단 시험은 Linux)
python3 -m unittest tests.test_esp32_bridge tests.test_esp32_bridge_linux tests.test_jetson_esp32_bridge_kit tests.test_esp32_smooth
python3 -m unittest tests.test_drive_distance tests.test_drive_distance_route
node --test tests/test_drive_distance.mjs
node --test tests/test_steer_drag.mjs

# Wi-Fi 관리 (가짜 nmcli. 화면 시험은 Node 22 이상)
python3 -m unittest tests.test_network_wifi_api tests.test_network_wifi_helper tests.test_network_helper_update tests.test_network_mode
node --test tests/test_network_wifi_ui.mjs

# 조종 회귀 (조종·STOP 경로를 바꿨을 때 꼭)
node tests/test_control_path_regression.js && node tests/test_rover_browser.js && node tests/test_rover_pad_touch.js
python3 -m unittest tests.test_control_deadman_regression

# 화면 점검: 메뉴 8쪽 × 5화면 크기 × 자료 2종 = 80쪽, 받아들인 기록과 비교 (Playwright, 깔린 Chrome)
python tools/ui_check/run_checks.py --static static --out <결과 폴더>
python tools/ui_check/compare.py --run <결과 폴더>/ui_check.json --baseline tools/ui_check/accepted/ui_check.json

# 느린 연결 시험대: 받아들인 값과 견주기 (사용법 tools/slow_link/README_KO.md)
python -B tools/slow_link/compare.py --run <결과 폴더>
```

CI(`.github/workflows/smoke.yml`)는 컴파일과 Python 시험을 돌린다. ROS가 있어야 하는 시험은 ROS 없이 건너뛴다.

## 현재 한계

- **실차 확인 전.** 이 판(메뉴 8쪽, 느린 연결 최적화, 조향 칸, Wi-Fi 관리)은 PC에서 가짜 센서·MCU·실행기로 확인했다. 2026-11-16 Jetson에 설치해
  페이지가 열리고 브리지가 보드와 연결되는 것까지 봤지만, 조종·조향·자율은 차에서 아직 눌러 보지 않았다.
- **ESP32-S3 보드는 차에서 확인 전.** 2026-11-16 차에 ESP32-S3(ABS-HOLD 펌웨어)가 달렸다. 브리지는 가짜 보드로 시험했고, 실제 보드에는 상태 읽기와 정지 명령만 보냈다(펌웨어 쓰기 한 번 제외, 아래).
  구동 방향(모터 2·3 의 F 가 앞인지), 명령 눈금과 실제 바퀴 각도의 관계는 재지 않았다. 원본 펌웨어에는 구동 시간 제한이 없어, 2026-11-16 사용자 승인으로
  구동 감시 패치(명령이 300 ms 끊기면 구동 정지)를 보드에 썼다([`docs/esp_rover_control.md`](docs/esp_rover_control.md) 1절). 첫 조향 시험을 위해 브리지는 지금
  구동 PWM 상한 0 으로 설치돼 있다(구동 방향 시험 때 푼다). 물리 비상정지는 그대로 마지막 수단이다.
- **자율 실행기는 SHADOW 로만 설치됨.** 실행기가 ESP32 브리지와 말하는 어댑터(시뮬레이션 레포 `docs/steering/EXECUTOR_ESP32_KO.md`)는 가짜 보드로 확인했다. Jetson 에는 2026-11-16 SHADOW(조종 소켓 없음)로 설치해 `/scan`·IMU 를 받는 것까지 봤다([`deploy/jetson_row_executor/README_KO.md`](deploy/jetson_row_executor/README_KO.md)). 차를 움직이는 MOTOR 는 현장 측정(구동 방향, 조향 극성·눈금·속도, 구동 C2·C3·C7)과 결정(조향 GO 문턱, 펌웨어 구동 감시) 뒤에만 쓴다([`docs/FIELD_STEER_AUTONOMY_TEST_KO.md`](docs/FIELD_STEER_AUTONOMY_TEST_KO.md)).
  차동 시절 자율·관찰 코드는 조향 실행기로 바뀔 때까지 남겨 둔다(결정 나5, [`docs/STEERING_SERVER_KO.md`](docs/STEERING_SERVER_KO.md) 9절).
- **느린 연결 값은 흉내 낸 값.** 연결 프로필(좋음 10 Mbit/s ~ 아주 나쁨 150 kbit/s)과 연결 등급 경계(25 / 90 / 220 ms)는 가정이다.
  현장에서 `?link_log=1`로 기록을 받아 고친다. 목표 32개 중 못 미친 것과 까닭은 [`docs/slow_link.md`](docs/slow_link.md) 12절.
- **브리지 쪽 변경은 이 설치 묶음에 없다.** LiDAR 이진 형식, IMU 흐름, GPS 응답 기한은 ROS 브리지 이미지를 다시 만들어야 차에 간다
  ([설치 문서 4-1절](deploy/jetson_dashboard/README_KO.md)). 대시보드만 바꿔도 옛 브리지와 함께 동작한다.
- **휠 엔코더가 없다.** 화면의 PWM·duty는 요청한 출력이고 속도가 아니다. 위치·지도는 LiDAR·IMU(와 품질이 맞는 GNSS)로만 만든다.
- 화면 그림은 모의 자료로 찍었다. 실차 화면과 실물 사진은 아직 없다.

## 라이선스

이 레포의 코드에는 아직 라이선스 파일이 없다(팀이 정한다). 함께 들어 있는 제3자 구성요소는 각자의 라이선스를 따른다.

| 구성요소 | 위치 | 라이선스 |
| --- | --- | --- |
| hls.js | `static/vendor/hls/` | Apache-2.0 (`LICENSE`) |
| MapLibre GL JS | `static/vendor/maplibre-gl/` | BSD-3-Clause (`LICENSE.txt`) |
| PMTiles | `static/vendor/pmtiles/` | BSD-3-Clause (`LICENSE.txt`) |
| Protomaps basemaps | `static/vendor/protomaps_basemaps/` | BSD-3-Clause, 지도 모양 CC0 (`LICENSE.txt`) |
| Pretendard(화면 글꼴 UBTS Sans의 원본) | `static/vendor/ubts-sans/` | SIL Open Font License 1.1 (`PRETENDARD-OFL.txt`) |
| 본고딕(Source Han Sans) 계열 한글 글꼴 | `static/vendor/korean-font/` | SIL Open Font License 1.1 (`KOREAN-OFL.txt`) |
| 관찰 도구 두 개 | `tools/cartographer_incremental_capture/`, `tools/gnss_receiver_utc/` | Apache-2.0 (`LICENSE`) |

제3자 파일의 판과 출처 해시는 `static/vendor/gps-map-vendors.json`, `static/vendor/hls/provenance.json`에 있다.
