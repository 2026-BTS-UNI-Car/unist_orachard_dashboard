# 과수 인식과 LiDAR-Camera Fusion

이 문서는 2D LiDAR와 RGB camera를 이용해 과수 위치 지도와 행간 중심선을 만드는 연구용 인식 구조를 정리한다. 현재 구현은 archive 하나에서 세 가지 지도를 동시에 만든다.

1. `LiDAR-only`: 2D LiDAR cluster만으로 과수 후보와 행간 중심선을 만든다.
2. `Camera-only`: camera trunk contour/line 후보를 단안 기하로 ground plane에 근사 투영해 과수 후보와 행간 중심선을 만든다.
3. `Weighted Fusion`: LiDAR 지도와 camera 지도를 confidence 기반 평균가중치로 병합하고, 병합된 과수 위치에서 최종 행간 중심선을 만든다.

연구 가설은 LiDAR-only 행간 중심선이 foliage clutter, 벽/가지 오인식, pseudo-odometry 오차로 흔들릴 수 있으므로, camera trunk evidence가 이를 보완해 fusion 중심선의 안정성을 높인다는 것이다.

## 목표 데이터셋

| archive | 조건 | 분석 의도 |
| --- | --- | --- |
| `20260708T053333Z_both` | 잎/가지 간섭이 적은 주행, LiDAR 뒤쪽에 사람 존재 | 뒤쪽 사람 후보를 제외하고 줄기/과수 후보가 안정적으로 누적되는지 확인 |
| `20260708T054036Z_both` | 잎/가지 간섭이 많은 주행, LiDAR 뒤쪽에 사람 존재 | foliage clutter가 많아도 반복 관측되는 과수 후보만 지도에 남는지 확인 |

2026-07-09 `orchard_three_layer_weighted_centerline_v7` 재분석 결과:

| archive | LiDAR-only trees / centerline | Camera-only trees / centerline | Weighted Fusion trees / centerline | 비고 |
| --- | ---: | ---: | ---: | --- |
| `20260708T053333Z_both` | 5 / 0 points | 19 / 11 points | 5 / 0 points | wall suppression을 강하게 적용해 false positive를 줄였지만, 양쪽 row 조건이 부족해 fusion centerline은 생성되지 않았다. |
| `20260708T054036Z_both` | 7 / 6 points | 42 / 16 points | 12 / 8 points | 잎/가지 간섭이 많은 조건에서도 반복 관측되는 후보와 camera evidence를 이용해 중심선을 생성한다. |

위 숫자는 수동 GT label과 비교한 precision/recall이 아니라 자동 후보 수와 자동 중심선 point 수다. 논문용 성능 지표는 수동 tree/centerline annotation 후 계산해야 한다.

Jetson에서 직접 재처리:

```bash
python3 /app/scripts/analyze_orchard_archives.py \
  20260708T053333Z_both \
  20260708T054036Z_both \
  --archive-dir /app/runtime/recordings \
  --output-dir /app/runtime/orchard_analysis
```

## 현재 구현

### 1. LiDAR-only 후보 검출

`dashboard_server/lidar_analysis.py`는 각 `/scan` frame에서 다음 순서로 후보를 만든다.

1. 유효 range point를 angle 순서로 정렬한다.
2. 인접 point 간 cartesian gap이 큰 지점에서 cluster를 나누고, 0/360도 wrap-around cluster를 병합한다.
3. cluster covariance 주축으로 line feature를 계산한다.
4. 길고 얇은 cluster는 wall로 분리한다.
5. wall이 아니고 작고 조밀한 cluster를 `orchard_tree` 후보로 둔다.
6. LiDAR 뒤쪽 사람을 제외하기 위해 `y_m < -0.25 && |x_m| < 5.0 && range < 9.0m` 영역 후보는 orchard 후보에서 제외한다.

후보 confidence는 compactness, density, line aspect, range를 가중합한다.

$$
C_{lidar}
= 0.16
+ 0.34 C_{compact}
+ 0.18 C_{density}
+ 0.18 C_{aspect}
+ 0.08 C_{points}
+ 0.06 C_{range}
- P_{clutter}
+ B_{trunk}
$$

여기서 `C_compact`는 과수 줄기 또는 가까운 줄기+가지 cluster가 너무 크거나 너무 작지 않은지, `C_density`는 점 밀도, `C_aspect`는 벽처럼 길쭉한 cluster가 아닌지, `C_points`는 점 개수가 너무 적거나 큰 blob인지, `C_range`는 멀리 있는 sparse cluster를 낮추는 항이다. `P_clutter`는 큰 foliage blob penalty이고, `B_trunk`는 반경/길이/점 개수가 줄기형일 때 주는 bonus다.

### 2. Camera-only support

Archive map 생성 시 `timeline.camera.frames[]`의 JPEG preview를 OpenCV가 있으면 decoding한다. 현재 기본 구현은 HSV 기반 green/brown mask, dark vertical mask, morphology, contour aspect ratio를 사용한다.

현재 알고리즘은 `opencv_orchard_scene_trunk_semantic_gate_v4`다.

- green mask: 잎/캐노피 영역을 찾되, trunk 후보에서 제거하는 negative mask로 먼저 사용한다.
- bark mask: brown, low-saturation dark, grayscale dark 영역을 합치고 vegetation green을 제거한다.
- contour candidate: 아래쪽/중간까지 이어지는 작고 길쭉한 bark component를 contour polygon으로 만든다.
- line candidate: CLAHE grayscale edge에서 HoughLinesP로 vertical/diagonal trunk line을 찾는다.
- false-positive filter: 100% frame box, 큰 canopy blob, 화면 하단의 바닥 조각, canopy 지지가 없는 낮은 사선 line, 비과수원 scene frame을 제거한다.
- temporal stabilizer: IoU/center matching으로 이전 frame 후보와 연결하고 bbox/contour/line geometry를 지수평활해 정지 화면에서 overlay가 튀지 않게 한다.
- overlay cap: 한 frame에서 confidence 상위 8개만 표시하고, UI는 네모 bbox가 아니라 contour 또는 line outline을 우선 그린다.

이 단계는 최종 semantic classifier가 아니라 camera support score다. 다음 단계는 SegFormer-B0 ADE20K처럼 `tree/plant/grass`와 `wall/table/chair/person/column/pole`을 동시에 분리할 수 있는 semantic segmentation model을 별도 inference container에서 실행하고, orchard 전용 annotation이 쌓이면 YOLO/SegFormer 계열을 `trunk`, `branch`, `canopy`, `background` class로 fine-tune해 TensorRT로 변환하는 것이다.

2026-07-08 기준 v2 camera overlay 검증:

| archive | checked camera frames in first 2s | detections | max bbox area |
| --- | ---: | ---: | ---: |
| `20260708T053333Z_both` | 4 | 5 | 6.2% |
| `20260708T054036Z_both` | 4 | 18 | 8.1% |

v1에서 보였던 full-frame canopy box는 제거되었다. 2번 archive는 잎/가지 간섭 때문에 후보가 더 많지만, camera-only 결과는 최종 과수 위치가 아니라 Fusion Trees의 support로 사용된다. 2026-07-09 v3부터는 live bridge, archive segment backfill, data recorder가 같은 stabilizer를 사용한다.

### 3. Three-layer 지도 생성

`/api/recordings/{id}/orchard-map`은 같은 archive에서 세 개의 map layer를 반환한다.

```json
{
  "maps": {
    "lidar": {"trees": [], "centerline": {}},
    "camera": {"trees": [], "centerline": {}},
    "fusion": {"trees": [], "centerline": {}}
  }
}
```

LiDAR-only layer는 LiDAR 후보만 누적한다. Camera-only layer는 camera detection bbox에서 bearing과 depth를 근사한다.

$$
\phi = \left({u_c \over W} - {1 \over 2}\right) FOV_x
$$

$$
d_h = {f_x H_{trunk} \over h_{box}}, \quad
d_b = 1.15 + (1 - {v_{bottom} \over H}) 11.2
$$

$$
d_{camera} = clip(0.62 d_h + 0.38 d_b, 1.15, 13.5)
$$

여기서 `u_c`는 bbox 중심 x좌표, `W/H`는 image size, `FOV_x=78deg`, `H_trunk=1.35m`는 현재 실험용 가정값이다. 이 값은 calibrated depth가 아니므로 논문용 최종 metric map에서는 camera intrinsic/extrinsic calibration 또는 visual odometry로 교체해야 한다.

Fusion layer는 LiDAR tree와 camera tree를 world frame에서 거리 gate로 matching한 뒤 confidence 기반 평균가중치로 위치를 계산한다.

$$
w_l = 0.64 C_{lidar}, \quad w_c = 0.36 C_{camera}
$$

$$
p_{fusion} = {w_l p_{lidar} + w_c p_{camera} \over w_l + w_c}
$$

unmatched camera 후보는 confidence가 매우 높고 반복 관측된 경우에만 fusion에 들어간다. 즉 camera는 LiDAR 오류를 보완하지만, camera-only false positive가 fusion map을 과도하게 오염시키지 않도록 제한한다.

### 4. Archive Orchard Map

Archive map 생성은 `timeline.json`을 읽어 다음을 만든다.

- `maps.lidar.trees`: LiDAR-only 누적 과수 후보 위치
- `maps.camera.trees`: Camera-only 누적 과수 후보 위치
- `maps.fusion.trees`: 평균가중 fusion 과수 후보 위치
- `maps.*.centerline`: 각 지도에서 만든 행간 중심선
- `path`: pseudo-odometry 기반 로버 경로
- `start`, `end`: 출발/도착 표시
- `camera`: camera support 분석 요약

현재 archive timeline에는 wheel odometry/TF가 없으므로, 연속 frame에서 LiDAR point cloud를 가볍게 scan matching해 sensor pose를 추정한다. 이전 frame 점군을 occupancy grid로 만들고, 현재 frame 점군에 작은 translation/rotation 후보를 적용해 grid hit ratio가 가장 높은 변환을 고른다.

$$
s(T)
= {1 \over |P_t|}
  \sum_{p \in P_t}
  \mathbb{1}
  [cell(Tp) \in \Omega_{t-1}]
$$

`P_t`는 현재 frame의 downsampled LiDAR point, `T`는 후보 SE(2) 변환, `Omega_{t-1}`는 이전 frame occupancy grid다. 이 값은 임시 pseudo-odometry이며, 실제 논문/실험용 지도에서는 wheel odometry, visual odometry, LiDAR odometry 또는 `/tf`를 함께 기록해야 한다.

### 5. 행간 중심선

각 map layer는 tree를 주행 진행축 좌표계로 투영한다. path 시작점에서 끝점으로 향하는 단위벡터를 `a`, 그 수직벡터를 `n`이라고 하면 tree point `p_i`는 다음 station/lateral 좌표를 가진다.

$$
s_i = (p_i - p_0) \cdot a,\quad
l_i = (p_i - p_0) \cdot n
$$

`l_i > 0`은 left row, `l_i < 0`은 right row로 나눈다. station을 2m bin으로 나누고 각 bin에서 좌/우 row의 confidence weighted lateral 평균을 계산한다.

$$
\bar{l}_{left,k}
= {\sum_i C_i l_i \over \sum_i C_i}, \quad
\bar{l}_{right,k}
= {\sum_j C_j l_j \over \sum_j C_j}
$$

양쪽 row가 모두 있으면 중심선 lateral은 단순 중점이다.

$$
l_{center,k}
= { \bar{l}_{left,k} + \bar{l}_{right,k} \over 2}
$$

한쪽 row만 관측되면, 양쪽이 함께 관측된 bin에서 얻은 median row gap을 이용해 반대쪽을 보정한다.

$$
g_{row} = median(\bar{l}_{left,k} - \bar{l}_{right,k})
$$

Fusion 중심선은 먼저 LiDAR tree와 camera tree를 평균가중치로 병합한 뒤, 병합된 tree set에서 같은 중심선 알고리즘을 적용한다. 따라서 최종 중심선은 LiDAR-only 중심선과 camera-only 중심선의 단순 선 평균이 아니라, 두 sensor의 landmark confidence가 반영된 tree map의 결과다.

## Overlay Mode

Connection, Rover Control, Archive는 같은 perception mode를 공유한다. mode 선택은 화면마다 따로 해석하지 않고 전역 상태로 유지된다.

| mode | 의미 |
| --- | --- |
| `2D Objects` | LiDAR canvas에만 wall/object/track overlay를 표시한다. Camera overlay는 숨긴다. |
| `Camera` | Camera image 위에 tree candidate bbox와 confidence를 표시한다. LiDAR overlay는 숨긴다. |
| `Fusion Trees` | LiDAR와 Camera 양쪽에 overlay를 표시한다. Archive LiDAR는 현재 frame LiDAR orchard 후보를 anchor로 쓰고, final orchard map tree는 matching/confidence 보조로 사용한다. |

Camera view는 별도 표시 모드를 가진다.

| camera mode | 의미 |
| --- | --- |
| `Camera` | 영상만 표시한다. |
| `Camera+Overlay` | 영상과 bbox overlay를 함께 표시한다. |
| `Overlay` | 영상은 숨기고 bbox overlay만 표시한다. |

초록색 object track은 local sensor frame에서 물체 위치를 이어 그리므로 주행 중에는 로버 ego-motion이 섞인다. v4에서는 track 자체는 유지하되, 비현실적인 jump와 순간 속도를 매칭 단계에서 제거하고 `track_drawable=true`인 짧은 안정 trail만 표시한다. world-frame 경로와 누적 과수 위치는 archive 하단 orchard map에서 별도로 그린다.

Archive에서 이전에 Fusion Trees 파란 원이 지도 tree보다 많아 보였던 이유는 표시 단계가 달랐기 때문이다. LiDAR canvas는 frame별 local candidate를 그리고 있었고, Orchard Map은 여러 frame에서 반복 관측되어 같은 world 위치로 병합된 final tree만 표시했다. 이후 final tree만 LiDAR canvas에 역투영해 표시했지만, pseudo-odometry 오차가 있으면 raw LiDAR point cluster와 원이 떨어져 보였다. 2026-07-09 기준 archive LiDAR canvas는 현재 frame의 LiDAR orchard 후보를 anchor로 사용하고, final map tree는 가까운 anchor에 matching될 때 confidence와 identity를 보조한다. 따라서 replay 중 과수 원은 raw LiDAR point 움직임을 따라가고, 하단 Orchard Map은 누적 world-frame final tree를 계속 표시한다.

Archive LiDAR 방향 보정은 live/connection 화면과 같은 규칙을 쓴다. raw point는 dashboard 표시 전에 `display_y=-raw_y`로 변환된다. frame anchor 후보도 같은 normalized overlay path를 통과하므로 raw point와 overlay가 같은 좌우/회전 보정으로 그려진다.

확률 표시는 겹침을 줄이기 위해 다음 규칙을 사용한다.

- Camera bbox는 confidence label을 box 내부 모서리에 표시한다.
- Camera label이 겹치면 작은 색상 dot만 표시한다.
- LiDAR fusion tree는 confidence label을 원 옆에 표시한다.
- LiDAR label이 겹치면 원 위의 작은 색상 dot으로 대체한다.
- 파랑은 높은 tree probability, 초록은 중간, 노랑은 낮은 후보를 뜻한다.

## Jetson Orin Nano 추론 설계

1. **실시간 LiDAR path**
   - ROS2 `/scan` 수신
   - polar clustering + wall rejection + orchard candidate extraction
   - track jump gate와 sensor-relative velocity filter로 object trail 안정화
   - 10 Hz 이하에서 Python으로도 가능하며, 필요하면 C++ node로 이식

2. **실시간 Camera path**
   - 현재: OpenCV vegetation-suppressed bark mask + contour/line outline + temporal stabilizer
   - 다음: YOLOv8n-seg 또는 YOLO11n 계열을 orchard trunk/branch/canopy dataset으로 fine-tuning
   - TensorRT FP16 engine으로 변환
   - camera frame을 640px 이하로 resize하여 10-20 FPS 목표

3. **Fusion path**
   - 현재: world frame에서 LiDAR tree와 camera tree를 거리 gate로 matching하고 평균가중치로 지도 생성
   - 다음: LiDAR-camera extrinsic calibration 후 LiDAR candidate center를 image plane에 project
   - projected ROI 안의 segmentation class probability와 LiDAR confidence를 결합

$$
s_{tree}
= \sigma(
  w_l C_{lidar}
  + w_c P_{camera}(tree | ROI)
  + w_t N_{temporal}
  - w_b P_{wall}
)
$$

여기서 `N_temporal`은 반복 관측 횟수, `P_wall`은 벽/긴 가지로 오인될 확률이다.

## 참고 자료와 활용한 부분

1. [TreeScope: An Agricultural Robotics Dataset for LiDAR-Based Mapping of Trees in Forests and Orchards](https://arxiv.org/abs/2310.02162)
   - 활용: tree stem semantic label, diameter estimation, agricultural robot LiDAR mapping 문제 설정을 참고했다.
   - 현재 구현 반영: 2D LiDAR 후보를 누적 지도에서 stem/tree candidate로 저장하고, hits/confidence를 남기는 구조.

2. [Adaptive Per-Tree Canopy Volume Estimation Using Mobile LiDAR in Structured and Unstructured Orchards](https://arxiv.org/abs/2506.08061)
   - 활용: structured/unstructured orchard에서 DBSCAN/spectral clustering으로 per-tree segmentation을 수행하는 방향성을 참고했다.
   - 현재 구현 반영: foliage clutter가 많은 archive에서는 단일 frame 후보보다 시간 누적과 반복 관측으로 tree를 남긴다.

3. [Orchard mapping and mobile robot localisation using on-board camera and laser scanner data fusion](https://www.sciencedirect.com/science/article/abs/pii/S0168169915003002)
   - 활용: laser scanner로 tree trunk width/edge 후보를 만들고 camera image에서 colour/parallel edge feature로 tree/non-tree를 검증하는 구조를 참고했다.
   - 현재 구현 반영: LiDAR cluster confidence와 camera trunk/canopy support를 분리 계산한 뒤 fusion confidence로 결합한다. 지금은 extrinsic calibration이 없어 image ROI projection 대신 nearest camera frame support만 사용한다.

4. [Accurate Fruit Localisation for Robotic Harvesting using High Resolution LiDAR-Camera Fusion](https://arxiv.org/abs/2205.00404)
   - 활용: LiDAR-camera extrinsic calibration과 sensor fusion이 orchard 인식 정확도를 높인다는 구조를 참고했다.
   - 현재 구현 반영: `C_fusion = 0.72 C_lidar + 0.28 C_camera` 형태의 score fusion을 먼저 두고, 이후 extrinsic projection 기반 ROI fusion으로 확장한다.

5. [Semantic Segmentation of Fruits on Multi-sensor Fused Data in Natural Orchards](https://arxiv.org/abs/2208.02483)
   - 활용: texture와 geometry feature를 결합하고 class imbalance를 다뤄야 한다는 점을 참고했다.
   - 현재 구현 반영: LiDAR-only 후보와 camera support를 분리 저장해 나중에 학습 label과 비교할 수 있게 했다.

6. [Comparing YOLOv8 and Mask R-CNN for object segmentation in complex orchard environments](https://arxiv.org/abs/2312.07935)
   - 활용: orchard segmentation에서 YOLOv8 계열이 Mask R-CNN보다 빠르고 좋은 precision/recall을 보인다는 비교를 참고했다.
   - 현재 구현 반영: Jetson 실시간 path의 다음 모델 후보를 YOLOv8n-seg/YOLO11n 계열 + TensorRT로 잡았다.

7. [Machine Vision-Based Crop-Load Estimation Using YOLOv8](https://arxiv.org/abs/2304.13282)
   - 활용: trunk/branch segmentation과 PCA 기반 geometry estimation 흐름을 참고했다.
   - 현재 구현 반영: camera path에서 trunk/branch/canopy class를 분리하는 segmentation model을 목표로 삼고, LiDAR map에서는 radius/diameter proxy를 남긴다.

8. [Multimodal Dataset for Localization, Mapping and Crop Monitoring in Citrus Tree Farms](https://arxiv.org/abs/2309.15332)
   - 활용: 실제 과수 로봇 데이터셋에서 LiDAR, IMU, GNSS RTK, wheel odometry, RGB/depth 등 navigation sensor를 함께 남기는 구성을 참고했다.
   - 현재 구현 반영: 지금 archive에는 `/tf`와 wheel odometry가 없어 scan matching pseudo-odometry를 쓰지만, 다음 기록부터 odometry/IMU/TF를 함께 timeline preview에 저장해야 한다는 TODO를 명시했다.

9. [Navigation of an Autonomous Spraying Robot for Orchard Operations Based on LiDAR](https://pmc.ncbi.nlm.nih.gov/articles/PMC10223392/)
   - 활용: LiDAR point clustering, 좌우 과수열 분리, RANSAC boundary line 계산 흐름을 참고했다.
   - 현재 구현 반영: v7 지도는 좌우 과수열을 station/lateral coordinate로 나누고, bin별 중점으로 행간 중심선을 만든다.

## 남은 실험 TODO

- `20260708T053333Z_both`, `20260708T054036Z_both`의 `LiDAR-only`, `Camera-only`, `Weighted Fusion` tree count와 centerline error를 수동 GT와 비교한다.
- 같은 archive에서 수동으로 과수 위치를 10-20개 label해 precision/recall을 계산한다.
- 중심선 GT를 label하고 LiDAR-only centerline과 fusion centerline의 lateral error를 비교한다.
- 다음 주행 기록부터 `/tf`, wheel odometry, IMU, `/cmd_vel` timeline preview를 함께 저장한다.
- LiDAR-camera extrinsic calibration target을 만들어 projection 기반 fusion으로 교체한다.
