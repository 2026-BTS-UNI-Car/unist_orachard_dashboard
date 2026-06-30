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

## 대시보드 테스트 방식

Connection 페이지의 LiDAR 테스트 버튼은 스캔을 시작하지 않습니다. 버튼을 누르면 다음 절차만 수행합니다.

1. `/dev/serial/by-id`, `/dev/ttyUSB*`, `/dev/ttyACM*` 후보를 찾습니다.
2. `/proc/*/fd`를 확인해 ROS2 driver 등 다른 프로세스가 포트를 잡고 있는지 검사합니다.
3. 포트가 점유 중이면 테스트를 중단합니다.
4. 점유 중이 아니면 RPLIDAR `GET_INFO`와 `GET_HEALTH`를 짧게 요청합니다.
5. 결과를 표시하고 포트를 즉시 닫습니다.

이 방식은 ROS2 LiDAR driver가 나중에 `/scan`을 사용하더라도 웹 대시보드가 포트를 지속 점유하지 않도록 하기 위한 설계입니다.

## 세부 LiDAR 대시보드 확장 방향

LiDAR scan stream은 웹 서버가 직접 serial 포트를 계속 읽지 않는 것이 안전합니다. 추천 구조는 다음과 같습니다.

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
