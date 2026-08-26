# ROS 2 IMU 실시간 표시·기록·시작 자세 보정

갱신일: 2026-08-27 (KST)  
대상 센서: Jetson I2C-7, `0x4A`에 연결된 BNO085

## 설계 결론

IMU는 LiDAR·카메라와 동일하게 **센서 장치를 소유하는 ROS 2 드라이버**와 **ROS 2 토픽을 구독하는 대시보드 브리지**를 분리한다. 대시보드 서버와 브라우저는 `/dev/i2c-*`를 열지 않는다.

```mermaid
flowchart LR
  BNO[BNO085 / I2C-7 0x4A] --> DRIVER[ros2-imu-driver]
  DRIVER -->|sensor_msgs/Imu| RAW[/imu/data_raw]
  DRIVER -->|sensor_msgs/MagneticField| MAG[/imu/mag]
  RAW --> REC[ros2-data-recorder]
  MAG --> REC
  REC -->|녹화 시작 정지 구간 보정| CORR[/imu/data]
  REC --> CAL[/orchard/imu/calibration]
  RAW --> BRIDGE[ros2-imu-bridge]
  MAG --> BRIDGE
  CORR --> BRIDGE
  CAL --> BRIDGE
  BRIDGE -->|/ws/imu| DASH[Dashboard]
  REC --> BAG[rosbag2]
  REC --> TL[timeline.json + imu_calibration.json]
```

이 소유권 규칙은 다음과 같다.

- `ros2-imu-driver`만 BNO085 I2C 장치를 연다.
- recorder는 ROS 2 토픽만 구독하고 시작 기준 보정 결과를 별도 토픽으로 발행한다.
- bridge는 ROS 2 원본·보정·자기장·보정 상태만 구독해 WebSocket JSON으로 변환한다.
- Dashboard는 bridge와 recorder 상태만 읽는다. 센서 폴링이나 I2C fallback은 없다.

## ROS 2 데이터 계약

| 토픽 | 형식 | 소유자 | 의미 |
|---|---|---|---|
| `/imu/data_raw` | `sensor_msgs/msg/Imu` | `ros2-imu-driver` | BNO085 rotation vector, gyro, acceleration 원본 |
| `/imu/mag` | `sensor_msgs/msg/MagneticField` | `ros2-imu-driver` | Tesla 단위 자기장 원본 |
| `/imu/data` | `sensor_msgs/msg/Imu` | `ros2-data-recorder` | 녹화 시작 시점 기준 자세와 gyro bias가 보정된 추가 스트림 |
| `/orchard/imu/driver_status` | `std_msgs/msg/String` JSON | `ros2-imu-driver` | 장치·단위·오류·샘플 상태 |
| `/orchard/imu/calibration` | `std_msgs/msg/String` JSON | `ros2-data-recorder` | 정지 판정과 시작 기준 보정 provenance |

드라이버 기본 주기는 20 Hz이고 브라우저 송신 상한도 20 Hz이다. timeline 미리보기는 기본 10 Hz로 줄이지만 rosbag2에는 원래 토픽을 그대로 기록한다. 자기장은 BNO085 라이브러리의 µT 값을 ROS 메시지 규약에 맞춰 Tesla로 변환한다.

BNO085가 report enable 직후 내보내는 일시적인 zero quaternion은 warm-up 표본으로 폐기하되 I2C 세션은 유지한다. 이 표본 때문에 장치를 재연결하면 매 연결의 첫 zero quaternion에서 다시 끊기는 루프가 생길 수 있기 때문이다.

센서가 제공하지 않는 공분산은 만들어내지 않는다. 원본 `Imu`의 각 covariance 첫 원소는 `-1`, `MagneticField`는 0 행렬로 두어 각각 메시지 규약의 unknown 표현을 따른다. 시작 보정 스트림의 gyro·acceleration 분산은 정지 창에서 측정한 뒤 보정 출력과 동일한 `imu_link_recording_start` 차체 좌표계로 회전한다. 절대 자세 불확실성은 검증 전이므로 orientation covariance는 unknown이다.

## 녹화 시작 보정

### 안전 조건

녹화 버튼을 누른 뒤 차량을 수평 지면에서 정지 상태로 유지해야 한다. 기본 gate는 다음을 모두 만족하는 연속 1.5초, 최소 20개 표본을 요구한다.

- gyro norm `≤ 0.08 rad/s`
- acceleration norm과 `9.80665 m/s²`의 차이 `≤ 2.0 m/s²`
- acceleration norm 표준편차 `≤ 0.35 m/s²`
- 단조 증가하는 수신 시각과 유효한 유한수 quaternion/vector

움직임, 큰 비중력 가속, 잘못된 값 또는 시각 역행이 감지되면 정지 창을 폐기하고 다시 모은다. gate가 완료되기 전에도 원본은 기록되지만 `/imu/data` 보정 표본은 만들지 않는다.

### 계산

정지 창 quaternion의 평균 `q0`는 quaternion 부호 불연속의 영향을 피하기 위해 Markley 고유벡터 평균으로 구한다. 시작 yaw만 남긴 quaternion을 `qyaw`라 하면 센서 장착 기울기의 추정치는 다음과 같다.

```text
q_mount = inverse(qyaw) ⊗ q0
gyro_bias = mean(gyro_stationary)
q_corrected(t) = inverse(qyaw) ⊗ q_raw(t) ⊗ inverse(q_mount)
gyro_corrected(t) = rotate(q_mount, gyro_raw(t) - gyro_bias)
accel_corrected(t) = rotate(q_mount, accel_raw(t))
```

따라서 보정 완료 시점의 roll/pitch/yaw는 모두 0 근처가 되고, 이후 자세는 녹화 시작 heading을 0으로 둔 로컬 body 기준으로 표현된다. 원본 quaternion·가속도·각속도는 수정하지 않고 같은 timeline frame 안에 함께 보존한다.

이 방식의 핵심 가정은 **차량 자체는 녹화 시작 순간 수평이고 정지했으며, 센서 장착만 기울어져 있다**는 것이다. 경사면에서 시작하면 경사까지 장착 오차로 흡수되므로 별도 외부보정 전에는 절대 자세로 해석하면 안 된다.

## 녹화·다운로드 산출물

IMU 기록 옵션은 UI와 recorder API 모두 기본 `true`이다. 명시적으로 끈 경우에만 IMU 토픽을 rosbag 목록에서 제외한다. `both`, `lidar`, `camera` 중 어떤 기록 종류를 선택해도 기본 IMU 동작은 같다.

다운로드 archive에는 다음이 포함된다.

- `rosbag2/`: `/imu/data_raw`, `/imu/mag`, `/imu/data`, driver/calibration status와 선택 센서 토픽
- `timeline.json`: raw, corrected, 가까운 자기장 표본, ROS header stamp, frame, 단위, calibration state
- `imu_calibration.json`: 정지 표본 수, 정지 시간, threshold, 시작 quaternion/Euler, mount 추정, gyro bias, 분산, reject/reset 횟수
- `metadata.json`: IMU 포함 여부, schema/revision, 샘플링 정책

원본 ROS header stamp와 수신 기반 `t_s`를 동시에 남긴다. `t_s`는 대시보드 재생용이고, 다중 센서 정밀 동기화는 rosbag의 ROS timestamp를 사용해야 한다.

## Dashboard 표시

`/connection`과 `/rover-control`에서 `/ws/imu`를 열어 다음을 표시한다.

- raw 또는 start-corrected roll/pitch/yaw 인공수평선
- acceleration, angular velocity, magnetic field
- raw/corrected topic 생존 상태와 보정 수집/완료 상태
- archive 재생 시 가장 가까운 IMU 표본과 calibration provenance

WebSocket은 Dashboard 세션 cookie가 없으면 거부된다. bridge가 꺼졌을 때 Dashboard는 센서를 직접 열지 않고 `bridge unavailable` 상태로 닫힌다.

## 향후 Occupancy Grid Mapping 사용 조건

현재 산출물은 재처리 입력으로 사용할 수 있지만 `mapping_ready=false`이다. 지도 융합 전에 다음을 완료해야 한다.

1. IMU 축 방향과 `base_link` 사이의 고정 extrinsic을 독립적으로 측정하고 TF로 고정한다.
2. wheel odometry, LiDAR, camera, IMU의 timestamp offset과 drift를 측정한다.
3. 자기장 hard/soft-iron 보정과 과수원 환경의 자기 간섭을 평가한다.
4. bias random walk와 noise density를 Allan variance 등으로 추정해 필터 공분산을 설정한다.
5. `robot_localization` 또는 동등한 상태 추정기에서 IMU+wheel odometry를 융합하고, bag replay와 ground truth로 yaw drift를 검증한다.
6. 추정 pose로 LaserScan을 시간 보상한 뒤 log-odds Occupancy Grid를 갱신한다.

검증되지 않은 BNO085 sensor-native 축이나 시작 보정만으로 지도를 회전시키지 않는다. raw와 calibration provenance를 보존한 이유는 외부보정이 확정된 뒤 같은 archive를 재처리할 수 있게 하기 위해서다.

## 운영 확인

```bash
# 토픽과 형식
ros2 topic list | grep -E '^/imu/|^/orchard/imu/'
ros2 topic info /imu/data_raw -v
ros2 topic echo /imu/data_raw --once

# recorder가 ROS 2 원본을 실제로 받는지
curl -s http://127.0.0.1:8790/status | python3 -m json.tool

# 기본 IMU 포함 녹화
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"kind":"both"}' http://127.0.0.1:8790/start
```

`imu_pipeline.raw_topic_alive=true`, `default_recording_enabled=true`를 확인한다. 시작 후 정지 상태를 유지해 `calibration.state=ready`가 되어야 `/imu/data`가 발행된다.

## 2026-08-27 실제 Jetson 검증

- BNO085 원본 `/imu/data_raw`는 평균 `19.98 Hz`로 측정됐다.
- 장착 원본 자세는 대략 roll `-86.6°`, pitch `-3.2°`, yaw `38.7°`였다.
- 1.50초 정지 창 완료 후 보정 자세는 roll `0.02°`, pitch `0.00°`, yaw `-0.09°`였다.
- `20260826T162056Z_both` 12.57초 통합 검증에서 LiDAR 22, camera 20,
  IMU timeline 95 frame을 저장했고 `include_imu=true`, `imu_calibration_valid=true`를 확인했다.
- 원본·보정·자기장·상태·보정 토픽이 MCAP rosbag2와 `timeline.json`,
  `imu_calibration.json`, `metadata.json`에 함께 들어갔다.
- ARM64 ROS Iron 컨테이너 전체 단위·통합·회귀 테스트 130개가 통과했다.

검증 후 생성한 세 테스트 archive(약 1.04 GB)는 결과를 문서화한 뒤 제거했으며,
기존 사용자 archive 19개는 그대로 보존했다.

이 검증은 시작 시점 상대 자세와 기록 경로가 정상이라는 근거다. `mapping_ready=false`
블로커인 독립적인 `imu_link`-`base_link` 외부보정과 wheel odometry 시간 정렬을 대신하지 않는다.

## 공식 기준과 라이브러리

- [ROS REP-145: IMU Sensor Driver Conventions](https://ros.org/reps/rep-0145.html)
- [ROS Iron sensor_msgs/Imu](https://docs.ros.org/en/iron/p/sensor_msgs/interfaces/msg/Imu.html)
- [ROS Iron sensor_msgs/MagneticField](https://docs.ros.org/en/iron/p/sensor_msgs/interfaces/msg/MagneticField.html)
- [Adafruit CircuitPython BNO08x library](https://github.com/adafruit/Adafruit_CircuitPython_BNO08x)
