# 메뉴와 페이지 구성 (UI-16, 수정 전 구성으로 되돌림)

- 작성: 2026-11-14 (브랜치 `ui-menu-restore-20261114`, 시작 `a92cfec`).
- 사용자 지시(2026-11-14): "대시보드 메뉴 구성이 수정하기 전 버전이랑 동일해야해." 답 세 가지:
  1. 메뉴는 수정 전과 같은 8개(영문 이름, 같은 순서, 같은 주소). Research(`/research`)는 되살리지 않는다.
  2. 각 페이지의 내용도 수정 전의 페이지와 순서로 돌린다.
  3. 그때 없던 기능(앞바퀴 조향 조종, 자율 시작·정지, 현장 조정 값, 실행기 국소 격자, 조향 가변저항 표시)은 Rover Control 안에 둔다. 메뉴 항목을 더하지 않는다.
- 기준: 수정 전은 `becfc6d`(태그 `main-before-ui-20261112` 뒤에 1차 버그 수정만 들어간 판)의 `static/index.html`이다.
  `tests/test_menu_restore.js`가 메뉴(이름·순서·주소)와 페이지마다의 패널 제목(순서 포함)을 이 판과 견준다(`tests/fixtures/menu_before_redesign_becfc6d.json`).
- 그대로 둔 것: 새 디자인(색·글꼴·부품, 아이콘), 모든 페이지 위의 상태 막대와 STOP, 느린 연결 최적화(필요할 때 받는 라이브러리, 보이는 패널만 스트림,
  상태 묶음, 연결 등급), 조종·STOP 코드(`static/app/10-rover-control.js`, `controlRequest()`, `static/steer/steer-drive.mjs`, `steer-control-rules.js`, `auto-panel.mjs`는 글자 그대로. `auto-panel.mjs`에는 PW-10의 온라인 배율 줄과 주행 뒤 저장 묻기만 더했고 조종·STOP·heartbeat 줄은 그대로).
- 2026-11-13의 화면 6개 구성(UI-06)은 이 문서의 git 이력(`a92cfec`)에 있다. 화면 점검의 그때 기록은 `tools/ui_check/accepted/six_screens_20261113.json`.

## 1. 메뉴와 주소

| 메뉴 | 주소 | 페이지 제목 | 패널(수정 전 순서) |
| --- | --- | --- | --- |
| Overview | `/` | UBTS Dashboard | 상태 띠(CPU, 메모리, 외부 IP, Docker), CPU / Memory, Network RX / TX, ROS2 / Rover, Jetson 정보 |
| Connection | `/connection` | 연결 상태 | 상태 띠(LiDAR, 프로토콜, 스캔 수신, 카메라), RPLIDAR 상태 확인, 2D LiDAR 지도, 카메라 실시간 화면, 서비스 연결, ROS2 센서 링크 |
| System | `/system` | 시스템 리소스 | CPU, Memory, Swap, Network, Power, Disk |
| Network | `/network` | 네트워크 상태 | 상태 띠(외부 IP, 내부 주소), 오프라인 AP / 저장된 Wi-Fi, RX / TX, 연결 정보 |
| Docker | `/docker` | 컨테이너 대시보드 | 상태 띠, Container CPU, Container Memory, 컨테이너 목록, 컨테이너 상세(중지·재시작 전 확인 창) |
| Fan Control | `/fan` | Fan Control | 온도, 팬 출력, Fan Temperature |
| Rover Control | `/rover-control` | Rover Control | 2절 |
| Docs | `/docs`, `/docs/<문서>` | 문서 | 문서 목록, 읽기 |

화면 6개 구성의 주소는 별칭으로 남는다. 들어오면 페이지가 주소창을 메뉴의 주소로 바꿔 적고(`history.replaceState`), Rover Control의 한 부분을
가리키던 주소는 그 부분으로 한 번 스크롤한다(`ROUTE_FOCUS`, `static/app/00-config-state.js`). 서버(`APP_ROUTES`, `dashboard_server/app.py`)는 둘 다에 같은 쪽을 준다.

| 옛 주소(화면 6개) | 가는 곳 |
| --- | --- |
| `/drive` | `/rover-control` |
| `/map` | `/rover-control`, 실시간 점유 격자 지도로 스크롤 |
| `/records` | `/rover-control`, 기록 제어로 스크롤 |
| `/tuning` | `/rover-control`, 현장 조정 값을 펼치고 스크롤 |
| `/sensors` | `/connection` |
| `/system/resources`, `/system/network`, `/system/docker`, `/system/fan` | `/system`, `/network`, `/docker`, `/fan` |
| `/research` | `/` (연구 페이지는 없앤 채로 둔다, 결정 나15) |

## 2. Rover Control

수정 전 순서: 상태 띠(ESP8266, 모드, ROS 2, 기록) → LiDAR(안에 실시간 점유 격자 지도) → 카메라 → GPS · 로버 위치 → IMU → 주행 제어 → 기록 제어 → 연동 상태 → 기록 데이터(안에 기록된 점유 격자 지도, Recorded IMU, 기록된 로버 위치, 기록된 모터 핀 신호).
넓은 화면(900 px 이상)에서는 수정 전처럼 세 칸이다: LiDAR | 카메라 | 주행 제어, 그 아래 기록 제어(셋째 칸), 그 아래 GPS(전체 폭), IMU와 현장 조정 값, 연동 상태, 기록 데이터.
900 px 미만(휴대폰, 태블릿 세로, 휴대폰 가로)에서는 주행 제어 패널을 맨 앞에 보인다(CSS `order`, 2026-11-17). 휴대폰 한 칸에서 주행 제어가 LiDAR·카메라·GPS·IMU 아래 약 4,100 px(5 화면) 밑에 있었기 때문이다.
마크업 순서(패널 제목 순서, `tests/test_menu_restore.js`)는 그대로다. 센서 스트림은 패널이 화면에 들어올 때 열리므로 휴대폰 첫 화면에서는 LiDAR·카메라가 스크롤해 내려갈 때 열린다.
주행 제어 패널 안 순서(조향 차): 조향 조종(`steerDrive`) → 자율 칸(`autoPanel`) → 조향 가변저항 → 조향 보정(접힘) → 거리 이동(접힘).

새 기능이 들어간 곳(`id`는 고정, 다른 작업이 이 이름으로 찾는다):

| 새 기능 | 자리 | id |
| --- | --- | --- |
| 앞바퀴 조향 수동 조종(구동 W/S, 조향 A/D·가운데, 계기) (UI-07) | 주행 제어 패널, 차동 부분(자율 순항속도 줄, 활성화, 속도, 패드) 다음 | `steerDrive` |
| 끌어서 조향 각(왼쪽·오른쪽으로 끄는 동안 그 각, 값 표시, 놓으면 0°) (2026-11-16, ESP32-S3) | 조향 패드 바로 아래, 계기 위 | 막대 `steerDrag`, 값 `steerDragValue` |
| 조향 가변저항(측정 각도·원값·보정·조향 펌웨어 SMOOTH/PULSE·결함) | 주행 제어 패널, 자율 칸 바로 아래 | `steerPotPanel`, 펌웨어 줄 `firmwareText`(`static/steer/steer-sensor.mjs`) |
| 조향 보정(접힌 칸: 지금 보정·가변저항·찾은 끝, 지금 위치를 가운데로 / 왼쪽 끝 찾기 / 오른쪽 끝 찾기 / 적용(여유) / 기본값으로, 진행·결과, STOP) (2026-11-16, ESP32-S3 `CAL_RUNTIME` 펌웨어) | 주행 제어 패널, 조향 가변저항 바로 아래 | `steerCalPanel`, `static/steer/steer-calibration.mjs`(칸을 처음 열 때 읽음), `POST /api/rover/calibrate` |
| 한 번 눌러 자율 시작, STOP, heartbeat (UI-08) | 주행 제어 패널, 조향 조종 바로 아래 '자율주행 · 열 따라가기'(2026-11-17부터. 그 전에는 패널 맨 아래). 칸 머리에 실행기 모드 칩(SHADOW · 모터 안 씀 / MOTOR · 차를 몬다)과 run 상태 칩 | `autoPanel`, 모드 칩 `autoMode` |
| 온라인 배율(LiDAR 속도 ÷ 표 속도, 최근 주행 20 s)과 주행 뒤 전체 배율 저장 묻기 (PW-10) | 자율 칸 안, 버튼·안내 줄 아래(실행기가 보낼 때만, 묻기는 run이 끝난 뒤만) | 줄 `autoScale`, 묻기 `autoScaleOffer`(`autoScaleAccept`, `autoScaleDismiss`) |
| 현장 조정 값(기본·고급·잠금 묶음, 프리셋, 변경 기록) (UI-12) | 주행 제어 다음의 '현장 조정 값' 패널, 접힌 칸 | `tuningPanel`, 접힘 `tuningDetails`, 폼 `tuneForm` |
| 실행기 국소 격자와 열 선·중심선 (UI-09) | LiDAR 패널 안 실시간 점유 격자 지도 바로 아래(격자가 올 때만 보임) | `localGridPanel`, 지도 칸 `liveMapPanel` |

조향·가변저항·자율 칸은 `front_steer` 대시보드(`DASHBOARD_VEHICLE=front_steer`)에서만 보이고, 차동 대시보드에서는 수정 전의 차동 패드가 보인다.
차동으로 뜬 대시보드도 조종 브리지가 `vehicle_profile: front_steer`를 내면(ESP32-S3 브리지) Rover Control의 주행 제어를 조향 패널로 바꾼다(`adoptBridgeVehicle`, `static/app/14-steer.js`).
메뉴와 페이지 묶음은 그대로다.

조향 입력 규칙(ESP32-S3 브리지, 상태 `steer_control.keys: full_limit`, `static/steer/steer-control-rules.js`):

| 입력 | 동작 |
| --- | --- |
| A / D 누름 | 왼쪽 / 오른쪽 끝(±349 mrad = ±20 명령 도)이 목표. 화면 쪽 경사 제한과 정지 중 5° 제한은 쓰지 않는다(펌웨어가 천천히, 약 6 명령 도/s 로 간다) |
| A / D 놓음 | '가운데로'(기본)면 0, '각 유지'면 그대로 |
| C 누름 / 놓음 | 0 / 그대로 0 |
| 막대 끌기 | 끄는 위치의 각(0.5° 단위)이 250 ms 마다 목표가 된다. 브리지는 `ANGLE` 을 0.5 s 이상 띄워 보낸다 |
| 막대 놓음 | 포인터 올림·취소·잡기 잃음, 터치 끝·취소, 창 초점 잃음, 탭 숨김 모두 0° |
| 키와 막대 | 먼저 잡은 쪽이 놓일 때까지 다른 쪽은 무시. W/S 구동은 따로 |
| STOP·Esc | 언제나 이긴다: 조종권을 버리고 키·막대 입력을 모두 지운다 |

조향 각 선택(5/10/15/20°)은 이 브리지에서 숨긴다. 상태 나이 한도는 브리지가 준 `age_limit_ms`(150~500 ms, ESP32 는 300 ms)다.
시험: `tests/test_steer_drag.mjs`(규칙, 가짜 요소로 포인터·터치·창 이벤트, 패널 바꾸기).
현장 조정 값은 모든 대시보드에 있다(실행기가 없으면 그렇게 말한다).

## 3. 한 페이지에 패널이 모여서 지키는 것

Rover Control에 패널이 다시 모였으므로, 느린 연결에서 페이지를 열 때 모든 것을 한꺼번에 받지 않게 했다.

- **스트림은 그 패널이 화면에 있을 때만 연다.** LiDAR·카메라·IMU 소켓은 그림이 화면에 들어올 때 열고, 1초 넘게 화면 밖이면 닫는다(사용자가 멈춘 것으로 치지 않음).
  시작은 관찰기(IntersectionObserver)의 첫 보고를 기다린다. 카메라의 NET-08 규칙을 LiDAR·IMU에 그대로 썼다(`static/app/05-live-lidar-camera.js`).
  GPS 소켓도 GPS 지도 칸이 화면에 있을 때만 연다(`static/gps-map.js`). 지도 라이브러리는 전처럼 그 칸이 가까워질 때 받는다.
- **주기적으로 묻는 것도 보일 때만.** 실시간 점유 격자 지도와 실행기 국소 격자는 지도 칸이 화면에 있을 때만 묻는다(`static/live-map.js`, `static/steer/local-grid.mjs`).
  현장 조정 값과 조향 보정은 접힌 칸을 열었을 때만 묻는다(`static/steer/tuning-page.mjs`, `static/steer/steer-calibration.mjs`). 조향 가변저항 줄은 조향 패널이 받은 상태 답을 같이 쓰고, 그 답이 1초보다 오래됐을 때만 따로 묻는다.
- **모듈도 필요할 때 받는다.** 조정 값 모듈은 접힌 칸을 처음 열 때, 국소 격자 모듈은 지도 칸이 처음 화면에 들어올 때 받는다(`static/app/14-steer.js`).
  모의 서버에서 Rover Control 첫 열기의 정적 파일은 39개로 화면 6개 판(a92cfec)의 주행 화면과 같다(고치기 전 42개, 느린 연결 측정: `docs/slow_link.md` 13절).
- **자율 heartbeat는 스크롤과 무관하다.** 자율 run 중에는 Rover Control이 열려 있고 탭이 보이면 heartbeat가 계속 나간다. 지도를 보려고 스크롤해도 차가 서지 않는다.
  다른 페이지로 가거나 탭을 숨기면 전처럼 멈춘다(차가 실행기 기한 뒤 선다).
- 온라인 배율과 주행 뒤 저장 묻기(PW-10)는 자율 칸이 이미 읽는 실행기 상태에 실려 온다. 새 주기 요청은 없다.
- 상태 요청은 Rover Control만 주행 묶음(`/api/telemetry?bundle=drive`, NET-06)을 쓰고 다른 페이지는 전체를 쓴다.
- Connection 페이지에는 LiDAR 지도와 카메라 보기가 따로 있다(수정 전과 같음). IMU 그림은 Rover Control에만 있어서 IMU 소켓은 Rover Control에서만 연다.

시험: `tests/test_menu_restore.js`(메뉴·페이지 제목·새 기능 자리·별칭), `tests/test_rover_panel_streams.js`(LiDAR·IMU·카메라 소켓과 패널, 주소),
`tests/test_rover_panel_polls.mjs`(지도·국소 격자·조정 값·가변저항의 요청), `tests/test_ui_frame.js`(상태 막대와 STOP, 페이지 머리).
화면 점검(`tools/ui_check`) 받아들인 기록은 이 구성의 80쪽이다. 느린 연결 측정은 `docs/slow_link.md` 13절.
