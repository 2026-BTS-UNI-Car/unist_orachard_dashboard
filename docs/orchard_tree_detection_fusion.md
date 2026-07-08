# 과수 인식과 LiDAR-Camera Fusion

이 문서는 Rover Control dashboard에서 주변 과수 위치를 실시간/아카이브로 표시하기 위한 인식 구조를 정리한다. 현재 구현은 2D LiDAR 기반 후보 검출을 즉시 사용하고, archive에서는 camera frame의 식생/줄기 support를 함께 사용해 fusion confidence를 만든다.

## 목표 데이터셋

| archive | 조건 | 분석 의도 |
| --- | --- | --- |
| `20260708T053333Z_both` | 잎/가지 간섭이 적은 주행, LiDAR 뒤쪽에 사람 존재 | 뒤쪽 사람 후보를 제외하고 줄기/과수 후보가 안정적으로 누적되는지 확인 |
| `20260708T054036Z_both` | 잎/가지 간섭이 많은 주행, LiDAR 뒤쪽에 사람 존재 | foliage clutter가 많아도 반복 관측되는 과수 후보만 지도에 남는지 확인 |

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
6. LiDAR 뒤쪽 사람을 제외하기 위해 `y_m < -0.35 && |x_m| < 4.5` 영역 후보는 orchard 후보에서 제외한다.

후보 confidence는 compactness, density, line aspect, range를 가중합한다.

$$
C_{lidar}
= 0.20
+ 0.32 C_{compact}
+ 0.24 C_{density}
+ 0.16 C_{aspect}
+ 0.08 C_{range}
$$

여기서 `C_compact`는 과수 줄기 또는 가까운 줄기+가지 cluster가 너무 크거나 너무 작지 않은지, `C_density`는 점 밀도, `C_aspect`는 벽처럼 길쭉한 cluster가 아닌지, `C_range`는 멀리 있는 sparse cluster를 낮추는 항이다.

### 2. Camera-only support

Archive map 생성 시 `timeline.camera.frames[]`의 JPEG preview를 OpenCV가 있으면 decoding한다. 현재 기본 구현은 HSV 기반 green/brown mask와 vertical blob contour를 사용한다.

- green mask: 잎/캐노피 후보
- brown/dark mask: 줄기/가지 후보
- vertical blob: 줄기 또는 나무 구조 후보

이 단계는 최종 semantic classifier가 아니라 camera support score다. Jetson Orin Nano에서는 다음 단계로 YOLO 계열 segmentation 모델을 TensorRT로 변환해 `trunk`, `branch`, `canopy` class를 분리하는 것이 목표다.

### 3. LiDAR-Camera Fusion

2D LiDAR 후보가 camera 전방 FOV 안에 있을 때 nearest camera frame의 support score를 confidence에 더한다.

$$
C_{fusion}
= 0.72 C_{lidar}
+ 0.28 I_{front} C_{camera}
$$

`I_front`는 LiDAR 후보가 카메라 전방 영역에 있을 때 1이다. 현재 extrinsic calibration이 없으므로 방향/FOV 기반 support만 사용한다. 정확한 fusion을 위해서는 LiDAR-camera extrinsic matrix를 구하고 LiDAR 후보를 image plane으로 project해야 한다.

### 4. Archive Orchard Map

`/api/recordings/{id}/orchard-map`은 `timeline.json`을 읽어 다음을 만든다.

- `trees`: 누적 과수 후보 위치
- `path`: pseudo-odometry 기반 로버 경로
- `start`, `end`: 출발/도착 표시
- `camera`: camera support 분석 요약

현재 archive timeline에는 wheel odometry/TF가 없으므로, 연속 frame에서 반복 관측되는 LiDAR tree 후보의 median displacement를 이용해 sensor translation을 추정한다.

$$
\Delta p_t
= median_i (z_{t-1}^{(i)} - z_t^{(i)})
$$

`z_t`는 같은 과수로 matching된 local LiDAR 후보 중심이다. 이 값은 임시 pseudo-odometry이며, 실제 논문/실험용 지도에서는 wheel odometry, visual odometry, LiDAR odometry 또는 `/tf`를 함께 기록해야 한다.

## Overlay Mode

Connection, Rover Control, Archive는 같은 perception mode를 공유한다.

| mode | 의미 |
| --- | --- |
| `2D Objects` | LiDAR cluster 기반 일반 물체 후보를 표시한다. 벽은 노란 선, 물체는 분홍 원이다. |
| `Camera` | camera classifier support를 보기 위한 모드다. 현재 LiDAR canvas에서는 geometry가 없으므로 camera support는 archive map/fusion confidence에 반영된다. |
| `Fusion Trees` | LiDAR 후보와 camera support를 결합한 과수 후보만 파란 원으로 표시한다. |

초록색 object track은 local sensor frame에서 물체 위치를 이어 그리던 방식이라 주행 중에는 의미가 없다. 로버가 움직이면 정지한 나무도 sensor frame에서는 움직이기 때문이다. 따라서 live/archive LiDAR overlay에서는 object track line을 기본 표시하지 않고, archive 하단 orchard map에서 로버 경로와 과수 위치를 별도로 그린다.

## Jetson Orin Nano 추론 설계

1. **실시간 LiDAR path**
   - ROS2 `/scan` 수신
   - polar clustering + wall rejection + orchard candidate extraction
   - 10 Hz 이하에서 Python으로도 가능하며, 필요하면 C++ node로 이식

2. **실시간 Camera path**
   - 현재: OpenCV HSV/contour support
   - 다음: YOLOv8n-seg 또는 YOLO11n 계열을 orchard trunk/branch/canopy dataset으로 fine-tuning
   - TensorRT FP16 engine으로 변환
   - camera frame을 640px 이하로 resize하여 10-20 FPS 목표

3. **Fusion path**
   - LiDAR-camera extrinsic calibration 필요
   - LiDAR candidate center를 image plane에 project
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

3. [Accurate Fruit Localisation for Robotic Harvesting using High Resolution LiDAR-Camera Fusion](https://arxiv.org/abs/2205.00404)
   - 활용: LiDAR-camera extrinsic calibration과 sensor fusion이 orchard 인식 정확도를 높인다는 구조를 참고했다.
   - 현재 구현 반영: `C_fusion = 0.72 C_lidar + 0.28 C_camera` 형태의 score fusion을 먼저 두고, 이후 extrinsic projection 기반 ROI fusion으로 확장한다.

4. [Semantic Segmentation of Fruits on Multi-sensor Fused Data in Natural Orchards](https://arxiv.org/abs/2208.02483)
   - 활용: texture와 geometry feature를 결합하고 class imbalance를 다뤄야 한다는 점을 참고했다.
   - 현재 구현 반영: LiDAR-only 후보와 camera support를 분리 저장해 나중에 학습 label과 비교할 수 있게 했다.

5. [Comparing YOLOv8 and Mask R-CNN for object segmentation in complex orchard environments](https://arxiv.org/abs/2312.07935)
   - 활용: orchard segmentation에서 YOLOv8 계열이 Mask R-CNN보다 빠르고 좋은 precision/recall을 보인다는 비교를 참고했다.
   - 현재 구현 반영: Jetson 실시간 path의 다음 모델 후보를 YOLOv8n-seg/YOLO11n 계열 + TensorRT로 잡았다.

6. [Machine Vision-Based Crop-Load Estimation Using YOLOv8](https://arxiv.org/abs/2304.13282)
   - 활용: trunk/branch segmentation과 PCA 기반 geometry estimation 흐름을 참고했다.
   - 현재 구현 반영: camera path에서 trunk/branch/canopy class를 분리하는 segmentation model을 목표로 삼고, LiDAR map에서는 radius/diameter proxy를 남긴다.

## 남은 실험 TODO

- `20260708T053333Z_both`, `20260708T054036Z_both`를 `/api/recordings/{id}/orchard-map`으로 재처리해 tree count, false positive, rear person exclusion 수를 비교한다.
- 같은 archive에서 수동으로 과수 위치를 10-20개 label해 precision/recall을 계산한다.
- 다음 주행 기록부터 `/tf`, wheel odometry, IMU, `/cmd_vel` timeline preview를 함께 저장한다.
- LiDAR-camera extrinsic calibration target을 만들어 projection 기반 fusion으로 교체한다.
