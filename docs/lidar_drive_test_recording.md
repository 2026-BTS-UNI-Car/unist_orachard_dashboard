# LiDAR 주행 테스트 데이터 기록

이 문서는 실제 주행 테스트 중 LiDAR 데이터를 나중에 재생하고 분석할 수 있게 남기는 기준 절차다.

## 권장 방식

주행 테스트는 rosbag2를 테스트 단위로 하나씩 남긴다. raw `/scan`만 저장하면 장애물은 볼 수 있지만, 차량이 어떤 명령을 받았는지와 좌표계가 어떻게 움직였는지 재현하기 어렵다. 따라서 아래 데이터를 함께 기록한다.

| 분류 | 토픽 |
|---|---|
| LiDAR raw | `/scan` |
| 좌표계 | `/tf`, `/tf_static` |
| 주행 명령 | `/cmd_vel`, `/rover/status` |
| 위치/자세 | `/odom`, `/imu/data` |
| 지도/인식 결과 | `/map`, `/orchard/detected_trees`, `/orchard/centerline_path`, `/orchard/fused_centerline_path`, `/orchard/lidar_reliability` |
| 카메라 보정 | `/camera/camera_info` |

`/camera/image_raw`는 데이터가 매우 커지므로 기본 기록에서 제외한다. 영상까지 동기 분석이 필요할 때만 `RECORD_CAMERA=1`로 켠다.

## 실행

Jetson에서 ROS2 환경을 로드한 뒤 실행한다.

```bash
cd /home/unist/unist_orachard_ros2
source /opt/ros/iron/setup.bash
source install/setup.bash

./tools/record_bag.sh orchard_row_01
```

종료는 `Ctrl+C`로 한다. 결과는 아래 형태로 저장된다.

```text
bags/drive_tests/20260707T063000Z_orchard_row_01/
  metadata.json
  topics.txt
  bag/
```

`metadata.json`에는 label, 시작/종료 시각, git commit, hostname, 기록 토픽 목록이 남는다.

## 주행 전 체크

```bash
ros2 topic hz /scan
ros2 topic echo /scan --once
ros2 run tf2_ros tf2_echo base_link laser
ros2 topic echo /rover/status --once
```

정상 기준은 `/scan`이 끊기지 않고 들어오며, TF에서 `base_link -> laser`가 확인되고, Rover bridge가 `serial_open` 상태인 것이다.

## 분석 재생

```bash
ros2 bag info bags/drive_tests/<run>/bag
ros2 bag play bags/drive_tests/<run>/bag --clock
```

RViz나 분석 node는 bag play 후 `/scan`, `/tf`, `/cmd_vel`, `/orchard/*` 토픽을 구독하면 된다.

## 운영 팁

- 한 주행, 한 방향, 한 조건을 bag 하나로 나눈다.
- label에는 장소와 반복 번호를 넣는다. 예: `orchard_row_01`, `lab_turn_test_02`.
- LiDAR 장착 높이, 전방 방향, 주행 속도, 지면 상태는 별도 실험 노트에 함께 남긴다.
- 카메라 원본 영상이 필요하면 `RECORD_CAMERA=1 ./tools/record_bag.sh orchard_row_01_camera`로 별도 기록한다.
