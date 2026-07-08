# LiDAR Archive 분석

Archive viewer는 recorded `/scan`에서 만든 `preview_scan.json`, `analysis.json`, `timeline.json`을 사용해 지도 위에 벽과 물체 움직임을 overlay한다. 실시간 화면은 `ros2-lidar-bridge`가 WebSocket scan마다 같은 알고리즘으로 overlay JSON을 생성한다.

## 입력

`ros2-data-recorder`는 recording 중 최신 `sensor_msgs/LaserScan`을 dashboard용 점 목록으로 변환한다.

- `angle_deg`
- `distance_m`
- `x_m`
- `y_m`
- `quality`

원본 전체 데이터는 `rosbag2/`에 그대로 남기고, dashboard는 빠른 조회와 재생을 위해 downsample된 timeline frame을 그린다.

## 알고리즘

현재 알고리즘 이름은 `polar_gap_cluster_line_fit_nearest_neighbor_tracks_v2`이다.

1. Polar scan 순서를 유지한 채 인접 점 사이의 cartesian gap을 계산한다.
2. gap이 `max(0.18m, range * 0.06)`보다 크면 cluster를 나눈다.
3. 4점 미만 cluster는 noise로 버린다.
4. 8점 이상 cluster는 covariance 주축을 구해 line fitting한다.
5. line 길이가 `0.65m` 이상이고 90 percentile residual 폭이 `0.16m` 이하이면 wall segment로 분류한다.
6. wall이 아닌 작은 cluster는 centroid/radius를 구해 object 후보로 분류한다.
7. object 후보는 직전 track centroid와 nearest-neighbor matching한다.
8. 0.55m 안의 track에 붙으면 같은 object로 보고, 위치 history와 velocity를 갱신한다.
9. 2초 동안 갱신되지 않은 track은 제거한다.

v2에서는 이 로직을 `dashboard_server/lidar_analysis.py`로 분리해 live bridge와 data recorder가 같은 함수를 사용한다. 그래서 실시간 overlay와 archive overlay의 wall/object 기준이 같다.

## Overlay

- Wall: fitted line segment를 LiDAR map 위에 노란 선으로 표시한다.
- Object: centroid와 cluster radius를 원으로 표시한다.
- Track: 같은 object로 매칭된 최근 위치 history를 선으로 표시한다.

Dashboard viewer는 다음 세 가지 표시 모드를 지원한다.

- `LiDAR`: raw/downsampled point만 표시한다.
- `LiDAR+Overlay`: point와 wall/object/track overlay를 같이 표시한다.
- `Overlay`: wall/object/track overlay만 표시한다.

## Timeline

`timeline.json`은 장시간 기록을 브라우저에서 동영상처럼 재생하기 위한 lightweight preview이다.

- `lidar.frames[]`: `t_s`, downsampled points, frame별 overlay
- `camera.frames[]`: `t_s`, JPEG preview frame
- `sync.time_base`: recording 시작 후 초 단위

LiDAR+Camera archive는 scrubber의 같은 `t_s`를 기준으로 LiDAR frame과 camera frame을 같이 표시한다. 원본 분석이나 재처리가 필요하면 `rosbag2/`를 기준으로 다시 처리한다.

## 한계

- 이 방식은 scan 한 장과 짧은 history만 사용하므로 사람/차체/벽의 semantic class를 확정하지 않는다.
- 로버 자체 흔들림, LiDAR 장착 위치 오차, 유리/반사체에 취약하다.
- 장기 분석이 필요하면 archive의 `rosbag2/`를 기준으로 TF, odometry, camera detection을 함께 fuse해야 한다.

## 개선 방향

- `/tf`와 wheel odometry를 기록해 scan을 world frame으로 누적한다.
- Camera object detection 결과와 LiDAR cluster를 time sync해서 class label을 붙인다.
- RANSAC line fitting을 추가해 벽과 나무 줄기를 더 안정적으로 분리한다.
- Track state를 Kalman filter로 바꿔 속도 추정 noise를 줄인다.
