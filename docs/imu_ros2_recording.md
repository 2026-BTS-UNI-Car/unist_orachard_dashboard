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
  REC -->|녹화 시작 정지 구간 또는 수동 영점| CORR[/imu/data]
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

## Jetson 장착축과 `base_link` 변환

2026-08-27 사용자가 확인한 기판의 물리 장착 방향은 다음과 같다.

- 기판 `+X`: 차량 위쪽
- 기판 `+Y`: 차량 왼쪽
- 기판 `+Z`: 차량 뒤쪽

ROS의 차량 기준은 REP-103에 따라 `base_link +X=전방`, `+Y=좌측`, `+Z=위쪽`이다.
실제 `/imu/data_raw` 정지 표본은 약 `(0.54, -10.27, 0.61) m/s²`로, BNO085
보고 좌표의 `-Y`가 현재 위쪽을 향한다. 따라서 기판 인쇄축을 raw 메시지에 바로
대입하지 않는다. 정지 창에서 roll/pitch 장착 기울기를 추정한 뒤, 중력만으로는
관측할 수 없는 수평축에 운영 설정 `RECORDER_IMU_BODY_YAW_OFFSET_DEG=90.0`을
적용한다.

기준 자세 $q_r$, raw 자세 $q_s$, raw yaw $q_{y,s}$, 고정 수평 장착 회전
$q_m=R_z(90^\circ)$에 대해 다음처럼 계산한다.

$$
q_{s\rightarrow b}=q_m\left(q_{y,s}^{-1}q_r\right),\qquad
q_{w\rightarrow b,r}=q_rq_{s\rightarrow b}^{-1}
$$

$$
q_{b,local}=q_{w\rightarrow b,r}^{-1}\left(q_sq_{s\rightarrow b}^{-1}\right)
$$

가속도, gyro bias 제거 후 각속도, 자기장은 모두 같은
`sensor_to_body_quaternion`으로 회전한다. 보정 출력 `/imu/data`의
`header.frame_id`는 `base_link`이며, covariance도 같은 축 순서로 저장한다.
Raw `/imu/data_raw`와 `/imu/mag` 원본은 변경하지 않는다.

각 archive에는 사용자 확인 축, yaw offset, quaternion, reference와 검증 상태를
`mount_transform`으로 남긴다. 정지 중력과 수학적 축 매핑은 검증했지만 실제 차량을
전진·좌측 이동시킨 부호 시험은 아직 수행하지 않았으므로 `mapping_ready=false`를
유지한다.

## Dashboard 수동 IMU 영점

Dashboard의 `현재 자세를 0점으로` 버튼은 최신 `/imu/data_raw` 표본의 quaternion을
즉시 기준으로 삼는다. 녹화 중이 아니어도 적용되며 이후 `/imu/data` 보정 스트림은
계속 발행된다. 버튼 직후의 보정 quaternion은 identity이고 roll/pitch/yaw는 모두 0이다.

```text
q_reference = q_raw(t_button)
q_reference_yaw = yaw_only(q_reference)
q_mount = inverse(q_reference_yaw) ⊗ q_reference
q_corrected(t) = inverse(q_reference_yaw) ⊗ q_raw(t) ⊗ inverse(q_mount)
gyro_bias = gyro_raw(t_button)
gyro_corrected(t) = rotate(q_mount, gyro_raw(t) - gyro_bias)
```

단일 표본 영점은 운영자가 선택한 현재 자세를 로컬 body frame으로 정의하는 기능이지,
정지 창 기반 통계 보정을 대체하지 않는다. 따라서 manual calibration에는
`source=dashboard_manual_zero`, `manual_zero=true`, `covariance_estimated=false`를
기록하고 `mapping_ready=false`를 유지한다. 녹화 중 버튼을 누르면 영점 이벤트와
reference id가 archive calibration event에 포함된다. 녹화 시작 시에는 기존 설계대로
새 정지 창을 수집해 해당 recording 전용 기준을 다시 만든다.

## 녹화·다운로드 산출물

IMU 기록 옵션은 UI와 recorder API 모두 기본 `true`이다. 명시적으로 끈 경우에만 IMU 토픽을 rosbag 목록에서 제외한다. `both`, `lidar`, `camera` 중 어떤 기록 종류를 선택해도 기본 IMU 동작은 같다.

다운로드 archive에는 다음이 포함된다.

- `rosbag2/`: `/imu/data_raw`, `/imu/mag`, `/imu/data`, driver/calibration status와 선택 센서 토픽
- `timeline.json`: raw, corrected, 가까운 자기장 표본, ROS header stamp, frame, 단위, calibration state
- `imu_calibration.json`: 정지 표본 수, 정지 시간, threshold, 시작 quaternion/Euler, mount 추정, gyro bias, 분산, reject/reset 횟수
- `metadata.json`: IMU 포함 여부, schema/revision, 샘플링 정책

원본 ROS header stamp와 수신 기반 `t_s`를 동시에 남긴다. `t_s`는 대시보드 재생용이고, 다중 센서 정밀 동기화는 rosbag의 ROS timestamp를 사용해야 한다.

### LiDAR source timing 보존

회전 중 2D LiDAR 한 sweep을 순간 촬영으로 취급하면 첫 ray와 마지막 ray가 서로 다른 body yaw에서 관측되어 벽이 두꺼워질 수 있다. recorder는 각 LiDAR timeline frame에 ROS header stamp와 함께 `scan_time_s`, `time_increment_s`, `angle_min_rad`, `angle_increment_rad`, `point_time_direction`을 저장한다. 현재 Jetson의 SLAMTEC full-scan 설정은 publish 배열이 물리 획득 순서와 반대이므로 `point_time_direction=-1.0`을 명시한다.

Occupancy renderer는 이 정보와 IMU source stamp를 사용해 ray를 scan 중앙 시각으로 회전 deskew한다. 기존 archive에는 scan duration이 없어 실제 Jetson에서 측정한 약 0.0977초를 fallback으로 사용한다. 이 처리는 yaw 회전 왜곡만 줄이며 scan 중 translation이나 LiDAR·IMU hardware clock offset을 해결하지는 않는다.

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

# 최신 실제 IMU 자세를 수동 영점으로 지정
curl -s -X POST http://127.0.0.1:8080/api/imu/zero | python3 -m json.tool

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
- Dashboard 수동 영점 실제 검증에서 원본 자세 roll `-86.26°`, pitch `-2.73°`,
  yaw `40.20°`가 즉시 `(0°, 0°, 0°)`가 됐고 1초 뒤 drift는 각각 약
  `-0.049°`, `-0.019°`, `-0.006°`였다.
- 사용자 확인 장착축을 반영한 v3 변환을 Jetson에 적용한 뒤 수동 영점 출력은
  `frame_id=base_link`, 정지 가속도 `(0.032, 0.014, 10.298) m/s²`, 자세
  `(0°, 0°, 0°)`였다. 수평축 중력 누설은 각 `0.04 m/s²` 미만이었다.
- ROS Iron 컨테이너 전체 단위·통합·회귀 테스트 145개가 통과했다.

검증 후 생성한 세 테스트 archive(약 1.04 GB)는 결과를 문서화한 뒤 제거했으며,
기존 사용자 archive 19개는 그대로 보존했다.

이 검증은 시작 시점 상대 자세와 기록 경로가 정상이라는 근거다. `mapping_ready=false`
블로커인 독립적인 `imu_link`-`base_link` 외부보정과 wheel odometry 시간 정렬을 대신하지 않는다.

## 공식 기준과 라이브러리

- [ROS REP-145: IMU Sensor Driver Conventions](https://ros.org/reps/rep-0145.html)
- [ROS Iron sensor_msgs/Imu](https://docs.ros.org/en/iron/p/sensor_msgs/interfaces/msg/Imu.html)
- [ROS Iron sensor_msgs/MagneticField](https://docs.ros.org/en/iron/p/sensor_msgs/interfaces/msg/MagneticField.html)
- [Adafruit CircuitPython BNO08x library](https://github.com/adafruit/Adafruit_CircuitPython_BNO08x)
