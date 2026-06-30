# Jetson 런타임 아키텍처

## 권장 서비스

Jetson 실제 운영은 세 서비스를 분리합니다.

```text
unist-orachard-ros2.service
  -> ROS2 real bringup
  -> RPLIDAR driver owns /dev/ttyUSB0
  -> publishes /scan

unist-orachard-lidar-bridge.service
  -> subscribes /scan
  -> serves ws://0.0.0.0:8765/ws/lidar

unist-orachard-dashboard.service
  -> serves http://0.0.0.0:8080
  -> browser receives WebSocket LiDAR points
```

Dashboard는 LiDAR bridge 없이도 실행됩니다. 이때 시스템/Fan/Docker/Network 정보는 표시되고, Connection 탭의 실시간 LiDAR 상태만 disconnected가 됩니다.

## 실행 순서

Terminal 1:

```bash
source /opt/ros/iron/setup.bash
source /home/unist/unist_orachard_ros2/install/setup.bash
ros2 launch orchard_bringup orchard_full.launch.py mode:=real use_rviz:=false use_lidar_driver:=true
```

Terminal 2:

```bash
python3 /home/unist/unist_orachard_dashboard/scripts/lidar_ws_bridge.py --host 0.0.0.0 --port 8765 --topic /scan
```

Terminal 3:

```bash
python3 -m dashboard_server --host 0.0.0.0 --port 8080
```

## 검증

ROS2:

```bash
ros2 topic list
ros2 topic echo /scan --once
ros2 topic hz /scan
ros2 run tf2_ros tf2_echo base_link laser
```

Dashboard:

```bash
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/telemetry
curl http://127.0.0.1:8080/api/fan/status
```

## 배포 방식

개발 PC/WSL에서는 rsync로 Jetson에 업로드하고, Jetson에서 build/restart를 수행합니다.

```bash
scripts/sync_to_jetson.sh --host unist --user unist --dest /home/unist/unist_orachard_dashboard
scripts/jetson_dashboard_remote_restart.sh --host unist --user unist --path /home/unist/unist_orachard_dashboard
```

systemd 템플릿은 `deploy/systemd`에 있습니다.
