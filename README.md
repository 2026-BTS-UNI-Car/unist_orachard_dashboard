# unist_orachard_dashboard

UNIST BTS 과수원 로버의 웹 대시보드입니다. 차에 실린 Jetson에서 돌며, 휴대폰이나 노트북 브라우저로 센서를 실시간으로 보고,
차를 조종하고 STOP을 누르고, 자율주행을 시작·정지하고, 주행 기록을 남기고 다시 봅니다. 차의 2.4 GHz 무선 AP처럼 느리고 끊기는 연결에서도
조종과 STOP이 먼저 지나가게 만들었습니다.

> **English summary.** Web dashboard of a small orchard rover (a converted ride-on toy car). It runs on the rover's
> Jetson Orin Nano and is opened from a phone or laptop over the rover's own 2.4 GHz Wi-Fi access point. Eight menu
> pages, as before the redesign: Overview, Connection (2D LiDAR RPLIDAR S2, USB camera, link status), System (Jetson
> resources), Network, Docker, Fan Control, Rover Control and Docs. Rover Control has four tabs inside the page: manual
> driving (LiDAR, live occupancy grid map, camera, GNSS position, BNO085 IMU, steering and drive with a STOP that always
> goes first), autonomy (row length, GPS on/off, speed, three buttons: prepare, start, stop; a monitoring view with a
> moving picture of the row executor's local grid; every run is recorded), set-up (steering calibration, tape
> corrections, the executor's field tuning set) and records (recording control, synchronized playback with the recorded
> map). A STOP is in the status bar of every page. The server is plain Python (standard library HTTP server, no web
> framework); sensors come from ROS 2 bridge containers over WebSocket, manual control goes to the motor bridge over a
> Unix socket, autonomy goes through the row executor's operator API. Since 2026-11-16 the vehicle has front-wheel
> steering driven by one ESP32-S3 with three BTS7960 motor drivers (steering DC motor with a belt-coupled potentiometer,
> left drive, right drive). The dashboard was used on the vehicle for the indoor corridor tests (2026-11-18/19) and the
> first orchard autonomy day (2026-11-20: 18 runs started from the autonomy tab, about 772 m, each recorded and played
> back here). The grid map takes its heading from the IMU orientation since 2026-11-21. The slow-link numbers were
> measured on a PC link emulator, not on the rover's real Wi-Fi. The screenshots are taken from the mock server. The
> documents are in Korean.

| Rover Control (노트북 1440 px) | 휴대폰 (390 px) |
|---|---|
| ![Rover Control, 노트북](docs/assets/screens/rover_control_1440.png) | ![Rover Control, 휴대폰](docs/assets/screens/rover_control_390.png) |

[화면 · 모의 자료] 2026-11-21 판(조향 차량, ESP32-S3 브리지의 상태를 흉내 냄). 화면 점검 도구의 모의 서버(`tools/ui_check`)가 내는 예시 자료로 찍었다(실제 센서 값이 아니다).
왼쪽은 Rover Control의 첫 화면(상태 띠, 탭 네 개, 수동 조종 탭의 LiDAR·카메라·주행 제어), 오른쪽은 같은 페이지의 휴대폰 첫 화면이다.
STOP은 모든 페이지의 상태 막대와 주행 제어에 있다. 메뉴는 2026-11-14에 수정 전 구성(8쪽)으로 되돌린 그대로이고, 새 기능은 Rover Control 안의 탭에 있다.

> **지금 상태 (2026-11-21).** 이 판은 차의 Jetson에 설치돼 있다. 2026-11-18·19 실내 복도 시험과 2026-11-20 과수원 첫 자율주행에 썼다:
> 그날 자율주행 탭에서 18번 출발했고(약 772 m, 10번은 열 끝까지), 주행마다 모든 센서가 자동으로 기록됐다. 주행 결과는 시뮬레이션 저장소의
> 결과 문서(`docs/steering/ORCHARD_1120_RESULT_KO.md`)에 있다. 조종 브리지는 ESP32-S3(BTS7960 3개: 조향, 왼쪽·오른쪽 구동)용이고 구동 상한 255,
> 자율 실행기는 MOTOR(실제 구동)로 설치돼 있다. 그날 드러난 화면 문제 가운데 기록 재생이 지도를 기다리며 멈추던 것과 격자 지도가 긴 열을 휘게
> 그리던 것은 2026-11-21에 고쳤다([`docs/SCREENS_KO.md`](docs/SCREENS_KO.md) 2-0절, [`docs/GRID_MAP_KO.md`](docs/GRID_MAP_KO.md)). 고친 뒤의 판은 Jetson에서
> 기록 재생으로 확인했고, 차를 몰면서는 아직 써 보지 않았다. 화면 그림은 모의 자료로 찍은 것이다. 자세한 것은 [현재 한계](#현재-한계).

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
| Rover Control | `/rover-control` | 페이지 안 탭 네 개. **수동 조종**: LiDAR, 실시간 점유 격자 지도, 카메라, GPS 위치(오프라인 지도), IMU, 주행 제어(조향 조종, 거리 이동). **자율주행**: 열 길이·GPS·속도, 준비·자율 주행 시작·멈추기, 주행 감시(실행기 국소 격자의 움직이는 그림), LiDAR, 자율주행 기록. **보정·설정**: 조향 가변저항, 조향 보정, 거리 이동 줄자 보정, 현장 조정 값, 연동 상태. **기록**: 기록 제어, 기록 데이터(동기 재생, 기록 지도, 내려받기) |
| Docs | `/docs` | 운영 문서 |

화면 6개 판의 주소(`/map`, `/records`, `/tuning`, `/sensors`, `/system/*` 등)는 별칭으로 남아 그 기능이 있는 페이지로 간다.

**센서 실시간.** LiDAR(RPLIDAR S2 `/scan`), 카메라(UVC, 녹화는 원본 1080p), IMU(BNO085 원본, 소프트웨어 보정 없음), GPS(ZED-F9P)를
ROS 2 브리지 컨테이너가 WebSocket으로 보내고 대시보드 서버가 중계한다. 화면이 받은 것을 확인한 뒤에만 다음 것을 보내서 느린 연결에서 밀리지 않는다.
화면에 보이지 않는 패널과 숨긴 탭은 받지 않는다(Rover Control처럼 패널이 많은 페이지에서도 화면에 들어온 패널의 스트림만 연다).

**조종과 STOP.** 조종 활성화 뒤 키보드(W/A/S/D, Q/E로 속도)나 화면 버튼으로 몬다. 손을 떼면 감속하고, 입력이 0.75초 끊기면 차가 스스로 선다.
STOP·Esc·창 이탈은 즉시 정지와 조종 해제다. 조종 요청은 다른 요청과 따로, 가장 먼저 보낸다(`controlRequest`, NET-05).
PWM 값은 요청한 출력이지 실제 바퀴 속도가 아니다(차에 휠 엔코더가 없다).

**Rover Control의 탭 네 개 (2026-11-19~).** 상태 띠 아래 탭 줄이 보일 부분을 고른다(수동 조종, 자율주행, 보정·설정, 기록). 고른 탭은 그 브라우저에 남고,
자율주행 탭 이름 옆에는 주행 상태 배지가 뜬다. STOP은 상태 막대와 각 탭에 있고 Esc도 어느 탭에서나 멈춘다. 조종 활성화를 쥔 채 수동 조종 탭을 떠나면 STOP을 보낸다.
탭마다 있는 것과 규칙은 [`docs/SCREENS_KO.md`](docs/SCREENS_KO.md) 2-0절.

**수동 조종 (조향 차량).** 차는 앞바퀴 조향이다. `DASHBOARD_VEHICLE=front_steer`이거나 조종 브리지가 `vehicle_profile: front_steer`를 내면
주행 제어 패널이 조향 패널이 된다(구동 W/S, 조향 A/D·가운데, 끌어서 조향 각, 목표·측정 조향각). 휴대폰·태블릿 폭(900 px 미만)에서는 주행 제어 패널이 맨 위에 온다.
ESP32-S3 브리지에서는 A/D를 누르는 동안 왼쪽·오른쪽 끝(±20 명령 도)까지 가고 놓으면 가운데로 오며, 막대를 끌면 그 각으로 가고 놓으면 0°로 돌아온다
(키와 막대는 먼저 잡은 쪽만 듣고, STOP이 언제나 이긴다). SMOOTH 펌웨어에서는 A/D 한 번이 끊김 없는 한 번의 구동이고 막대는 0.25° 단위로 따라간다.
구동 속도 상한은 0~100 %에서 고르고, 브리지 구동 상한(설치 값)을 '구동 duty' 칸 아래에 보인다. '거리 이동'에서는 거리(앞·뒤)와 속도(duty)를 고르고
누르고 있는 동안 그만큼 간다: 2026-10 조향 장치 전 차의 duty 표로 시간을 정하고, 줄자로 잰 값으로 속도별 배율을 고친다(보정·설정 탭의 줄자 보정).
브리지와 구동 안전: [`docs/esp_rover_control.md`](docs/esp_rover_control.md).

**자율주행 (열 따라가기).** 자율주행 탭에서 열 길이, GPS 사용 여부, 속도를 고르고 버튼 셋을 쓴다. **준비**는 실행기 상태를 새로 읽고 필요한 것(멈춤 확인, 열린 주행 끝내기, 시작, Arm)을
차례로 한다. 통로를 잡아 출발할 수 있게 되면 **자율 주행 시작**이 켜지고, 누르면 모든 센서 기록을 먼저 켠 뒤 출발한다(기록을 못 켜면 출발하지 않는다).
**멈추기**는 늘 켜져 있다. 상태 한 줄이 지금 상태와 다음 할 일을 문장으로 알린다('통로를 찾고 있습니다.', '주행 중 · 12.3 m · 0.38 m/s · 35초', '열 끝에 도착해 멈췄습니다.').
주행 감시에는 타일 일곱 개(상태, 속도, 횡 오차, 남은 거리, 주행 시간, GPS, 기록)와 실행기 국소 격자의 움직이는 그림(차를 한자리에 두고 바닥이 흐른다: 점유 셀, 두 나무 줄, 중심선, 지나온 길)이 있다.
같은 탭에서 LiDAR 그림을 실시간으로 받는다. Rover Control을 떠나거나 브라우저 탭을 숨기면 화면이 운영자 연결 신호를 멈추고, 실행기의 운영자 연결 기한(기본 1.0 s)이 지나면 차가 선다.
다시 출발은 사람이 누른다. 실행기가 SHADOW(연습 모드)면 상태 줄이 차가 움직이지 않는다고 알린다. 2026-11-20 과수원에서 이 탭으로 18번 출발했다.
서버 사양: [`docs/STEERING_SERVER_KO.md`](docs/STEERING_SERVER_KO.md), 현장 절차: [`docs/ORCHARD_DAY_20261120_KO.md`](docs/ORCHARD_DAY_20261120_KO.md).

**보정·설정.** 조향 가변저항 값(측정 각도, 원값, 조향 펌웨어 SMOOTH·PULSE 줄, 결함), 조향 보정(지금 위치를 가운데로, 영점을 0.2°씩, 끝 찾기와 적용은 접힘; 펌웨어 `CAL_RUNTIME=1` 이 있어야 함,
[docs/esp_rover_control.md](docs/esp_rover_control.md) 1-4절), 거리 이동 줄자 보정, 실행기의 현장 조정 값(접힘: 기본·고급·잠금 묶음, 프리셋, 변경 기록), 연동 상태(접힘)가 있다.

| 자율주행 탭 (노트북 1440 px, 주행 중) | 자율주행 탭 (휴대폰 390 px) |
|---|---|
| ![자율주행 탭](docs/assets/screens/rover_autonomy_1440.png) | ![자율주행 탭, 휴대폰](docs/assets/screens/rover_autonomy_390.png) |

| 수동 조종의 조향 패널 (휴대폰) | 보정·설정 탭 | 기록 탭 |
|---|---|---|
| ![조향 패널](docs/assets/screens/rover_drive_390.png) | ![보정·설정 탭](docs/assets/screens/rover_setup_1440.png) | ![기록 탭](docs/assets/screens/rover_record_1440.png) |

[화면 · 모의 자료] 2026-11-21. `front_steer`(조향 차량)의 Rover Control을 모의 서버의 예시 자료로 찍은 것이다. 주행 상태, 실행기 격자, 조향 각, 보정 값, 기록 목록은 모두 지어낸 값이고
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

**기록·재생.** 녹화는 ROS bag(LiDAR, 원본 IMU, GPS, 조종 요청·MCU 보고 PWM, 조향 상태, 실행기 상태)과 원본 1080p 카메라를 함께 남긴다. 자율주행은 달린 구간마다 기록 하나가 자동으로 생기고
(출발과 다시 출발마다 새 기록, 주행이 멈추면 끝), 구간 기록에 주행 시간·거리·끝난 이유·다시 출발 여부가 남는다. 기록 목록은 탭에 따라 나뉜다(수동 주행은 기록 탭, 자율주행은 자율주행 탭).
재생은 센서와 영상을 같은 시각으로 되감아 보여 주고, 자율주행 기록에서는 기록된 실행기 상태를 주행 감시와 같은 움직이는 그림으로 그린다. 영상과 센서는 바로 재생되고 기록 지도가 뒤따른다(2026-11-21부터.
그 전에는 지도가 0.35초 넘게 늦으면 재생을 멈추고 기다려서, 터널 접속에서 재생이 0.58배로 느려졌다). 기록 지도는 그 기록을 처음 재생할 때 Jetson이 재생 시각까지 만든다(잰 값: 실시간보다 1.1~1.7배 빠르게).
느린 연결용 영상(HLS, 1080~240p)은 녹화기가 한 번에 기록 하나씩 만든다(1080p 한 장에 0.73초, 기록 길이의 11~12배가 걸린다. 기다리는 줄은 32개까지, 2026-11-21 전에는 8개).
주행 중 Jetson이 꺼져 녹화기가 끝맺지 못한 기록(bag이 닫히지 않고 타임라인이 없어 재생되지 않음)은 녹화기 컨테이너 안에서 `scripts/recover_interrupted_recording.py`로 마무리한다: bag 색인을 다시 만들고, bag을 녹화기 코드에 받은 시각 그대로 다시 흘려 타임라인·미리보기·지도 원본을 만든 뒤 영상 준비 줄에 넣는다(bag과 옆 기록 파일은 바꾸지 않는다). 2026-11-20 17:33 기록을 이것으로 되살렸다.
([`docs/motor_pwm_recording.md`](docs/motor_pwm_recording.md), [`docs/imu_ros2_recording.md`](docs/imu_ros2_recording.md), [`docs/SCREENS_KO.md`](docs/SCREENS_KO.md) 2-0절)

**격자 지도.** 실시간 지도와 기록 지도는 같은 위치 추정기를 쓴다: LiDAR 스캔을 앞 스캔(또는 1.25 m마다 잡는 기준 스캔)에 맞춰 움직임을 재고, 방향은 IMU 방향의 변화를 따르며(2026-11-21부터),
GNSS는 속도와 큰 어긋남을 거르는 데만 쓴다. 조향 각, 구동 PWM, 카메라는 쓰지 않는다(휠 엔코더는 없다). 2026-11-20 과수원 기록으로 재 보니 고치기 전에는 100 m 열을 7~18° 휘게 그렸고,
고친 뒤에는 1° 안이다. 방법, 측정, 한계: [`docs/GRID_MAP_KO.md`](docs/GRID_MAP_KO.md). 이 변경 전에 만든 기록 지도는 '새 지도 만들기'를 누른 뒤 재생하면 새 방식으로 다시 만들어진다.

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

[화면 · 모의 자료] 2026-11-21(Network 쪽을 정리한 뒤의 화면). 화면 점검 도구의 모의 서버에 지어낸 SSID·주소(문서 예시 대역)를 넣어 찍었다. 실제 Jetson의 Wi-Fi 목록이 아니다.
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
| [`docs/GRID_MAP_KO.md`](docs/GRID_MAP_KO.md) | 격자 지도를 그리는 법(쓰는 센서, 위치 추정), 2026-11-21 방향 추정 변경과 과수원 기록으로 잰 결과, 한계 |
| [`docs/ORCHARD_DAY_20261120_KO.md`](docs/ORCHARD_DAY_20261120_KO.md) | 과수원 자율주행 날의 현장 절차(준비, 버튼 셋, 기록, 멈췄을 때) |
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

- **차에서 쓴 범위.** 2026-11-18·19 실내 복도와 2026-11-20 과수원 하루다. 수동 조종, 조향, 자율 시작·정지, 기록, 재생을 차에서 썼다. 2026-11-21에 고친 것(기록 재생, 격자 지도의 방향)은
  Jetson에 설치해 그날의 기록으로 확인했고, 차를 몰면서는 아직 써 보지 않았다.
- **ESP32-S3 브리지.** 2026-11-20 주행 중 브리지 오류가 7번 났다(Jetson에서 보드로 가는 명령이 잠깐 끊김: 보드의 구동 감시 2번, 브리지의 쓰기 기한 5번). 일곱 번 모두 차는 섰고 원인은 가르지 못했다.
  그날 밤 브리지에 원인 기록(`link_diag`, `slow_step`)을 넣었다. 명령 눈금과 실제 바퀴 각도의 관계는 재지 않았다. 펌웨어에는 구동 감시(명령이 300 ms 끊기면 구동 정지)가 들어 있고
  ([`docs/esp_rover_control.md`](docs/esp_rover_control.md) 1절), 물리 비상정지는 그대로 마지막 수단이다.
- **자율 실행기.** MOTOR로 설치돼 있다([`deploy/jetson_row_executor/README_KO.md`](deploy/jetson_row_executor/README_KO.md)). 과수원에서 끝까지 간 10번은 모두 운영자가 넣은 열 길이의 거리 한도에서 섰고,
  LiDAR로 열 끝을 보고 선 적은 없다. 주행 값(횡 오차, 거리)은 실행기의 자체 추정이고 줄자로 대조하지 않았다. 차동 시절 자율·관찰 코드는 남겨 둔다(결정 나5, [`docs/STEERING_SERVER_KO.md`](docs/STEERING_SERVER_KO.md) 9절).
- **Jetson.** 2026-11-20 주행 중 한 번 재시작했다(Wi-Fi 카드 오류 뒤 커널 기록이 폭주하던 날, 원인 미확정). 현장에서 Wi-Fi 방식을 여러 번 바꾼 뒤에는 Jetson을 한 번 다시 켜고 주행한다.
  인터넷 없이 켜면 Jetson 시계가 틀릴 수 있어, 화면은 상태의 나이를 화면 쪽 시계로 잰다.
- **기록 용량과 영상 준비.** 모든 센서 기록은 5.6분에 약 4.8 GB다(2026-11-20, 18개 약 40 GB). 영상 변환은 한 번에 하나씩이고 기다리는 줄은 32개까지다(2026-11-21에 8개에서 늘림. 그날 18개 가운데 9개가 줄을 넘어 손으로 다시 걸었다).
  기록 지도는 한 번에 기록 하나만 만든다.
- **격자 지도.** 위치와 방향은 LiDAR·IMU(와 품질이 맞는 GNSS)로만 추정한다. 방향은 IMU가 흐르는 만큼(2026-11-20 기록에서 5~8분에 1.5° 안) 흐르고, 한 바퀴 돌아와 맞추는 보정은 없다.
  측량 기준과 견준 정확도는 없다([`docs/GRID_MAP_KO.md`](docs/GRID_MAP_KO.md) 6절).
- **느린 연결 값은 흉내 낸 값.** 연결 프로필(좋음 10 Mbit/s ~ 아주 나쁨 150 kbit/s)과 연결 등급 경계(25 / 90 / 220 ms)는 가정이다.
  현장에서 `?link_log=1`로 기록을 받아 고친다. 목표 32개 중 못 미친 것과 까닭은 [`docs/slow_link.md`](docs/slow_link.md) 12절.
- **브리지 쪽 변경은 이 설치 묶음에 없다.** LiDAR 이진 형식, IMU 흐름, GPS 응답 기한은 ROS 브리지 이미지를 다시 만들어야 차에 간다
  ([설치 문서 4-1절](deploy/jetson_dashboard/README_KO.md)). 대시보드만 바꿔도 옛 브리지와 함께 동작한다. 녹화기(영상 변환 줄 포함)도 이미지 안에 있다.
- **휠 엔코더가 없다.** 화면의 PWM·duty는 요청한 출력이고 속도가 아니다.
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
