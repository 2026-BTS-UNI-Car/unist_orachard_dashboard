# Rover Control TODO

이 문서는 Rover Control dashboard, camera/LiDAR live stream, archive replay, object overlay 작업의 현재 기준 TODO이다.

## 완료 기준

- Docker 탭에서 Dashboard self container는 `unist-orachard-dashboard` 이름 또는 compose service label로 식별한다.
- Camera capture 설정은 Connection 탭과 Rover Control 탭에서 모두 가능하다.
- Camera driver는 요청 FPS로 ROS2 image publish를 throttle한다.
- Camera driver는 요청 해상도를 실제 frame 크기로 받지 못하면 해당 `/dev/video*` node를 버리고 다음 후보를 시도한다.
- LiDAR raw stream은 bridge source 최대 point를 유지하고 browser `max_points`만 조절한다.
- LiDAR live WebSocket은 scan과 별도로 overlay 데이터를 생성한다.
- Rover Control 상단은 LiDAR와 Camera live view를 좌우로 표시한다.
- Rover Control 하단은 Drive와 Recording control을 좌우로 표시한다.
- `W/A/S/D`와 화면 버튼은 누르고 있을 때만 command TTL을 갱신한다.
- 출발 페이드와 정지 페이드 옵션을 독립 toggle로 제공한다.
- Recording 상태는 `/api/recordings/status`와 telemetry로 복원한다.
- Archive는 `timeline.json`으로 LiDAR/Camera preview를 동영상처럼 재생한다.
- LiDAR archive view는 `LiDAR`, `LiDAR+Overlay`, `Overlay` 모드를 지원한다.

## 알고리즘 TODO

- `polar_gap_cluster_line_fit_nearest_neighbor_tracks_v2`를 주행 테스트 bag 기준으로 threshold tuning한다.
- 벽과 과수 줄기 cluster가 섞이는 케이스를 RANSAC line fitting으로 분리한다.
- `/tf`, odometry, IMU를 기록해 scan frame을 world frame으로 누적한다.
- Camera object detection 결과와 LiDAR cluster를 time sync해서 object class label을 붙인다.
- Track matching을 nearest-neighbor에서 Kalman filter 기반으로 바꿔 속도 추정 noise를 줄인다.

## 주행 테스트 전 확인

1. `/connection`에서 Camera source 해상도와 선택한 capture 해상도가 일치하는지 확인한다.
2. `/connection`에서 LiDAR point count가 streaming limit 이하로 내려가는지 확인한다.
3. `/rover-control`에서 카메라/라이다 스트림을 켠 뒤 overlay toggle이 즉시 반영되는지 확인한다.
4. 로버는 바퀴를 띄우거나 안전 공간에서만 실제 이동 테스트를 한다.
5. 기록 시작 후 페이지를 벗어났다가 돌아와도 timer와 kind가 복원되는지 확인한다.
6. 기록 종료 후 archive item을 열어 scrubber, 동시 재생, 다운로드를 확인한다.

## 운영 메모

- WAVE ROVER network control은 사용하지 않는다.
- Dashboard는 `/app/runtime/rover_command.json`에 짧은 TTL command를 쓰고, `ros2-rover-bridge`가 UART로 전달한다.
- `STOP`, pagehide, command TTL 만료는 모두 zero command 경로로 수렴해야 한다.
