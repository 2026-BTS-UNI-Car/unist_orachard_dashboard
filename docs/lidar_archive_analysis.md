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

현재 알고리즘 이름은 `polar_gap_cluster_line_fit_fragment_merge_predictive_tracks_v5`이다.

1. Polar scan 순서를 유지한 채 인접 점 사이의 cartesian gap을 계산한다.
2. range에 따라 gap threshold를 조정하고 0/360도 wrap-around cluster를 병합한다.
3. 3점 미만 cluster는 noise로 버린다.
4. covariance 주축으로 line fitting을 수행하고 길이, 폭, aspect ratio를 계산한다.
5. 길고 얇은 cluster는 wall segment로 분류한다. 같은 방향의 wall fragment가 일정 lateral distance와 endpoint gap 안에 있으면 하나의 wall로 병합한다.
6. wall이 아닌 작은 cluster는 centroid/radius/density를 구해 object 후보로 분류한다. 작고 조밀한 후보는 과수 주행에서 나무 기둥 후보로 `trunk` label을 붙인다.
7. object 후보는 track velocity로 예측한 위치와 matching한다.
8. wall로 판정된 segment 주변 orchard 후보는 wall proximity suppression으로 제거한다. 이 단계는 확실한 벽 line 위에 red tree circle이 찍히는 문제를 줄이기 위한 보수 gate다.
9. 로버 이동으로 고정 물체가 센서 좌표계에서 움직여 보여도 예측 gate, 반경 변화, 순간 속도 제한을 통과할 때만 같은 object로 보고 history와 velocity를 갱신한다.
10. 4초 동안 갱신되지 않은 track은 제거한다.

v5에서는 이 로직을 `dashboard_server/lidar_analysis.py`로 분리해 live bridge와 data recorder가 같은 함수를 사용한다. Archive segment API도 저장 당시 overlay를 그대로 믿지 않고 segment 구간을 v5로 재분석해 오래된 긴 track이 다시 표시되지 않게 한다.

## 좌표계

Live bridge와 data recorder는 같은 dashboard 좌표계를 사용한다.

- `x_m = sin(theta) * range`
- `y_m = cos(theta) * range`
- `coordinate_frame = lidar_bridge_xy_v1`

초기 archive는 recorder가 `x=cos(theta)`, `y=sin(theta)`로 저장하던 legacy 좌표계가 섞여 있을 수 있다. Dashboard archive viewer는 `coordinate_frame`이 없는 기록을 legacy archive로 보고 표시 시 `x/y`를 교환한 뒤 live 화면과 같은 방향으로 그린다.

## Overlay

- Wall: fitted line segment를 LiDAR map 위에 노란 선으로 표시한다.
- Object: centroid와 cluster radius를 원으로 표시한다.
- Track: 같은 object로 매칭된 최근 위치 history 중 `track_drawable=true`인 안정 trail만 초록 선으로 표시한다.

Dashboard viewer는 다음 세 가지 표시 모드를 지원한다.

- `LiDAR`: raw/downsampled point만 표시한다.
- `LiDAR+Overlay`: point와 wall/object/track overlay를 같이 표시한다.
- `Overlay`: wall/object/track overlay만 표시한다.

## Timeline

`timeline.json`은 장시간 기록을 브라우저에서 동영상처럼 재생하기 위한 lightweight preview이다.

- `lidar.frames[]`: `t_s`, downsampled points, frame별 overlay
- `camera.frames[]`: `t_s`, JPEG preview frame
- `sync.time_base`: recording 시작 후 초 단위

LiDAR+Camera archive는 scrubber의 같은 `t_s`를 기준으로 LiDAR frame과 camera frame을 같이 표시한다. Dashboard는 `/api/recordings/{id}/timeline?start_s=...&duration_s=...`로 필요한 시간 구간만 받아 cache하고, 재생 중에는 다음 구간을 미리 요청한다. 원본 분석이나 재처리가 필요하면 `rosbag2/`를 기준으로 다시 처리한다.

## Orchard Map

`/api/recordings/{id}/orchard-map`은 archive `timeline.json`을 재처리해 과수 위치 지도를 만든다.

- LiDAR frame마다 `orchard_tree` 후보를 검출한다.
- LiDAR 뒤쪽 사람 영역 후보는 제외한다.
- 연속 frame 사이의 downsampled LiDAR point cloud를 occupancy scan matching으로 정합해 임시 pseudo-odometry를 추정한다.
- 같은 world 좌표에 반복 누적된 trunk-like/foliage cluster만 지도 과수 후보로 승격한다.
- 누적 과수 후보는 지도에서 파란 원으로 표시하고, 출발지/도착지는 `S`/`E` marker로 표시한다.
- Web UI에서는 orchard map을 drag로 이동하고 wheel 또는 버튼으로 확대/축소할 수 있으며 PNG로 다운로드할 수 있다.
- 결과는 `/runtime/orchard_analysis/{recording_id}_orchard_map.json`에 cache한다. 같은 algorithm version이면 이후 조회는 cache를 사용한다.

## 한계

- 이 방식은 scan 한 장과 짧은 history만 사용하므로 사람/차체/벽의 semantic class를 확정하지 않는다.
- 로버 자체 흔들림, LiDAR 장착 위치 오차, 유리/반사체에 취약하다.
- 장기 분석이 필요하면 archive의 `rosbag2/`를 기준으로 TF, odometry, camera detection을 함께 fuse해야 한다.

## 개선 방향

- `/tf`와 wheel odometry를 기록해 scan을 world frame으로 누적한다.
- Camera object detection 결과와 LiDAR cluster를 time sync해서 class label을 붙인다.
- RANSAC line fitting을 추가해 벽과 나무 줄기를 더 안정적으로 분리한다.
- Track state를 Kalman filter로 바꿔 속도 추정 noise를 줄인다.
