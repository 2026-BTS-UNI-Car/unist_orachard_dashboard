# WAVE ROVER 연동

이 문서는 Waveshare WAVE ROVER를 Jetson Orin Nano Super와 연결해 ROS2 제어 경로와 Dashboard Rover Control 화면을 함께 사용하는 기준이다.

## 결론

현재 배선이 `GND, TX, Null, RX, SCL, GND, SDA, 5V, Null, 5V` 형태라면 실제 로버 제어는 UART를 우선 사용한다.

- `GND`: Jetson과 Rover controller의 기준 전압 공유
- `TX/RX`: Jetson UART와 Rover controller UART 통신
- `SCL/SDA`: I2C 확장용 후보
- `5V`: 보드 전원 또는 로직 전원 계통이므로 전압/전류 방향을 반드시 확인

Dashboard가 직접 UART를 열지 않는다. `wave_rover_bridge` ROS2 node가 UART를 소유하고 `/cmd_vel`을 WAVE ROVER JSON 명령으로 변환한다.

## 공식 제어 경로 요약

Waveshare WAVE ROVER는 ESP32 slave와 JSON 명령으로 통신한다. 공식 문서 기준으로 AP WiFi 이름은 `UGV`, 기본 비밀번호는 `12345678`, AP 모드 Web UI 주소는 `http://192.168.4.1`이다. 같은 JSON 명령은 Web application, HTTP, ESP-NOW, GPIO/USB serial 경로로 보낼 수 있다.

Dashboard의 `/rover-control` 페이지는 원본 Web UI를 `/rover-proxy/`로 보조 표시한다. 실제 연구 주행 제어는 ROS2 `/cmd_vel` 경로를 사용한다.

## ROS2 bridge 구조

```text
/cmd_vel
  -> orchard_real_hw wave_rover_bridge
  -> Jetson UART /dev/ttyTHS1
  -> WAVE ROVER JSON {"T":1,"L":...,"R":...}
```

기본 launch 값:

| 항목 | 기본값 |
|---|---|
| transport | `serial` |
| serial port | `/dev/ttyTHS1` |
| baudrate | `115200` |
| command topic | `/cmd_vel` |
| status topic | `/rover/status` |

실제 Jetson에서 40핀 UART 장치명이 다르면 `rover_serial_port` launch argument를 바꾼다.

```bash
ros2 launch orchard_bringup orchard_full.launch.py \
  mode:=real \
  use_rover_bridge:=true \
  rover_transport:=serial \
  rover_serial_port:=/dev/ttyTHS1 \
  rover_serial_baudrate:=115200
```

## Dashboard Rover Control

`/rover-control` 페이지는 두 가지를 제공한다.

- WAVE ROVER 원본 Web UI proxy: `/rover-proxy/`
- ROS2 Rover bridge 상태: `/rover/status`와 `runtime/rover_bridge_status.json`

원본 Web UI가 열리지 않아도 ROS2 UART bridge는 별도로 동작할 수 있다. 반대로 Web UI proxy가 열려도 Dashboard가 UART를 직접 점유하지 않는다.

Docker Compose에서는 `ros2-rover-bridge` 컨테이너가 이 역할을 수행한다. 전체 ROS2 bringup에서 별도 `wave_rover_bridge`를 실행할 때는 UART를 여는 bridge가 하나만 남도록 Compose service를 중지한다.

## 충돌 방지

- UART는 `wave_rover_bridge`만 연다.
- Dashboard는 `/rover/status`와 service 상태만 표시한다.
- 수동 Web UI 조작과 ROS2 `/cmd_vel` 주행 명령을 동시에 쓰지 않는다.
- 실제 주행 테스트 전에는 바퀴를 띄운 상태에서 `/cmd_vel` 0 명령과 timeout stop 동작을 먼저 확인한다.

## 확인 명령

```bash
ls -l /dev/ttyTHS* /dev/ttyAMA* /dev/ttyS* 2>/dev/null || true
ros2 topic list | grep -E 'cmd_vel|rover/status'
ros2 topic echo /rover/status
```

간단한 정지 명령:

```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist '{}'
```

저속 직진 테스트:

```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.05}, angular: {z: 0.0}}"
```

## 참고

- Waveshare WAVE ROVER 문서: https://www.waveshare.com/wiki/WAVE_ROVER
