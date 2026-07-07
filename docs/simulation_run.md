# ROS2 시뮬레이션 실행 방법

이 문서는 `unist_orachard_ros2`의 RViz 기반 과수원 시뮬레이션을 실행하는 기준 절차다. 시뮬레이션은 `orchard_bringup` 패키지의 `orchard_full.launch.py` 하나로 올린다.

## 실행 위치

ROS2 Iron 환경에서 실행한다. Docker 컨테이너를 쓰는 경우 먼저 ROS2 컨테이너로 들어간다.

```bash
docker exec -it ros_iron1 bash
```

컨테이너 또는 ROS2가 설치된 터미널에서 workspace로 이동한다.

```bash
cd /root/unist_orachard_ros2
source /opt/ros/iron/setup.bash
```

## 빌드

처음 실행하거나 코드를 수정한 뒤에는 빌드한다.

```bash
colcon build
source install/setup.bash
```

빌드 스크립트를 써도 된다.

```bash
./scripts/build.sh
source install/setup.bash
```

## RViz 포함 실행

화면으로 과수원 환경, 로봇, TF, LiDAR scan, map, 중심선 추정 결과를 확인할 때 사용한다.

```bash
ros2 launch orchard_bringup orchard_full.launch.py mode:=sim use_rviz:=true autostart:=false
```

주요 실행 노드:

| 노드 | 역할 |
|---|---|
| `robot_state_publisher` | URDF 기반 로봇 link/joint TF 발행 |
| `kinematic_robot_sim` | `/cmd_vel` 기반 로봇 이동, `/odom`, `/tf`, `/imu/data`, `/joint_states` 발행 |
| `orchard_world_publisher` | 과수원 나무, 지형, clutter marker 발행 |
| `orchard_lidar_sim` | 과수원 환경 기반 360도 `/scan` 생성 |
| `orchard_mapping_node` | `/scan` + `/odom` 기반 누적 `/map` 생성 |

## 터미널 확인용 실행

GUI 없이 topic과 로그만 확인할 때 사용한다.

```bash
ros2 launch orchard_bringup orchard_full.launch.py mode:=sim use_rviz:=false autostart:=false
```

## Mapping만 확인

자율주행 baseline은 끄고 mapping 흐름만 볼 때 사용한다.

```bash
ros2 launch orchard_bringup orchard_full.launch.py mode:=sim enable_autopilot:=false enable_mapping:=true
```

## 자율주행 켜기

실행 중 별도 터미널에서 `/orchard/autonomy_enable`을 켠다.

```bash
source /opt/ros/iron/setup.bash
cd /root/unist_orachard_ros2
source install/setup.bash
ros2 topic pub --once /orchard/autonomy_enable std_msgs/msg/Bool "{data: true}"
```

끄기:

```bash
ros2 topic pub --once /orchard/autonomy_enable std_msgs/msg/Bool "{data: false}"
```

## 실행 확인

topic 목록:

```bash
ros2 topic list
```

주요 topic:

```bash
ros2 topic hz /scan
ros2 topic echo /odom --once
ros2 topic echo /imu/data --once
ros2 topic echo /orchard/fused_centerline_path --once
```

TF 확인:

```bash
ros2 run tf2_ros tf2_echo base_link laser
ros2 run tf2_ros tf2_echo map base_link
```

## 주의사항

- Full sim에서는 `orchard_lidar_sim`이 `/scan`을 발행한다.
- `fake_scan_publisher`를 동시에 켜면 `/scan` publisher가 중복된다.
- 실제 LiDAR driver와 sim launch를 동시에 켜지 않는다.
- RViz에서 terrain이 `/map`에 가려지면 Map alpha를 낮추고 Draw Behind를 켠다.
- REAL 모드는 `mode:=real`이고, 실제 센서/액추에이터 source만 바뀐다. 상위 perception, fusion, control topic 구조는 SIM과 동일하다.
