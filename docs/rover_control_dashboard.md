# Rover Control Dashboard

`/rover-control`은 주행 조작, live camera/LiDAR 확인, 주행 데이터 기록, archive 조회를 한 화면에서 처리한다.

## 제어 경로

- Dashboard는 UART를 직접 열지 않는다.
- Dashboard는 `/api/rover/command`로 `/app/runtime/rover_command.json`에 짧은 TTL 명령을 쓴다.
- `ros2-rover-bridge`가 command file을 읽고 Jetson 40-pin UART로만 WAVE ROVER JSON 명령을 보낸다.
- WAVE ROVER WiFi/AP/HTTP network control은 기본 비활성이다.

## 조종 화면

- Speed slider는 `0-100%`를 `0-0.45 m/s`, `0-1.4 rad/s` 범위로 변환한다.
- `W/A/S/D` 키와 화면 방향 버튼은 누르고 있는 동안만 `ttl_s=0.65` 명령을 갱신한다.
- 키를 떼거나 `STOP`을 누르면 `/api/rover/stop`으로 zero command를 기록한다.
- 명령이 갱신되지 않으면 `wave_rover_bridge`가 TTL 만료 후 stop을 보낸다.
- 출발 페이드와 정지 페이드는 별도 toggle이다. 기본은 출발 페이드 ON, 정지 페이드 OFF이다.

## Live Sensor

- Camera toggle은 dashboard `/ws/camera` proxy를 사용한다.
- LiDAR toggle은 dashboard `/ws/lidar` proxy를 사용한다.
- 두 WebSocket은 ROS2 bridge가 발행하는 topic을 구독만 하며 장치를 직접 열지 않는다.
- USB camera는 `CAMERA_ROTATE_180=1`이 기본값이므로 ROS2 publish 전 frame을 180도 회전한다.
- Camera capture 해상도/FPS 설정은 Connection 탭과 Rover Control 탭에서 같은 `/api/camera/settings` 파일을 수정한다.
- Camera UI는 capture target과 observed FPS를 분리해서 표시한다. capture target은 UVC device에 요청한 해상도/FPS이고, observed FPS는 ROS2 image topic과 WebSocket JPEG preview 처리량이다.
- 1080p30 capture가 성공해도 Jetson CPU/JPEG encode 부하 때문에 browser preview observed FPS는 더 낮을 수 있다. 이 경우 source 해상도가 target과 일치하면 crop/resize 문제가 아니라 downstream 처리량 문제로 본다.
- LiDAR 원본 수집은 최대 point 수를 유지하고, browser streaming만 `max_points`로 조절한다.
- LiDAR overlay mode는 `LiDAR`, `LiDAR+Overlay`, `Overlay`를 지원한다.

## 기록

기록 버튼은 `/api/recordings/start`를 호출한다.

| kind | topics |
| --- | --- |
| `lidar` | `/scan`, `/cmd_vel`, `/rover/status` |
| `camera` | `/camera/image_raw`, `/camera/camera_info`, `/cmd_vel`, `/rover/status` |
| `both` | 위 LiDAR와 camera topics 전체 |

`ros2-data-recorder`는 `ros2 bag record`를 실행하고 archive를 `/app/runtime/recordings/<id>`에 저장한다.

## Archive

각 archive는 다음 파일을 가진다.

- `metadata.json`: kind, topics, 시작/종료 시각, duration
- `rosbag2/`: 원본 ROS2 bag
- `preview_scan.json`: dashboard visualization용 LiDAR sample
- `analysis.json`: wall/object/track overlay 결과
- `timeline.json`: 재생용 LiDAR frame/Camera preview frame sequence
- `rosbag2.log`: rosbag process log

Dashboard의 기록 데이터 목록에서 항목을 선택하면 timeline scrubber와 재생 버튼으로 LiDAR/Camera preview를 재생한다. LiDAR+Camera archive는 같은 `t_s`에서 두 센서 preview를 같이 표시할 수 있다. 다운로드 버튼은 해당 archive 전체를 `tar.gz`로 내려준다.
