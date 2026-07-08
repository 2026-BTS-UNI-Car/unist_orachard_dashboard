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
| `LiDAR+Overlay` | raw point, wall, object, track을 함께 표시 |
| `Overlay` | wall, object, track만 표시 |

## 데이터 생성

실시간 overlay는 `ros2-lidar-bridge`가 `/scan`을 받을 때마다 생성한다. Archive overlay는 `ros2-data-recorder`가 `timeline.json`을 만들 때 각 LiDAR frame에 별도 field로 저장한다.

```json
{
  "overlay": {
    "algorithm": "polar_gap_cluster_line_fit_nearest_neighbor_tracks_v2",
    "walls": [],
    "objects": [],
    "tracks": []
  }
}
```

Raw LiDAR points와 overlay 데이터는 분리되어 저장된다. 그래서 archive replay에서 `LiDAR`, `LiDAR+Overlay`, `Overlay`를 바꿔도 원본 point를 다시 분석하지 않는다.

## 알고리즘 요약

1. range가 유효한 scan point만 XY 좌표로 변환한다.
2. angle 순서에서 distance gap이 큰 지점을 기준으로 cluster를 나눈다.
3. cluster 길이, 두께, point 수를 계산한다.
4. 길고 얇은 cluster는 wall 후보로 분류하고 양 끝점을 line segment로 저장한다.
5. wall이 아닌 작은 cluster는 중심점과 반경을 가진 object 후보로 저장한다.
6. 직전 frame object 중심점과 nearest-neighbor matching을 수행해 track history와 velocity를 갱신한다.

## 운영 메모

- 현재 overlay는 class label을 추정하지 않는다. Camera object detection과 time sync가 붙기 전까지는 모든 작은 cluster가 generic object다.
- 정지 상태에서 흔들리는 object velocity는 LiDAR noise의 영향을 받는다. 주행 테스트 bag이 쌓이면 threshold와 track filter를 튜닝한다.
- 표시가 복잡하면 `Overlay` 대신 `LiDAR` 또는 `LiDAR+Overlay`에서 stream point 수를 줄여 확인한다.
