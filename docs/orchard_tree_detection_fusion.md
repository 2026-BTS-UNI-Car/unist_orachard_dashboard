# 과수 인식과 LiDAR-Camera Fusion

Last updated: 2026-08-22
Map algorithm: `orchard_metric_anchor_semantic_fusion_local_frenet_v10`
Cache revision: `v10_rep103_robust_tracks_unique_semantics_row_model_native_input_r3`

## Current v10 contract (normative)

1. The LiDAR coordinate convention is y-forward, x-left (REP-103 left-positive bearing), while image `u` increases rightward. Projection is therefore `u=cx-fx*tan(bearing)` and the inverse is `bearing=-atan((u-cx)/fx)`.
2. The nominal camera model is 1920x1080, `fx=fy=1371.022086`, `cx=960`, `cy=540`, approximately 70-degree horizontal FOV. CameraInfo without an explicit validation flag is `camera_info_nominal_or_unverified`, not calibrated.
3. Fusion metric position is always the LiDAR anchor. Camera contour/bbox evidence updates semantic probability and confidence with dynamic quality weighting. Missing or low-quality camera evidence never penalizes LiDAR.
4. Camera-only monocular landmarks stay in the research camera layer. Metric promotion requires explicitly validated intrinsics, extrinsics, frame, and range. Calibrated and uncalibrated observations use separate tracks; every hit in a promoted camera track must have validated metric provenance.
5. Each camera contour can support at most one LiDAR candidate per frame. Common ROS header stamps are paired in original archive order with a bounded one-to-one maximum-cardinality/minimum-cost solver. Relative timeline time is an all-or-nothing archive compatibility fallback; clocks are never mixed. Header regressions and duplicates are reported and close the navigation gate.
6. Global LiDAR landmarks use deterministic static tracks with at most one observation per frame, robust centers, bounded spatial residual, confidence, hit-count, and duration gates. The merge is input-order invariant and cannot inflate persistence by merging two candidates from one frame.
7. The accepted camera overlay remains the only semantic input. A temporally identified stable pending proposal may provide low-weight semantic support to an existing LiDAR anchor, but cannot create or move a metric landmark. Raw `rejected_detections` are audit-only and unused. Known wall/fence/post/pole/person contexts are blocked from the pending path.
8. Centerline generation combines two independent evidence channels per trajectory segment: robust global landmark rows and a time-local bilateral corridor fitted directly from wall-suppressed LiDAR geometry. Both retain the 3--7 m row-gap gate and require parallel, longitudinally diverse, repeated evidence. Unsupported points are never extrapolated; failure emits an empty fail-closed centerline.
9. Map computation success, evaluation data sufficiency, centerline evidence, and navigation readiness are distinct. Navigation additionally requires monotonic sensor clocks and explicit validation of GT, LiDAR-base extrinsics, camera intrinsics, camera-LiDAR extrinsics, trajectory pose, and full-rate replay. These self-consistency checks are not precision, recall, or centerline-error claims because the latest three recordings have no human ground truth.

## Legacy v8 notes (historical, non-normative)

The July 10 formulas and counts below describe the retired v8 prototype. In particular, 78-degree linear-FOV projection, confidence-weighted camera/LiDAR metric positions, a single global path axis, and exact layer counts are not current v10 behavior.

## 1. 세 지도

Archive마다 같은 pose timeline 위에 세 layer를 동시에 만든다.

| Layer | Source | Admission |
| --- | --- | --- |
| LiDAR-only | wall/linear/rear OOD를 통과한 compact scan candidate | 반복 world observation |
| Camera-only | TensorRT contour + CLIP OOD | detection temporal confirmation |
| Weighted Fusion | 가까운 LiDAR/Camera landmark | confidence weighted matching |

2026-07-10 결과:

| Archive | LiDAR | Camera | Fusion |
| --- | ---: | ---: | ---: |
| `20260708T053333Z_both` | 9 | 19 | 14 |
| `20260708T054036Z_both` | 7 | 17 | 9 |

## 2. Camera projection

Camera-only 지도는 아직 calibrated depth가 아니다. Bbox 중심 `u_c`, 보이는 높이 `h`, bottom ratio를 사용한다.

$$
phi = (u_c/W - 1/2) FOV_x
$$

$$
d_h = f_x H_trunk / h
$$

$$
d_b = 1.15 + (1 - v_bottom/H) 11.2
$$

$$
d_camera = clip(0.62 d_h + 0.38 d_b, 1.15, 13.5)
$$

현재 `FOV_x=78 deg`, `H_trunk=1.35 m`다. Camera-only 위치는 연구 비교용이며 extrinsic calibration 전에는 안전 주행 좌표로 사용하지 않는다.

## 3. Landmark fusion

LiDAR와 camera tree가 distance gate 안에 있으면:

$$
w_l = 0.64 C_l,  w_c = 0.36 C_c
$$

$$
p_f = (w_l p_l + w_c p_c)/(w_l+w_c)
$$

Match gate는 range 증가에 따라 0.9-2.2 m로 완만하게 넓어진다. Camera-only unmatched 후보는 높은 confidence와 반복 관측 조건을 만족할 때만 fusion에 남는다.

## 4. 행간 중심선

Path 진행축 `a`, 수직축 `n`에 tree를 투영한다.

$$
s_i = (p_i-p_0)^T a,  l_i = (p_i-p_0)^T n
$$

2 m station bin에서 좌우 confidence-weighted row를 만들고 중점을 계산한다.

$$
l_center,k = (l_left,k + l_right,k)/2
$$

Fusion centerline은 LiDAR centerline과 Camera centerline을 평균한 결과가 아니다. 먼저 landmark를 fusion하고 그 결과에서 centerline estimator를 다시 실행한다.

Tree가 양쪽 row 조건을 만들지 못하면 `insufficient_tree_rows`를 반환한다. 억지로 선을 만들지 않는 fail-closed 동작이다.

## 5. Frame overlay와 map tree 수가 다른 이유

LiDAR canvas의 원은 현재 frame의 sensor-relative candidate다. 지도 원은 다음 단계를 모두 통과한 world landmark다.

```text
frame candidate
  -> geometry/OOD gate
  -> local track confirmation
  -> pose transform
  -> repeated observation merge
  -> confidence/time-span gate
  -> minimum-spacing thinning
  -> map landmark
```

따라서 frame에 파란 원이 많아도 지도에는 적은 수만 남는다. 이전 구현처럼 final map tree를 LiDAR canvas에 강제로 역투영하지 않고, 현재 frame raw point에 붙은 candidate를 anchor로 사용해 point/overlay 불일치를 막는다.

## 6. Timeline marker

Map path의 첫 항목은 첫 LiDAR timestamp의 `(0,0,0 deg)` pose 하나뿐이다. 중복 initial pose를 넣지 않는다. Scrubber가 첫 timestamp보다 앞이면 `start`를 표시하고, 이후 nearest/interpolated timestamp에 맞춰 rover marker와 heading을 이동한다.

두 scrubber는 같은 `setArchiveTime()`을 호출한다.

- 상단 archive scrubber
- 과수 위치 지도 scrubber
- 지도 drag/seek

실제 browser 검증에서 5.2초 재생 동안 두 scrubber 오차는 최대 0.04초였다.

## 7. Overlay mode contract

| Perception | LiDAR | Camera |
| --- | --- | --- |
| `2D Objects` | wall/object/track | no AI overlay |
| `Camera` | raw only | tree contour |
| `Fusion Trees` | frame LiDAR trees with fusion confidence | tree contour |

Camera view는 `Camera`, `Camera+Overlay`, `Overlay`를 지원한다. 세 탭 Connection, Rover Control, Archive는 같은 전역 설정을 사용한다.

## 8. 한계와 다음 교체점

- Camera metric position을 calibrated LiDAR-camera projection으로 교체한다.
- Wheel odometry, IMU, `/tf`를 기록해 scan-only pose를 보조한다.
- 독립 surveyed tree GT로 LiDAR/Camera/Fusion 위치 RMSE를 비교한다.
- Centerline GT로 각 layer lateral RMSE를 비교한다.

상세 수치와 그림은 [과수 인식 운영 검증 최종 보고서](docs/orchard_perception_final_report.md)를 참고한다.
