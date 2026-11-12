# GPS와 대한민국 오프라인 지도

## 최신 작업 · 2026-09-30 Jetson USB 재연결 복구

GPS를 Jetson으로 되돌린 후 USB 분리 예외로 종료됐던 기존 드라이버를 복구했습니다.
새 패킷 수신은 정상이며 실내 위치 상태는 no-fix입니다. 분리 처리 수정과 검증 범위는
[USB 재연결 처리](gps_usb_reconnect.md)에 기록합니다. 아래 Windows 비교는 이전 관측입니다.

## 최신 관측 · 2026-09-30 Windows 실내 비교003

사용자는 같은 PC·C-to-C USB 케이블·외장 안테나를 유지하면서 GPS 보드와
안테나를 모두 창가에서 실내로 옮겼습니다. 60.048초 관측에서 35,209바이트,
정상 패킷 922개·거절 0개를 받았습니다. NAV-SIG 14개 응답 모두 추적 잠금은
0개였고, 마지막 두 응답의 GPS 14만 C/N₀ 12/7 dBHz·quality 3(검출됐으나 사용 불가)였습니다.
독립 NAV-PVT 15개도 모두 no-fix·사용 위성 0이었습니다.
[실내 비교 근거](https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/blob/archive-before-cleanup-20261113/docs/windows_gps_placement_comparison.json).

앞선 창가 관측 마지막에는 GPS 8/27·GLONASS 18의 추적 잠금과 C/N₀ 30/29/27 dBHz를
확인했습니다. 동일 PC/케이블에서 배치에 따라 수신 상태가 달라진 근거지만,
다른 위성의 C/N₀를 같은 위성의 감쇠량으로 계산하지 않습니다. 경과 시간·획득 이력·전원
연속성이 통제된 반복 시험은 아니며 실내 차폐를 유일한 원인으로, 또는 Jetson/케이블
고장으로 단정하지 않습니다. 두 배치 모두 위치 fix는 아직 없습니다.

같은 프로브·파서로 읽기 전용 조회했고 수신기 설정 쓰기·리셋·펌웨어 변경 없이
포트를 닫았습니다. 최신 관측 위치는 다시 실내입니다. 다음에는 안테나의 하늘 가시성,
정확한 모델·전원/커넥터 경로를 확인하고 위치 fix를 검증해야 합니다. 호스트/케이블을
비교할 때는 안테나 배치·방향을 고정하고 획득 이력을 함께 기록합니다. 후속 시험은 아직 수행하지 않았습니다.

## 2026-09-30 Windows USB 관측: 신호 추적 확인, 위치 확정 미완료

수신기를 Windows PC의 COM3(USB VID:PID `1546:01A9`)에서 직접 읽었습니다.
60.055초와 120.016초의 두 관측에서 정상 패킷은 각각 908개/1,837개(합계 2,745개),
거절은 0개였습니다. ROS·Docker·대시보드를 거치지 않은 관측입니다.
두 번째 관측 마지막에는 GPS 8/27과 GLONASS 18이 각각 C/N₀ 30/29/27 dBHz,
quality 7(코드·반송파 잠금 및 시각 동기화)로 보고됐습니다.
그러나 독립 NAV-PVT 45개는 모두 `fix_type=0`, `fix_ok=false`, `used_satellites=0`입니다.
**신호 추적은 확인했지만 위치 fix는 아직 없습니다.** 신호/위성 목록의 행 수를 위치 계산에 사용한 위성 수로 취급하지 않습니다.
[Windows 관측 근거](https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/blob/archive-before-cleanup-20261113/docs/windows_gps_reception_diagnosis.json).

사용자는 같은 안테나를 유지하면서 GPS 보드와 외장 안테나를 모두 창가 쪽으로 옮겼고 USB 케이블은
Jetson의 USB 3.0 C-to-A에서 PC의 C-to-C로 바꿨다고 확인했습니다.
호스트·배치·케이블·경과 시간이 함께 달라졌으므로 개선 원인을 하나로 확정할 수 없습니다.
안테나 감독 설정 5개는 여전히 false이며 실제 bias 전압 측정값이 아닙니다.
수신기 설정 쓰기·리셋·펌웨어 변경은 없었고 두 관측 후 포트를 닫았습니다.
다음에는 안테나/배치를 고정한 상태에서 호스트·케이블 조건을 하나씩 비교하고
실제 fix/사용 위성/정확도를 확인해야 합니다. 비교 실험과 야외/RTK 검증은 아직 수행하지 않았습니다.

## 이전 Jetson 추가 진단002 · 2026-09-30: 안테나 감독 설정 확인

큰 외장 안테나로 교체한 상태에서 16.011초 동안 읽기 전용 조회를 수행했습니다.
MON-RF/NAV-SAT/NAV-SIG 응답은 각각 4회이며 위성 목록은 매번 0개였습니다.
신호 목록은 0/0/1/0개였고, 한 번 나타난 BeiDou 행은 **C/N₀=0, quality=1(탐색 중)**입니다.
수신·추적 성공이나 위치 확정으로 취급하지 않습니다. 7,059바이트를 받았고 UBX 거절은 0건입니다.
[002 진단 근거](https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/blob/archive-before-cleanup-20261113/docs/jetson_gps_antenna_diagnosis.json).

RAM에서 안테나 전압 제어·단락 감지·개방 감지·단락 시 전원 차단·자동 복구를
각각 독립 조회했고, 5개 값 모두 `false`로 응답했습니다. 이는 수신기의 제어·감독
설정이며 실제 안테나 공급 전압 측정값이 아닙니다. MON-RF 상태 1/전원 2의
**DONTKNOW**와 함께 보더라도 전원 OFF·단선·고장을 확정할 수 없습니다.
[공식 HPG1.51 인터페이스](https://content.u-blox.com/sites/default/files/documents/u-blox-F9-HPG-1.51_InterfaceDescription_UBXDOC-963802114-13124.pdf)의
NAV-SIG 및 CFG-HW/MON-RF 정의를 따르며, 보드 회로를 확인하지 않은 설정 변경은 하지 않았습니다.

사용자는 휴대폰 Google 지도가 Wi-Fi·셀룰러를 모두 끈 상태에서도 동작한다고
확인했습니다. 휴대폰의 GNSS 표본·최신성은 독립 측정하지 않았으며, 실내라는
이유만으로 원인을 확정하지 않습니다. 구매한 [UB-ZED03 제품 페이지](https://www.devicemart.co.kr/goods/view?no=15887632)는
USB 5V 보드 전원을 지원하지만, 이는 실제 안테나 bias 공급 전압·연결 경로를
검증한 결과가 아닙니다. 안테나 모델·능동/수동·필요 전압·지원 대역과 실제 PCB/커넥터 경로가 미확인입니다.

GPS 서비스만 일시 중지하여 단독 조회한 뒤 같은 서비스로 복구했고, 복구 7개 검사와
다른 15개 컨테이너·32개 기록의 이름/크기/mtime 보존을 확인했습니다.
설정 쓰기·리셋·펌웨어 변경·모터 명령·신규 녹화는 없었습니다. 002 파서 로컬 검사 39개를 통과했습니다.
이후 Windows PC USB 관측을 수행한 결과는 위 최신 절에 구분해 기록했습니다.
아래 Jetson 진단 당시 결과를 현재 PC의 신호 수신 상태로 해석하지 않습니다.
야외 위치 확정과 RTK 검증은 별도로 남아 있습니다.

## 이전 진단001 · 2026-09-30: USB 정상, 실외 위치 확정 대기

실내 안테나 상태에서 수신기에 직접 읽기 전용 조회를 수행했습니다.16초 동안
MON-RF/NAV-SAT/NAV-SIG를 각각4회 받았고 위성·신호 목록은 모두0개였습니다.
GPS/GLONASS/Galileo/BeiDou/QZSS와 조회한 L1/L2 계열 신호 활성값15개는
모두 켜져 있었습니다.7441바이트를 받고 UBX 체크섬 거절은0건이었습니다.
따라서 포트/통신이 끊긴 상태와 실제 위성 신호가 잡히지 않은 상태를 구분합니다.

MON-RF의 안테나 상태1과 전원2는 모두 **UNKNOWN**입니다. 이 값만으로
안테나 미연결·전원 꺼짐·단선을 단정할 수 없습니다.
[공식 HPG1.51 인터페이스](https://content.u-blox.com/sites/default/files/documents/u-blox-F9-HPG-1.51_InterfaceDescription_UBXDOC-963802114-13124.pdf)의
MON-RF 정의를 따릅니다. 실내 차폐와 안테나 경로 문제를 구분하려면 하늘이
트인 곳으로 외장 안테나를 옮긴 후 사용 위성·fix·수신기 보고 정확도를 재확인해야 합니다.
당시 사용자가 실외로 옮기기 어렵다고 확인했으며 실외 검증은 미완료로 유지합니다.

진단 중 GPS 서비스만 잠시 중지하여 장치를 단독 조회했고 동일 서비스로
복구했습니다. 수신기 설정/리셋/Flash 쓰기는 하지 않았습니다. 복구7개 검사,
다른15개 컨테이너와32개 보관 기록 보존을 확인했습니다.
[정제된 진단 근거](https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/blob/archive-before-cleanup-20261113/docs/jetson_gps_reception_diagnosis.json).

## 2026-09-30 USB 연결 적용

GPS 드라이버 한 서비스의 장치 매핑을 기존 UART에서 확인된 USB로 바꾸고
기존 ROS → WebSocket → 대시보드/기록 경로에 연결했습니다. 12개 적용 검증이
통과했으며 다른 컨테이너·설정·32개 기록은 보존했습니다. 8초 사전 수신에서
정상 패킷131개/거절0개·위치 메시지16개를 받았습니다. 현재 상태는
**장치 연결됨, 위성0개, no_fix**입니다. USB 통신 성공과 위치 확정을 구분합니다.
[적용 근거](https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/blob/archive-before-cleanup-20261113/docs/jetson_usb_gps_deployment.json)

아래 9월26일 UART 진단은 연결 변경 전의 이력입니다.

## 구성

```text
ZED-F9P USB CDC ACM → ros2-gps-driver → /gps/fix (sensor_msgs/NavSatFix)
                              → /gps/data (std_msgs/String)
                              → /gps/driver_status (std_msgs/String)
                                    ├─ ros2-gps-bridge → localhost:8778 → dashboard /ws/gps
                                    └─ ros2-data-recorder → rosbag + gps.jsonl + timeline
Jetson data/maps → dashboard /maps/* → MapLibre 지도
```

GPS UART는 GPS 드라이버만 엽니다. 웹 브리지와 기록기는 ROS 토픽만 구독합니다.
기존 ESP, LiDAR, IMU, 카메라 장치 설정 및 모터 명령과 독립적입니다. 드라이버는
프로세스 점유 검사, 파일 잠금, `TIOCEXCL`을 사용합니다. UART 소유자가 있으면
수신을 시작하지 않고 상태에 오류를 남깁니다.

2026-09-30 실제 USB 연결을 확인했습니다. 기본 장치는 u-blox의 고정 경로
`/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_GNSS_receiver-if00`이며
현재 커널 장치는 `/dev/ttyACM0`, USB VID:PID는 `1546:01a9`입니다.
GPS 드라이버만 해당 장치를 `/dev/ubts-gps`로 사용합니다. 다른 장치를 추측해서
자동으로 여는 동작은 없습니다. `.env`의 `GPS_SERIAL_DEVICE`로 명시적으로
다른 연결을 선택할 수 있습니다. 기존 `ttyTHS1` UART 연결은 사용하지 않습니다.
`GPS_BAUD=0`은 38400/9600/115200/57600/19200/230400/460800/921600 순으로 자동 탐색합니다.
고정 속도가 확인되면 예를 들어 `GPS_BAUD=38400`으로 지정합니다.

드라이버가 보내는 UBX 메시지는 읽기 전용 MON-VER, NAV-PVT,
NAV-HPPOSLLH 조회뿐입니다. 위치 응답이 없으면 GN/GP talker에 GGA/RMC NMEA
조회를 추가합니다. UBX 입력 또는 정기 NMEA 출력이 꺼진 수신기도 조회할 수 있도록
하며, 응답이 돌아오면 NMEA 추가 조회를 멈춥니다. `--no-poll-ubx`는 모든 송신을
끄고 수동 수신만 합니다. CFG, 리셋, 플래시 저장, 펌웨어 및 핀mux 변경은 없습니다.
NMEA와 UBX 체크섬을 검증하고 부분 패킷, 잘못된 좌표와 수신 끊김을 처리합니다.
위치가 확정되지 않으면 좌표를 생성하지 않습니다. ROS에서는 STATUS_NO_FIX와 NaN,
웹 JSON에서는 `valid:false`와 null로 구분합니다. 3초간 새 위치가 없으면 마지막
위치를 회색으로 표시하며 현재 위치로 취급하지 않습니다.

원격 진단 시 `/api/gps`의 `driver.serial_device`는 Docker 별칭에 대응하는 실제
커널 장치명입니다. 이 이름으로 시스템 콘솔을 차단합니다. `baud_attempts`는 현재
포트를 연 이후 시도한 속도이며, `serial_open_s`는 그 경과 시간입니다.
`uart_counters`는 포트를 연 시점부터의 커널 RX/TX, framing/parity/overflow 수치로,
지원하지 않는 USB 드라이버/PTY에서는 null입니다. UART TX 증가는 커널 송신 처리의
증거이며 실제 핀 전압이나 GPS 응답을 증명하지 않습니다. `bytes_received`와
`valid_packets`를 함께 확인해야 합니다.

## 지도 사용

Rover Control 진입 시 GPS 스트림이 연결되고 위치 확정 후 로버를 중앙에 둡니다.
드래그하면 추적이 해제됩니다. **로버 중심으로** 버튼을 누르면 현재 위치로 돌아가
이동 중에도 중앙을 유지합니다. 휠, 두 손가락, +/- 버튼으로 확대·축소할 수 있습니다.
첫 위치 수신 전에는 울산 지도를 표시하며 임의의 로버 마커를 만들지 않습니다.

지도, 스타일, 렌더러, 아이콘과 한글 글꼴은 모두 Jetson에서 제공합니다.
실행 중 지도 타일 서버, CDN, 브라우저 위치 권한, 외부 지오코딩 서비스가 필요하지
않습니다. 인터넷이 없어도 PC/휴대폰과 Jetson 사이의 로컬 연결은 필요합니다.

대한민국 범위(124.0, 32.8, 132.1, 38.9)를 zoom 0–15로 추출했습니다.
울산뿐 아니라 제주·울릉도·독도를 포함합니다. 더 높은 화면 확대는 zoom 15의
벡터 데이터를 확대하며, 원본에 없는 건물이나 도로를 추가하지 않습니다.
OSM 데이터의 지역별 완성도와 측량 정확도를 보장하지는 않습니다.

기본 지도 데이터: 2026-09-25, 804,858,073 bytes (약 768 MiB).
SHA-256: `4c0e65c353e055492b1d101e7d79d74ce40380a6b1752835b4ecfaa9e92e5ac8`.
원본과 검증 정보는 `data/maps/manifest.json`에 저장됩니다.
지도는 Git 및 Docker 이미지에서 제외되고 Jetson의 data 볼륨에 보관됩니다.

## 느린 네트워크

PMTiles HTTP Range 요청으로 현재 화면에 필요한 타일만 가져옵니다. 전체 804 MB를
브라우저에 내려받지 않습니다. 지도 요청은 브라우저 캐시를 사용합니다. 처음 여는
영역이나 높은 확대에서는 데이터 전송 시간이 필요합니다.
JS/ESM/CSS/JSON에는 gzip 및 ETag 재검증을 적용하여 재접속 시 재전송을 줄입니다.

GPS는 기본 자동/64 kbps 설정에서 1 Hz, 매우 느린 망에서 2 Hz, 나머지는 최대
5 Hz입니다. 클라이언트 ACK가 도착하기 전에는 다음 메시지를 보내지 않으며
대기 중 표본을 누적하지 않습니다. ROS 원본 기록 주기에는 영향을 주지 않습니다.
한글 글꼴은 약 630 KB이며 지도 생성 전에 로드합니다. 좌표 스트림은 글꼴 로딩과
독립적으로 시작됩니다. 기존 카메라/LiDAR/IMU 전송 품질 설정도 그대로 사용합니다.

## 기록과 재생

**GPS 함께 기록**은 기본으로 켜집니다. LiDAR/Camera/동시 기록 중 어느 것을
선택해도 GPS 옵션이 켜져 있으면 `/gps/fix`, `/gps/data`, `/gps/driver_status`가
동일 rosbag에 기록됩니다. 원본 ROS 메시지는 수신 주기로 보존합니다.

웹 재생용 `gps.jsonl`은 최대 5 Hz로 저장하며 위치, 품질, 수신기 보고 정확도,
위성 수, 속도, GNSS UTC 및 수신 시각을 보존합니다. 수신 불가 상태도 기록합니다.
타임라인의 `t_s`는 다른 센서와 동일한 기록 시작 시각 기준 호스트 수신 시간입니다.
PPS를 이용한 하드웨어 시각 동기화나 센서 지연 보정이 완료되었다는 의미는 아닙니다.

RAM에는 최근 18,000개 GPS 표본만 두지만 전체 기록은 JSONL에 남깁니다.
재생 API는 요청한 시간대의 JSONL을 읽으므로 오래된 GPS 기록도 찾을 수 있습니다.
현재 재생 시각 이전의 가장 최근 표본만 표시하고 3초 이상 빈 구간은 현재 위치로
연결하지 않습니다. 수신 소실이나 긴 간격을 건너뛰어 이동 궤적을 잇지 않습니다.
GPS가 없던 기존 기록도 계속 열 수 있습니다.

## 정확도와 RTK

제품 페이지는 **ZED-F9P 04B, UB-ZED03**입니다. 실제 USB MON-VER 응답은
`MOD=ZED-F9P`, `FWVER=HPG 1.51`, `PROTVER=27.50`으로 확인됐습니다.
이 응답만으로 제품 보드의 세부 리비전까지 확정하지 않습니다. 센티미터급 사양은 RTK 보정 및 적절한 위성/안테나
조건을 전제로 합니다. 현재 구현은 인터넷 없이도 일반 GNSS 위치를 수신하지만
RTCM/NTRIP/기준국을 자동으로 설정하거나 RTK FIX를 보장하지 않습니다.

화면의 정확도는 UBX hAcc/vAcc 또는 같은 epoch의 NMEA GST 값입니다.
HDOP를 미터로 환산해서 정확도를 만들어내지 않습니다. NavSatFix 공분산은 보고된
정확도에 기반한 근사값이며, `gps_link`와 `base_link` 사이의 측량된 TF는 설정하지
않았습니다. GPS를 기존 LiDAR/IMU 융합에 무조건 넣지 않습니다.

## 설치·확인·복구

공식 [PMTiles CLI](https://docs.protomaps.com/pmtiles/cli)를 설치한 뒤:

```sh
python3 scripts/install_offline_maps.py --pmtiles /path/to/pmtiles
docker compose build ros2-gps-driver ros2-gps-bridge ros2-data-recorder dashboard
docker compose up --no-deps -d ros2-gps-driver ros2-gps-bridge ros2-data-recorder dashboard
curl http://127.0.0.1:8080/api/maps/status
curl http://127.0.0.1:8080/api/gps
```

기존 지도 파일이 있으면 `--existing-map`으로 검증 및 보조 자산 설치만 합니다.
다른 날짜의 지도는 새 디렉터리에 설치·검증한 뒤 디렉터리를 교체하세요.
진행 중 기록이나 조종이 있을 때 서비스를 재시작하지 마세요.
GPS 서비스만 중지하려면 `docker compose stop ros2-gps-driver ros2-gps-bridge`를
사용합니다. 드라이버는 종료 시 UART 설정과 잠금을 해제합니다.

### 2026-09-26 초기 확인 및 원격 추가 진단

- UART를 7가지 baud에서 열고 읽기 전용 UBX 조회까지 했으나 수신 0 bytes.
- 최초 I2C 조회는 버스 0/7에서 수행했다. 이후 전체 핀 대조에서 27/28번은 버스 1로
  확인하여 정정했다. 올바른 헤더 버스 **1/7**에서도 GNSS 주소 0x42는 응답하지 않았다.
- GPIO/핀mux/수신기 설정·펌웨어는 변경하지 않음. 실제 위치/RTK는 미확인.
- 최초 조사 시 `ttyTHS1` 소유 프로세스 없음. 배포 후에는 GPS 드라이버만 소유하며,
  시스템 콘솔은 ttyTCU0/tty0임을 확인.
- GPS 파서, 스트림 ACK, 지도 범위 전송, GPS 기록/재생 등 격리된 ROS 테스트 통과.
- 별도 네트워크 격리 ROS domain 173에서 네 센서 동시 rosbag 및 타임라인 기록 확인.
  7초 표본: LiDAR 14, Camera 14, IMU 67, GPS 34 타임라인 프레임.

현장 확인이 불가능한 상태에서 추가로 조사한 결과:

- 실제 포트 `serial@3100000 → ttyTHS1`은 활성화되어 있고 RX/TX pinmux는
  `uarta`, RX input enable=1, TX tristate=0이다. 점유 충돌이나 flow control은 없다.
- L4T 36.5에서 알려진 UART DMA 문제와 달리 현재 UART는 이미 RX/TX 모두 PIO
  모드이며 live device tree에도 DMA 속성이 없다. 따라서 해당 DMA 수정의 재적용이나
  재부팅은 하지 않았다.
- 약 8시간 실행 후 드라이버 수신은 0 bytes였다. 커널 송신 카운터는 증가했지만
  수신 카운터는 2, framing 오류도 2에서 유지되었다. 유효 GPS 패킷은 없었다.
  이 수치는 부팅 이후 누적값이며 이후 API 진단 수치는 포트를 연 이후 증가량이다.
- `ttyTHS2`는 `serial@3140000`으로, NVIDIA가 설명하는 Orin Nano의 가상 UART이다.
  실제 다른 헤더 UART로 추정하여 열지 않았다. ttyTCU0는 사용 중인 디버그 콘솔이다.
- USB 직렬 장치는 기존 ESP와 LiDAR 두 개뿐이며 별도의 GNSS USB 장치는 없다.
  현재 ESP 펌웨어에도 GPS 수신/중계 기능은 없다.

따라서 현재는 **위치 fix 실패가 아니라 호스트에서 GPS 통신 응답을 확인하지 못한
상태**다. GPS 자체의 전원/배선 불량으로 단정할 수는 없다. 수신기 UART2는 기본
RTCM 인터페이스이고 출력/입력 프로토콜 설정에 따라 NMEA/UBX 응답이 없을 수 있다.
어느 핀에 연결되었는지 모르는 상태에서 전원 GPIO, 핀mux 또는 수신기 설정을
추측해서 바꾸지 않는다. 자동 감지와 재접속은 계속 동작하므로 유효 데이터가 들어오면
지도와 기록기에 전달된다. 실제 전압/배선과 수신기 내부 설정은 원격 소프트웨어만으로
확정하지 못했다.

참고용 정상 UART 배선은 Jetson 10번 RX ← GPS TX, Jetson 8번 TX → GPS RX,
공통 GND다. 전원 입력은 캐리어 보드 사양에 따라 다르며 모듈 칩 전압과 혼동하지 않는다.

40핀 전수 설정 확인과 GPIO 입력 관찰은 [전체 핀 원격 점검표](https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/blob/archive-before-cleanup-20261113/docs/gps_pin_inspection.md)를
참고한다. 40핀 헤더의 I2C는 3/5번 → Linux 버스 7, 27/28번 → Linux 버스 1이다.

## 출처 및 라이선스

- [제품 UB-ZED03](https://www.devicemart.co.kr/goods/view?no=15887632)
- [u-blox ZED-F9P integration manual](https://content.u-blox.com/sites/default/files/ZED-F9P_IntegrationManual_UBX-18010802.pdf)
- [NVIDIA Orin Nano carrier specification](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/orin_nano/docs/jetson_orin_nano_devkit_carrier_board_specification_sp.pdf)
- [NVIDIA L4T 36.5 UART DMA 문제와 PIO 우회 설명](https://forums.developer.nvidia.com/t/solved-uart-serial-port-not-working-after-upgradint-to-jetpack-6-2-2-orin-nano-nx/363837/7)
- [NVIDIA Orin Nano UART 매핑 설명](https://forums.developer.nvidia.com/t/to-use-uart2-ttyths2-as-a-standart-uart/339534)
- [u-blox HPG 1.32 NMEA GNQ/GPQ 조회](https://content.u-blox.com/sites/default/files/documents/u-blox-F9-HPG-1.32_InterfaceDescription_UBX-22008968.pdf)
- [Protomaps 다운로드](https://docs.protomaps.com/basemaps/downloads), [OSM ODbL 및 저작자 표시](https://www.openstreetmap.org/copyright)
- [Protomaps fonts/sprites](https://github.com/protomaps/basemaps-assets), revision `028c18f713baecad011301ff7a69acc39bcc2ae7`
- 렌더러: MapLibre GL JS 6.11.2; PMTiles JS 4.5.0; Protomaps basemaps style 5.7.2.
  버전/출처/해시는 `static/vendor/gps-map-vendors.json`, 각 LICENSE 파일 참조.
- 한글: Google Fonts Noto Sans KR을 weight 400 및 한글/라틴 문자로 subset하고
  `UBTS Map Korean`으로 이름을 변경했습니다. SIL OFL 1.1, `static/vendor/korean-font/KOREAN-OFL.txt`.
- 지도 화면에 OpenStreetMap/Protomaps attribution을 항상 표시합니다.
