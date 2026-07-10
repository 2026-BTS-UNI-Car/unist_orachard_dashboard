# 과수 Segmentation 수동 검수와 Fine-Tuning

Last updated: 2026-07-10

## 1. 이미 준비된 것

Jetson에는 sample video 2개와 archive 2개를 합친 annotation package가 있다.

```text
/home/unist/workspace/unist_orachard_dashboard/runtime/operational_readiness/
  orchard_teacher_v1/
  orchard_teacher_v1_review.tar.gz
```

| 항목 | 값 |
| --- | ---: |
| Frames | 321 |
| Teacher masks | 981 |
| Empty hard-negative frames | 31 |
| Package size | 102.6 MiB |

Teacher mask는 Grounding DINO `tree trunk.` proposal을 SAM2.1 Tiny로 segment한 뒤 CLIP OOD gate를 통과한 초안이다. 정답 GT가 아니므로 사람이 확인해야 한다.

## 2. Package 다시 만들기

```bash
cd /home/unist/workspace/unist_orachard_dashboard
python3 scripts/package_orchard_annotation_review.py \
  --dataset runtime/operational_readiness/orchard_teacher_v1
```

Helper가 수행하는 일:

- image/label/metadata pair 존재 확인
- YOLO polygon token과 `[0,1]` 좌표 범위 확인
- `review_queue.csv` 생성
- `ANNOTATION_GUIDE.md` 생성
- `review_validation.json` 생성
- gzip level 1의 단일 review package 생성

2026-07-10 검증은 missing file, invalid polygon 모두 0건이다.

## 3. 검수 순서

압축을 풀고 `review_queue.csv`의 rank 1부터 본다.

| Priority | 의미 |
| --- | --- |
| 0 | Teacher/CLIP가 거부하거나 애매하다고 본 후보가 있는 frame |
| 1 | 수용 mask가 0개인 frame; 실제 누락인지 hard negative인지 확인 |
| 2 | mask가 6개 이상인 복잡한 frame |
| 3 | 일반 확인 frame |

Priority 0은 가능하면 두 사람이 독립 확인한다. 이 구간이 사람, pavilion post, pole, 가구 다리 오탐과 thin trunk 누락을 가장 많이 포함한다.

## 4. 어떤 polygon을 남기는가

Positive class는 먼저 하나로 유지한다.

```yaml
names:
  0: orchard_trunk
```

포함:

- 실제 과수/나무의 보이는 stem과 trunk
- 기울거나 가는 줄기
- 잎과 가지에 일부 가려진 줄기
- 뿌리 근처에서 여러 갈래로 갈라진 stem

삭제:

- 사람과 사람 다리
- pavilion/건물 기둥
- utility pole와 표지 pole
- 의자·테이블 다리
- 벽, 도로, curb, 플라스틱 edge
- 줄기 없이 잎/풀/관목만 있는 region
- 그림자와 바닥 선

여러 갈래 tree는 보이는 stem마다 polygon을 만든다. 같은 root에서 나온 stem이라는 사실은 CVAT attribute나 frame note에 같은 `root_group_id`로 기록한다. Runtime은 bottom-point proximity로 다시 group한다.

## 5. CVAT 작업

1. `orchard_teacher_v1_review.tar.gz`를 작업 PC로 가져온다.
2. CVAT project를 만들고 `orchard_trunk` polygon label을 추가한다.
3. `images/train`, `images/val`의 이미지를 각각 task로 import한다.
4. 현재 YOLO segmentation label을 import한다. CVAT 버전에서 polygon import가 맞지 않으면 Ultralytics/Roboflow를 형식 변환에만 사용한다.
5. `review_queue.csv` 순서로 polygon을 추가·수정·삭제한다.
6. 나무가 없는 frame은 빈 label로 유지한다.
7. 모든 frame 1회 검수 후 priority 0을 다시 본다.
8. 최종 YOLO segmentation 형식으로 export한다.

인터넷 기반 annotation UI를 사용해도 Jetson runtime에는 token이나 API key를 넣지 않는다.

## 6. Split 원칙

인접 video frame을 random split하면 거의 같은 화면이 train과 test에 동시에 들어가 성능이 부풀려진다.

권장:

- Train: archive 1 일부 + sample video 일부
- Validation: 다른 시간 구간
- Test: 다른 주행, 다른 위치 또는 다음 현장 촬영

논문용 test는 threshold나 OOD prompt를 조정할 때 절대 보지 않는다. 가능하면 새로운 과수원 주행을 독립 test로 둔다.

## 7. 학습

Jetson 또는 별도 NVIDIA GPU에서 실행한다.

```bash
yolo task=segment mode=train \
  model=yolo11n-seg.pt \
  data=/path/to/reviewed_orchard/data.yaml \
  imgsz=640 \
  epochs=100 \
  batch=8 \
  device=0 \
  project=runtime/training_runs \
  name=orchard_trunk_reviewed_v2
```

초기에는 `YOLO11n-seg`를 유지해 Orin Nano latency를 보장한다. Recall이 부족하고 GPU 여유가 측정되면 동일 split에서 `YOLO11s-seg`를 비교한다.

## 8. 반드시 계산할 지표

Mask IoU:

$$
IoU = |M_pred intersection M_gt| / |M_pred union M_gt|
$$

Tree instance:

$$
Precision = TP / (TP + FP)
$$

$$
Recall = TP / (TP + FN)
$$

추가 운영 지표:

- false positive per frame
- 사람/post/pole/furniture별 false positive 수
- zero-detection frame 중 실제 tree가 있는 비율
- multi-stem root 중 중복 count 비율
- Jetson mean/p95 inference latency
- confidence reliability diagram과 ECE

## 9. TensorRT export

검증을 통과한 `best.pt`만 Jetson에서 export한다.

```bash
yolo export \
  model=runtime/training_runs/orchard_trunk_reviewed_v2/weights/best.pt \
  format=engine \
  half=True \
  imgsz=640 \
  device=0
```

Engine file만 교체하지 말고 revision도 올린다.

```bash
ORCHARD_YOLO_MODEL=/app/runtime/training_runs/orchard_trunk_reviewed_v2/weights/best.engine
ORCHARD_VISION_REVISION=orchard_trunk_reviewed_v2_trt_clip_ood_v1
```

## 10. 배포 전 비교 gate

새 모델은 현재 운영 모델과 같은 320-frame evaluator를 통과해야 한다.

```bash
docker exec unist-orachard-vision-ai python /app/scripts/evaluate_orchard_runtime_model.py \
  --model /app/runtime/training_runs/orchard_trunk_reviewed_v2/weights/best.engine \
  --clip-model /app/runtime/models/hf/hub/models--openai--clip-vit-base-patch32/snapshots/<revision> \
  --video /app/runtime/operational_readiness/input/IMG_4469.mov \
  --video /app/runtime/operational_readiness/input/IMG_4471.mov \
  --timeline /app/runtime/recordings/20260708T053333Z_both/timeline.json \
  --timeline /app/runtime/recordings/20260708T054036Z_both/timeline.json \
  --output /app/runtime/analysis/operational_readiness/reviewed_v2_eval \
  --device cuda
```

배포 조건:

- 독립 GT의 recall과 precision이 현재 모델 이상
- 사람/post/pole false positive가 증가하지 않음
- archive/sample contact sheet 수동 감사 통과
- TensorRT mean/p95가 실시간 budget 이내
- worker health의 backend/revision/model 확인
- 두 archive precompute 실패 0
- 공개 dashboard browser smoke 통과

## 11. 현재 모델을 바로 덮어쓰지 않는 이유

현재 TensorRT 모델의 validation mask mAP50-95는 0.224이고 320-frame 운영 감사에서 927개 후보를 OOD로 거부했다. 수동 GT가 없는 새 모델이 raw detection 수만 늘리는 것은 개선 증거가 아니다. 독립 GT와 hard-negative false positive가 함께 좋아질 때만 승격한다.
