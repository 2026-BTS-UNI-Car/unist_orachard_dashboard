# Jetson 런타임 아키텍처

## 권장 서비스

Jetson 실제 운영은 Docker Compose가 주요 런타임 컨테이너를 관리합니다.

```text
unist-orachard-ros2-lidar-driver
  -> ROS2 Iron container
  -> owns /dev/ttyUSB0
  -> publishes /scan

unist-orachard-ros2-lidar-bridge
  -> ROS2 Iron container
  -> subscribes /scan only
  -> does not mount or open /dev/ttyUSB0
  -> serves ws://0.0.0.0:8765/ws/lidar
  -> writes runtime/lidar_bridge_status.json

unist-orachard-dashboard
  -> serves http://0.0.0.0:8080
  -> proxies /ws/lidar to ws://127.0.0.1:8765/ws/lidar
  -> reads runtime/lidar_bridge_status.json for API sensor/service state
  -> reads Jetson system, Docker, network, fan telemetry
  -> browser receives WebSocket LiDAR points from the same origin
```

Dashboard는 LiDAR bridge 없이도 실행됩니다. 이때 시스템/Fan/Docker/Network 정보는 표시되고, Connection 탭의 실시간 LiDAR 상태에는 bridge unavailable 상태가 표시됩니다.

## 실행 순서

Compose:

```bash
cd /home/unist/workspace/unist_orachard_dashboard
docker-compose up -d --build
docker-compose ps
```

Compose 기본 구성에서는 `ros2-lidar-driver`가 `/scan`을 publish합니다. 추후 전체 로버 bringup이 LiDAR driver를 포함해서 실행되는 구조로 바뀌면 동일 serial device를 여는 driver는 하나만 남겨야 합니다.

```bash
source /opt/ros/iron/setup.bash
source /home/unist/unist_orachard_ros2/install/setup.bash
ros2 launch orchard_bringup orchard_full.launch.py mode:=real use_rviz:=false use_lidar_driver:=true
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
curl -i http://127.0.0.1:8080/ws/lidar
docker-compose logs --tail=80 ros2-lidar-bridge
cat runtime/lidar_bridge_status.json
```

`/ws/lidar`에 일반 HTTP로 접근하면 `426 WebSocket upgrade required`가 정상입니다. 실제 브라우저는 `ws://127.0.0.1:8080/ws/lidar` 또는 `wss://bts.sarihub.com/ws/lidar`로 연결합니다.

## 배포 방식

개발 PC/WSL에서는 rsync로 Jetson에 업로드하고, Jetson에서 build/restart를 수행합니다.

```bash
scripts/sync_to_jetson.sh --host unist --user unist --dest /home/unist/workspace/unist_orachard_dashboard
scripts/jetson_dashboard_remote_restart.sh --host unist --user unist --path /home/unist/workspace/unist_orachard_dashboard
```

systemd 템플릿은 `deploy/systemd`에 있습니다.
