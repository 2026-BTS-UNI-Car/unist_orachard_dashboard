# 과수 인식 알고리즘 보고서

작성일: 2026-07-08  
마지막 갱신: 2026-07-09

이 문서는 `20260708T053333Z_both`, `20260708T054036Z_both` archive를 기준으로 Rover Control dashboard에 구현한 과수 인식 알고리즘, 참고 문헌에서 가져온 설계 요소, 현재 코드 반영 지점, 시행착오, 다음 연구 단계까지 논문 초안에 옮길 수 있는 형태로 정리한 보고서다.

2026-07-09부터 지도 생성 목표를 하나의 fusion map에서 세 개의 연구용 map layer로 분리했다. 최신 알고리즘 이름은 `orchard_three_layer_weighted_centerline_v7`이다.

2026-07-09 추가 디버깅에서는 벽을 과수로 오인하는 LiDAR 문제와, 실내/비나무 수직 물체를 camera가 tree로 고신뢰 표시하는 문제를 우선 수정했다. LiDAR는 `polar_gap_cluster_line_fit_fragment_merge_predictive_tracks_v5`로 올려 wall proximity suppression을 추가했고, camera는 `opencv_orchard_scene_trunk_semantic_gate_v4`로 올려 orchard scene gate와 local canopy support gate를 추가했다.

## 1. 연구 목표

목표는 2D LiDAR와 RGB camera를 사용해 주행 중 주변 과수의 상대 위치를 거의 실시간으로 표시하고, 기록된 archive에서 로버 경로와 과수 위치 지도를 재생/다운로드할 수 있게 하는 것이다.

현재 구현은 세 단계로 나뉜다.

1. 2D LiDAR-only: `/scan` point를 cluster로 나눠 wall, generic object, trunk-like orchard candidate를 추출한다.
2. Camera-only: camera frame에서 줄기/캐노피 후보 bbox와 confidence를 계산해 영상 위에 표시한다.
3. Weighted Fusion: LiDAR-only 지도와 Camera-only 지도를 confidence 기반 평균가중치로 결합해 최종 과수 위치 지도와 행간 중심선을 만든다.

각 archive map은 다음 세 layer를 동시에 생성한다.

| layer | 입력 | 산출물 | 연구적 의미 |
| --- | --- | --- | --- |
| `LiDAR-only` | 2D LiDAR trunk-like cluster | 과수 후보, LiDAR 기반 행간 중심선 | baseline. foliage/벽/가지 오인식에 취약할 수 있음 |
| `Camera-only` | camera trunk contour/line 후보 | 단안 기하 근사 과수 후보, camera 기반 행간 중심선 | LiDAR가 놓친 줄기 방향 evidence 제공 |
| `Weighted Fusion` | LiDAR map + camera map | 평균가중 과수 후보, 최종 행간 중심선 | LiDAR 중심선 오류를 camera evidence로 보완 |

2026-07-09 v7 재분석 결과:

| archive | camera frame detections | LiDAR wall-suppressed candidates | LiDAR-only trees / centerline | Camera-only trees / centerline | Weighted Fusion trees / centerline |
| --- | ---: | ---: | ---: | ---: | ---: |
| `20260708T053333Z_both` | 55 detections / 93 frames | 84 | 5 / 0 points | 19 / 11 points | 5 / 0 points |
| `20260708T054036Z_both` | 137 detections / 125 frames | 106 | 7 / 6 points | 42 / 16 points | 12 / 8 points |

1번 archive의 Fusion centerline이 0인 이유는 v7 wall suppression 후 확실한 LiDAR/Fusion tree가 한쪽 row 위주로 남아, 양쪽 row 중점을 만들 조건이 부족하기 때문이다. 이는 false positive를 줄인 대가로 recall이 낮아진 상태이며, 논문용 결과에서는 수동 annotation 기준으로 threshold를 다시 조정해야 한다.

## 2. 참고 문헌과 반영 내용

### TreeScope

Reference: TreeScope: An Agricultural Robotics Dataset for LiDAR-Based Mapping of Trees in Forests and Orchards  
URL: https://arxiv.org/abs/2310.02162

TreeScope는 농업/임업 환경에서 로봇 platform으로 수집한 LiDAR data와 tree stem semantic label, diameter ground truth를 제공한다. 여기서 가져온 핵심은 “나무를 단일 frame의 물체가 아니라 stem semantic object와 diameter/radius를 가진 map object로 저장한다”는 관점이다.

현재 코드 반영:

- `dashboard_server/lidar_analysis.py`: frame별 trunk-like candidate 생성
- `dashboard_server/orchard_mapping.py`: candidate를 `x_m`, `y_m`, `radius_m`, `confidence`, `hits`를 가진 map tree로 병합
- `static/app.js`: Archive Fusion Trees LiDAR overlay는 현재 frame LiDAR 후보를 anchor로 쓰고 orchard map final tree를 matching/confidence 보조로 사용

### Orchard camera-laser scanner fusion

Reference: Orchard mapping and mobile robot localisation using on-board camera and laser scanner data fusion  
URL: https://ui.adsabs.harvard.edu/abs/2015CEAgr.119..267S/abstract

해당 연구는 laser scanner 기반 tree trunk 후보와 camera 기반 tree/non-tree 검증을 결합해 local orchard map을 구성한다. 현재 구현도 같은 구조를 따른다. 단, 아직 LiDAR-camera extrinsic calibration이 없어서 image plane projection은 하지 않고, camera detection을 단안 기하로 ground plane에 근사 투영한 뒤 world-frame tree map에서 LiDAR tree와 matching한다.

현재 코드 반영:

- `dashboard_server/camera_analysis.py`: camera frame에서 trunk/canopy candidate bbox 추출
- `dashboard_server/orchard_mapping.py`: `p_fusion = (w_l p_lidar + w_c p_camera) / (w_l + w_c)`
- `static/app.js`: `Camera` mode는 영상 bbox만, `Fusion Trees` mode는 LiDAR와 camera 양쪽 overlay를 동시에 표시

### LiDAR orchard navigation with clustering and RANSAC

Reference: Navigation of an Autonomous Spraying Robot for Orchard Operations Using LiDAR for Tree Trunk Detection  
URL: https://www.mdpi.com/1424-8220/23/10/4808

이 연구는 DBSCAN, K-means, RANSAC를 조합해 orchard row navigation을 수행한다. 현재 dashboard 구현에서는 DBSCAN 전체를 쓰지는 않고 polar scan의 angular order를 이용한 gap clustering을 사용한다. 이유는 RPLIDAR 2D scan이 이미 angle-sorted sequence라서, 실시간 Python path에서는 angular continuity를 활용하는 편이 Jetson Orin Nano에서 가볍다.

현재 코드 반영:

- 인접 point gap 기반 cluster split
- covariance principal axis 기반 line/wall 판단
- wall fragment merge와 object track gate
- archive map에서는 scan matching으로 pseudo-odometry 계산

### ADE20K / SegFormer semantic segmentation path

Reference: SegFormer: Simple and Efficient Design for Semantic Segmentation with Transformers  
URL: https://arxiv.org/abs/2105.15203  
Reference: NVIDIA SegFormer-B0 ADE20K checkpoint  
URL: https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512  
Reference: ADE20K semantic class list  
URL: https://docs.ultralytics.com/datasets/semantic/ade20k

SegFormer는 hierarchical Transformer encoder와 lightweight MLP decoder로 semantic segmentation을 수행한다. Hugging Face의 `nvidia/segformer-b0-finetuned-ade-512-512`는 ADE20K 150-class scene parsing checkpoint이며, ADE20K class에는 `wall`, `tree`, `grass`, `person`, `table`, `plant`, `chair`, `column`, `pole` 등이 포함된다. 따라서 현재 문제처럼 실내 벽/테이블/플라스틱/기둥류가 tree로 보이는 오탐을 줄이는 데 generic COCO detector보다 적합하다.

다만 현재 dashboard Docker image는 `numpy`와 `opencv-python-headless`만 설치되어 있다. PyTorch/Transformers를 즉시 requirements에 추가하면 Jetson ARM image build, GPU runtime, model download/cache, TensorRT export 문제가 한 번에 들어와 서비스 안정성이 떨어진다. 그래서 v7 배포에서는 OpenCV 기반 gate를 안정 baseline으로 적용하고, 논문/실험용 다음 단계는 다음 순서로 둔다.

1. Jetson host 또는 별도 inference container에 SegFormer-B0 ADE20K를 설치한다.
2. `tree`, `plant`, `grass` mask와 `wall`, `floor`, `table`, `chair`, `person`, `column`, `pole` mask를 분리한다.
3. 현재 OpenCV trunk candidate가 positive semantic mask와 겹치거나 인접할 때만 tree로 승격한다.
4. negative semantic mask와 겹치면 confidence를 0으로 낮추거나 후보를 제거한다.
5. 충분한 orchard 수동 annotation이 모이면 SegFormer/YOLO semantic head를 `tree_trunk`, `canopy`, `branch`, `background` class로 fine-tune하고 TensorRT로 export한다.

현재 코드 반영:

- `dashboard_server/camera_analysis.py`: model-agnostic schema 유지. `detections[].bbox`, `confidence`, `label`, `contour`, `line`, `semantic_gate`를 저장한다.
- `scripts/camera_ws_bridge.py`: live frame payload에 `overlay` 포함.
- `scripts/data_recorder_service.py`: archive timeline camera frame에 `overlay` 저장.
- 추후 SegFormer/TensorRT worker는 같은 schema를 채우면 UI와 archive API 변경 없이 교체 가능하다.

### YOLO / TensorRT deployment path

Reference: Ultralytics NVIDIA Jetson guide  
URL: https://docs.ultralytics.com/guides/nvidia-jetson  
Reference: Ultralytics TensorRT export guide  
URL: https://docs.ultralytics.com/integrations/tensorrt

YOLO 계열은 real-time detection/segmentation을 Jetson/TensorRT 경로로 배포하기 쉽다. 그러나 COCO 계열 pretrained segmentation은 “tree trunk”나 “orchard row”를 직접 분리하지 못하고, table leg/pole/column과 trunk를 구분하기 어렵다. 따라서 즉시 사용할 public model은 ADE20K semantic segmentation이 더 적합하고, YOLO는 수동 annotation이 쌓인 뒤 orchard-specific segmentation model로 fine-tune하는 방향이 더 맞다.

## 3. LiDAR-only 알고리즘

입력 point:

```text
p_i = (x_i, y_i, r_i, theta_i, q_i)
x_i = r_i sin(theta_i)
y_i = r_i cos(theta_i)
```

각 frame에서 angle 순서로 point를 훑으며 cartesian gap이 threshold보다 커지면 cluster를 끊는다.

```text
d_i = ||p_i - p_{i-1}||_2
tau_i = max(0.13, 0.045 max(r_i, r_{i-1}))
split if d_i > tau_i
```

cluster covariance로 주축을 구한다.

```text
Sigma = (1/N) sum_i (p_i - mu)(p_i - mu)^T
theta = 0.5 atan2(2 sigma_xy, sigma_xx - sigma_yy)
```

주축 방향 projection 길이 `L`, 수직 residual 폭 `W`, point density, radius를 계산한다. 긴 선형 cluster는 wall로 제외하고, compact하고 density가 높은 cluster를 orchard tree candidate로 둔다.

v5부터는 wall로 판정된 선분 주변의 orchard candidate를 한 번 더 제거한다. 벽 line 자체가 scan noise 때문에 여러 조각으로 끊기면 일부 조각이 compact cluster처럼 보여 tree 후보가 되기 때문이다.

```text
d_wall = min_j distance(p_candidate, wall_segment_j)
tau_wall = max(0.28, min(0.62, r_candidate + 0.22 + 0.014 range_candidate))
suppress if d_wall <= tau_wall and projection is inside the wall segment with <= 0.42 m endpoint slack
```

이 gate는 사용자가 지적한 “확실히 벽인 부분이 나무로 보이는” 문제를 직접 겨냥한다. 단점은 실제 과수가 벽/긴 선형 구조물 30-60cm 안쪽에 붙어 있으면 보수적으로 제거될 수 있다는 점이다. 논문용 최종 실험에서는 수동 GT 기준으로 wall suppression threshold의 precision/recall을 다시 산출해야 한다.

LiDAR confidence:

```text
C_lidar =
  0.16
  + 0.34 C_compact
  + 0.18 C_density
  + 0.18 C_aspect
  + 0.08 C_points
  + 0.06 C_range
  - P_clutter
  + B_trunk
```

뒤쪽 사람 제외:

```text
rear_person_zone = (y < -0.25) and (|x| < 5.0) and (range < 9.0)
```

이 조건은 주어진 두 archive에서 LiDAR 뒤쪽에 서 있는 사람을 orchard candidate에서 제외하기 위한 실험 조건이다.

## 4. Camera-only 알고리즘

현재 camera path는 `dashboard_server/camera_analysis.py`에 구현되어 있다.

현재 알고리즘 이름은 `opencv_orchard_scene_trunk_semantic_gate_v4`다. v1은 green/canopy mask와 dark mask를 합쳐 contour를 만들었기 때문에 잎, 그림자, 전체 frame을 나무로 잡는 문제가 있었다. v2는 bark mask와 trunk line을 분리했지만 UI가 여전히 bbox를 그려 나무 형태를 가렸다. v3는 bbox 대신 contour/line outline과 temporal stabilizer를 적용했다. v4는 실내/비과수 장면을 먼저 거르고, 후보 주변에 canopy/vegetation 문맥이 없는 수직 물체를 tree로 승격하지 않는다.

1. BGR frame을 HSV/gray/LAB로 변환한다.
2. HSV green mask로 잎/캐노피 영역을 추출한다.
3. brown, low-saturation dark, grayscale dark mask를 합쳐 bark/trunk 후보를 만든다.
4. trunk 후보에서 충분히 초록인 vegetation 영역을 제거한다.
5. morphology open/close로 작은 noise를 제거하고, 아래쪽까지 이어지는 trunk-like contour를 bbox로 만든다.
6. CLAHE grayscale + Canny + HoughLinesP로 diagonal trunk line 후보를 추가한다.
7. image-level orchard scene gate를 통과하지 못하면 frame 전체 camera tree overlay를 비운다.
8. 너무 큰 bbox, 화면 하단의 바닥 조각, canopy support가 없는 낮은 사선 line을 제거한다.
9. contour candidate는 polygon contour를, line candidate는 trunk line segment를 함께 저장한다.
10. IoU/center matching temporal stabilizer로 이전 frame 후보와 연결하고 bbox/contour/line geometry를 지수평활한다.
11. 한 frame에서 confidence 상위 8개만 overlay로 내보낸다.

Scene gate:

```text
R_green = count(green_mask) / image_area
R_upper = count(green_mask in upper 62% image) / upper_area
orchard_scene = R_green >= 0.035 and (R_upper >= 0.022 or R_mid >= 0.030)
```

Candidate gate:

```text
S_canopy = max(R_context, 1.15 R_above, 0.75 R_side)
min_support = 0.082 if R_green < 0.12 else 0.064
min_support_line = min_support + 0.04
reject if S_canopy < min_support
reject line if R_above < 0.06 and R_context < 0.09
reject edge candidate if S_canopy < 0.12
```

이 gate의 목적은 실내 테이블 다리, 의자 다리, 플라스틱 경계, 벽지 texture처럼 “수직이고 어두운 물체”를 과수로 표시하지 않는 것이다. 카메라가 실제 과수를 판단할 때는 줄기 색/형상만으로 충분하지 않고, canopy/vegetation 문맥 또는 semantic segmentation support가 필요하다는 시행착오를 반영했다.

Camera confidence:

```text
C_camera =
  C_base
  + w_a min(1, aspect / 4)
  + w_h min(1, h_box / 0.7 h_img)
  + w_t min(1, R_trunk * 2.2)
  + w_g min(1, R_canopy * 2.5)
  + B_lower
```

line 후보는 다음 항을 추가로 사용한다.

```text
C_line =
  0.10
  + 0.24 min(1, L_line / 0.72 h_img)
  + 0.18 V_line
  + 0.18 D_line
  + 0.14 min(1, R_trunk,line * 1.6)
  + 0.16 min(1, S_canopy * 3.0)
  - P_gate
  + B_lower
```

여기서 `V_line = |dy| / sqrt(dx^2 + dy^2)`이고, `D_line`은 line mask 내부 gray median 기반 dark score다. v4에서는 `V_line < 0.68`인 line, 화면 하단에서만 시작하는 line, `S_canopy`가 낮은 line을 제거한다. 이 방식은 카메라가 기울어져 tree trunk가 화면에서 대각선으로 보이는 조건은 허용하되, 바닥 사선/벽 경계가 tree로 보이는 문제를 줄인다.

Temporal smoothing:

```text
B_t = (1 - alpha) B_{t-1} + alpha B_{raw,t}
alpha = 0.42
```

candidate matching은 bbox IoU와 center distance를 함께 사용한다. contour와 line geometry는 smoothing된 bbox에 맞춰 재스케일한다. 이 방식은 정지 화면에서도 frame별 threshold 변화 때문에 overlay가 크게 흔들리는 문제를 줄이기 위한 baseline이다.

Camera overlay 표시:

- `2D Objects`: camera overlay를 표시하지 않는다.
- `Camera`: camera contour/line outline만 표시한다.
- `Fusion Trees`: camera contour/line outline과 LiDAR fusion tree를 함께 표시한다.
- camera 표시 방식은 `Camera`, `Camera+Overlay`, `Overlay` 세 가지다.

## 5. Three-layer 지도와 Weighted Fusion

현재 지도 생성은 calibration 전 단계다. LiDAR-only와 Camera-only는 같은 pseudo-odometry path 위에 별도로 누적되고, Fusion은 두 tree set을 world frame에서 matching한 뒤 평균가중치로 결합한다.

Camera-only projection은 bbox 중심과 높이를 이용한다.

```text
phi = (u_c / W - 1/2) FOV_x
d_h = f_x H_trunk / h_box
d_b = 1.15 + (1 - v_bottom / H) 11.2
d_camera = clip(0.62 d_h + 0.38 d_b, 1.15, 13.5)
x_camera = d_camera tan(phi)
y_camera = d_camera
```

현재 값:

- `FOV_x = 78 deg`
- `H_trunk = 1.35 m`
- `d_camera` clip range: `1.15-13.5 m`

이 모델은 depth camera가 아니라 단안 camera의 heuristic metric projection이다. 따라서 Camera-only map은 최종 계측값이 아니라 LiDAR 중심선을 보완하기 위한 row evidence로 해석한다.

LiDAR tree `p_l`와 camera tree `p_c`가 거리 gate 안에서 matching되면 다음 평균가중치를 적용한다.

```text
w_l = 0.64 C_lidar
w_c = 0.36 C_camera
p_fusion = (w_l p_lidar + w_c p_camera) / (w_l + w_c)
```

unmatched camera 후보는 confidence가 `0.90` 이상이고 반복 관측 `hits >= 5`일 때만 fusion에 들어간다. 이 제한은 camera-only false positive가 최종 row centerline을 오염시키는 것을 줄이기 위한 보수적 정책이다.

Archive map에서는 frame별 local candidate를 world 후보로 변환한다.

```text
g_t = R(theta_t) p_t + T_t
```

여기서 `T_t`, `theta_t`는 scan matching으로 추정한 pseudo-pose다.

연속 frame 병합 gate:

```text
gate = max(0.48, min(1.05, 0.32 + radius + 0.018 max(|x|, |y|)))
```

최종 map tree filtering:

- hits >= 8 and time_span >= 2.0
- 또는 hits >= 5 and confidence >= 0.74
- 또는 camera_confidence가 높고 confidence >= 0.78

## 5.1 행간 중심선

행간 중심선은 각 layer의 tree set에서 별도로 계산한다. path 시작점 `p_0`에서 도착점 방향 단위벡터를 `a`, 수직벡터를 `n`이라고 할 때 tree point `p_i`는 station/lateral 좌표로 투영된다.

```text
s_i = (p_i - p_0) dot a
l_i = (p_i - p_0) dot n
```

`l_i > 0`은 left row, `l_i < 0`은 right row로 두고, station을 2m bin으로 나눈다. 각 bin에서 confidence weighted lateral 평균을 구한다.

```text
l_left,k  = sum(C_i l_i) / sum(C_i)
l_right,k = sum(C_j l_j) / sum(C_j)
l_center,k = (l_left,k + l_right,k) / 2
```

양쪽 row가 모두 관측되지 않은 bin에서는 paired bin에서 얻은 median row gap을 사용한다.

```text
g_row = median(l_left,k - l_right,k)
```

Fusion 중심선은 LiDAR 중심선과 Camera 중심선을 직접 평균내지 않는다. 먼저 tree position을 평균가중치로 결합한 뒤, 결합된 tree set에서 같은 centerline estimator를 돌린다. 이렇게 해야 camera가 “선”을 억지로 미는 것이 아니라, camera가 보완한 trunk landmark가 중심선에 반영된다.

## 6. 파란 원이 많고 지도 tree가 적었던 이유와 수정

이전 UI는 archive Fusion Trees에서 frame별 local orchard candidate를 그대로 파란 원으로 그렸다. 이 값은 “현재 scan에서 나무일 수 있는 후보”라서 foliage clutter, 가지, 순간 noise가 많이 섞인다. 반면 하단 orchard map은 여러 frame에서 반복 관측되고 scan matching pose에서 같은 위치로 병합된 후보만 남긴다.

따라서 이전에는 같은 `Fusion Trees`라는 이름 아래에서 서로 다른 단계의 결과를 비교하게 되어 혼란이 생겼다.

수정 후 규칙:

- Live/Connections: 아직 누적 map이 없으므로 현재 frame의 fusion candidate를 표시한다.
- Archive LiDAR canvas: 현재 frame LiDAR orchard 후보를 anchor로 표시하고, final map tree는 가까운 anchor에 matching될 때 confidence/identity를 보조한다.
- Archive map: 최종 tree list를 world frame에서 표시하고, timeline에 맞춰 rover pose marker를 움직인다.

즉 archive replay의 LiDAR 파란 원은 current sensor-relative point cluster에 붙어 움직이고, 하단 지도 tree는 world-frame 누적 final tree set에서 나온다. LiDAR canvas에서는 raw point 정합성을 우선한다.

## 7. 실험 데이터 결과

2026-07-09 기준 local archive 재처리 결과:

| archive | camera segment frames checked | camera detections in first 2s | LiDAR-only map | Camera-only map | Weighted Fusion map |
| --- | ---: | ---: | ---: | ---: | ---: |
| `20260708T053333Z_both` | 4 | 5 | 36 trees / 13 center points | 42 trees / 11 center points | 35 trees / 13 center points |
| `20260708T054036Z_both` | 4 | 18 | 36 trees / 16 center points | 42 trees / 15 center points | 39 trees / 16 center points |

해석:

- 1번 archive는 foliage clutter가 적어 LiDAR-only 중심선과 fusion 중심선이 비슷하게 나온다. camera-only는 단안 depth 근사 오차가 있으므로 후보 수가 많지만 fusion에서는 unmatched camera 후보를 제한한다.
- 2번 archive는 잎/가지 간섭이 많아 LiDAR-only 후보가 흔들릴 가능성이 커진다. fusion은 camera row evidence를 평균가중치로 반영해 row gap과 centerline을 보정한다.
- v1 camera overlay에서 보였던 100% full-frame canopy box는 v2에서 제거되었고, v3에서는 bbox 대신 contour/line outline과 temporal stabilizer를 적용했다. camera-only는 여전히 확정 semantic tree가 아니라 trunk-like support이며, 최종 과수 위치 판단은 LiDAR 후보, 반복 관측, camera support를 결합한 Fusion Trees가 담당한다.
- 이 숫자는 ground truth precision/recall이 아니라 automatic candidate count다. 논문용 정량 평가는 manual label이 필요하다.

## 8. 시행착오

1. Object track green path는 sensor-relative history라서 로버가 움직일 때 고정 물체가 움직이는 것처럼 보였다. 현재는 track 자체는 유지하되, jump가 큰 track은 표시하지 않고, world-frame 누적은 orchard map으로 분리했다.
2. Archive Fusion Trees에서 frame 후보를 바로 그리면 파란 원이 너무 많아졌다. 한때 final map tree를 역투영하는 방식으로 변경했지만, pseudo-odometry 오차 때문에 raw LiDAR point와 원이 떨어져 보였다. 최종적으로 LiDAR canvas는 current frame 후보를 anchor로 쓰고 final map은 matching/confidence 보조로 쓰는 절충안을 적용했다.
3. Camera support를 처음에는 frame-level confidence만 사용했지만, 사용자가 영상 위에서 나무를 어떻게 인식했는지 보고 싶어 했기 때문에 contour/line overlay schema를 추가했다.
4. Camera overlay label은 글자가 겹치면 작은 confidence dot으로 대체한다. LiDAR tree probability도 label 충돌 시 dot으로 대체한다.
5. Camera v1은 green/canopy contour가 frame 전체로 합쳐져 100% bbox를 만들었다. v2에서는 vegetation-suppressed bark mask와 trunk-line 후보를 분리하고, 큰 bbox와 낮은 화면 바닥 조각을 제거했다. v3에서는 bbox 표시를 contour/line outline으로 바꾸고 temporal stabilizer를 적용했다.
6. Archive LiDAR raw point는 표시 전에 `display_y=-raw_y`로 보정되는데, final fusion tree 역투영 결과가 이 경로를 우회해 LiDAR와 overlay가 반전된 것처럼 보였다. 이후 y 보정은 맞췄지만 pseudo-odometry 오차는 남았기 때문에, LiDAR canvas에서는 frame anchor를 우선 사용하도록 다시 수정했다.

## 9. 다음 연구 단계

1. LiDAR-camera extrinsic calibration을 수행한다.
2. LiDAR candidate center를 image plane에 projection한다.
3. Projected ROI와 camera segmentation class probability를 결합한다.
4. Wheel odometry, IMU, `/tf`를 archive timeline에 저장한다.
5. 수동 label set을 만들어 precision, recall, F1, localization error를 계산한다.

Projection 기반 fusion:

```text
P_img = K [R | t] P_lidar
P_tree = sigmoid(w_l C_lidar + w_c P_camera(tree | ROI) + w_h H_hits - w_w P_wall)
```

평가지표:

```text
precision = TP / (TP + FP)
recall = TP / (TP + FN)
F1 = 2 precision recall / (precision + recall)
e_pos = ||p_pred - p_gt||_2
```

## 10. 코드 위치

| 파일 | 역할 |
| --- | --- |
| `dashboard_server/lidar_analysis.py` | LiDAR cluster, wall/object/orchard candidate, track gate |
| `dashboard_server/camera_analysis.py` | camera bbox detection, confidence, model-agnostic overlay schema |
| `dashboard_server/orchard_mapping.py` | scan matching, local-to-global tree transform, map merge/filter |
| `dashboard_server/telemetry.py` | archive segment 재분석, camera overlay backfill, orchard map API |
| `scripts/camera_ws_bridge.py` | live camera frame overlay 생성 |
| `scripts/data_recorder_service.py` | recording timeline에 camera overlay 저장 |
| `static/app.js` | LiDAR/camera/archive overlay 렌더링, map current pose marker |
