# LiDAR Object Overlay

LiDAR object overlay는 `/scan` point cloud에서 벽 후보와 작은 물체 후보를 분리해 Rover Control과 archive replay 위에 그리는 시각화 계층이다.

## 표시 원칙

- Raw LiDAR point는 센서가 받은 점 자체를 보여준다.
- Wall overlay는 길고 얇은 cluster를 line segment로 그린다.
- Object overlay는 작은 cluster를 원형 outline으로 그린다.
- Track overlay는 같은 object로 매칭된 최근 중심점 history를 선으로 그린다.
- Object text label은 지도 위에 표시하지 않는다. 좁은 실내나 과수열 환경에서는 object label이 서로 겹쳐 실제 geometry를 가리기 때문이다.

## 화면 모드

Rover Control과 archive replay는 같은 세 가지 모드를 지원한다.

| mode | 표시 |
| --- | --- |
| `LiDAR` | raw point만 표시 |
| `LiDAR+Overlay` | raw point와 선택된 perception overlay를 함께 표시 |
| `Overlay` | 선택된 perception overlay만 표시 |

Perception source는 Connection, Rover Control, Archive가 같은 설정을 공유한다.

| source | 표시 |
| --- | --- |
| `2D Objects` | LiDAR cluster 기반 wall/object 후보만 LiDAR canvas에 표시 |
| `Camera` | Camera image 위에 tree candidate bbox와 confidence만 표시. LiDAR overlay는 숨김 |
| `Fusion Trees` | LiDAR와 Camera 양쪽에 overlay 표시. Archive LiDAR는 현재 frame LiDAR orchard 후보를 anchor로 쓰고 final orchard map tree는 matching/confidence 보조로 사용 |

`2D Objects` mode에서는 object 원과 함께 v5 track gate를 통과한 녹색 trail이 표시된다. 이 trail은 sensor-relative history라서 로버가 움직이면 world-frame 물체 경로가 아니다. 실제 로버 경로와 누적 과수 위치는 Rover Control archive의 Orchard Map에서 확인한다.

Camera overlay는 raw 영상과 분리된 canvas에 그린다. 표시 모드는 `Camera`, `Camera+Overlay`, `Overlay` 세 가지다. bbox confidence label이 서로 겹치면 label 대신 작은 dot만 표시해 영상의 나무 구조를 가리지 않는다.

Archive replay의 LiDAR point와 overlay는 같은 화면 좌표 보정을 공유한다. 저장된 `lidar_bridge_xy_v1` point는 dashboard에서 `display_y = -raw_y`로 그려지고, archive Fusion Trees의 frame anchor도 같은 normalized overlay path를 통과한다. final map tree만 현재 rover pose로 역투영하면 pseudo-odometry 오차 때문에 raw point cluster와 원이 떨어져 보일 수 있으므로, LiDAR canvas는 현재 frame의 orchard 후보를 우선 anchor로 사용한다. final map tree는 가까운 frame anchor와 matching될 때 confidence와 identity만 보조한다.

## 데이터 생성

실시간 overlay는 `ros2-lidar-bridge`가 `/scan`을 받을 때마다 생성한다. Archive overlay는 `ros2-data-recorder`가 `timeline.json`을 만들 때 각 LiDAR frame에 별도 field로 저장한다.

```json
{
  "overlay": {
    "algorithm": "polar_gap_cluster_line_fit_fragment_merge_predictive_tracks_v5",
    "coordinate_frame": "lidar_bridge_xy_v1",
    "walls": [],
    "objects": [],
    "tracks": []
  }
}
```

Raw LiDAR points와 overlay 데이터는 분리되어 저장된다. 그래서 archive replay에서 `LiDAR`, `LiDAR+Overlay`, `Overlay`를 바꿔도 원본 point를 다시 분석하지 않는다.

## 알고리즘 요약

1. range가 유효한 scan point만 live bridge 좌표계(`x=sin(theta)*r`, `y=cos(theta)*r`)로 변환한다.
2. angle 순서에서 cartesian gap이 큰 지점을 기준으로 cluster를 나누고 0/360도 wrap-around cluster를 병합한다.
3. cluster 길이, 두께, aspect ratio, point 수를 계산한다.
4. 길고 얇은 cluster는 wall 후보로 분류한다. 벽이 라이다 noise 때문에 여러 조각으로 끊기면 방향, lateral distance, endpoint gap이 가까운 wall fragment를 다시 병합한다.
5. wall이 아닌 작은 cluster는 중심점과 반경을 가진 object 후보로 저장한다. 작고 조밀한 cluster는 orchard 주행에서 나무 기둥 후보(`trunk`)로 표시한다.
6. 별도의 orchard 후보 검출은 뒤쪽 사람 영역을 제외하고, compactness/density/aspect/range score로 `orchard_tree` 후보를 만든다. v5부터는 wall segment 주변 후보를 `d_wall <= max(0.28, min(0.62, r_candidate + 0.22 + 0.014 range))` 조건으로 한 번 더 제거해 벽 조각이 과수로 표시되는 문제를 줄인다.
7. 직전 frame object 중심점만 보지 않고 track velocity로 다음 위치를 예측한 뒤 matching한다.
8. matching은 거리뿐 아니라 cluster 반경 변화, label 변화, 순간 sensor-relative speed를 같이 검사한다.
9. track history는 분석 JSON에 유지하지만, UI는 `track_drawable=true`이고 segment jump가 작은 짧은 trail만 초록 선으로 그린다.

## 운영 메모

- `trunk` label은 LiDAR 형상 기반 후보일 뿐 semantic 확정값은 아니다. Camera object detection과 time sync가 붙으면 class label을 더 정확하게 붙인다.
- 정지 상태에서 흔들리는 object velocity는 LiDAR noise의 영향을 받는다. 주행 테스트 bag이 쌓이면 wall merge threshold와 track gate를 계속 튜닝한다.
- 표시가 복잡하면 `Overlay` 대신 `LiDAR` 또는 `LiDAR+Overlay`에서 stream point 수를 줄여 확인한다.
