# GPS와 대한민국 오프라인 지도

## 구성

```text
ZED-F9P UART → ros2-gps-driver → /gps/fix (sensor_msgs/NavSatFix)
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

기본 장치는 Jetson Orin Nano 40핀 헤더 UART인 `/dev/ttyTHS1`입니다.
실제 연결 위치는 아직 확인되지 않았습니다. 다른 UART나 USB 장치를 추측해서
자동으로 여는 동작은 없습니다. 장치가 확인되면 `.env`에
`GPS_SERIAL_DEVICE=/dev/serial/by-id/...`를 지정할 수 있습니다.
`GPS_BAUD=0`은 38400/9600/115200/57600/19200/230400/460800 순으로 자동 탐색합니다.
고정 속도가 확인되면 예를 들어 `GPS_BAUD=38400`으로 지정합니다.

드라이버가 보내는 UBX 메시지는 읽기 전용 MON-VER, NAV-PVT,
NAV-HPPOSLLH 조회뿐입니다. CFG, 리셋, 플래시 저장, 펌웨어 및 핀mux 변경은 없습니다.
NMEA와 UBX 체크섬을 검증하고 부분 패킷, 잘못된 좌표와 수신 끊김을 처리합니다.
위치가 확정되지 않으면 좌표를 생성하지 않습니다. ROS에서는 STATUS_NO_FIX와 NaN,
웹 JSON에서는 `valid:false`와 null로 구분합니다. 3초간 새 위치가 없으면 마지막
위치를 회색으로 표시하며 현재 위치로 취급하지 않습니다.

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

제품 페이지는 **ZED-F9P 04B, UB-ZED03**입니다. 실제 수신기 식별은 MON-VER 응답을
받아야 확인할 수 있습니다. 센티미터급 사양은 RTK 보정 및 적절한 위성/안테나
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

### 2026-09-26 확인 결과

- UART를 7가지 baud에서 열고 읽기 전용 UBX 조회까지 했으나 수신 0 bytes.
- 40핀 헤더 관련 I2C 버스 0/7의 GNSS 주소 0x42에서 응답 없음.
- GPIO/핀mux/수신기 설정·펌웨어는 변경하지 않음. 실제 위치/RTK는 미확인.
- `ttyTHS1` 소유 프로세스 없음, 시스템 콘솔은 ttyTCU0/tty0임을 확인.
- GPS 파서, 스트림 ACK, 지도 범위 전송, GPS 기록/재생 등 격리된 ROS 테스트 통과.
- 별도 네트워크 격리 ROS domain 173에서 네 센서 동시 rosbag 및 타임라인 기록 확인.
  7초 표본: LiDAR 14, Camera 14, IMU 67, GPS 34 타임라인 프레임.

이는 안테나가 위성을 못 잡는 상황과 다릅니다. 위성 fix가 없어도 UART의 NMEA 또는
UBX 응답은 수신되어야 합니다. 전원, 공통 GND, 연결 대상, TX/RX 배선을 확인해야
합니다. 공식 40핀 UART는 Jetson 10번 RX ← GPS TX, Jetson 8번 TX → GPS RX입니다.
제품의 전원 입력과 3.3V 신호 레벨을 실물 실크 및 매뉴얼로 확인한 후 배선하세요.

## 출처 및 라이선스

- [제품 UB-ZED03](https://www.devicemart.co.kr/goods/view?no=15887632)
- [u-blox ZED-F9P integration manual](https://content.u-blox.com/sites/default/files/ZED-F9P_IntegrationManual_UBX-18010802.pdf)
- [NVIDIA Orin Nano carrier specification](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/orin_nano/docs/jetson_orin_nano_devkit_carrier_board_specification_sp.pdf)
- [Protomaps 다운로드](https://docs.protomaps.com/basemaps/downloads), [OSM ODbL 및 저작자 표시](https://www.openstreetmap.org/copyright)
- [Protomaps fonts/sprites](https://github.com/protomaps/basemaps-assets), revision `028c18f713baecad011301ff7a69acc39bcc2ae7`
- 렌더러: MapLibre GL JS 6.11.2; PMTiles JS 4.5.0; Protomaps basemaps style 5.7.2.
  버전/출처/해시는 `static/vendor/gps-map-vendors.json`, 각 LICENSE 파일 참조.
- 한글: Google Fonts Noto Sans KR을 weight 400 및 한글/라틴 문자로 subset하고
  `UBTS Map Korean`으로 이름을 변경했습니다. SIL OFL 1.1, `static/vendor/korean-font/KOREAN-OFL.txt`.
- 지도 화면에 OpenStreetMap/Protomaps attribution을 항상 표시합니다.
