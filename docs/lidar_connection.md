# LiDAR 연결과 테스트

## 확인된 장치

Jetson에서 현재 LiDAR는 USB-serial 장치로 인식됩니다.

```text
USB: Silicon Labs CP2102N USB to UART Bridge Controller
Device: /dev/ttyUSB0
Stable path: /dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_...-if00-port0
Protocol response: RPLIDAR GET_INFO
Baud: 1,000,000 bps
```

Silicon Labs CP210x 릴리즈 노트는 구형 Linux VCP 드라이버 안내입니다. 현재 Jetson에서는 커널 내장 `cp210x` 드라이버가 CP2102N USB-UART를 이미 `/dev/ttyUSB0`로 노출하므로 별도 VCP 드라이버 설치보다 장치 권한, Docker device mount, serial baud 설정이 중요합니다.

제품 스펙 기준 RPLIDAR S2는 360도 스캔, 10 Hz scanning frequency, 1 Mbps TTL UART, 최대 30 m 측정 반경을 사용합니다. 대시보드의 실제 스캔 테스트도 이 조건에 맞춰 `1,000,000 bps`를 우선 시도합니다.

## 대시보드 테스트 방식

Connection 페이지에는 두 가지 수동 테스트가 있습니다.

### 정보 확인

정보 확인 버튼은 스캔을 시작하지 않습니다. 버튼을 누르면 다음 절차만 수행합니다.

1. `/dev/serial/by-id`, `/dev/ttyUSB*`, `/dev/ttyACM*` 후보를 찾습니다.
2. `/proc/*/fd`를 확인해 ROS2 driver 등 다른 프로세스가 포트를 잡고 있는지 검사합니다.
3. 포트가 점유 중이면 테스트를 중단합니다.
4. 점유 중이 아니면 RPLIDAR `GET_INFO`와 `GET_HEALTH`를 짧게 요청합니다.
5. 결과를 표시하고 포트를 즉시 닫습니다.

### 360 스캔 지도

360 스캔 테스트 버튼은 실제 동작 확인을 위해 짧은 normal scan을 수행합니다.

1. 정보 확인과 동일하게 포트 점유 여부를 먼저 확인합니다.
2. 점유 중이 아니면 serial 포트를 `1,000,000 bps`로 열고 DTR motor line을 활성화합니다.
3. RPLIDAR normal scan 명령 `A5 20`을 전송하고 기본 1.2초 동안 scan node를 수집합니다.
4. STOP 명령 `A5 25`를 전송하고 DTR/RTS를 끈 뒤 포트를 닫습니다.
5. 수집된 angle/distance point를 Connection 페이지의 360도 polar map에 표시합니다.

이 방식은 실제 회전 scan point가 들어오는지 눈으로 확인하기 위한 단발 테스트입니다. ROS2 LiDAR driver가 실행 중이면 포트 점유가 감지되어 테스트를 실행하지 않습니다.

## 세부 LiDAR 대시보드 확장 방향

상시 LiDAR scan stream은 웹 서버가 직접 serial 포트를 계속 읽지 않는 것이 안전합니다. 추천 구조는 다음과 같습니다.

```text
LiDAR serial device
  -> ROS2 LiDAR driver
  -> /scan
  -> ROS2 telemetry bridge
  -> runtime/telemetry_snapshot.json
  -> dashboard_server
```

추후 bridge가 아래 지표를 JSON으로 제공하면 대시보드에 상세 화면을 추가할 수 있습니다.

- `/scan` topic rate
- frame id
- range min/max
- 유효 scan point 수
- obstacle distance min/median
- 과수열 검출 confidence
- LiDAR reliability
- Vision-LiDAR fusion weight

이 구조에서는 ROS2가 LiDAR 장치를 단독으로 소유하고, 대시보드는 ROS2가 요약한 상태만 읽으므로 포트 충돌 위험이 낮습니다.
