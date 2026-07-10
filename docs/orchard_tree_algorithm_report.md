# 과수 인식 알고리즘 기술 부록

Last updated: 2026-07-10

운영 수치, 모델 비교 이미지, OOD 결과, U-turn 복원, 배포 검증은 [과수 인식 운영 검증 최종 보고서](docs/orchard_perception_final_report.md)를 기준으로 한다. 이 문서는 논문 방법론에 옮길 핵심 변수와 비교 실험 설계를 간결히 정리한다.

## 1. 연구 질문

1. 2D LiDAR geometry만으로 벽·사람·가지 간섭을 억제하면서 반복 가능한 tree landmark를 만들 수 있는가?
2. RGB instance segmentation과 open-set semantic gate가 LiDAR-only의 tree miss/false positive를 보완하는가?
3. LiDAR-only, Camera-only, Weighted Fusion에서 계산한 행간 중심선 중 fusion이 독립 GT의 lateral error를 줄이는가?

현재 1번과 2번의 운영 prototype은 구현했다. 3번은 calibrated extrinsic과 독립 centerline GT 전에는 가설 검증이 완료된 것이 아니다.

## 2. Camera formulation

Segmentation confidence `p_s`와 CLIP tree semantic support `p_c`를 결합한다.

$$
p_tree = clip(0.70 p_s + 0.30 p_c + b_context + b_shape, 0, 0.995)
$$

그러나 이 scalar가 hard gate를 대체하지 않는다. 비수직 shape, 화면 상단에 떠 있는 mask, oversized region, strong person/post/pole/furniture/wall similarity는 별도로 거부한다.

Temporal map admission:

$$
A_camera = I(hits >= 2) I(p_tree >= tau_camera)
$$

현재 구현은 화면 표시와 map admission을 분리한다. 높은 confidence mask는 첫 frame에도 보일 수 있지만 map에는 두 번째 일관된 관측 이후 들어간다.

## 3. LiDAR formulation

Polar gap clustering:

$$
tau_gap(r) = max(0.16, min(0.62, 0.05 + 0.045 r))
$$

Candidate rejection:

$$
A_lidar = I(C_structure >= tau_s)
          I(d_wall > tau_wall)
          I(not linear_context)
          I(not rear_operator_zone)
$$

Map admission은 관측 횟수와 time span을 사용한다.

$$
A_map = I(hits >= h_min) I(Delta t >= t_min) I(C >= tau_map)
$$

운영 구현은 하나의 고정 `h_min` 대신 높은 confidence, 긴 지속시간, 짧은 unit fixture에 따라 세 gate를 사용한다. 모든 landmark에는 `hits`, `first_seen_s`, `last_seen_s`, `wall_distance_m`, `structure_score`, `rejection_reasons`를 남겨 추적 가능성을 유지한다.

## 4. Pose estimation

Binary scan-context descriptor `D`의 circular shift similarity로 큰 회전 hypothesis를 만든다.

$$
s_SC(k) = <D_(t-1), roll(D_t, k)> / (||D_(t-1)|| ||D_t||)
$$

각 hypothesis의 rigid transform은 이전 scan의 Euclidean distance field `F`에서 평가한다.

$$
s_DF(T) = 0.62 r_inlier + 0.28 exp(-mean(min(F(Tp),0.8))/0.18) + 0.10 r_valid
$$

Coarse search 후 translation 0.06 m, rotation 1도로 refine한다. Score, inlier ratio, RMSE, descriptor margin, objective margin이 낮으면 transform을 0으로 두어 잘못된 motion 누적을 막는다.

## 5. Three-layer map

LiDAR landmark `p_l`와 camera landmark `p_c`가 거리 gate 이내일 때:

$$
w_l = 0.64 C_l,  w_c = 0.36 C_c
$$

$$
p_f = (w_l p_l + w_c p_c)/(w_l+w_c)
$$

Camera-only metric은 현재 monocular trunk-height 가정이므로 calibration 전에는 보조 evidence다. Fusion centerline은 두 centerline의 평균이 아니라 fused landmark set에서 다시 계산한다.

## 6. 실험 표 설계

| Experiment | Input | Primary metric | Required GT |
| --- | --- | --- | --- |
| Camera tree instance | RGB | mask AP, precision, recall, FP/frame | polygon masks |
| Camera OOD | RGB hard negatives | FPR by person/post/pole/furniture | negative labels |
| LiDAR tree | 2D scan + pose | landmark precision/recall, position RMSE | surveyed tree points |
| Wall suppression | 2D scan | wall fragment FP reduction / tree recall loss | wall/tree labels |
| Odometry | scan sequence | ATE, heading error, U-turn error | wheel/IMU/RTK or SLAM GT |
| Row centerline | three maps | lateral RMSE, max deviation | manual/survey centerline |

같은 장소의 인접 frame이 train과 test에 섞이지 않도록 recording/location 단위 split을 사용한다.

## 7. Ablation

최소 비교:

1. YOLO segmentation only
2. YOLO + geometry gate
3. YOLO + CLIP OOD
4. YOLO + CLIP OOD + temporal admission
5. LiDAR compact cluster only
6. + wall proximity
7. + linear context
8. + repeated world observation
9. LiDAR-only map
10. Camera-only map
11. Weighted Fusion map

각 단계에서 detection 수만 비교하지 말고 GT precision/recall과 false positive 종류를 함께 기록한다.

## 8. 재현 코드

- `scripts/evaluate_orchard_runtime_model.py`
- `scripts/evaluate_lidar_tree_ood.py`
- `scripts/render_lidar_odometry_audit.py`
- `scripts/build_operational_readiness_summary.py`
- `scripts/package_orchard_annotation_review.py`

## 9. 참고 문헌

- [Grounding DINO](https://arxiv.org/abs/2303.05499)
- [SAM 2](https://arxiv.org/abs/2408.00714)
- [CLIP](https://arxiv.org/abs/2103.00020)
- [Scan Context](https://doi.org/10.1109/IROS.2018.8593953)
- [Real-Time Correlative Scan Matching](https://april.eecs.umich.edu/pdfs/olson2009icra.pdf)
- [TensorRT Quick Start](https://docs.nvidia.com/deeplearning/tensorrt/latest/getting-started/quick-start-guide.html)
