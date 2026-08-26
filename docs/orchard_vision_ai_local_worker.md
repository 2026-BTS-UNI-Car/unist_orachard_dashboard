# Orchard Vision AI Local Worker

Last updated: 2026-08-22

## 1. 운영 원칙

카메라 인식은 Jetson 내부 AI-only 경로다. 외부 API, Hugging Face token, Roboflow token, OpenCV tree heuristic을 운영 중에 사용하지 않는다.

```text
camera frame
  -> localhost:8785/analyze
  -> YOLO11n-seg TensorRT FP16
  -> local CLIP ViT-B/32 OOD gate
  -> contour + probability + rejection evidence
  -> dashboard / recorder / archive
```

AI worker가 없거나 model revision이 다르면 `ok=false`로 실패한다. Dashboard는 `Camera AI unavailable`을 표시하며 다른 알고리즘으로 조용히 대체하지 않는다.

## 2. 현재 model artifact

Jetson runtime:

```text
runtime/training_runs/orchard_trunk_teacher_ood_v1/weights/
  best.pt       # 재학습/검증용 PyTorch weight
  best.onnx     # 교환 및 TensorRT export 중간 형식
  best.engine   # 현재 Jetson 운영 TensorRT FP16 engine

runtime/models/hf/hub/models--openai--clip-vit-base-patch32/
  snapshots/<revision>/
```

2026-07-10 확인 크기:

| Artifact | Size |
| --- | ---: |
| `best.pt` | 5.70 MiB |
| `best.onnx` | 11.09 MiB |
| `best.engine` | 9.09 MiB |

TensorRT engine은 target Jetson의 TensorRT/CUDA/GPU 조합에 종속될 수 있으므로 해당 Jetson에서 다시 build하는 것을 원칙으로 한다.

## 3. 환경 변수

Jetson repository의 `.env`에는 API token을 넣지 않는다. 다음 local path만 설정한다.

```bash
ORCHARD_VISION_AI_URL=http://127.0.0.1:8785/analyze
ORCHARD_VISION_BACKEND=operational
ORCHARD_YOLO_MODEL=/app/runtime/training_runs/orchard_trunk_teacher_ood_v1/weights/best.engine
ORCHARD_CLIP_MODEL=openai/clip-vit-base-patch32
ORCHARD_CLIP_FP16=1
ORCHARD_VISION_REVISION=orchard_trunk_yolo11_trt_clip_ood_v1
ORCHARD_VISION_CONFIDENCE=0.08
ORCHARD_VISION_DEVICE=cuda
HF_HOME=/app/runtime/models/hf

# 0 = source/native pixels. This input is independent of the 640 px preview.
ORCHARD_VISION_INFERENCE_WIDTH=0
ORCHARD_VISION_INFERENCE_JPEG_QUALITY=86
RECORDER_CAMERA_PREVIEW_WIDTH=640
RECORDER_CAMERA_JPEG_QUALITY=68
```

운영 값을 원격 `.env`에 보수적으로 반영하는 helper:

```bash
cd /home/unist/workspace/unist_orachard_dashboard
./scripts/configure_operational_vision_env.sh .env
```

이 스크립트는 token을 만들거나 다운로드하지 않는다.

### 추론 입력과 프리뷰 분리

`ORCHARD_VISION_INFERENCE_WIDTH=0`은 ROS image source/native 해상도를 추론에 그대로 사용한다. 양수 값은 aspect ratio를 유지하는 최대 폭이며 source보다 큰 값으로 upscale하지 않는다. WebSocket 표시 해상도와 recorder의 `RECORDER_CAMERA_PREVIEW_WIDTH=640`은 추론 입력을 바꾸지 않는다. worker 모델 revision은 그대로 유지하고 전처리 provenance는 `source_native_preview_decoupled_jpeg_v1`로 별도 기록한다. 과거 overlay에 이 provenance가 없거나 bounded/preview 입력이면 native v10으로 재사용하지 않고 fail-closed한다.

72-frame 동일 입력을 같은 JPEG quality 86으로 재압축해 비교한 yield/지연은 다음과 같다. 사람 기준 GT가 없으므로 accepted 수는 precision, recall 또는 정확도가 아니며, 이 표는 입력 정책 선택을 위한 proposal yield와 처리시간 측정일 뿐이다.

| 추론 폭 | accepted | rejected | accepted frame | worker mean | worker p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 640 | 76 | 291 | 34/72 | 124.7 ms | 187.7 ms |
| 960 | 85 | 미집계 | 36/72 | 125.688 ms | 195.39 ms |
| 1920/native | 114 | 239 | 38/72 | 147.79 ms | 222.04 ms |

1920/native는 640 대비 accepted yield가 50.0%, accepted frame 수가 11.8% 많았고 mean/p95는 각각 23.1/34.3 ms 길었다. 5 Hz 주기의 200 ms보다 p95가 길기 때문에 camera bridge는 큐를 쌓지 않고 단일 최신 frame slot만 처리한다. 처리 중 도착한 이전 frame은 합쳐 버리고 `dropped_frame_count`에 기록한다. recorder camera subscription도 DDS `BEST_EFFORT + KEEP_LAST(1)`로 제한하여 느린 추론 중 backlog를 누적하지 않는다. 운영 상태 파일은 policy revision, 실제 source/inference 크기, latency, inference/failure/drop 수와 `latest_only`를 노출한다.

## 4. Compose service

`vision-ai-worker`는 dashboard와 분리된 NVIDIA runtime container다.

```yaml
runtime: nvidia
network_mode: host
ipc: host
```

Worker는 `127.0.0.1:8785`, dashboard는 `127.0.0.1:8080`을 사용한다. 외부 모델 API가 아니라 같은 Jetson의 loopback 통신이다.

Jetson은 Docker 29와 `docker-compose 1.29.2` 조합이다. 오래된 Compose에서 `ContainerConfig` 오류가 나면 CI fallback은 `unist-orachard-*` 관리 container만 다시 만들고 `portainer`는 제거하지 않는다.

## 5. Health contract

```bash
curl -fsS http://127.0.0.1:8785/health | python3 -m json.tool
```

필수 응답:

```json
{
  "ok": true,
  "algorithm": "local_orchard_vision_ai_worker_v4",
  "revision": "orchard_trunk_yolo11_trt_clip_ood_v1",
  "backend": "operational_yolo11_seg_clip_ood",
  "model": "/app/runtime/training_runs/orchard_trunk_teacher_ood_v1/weights/best.engine + openai/clip-vit-base-patch32",
  "device": "cuda",
  "camera_policy": "AI-only; no OpenCV or remote API fallback"
}
```

다음 중 하나면 배포 실패로 처리한다.

- `ok != true`
- backend가 `operational_yolo11_seg_clip_ood`가 아님
- revision이 다름
- model path에 `best.engine` 또는 CLIP model이 없음
- dashboard 또는 camera bridge의 `ORCHARD_VISION_AI_URL` 누락. recorder는 독립 추론을 하지 않으므로 이 환경 변수를 갖지 않고 shared vision topic의 수신 freshness/provenance를 검증한다.

## 6. Analyze schema

Request:

```json
{
  "image": "<base64 jpeg>",
  "schema": "dashboard_camera_overlay_v1",
  "prefer": "tree_trunk_instance_segmentation"
}
```

Accepted detection 예:

```json
{
  "label": "orchard_tree",
  "bbox": [120, 80, 66, 310],
  "contour": [[132, 84], [146, 211], [151, 383]],
  "model_confidence": 0.82,
  "tree_probability": 0.87,
  "ood_score": 0.13,
  "admission": "accepted",
  "rejection_reasons": [],
  "root_group_id": "root-1",
  "stem_count": 2,
  "model_provenance": {
    "segmentation": "orchard_trunk_teacher_ood_yolo11_seg",
    "semantic_ood": "openai_clip_vit_b32_offline"
  }
}
```

Rejected candidate는 UI tree overlay나 지도에 들어가지 않지만 audit response에는 `rejected_detections[]`로 남는다.

## 7. OOD 정책

Worker는 low threshold `0.08`에서 후보를 넓게 만든 뒤 다음 evidence로 거른다.

- YOLO segmentation confidence
- CLIP tree prompt probability
- person, pole, building column, furniture leg, wall/road, foliage-only negative probability
- mask aspect, area, bottom contact, edge truncation
- scene vegetation ratio와 canopy support
- canopy 없는 structural post hard rule

최종 probability는 기본적으로 `0.70 * segmentation + 0.30 * CLIP`이고 shape/context bonus를 작게 더한다. 이 점수는 calibration된 확률이 아니라 운영 ranking score다. 논문에서 probability calibration을 주장하려면 별도 GT에서 reliability diagram과 ECE를 계산해야 한다.

## 8. Archive precompute

재생 중 추론하지 않도록 recording 완료 후 camera와 LiDAR overlay를 순서대로 저장하고 map을 생성한다.

```bash
docker exec unist-orachard-dashboard \
  python /app/scripts/precompute_archive_camera_overlays.py \
  --recording-id 20260708T053333Z_both \
  --recording-id 20260708T054036Z_both

docker exec unist-orachard-dashboard \
  python /app/scripts/precompute_archive_lidar_overlays.py \
  --recording-id 20260708T053333Z_both \
  --recording-id 20260708T054036Z_both

docker exec unist-orachard-dashboard \
  python /app/scripts/precompute_orchard_maps.py \
  --recording-id 20260708T053333Z_both \
  --recording-id 20260708T054036Z_both
```

2026-07-10 camera 218 frame와 LiDAR 216 frame precompute는 실패 0건이었다.

## 9. 재배포 검증

```bash
docker ps --format '{{.Names}} {{.Status}}'
curl -fsS http://127.0.0.1:8785/health
curl -fsS http://127.0.0.1:8080/api/health
python3 scripts/build_operational_readiness_summary.py
```

실제 browser smoke:

```bash
python3 scripts/validate_dashboard_ui.py \
  --base-url https://bts.sarihub.com \
  --recording-id 20260708T053333Z_both
```

이 browser test는 조종 명령을 보내지 않는다. Archive 선택, canvas pixel, 재생, 두 scrubber 동기화, mobile/wide overflow, console error만 검사한다.

## 10. 새 model 교체

사람이 검수한 dataset으로 재학습한 뒤 Jetson에서 export한다.

```bash
yolo task=segment mode=train \
  model=yolo11n-seg.pt \
  data=/path/to/orchard/data.yaml \
  imgsz=640 epochs=100 batch=8 device=0

yolo export \
  model=runs/segment/train/weights/best.pt \
  format=engine half=True imgsz=640 device=0
```

새 engine을 별도 revision으로 배포하고 320-frame evaluator를 다시 통과하기 전에는 기존 운영 engine을 덮어쓰지 않는다.

## 11. 참조

- [Ultralytics export documentation](https://docs.ultralytics.com/modes/export/)
- [NVIDIA TensorRT Quick Start](https://docs.nvidia.com/deeplearning/tensorrt/latest/getting-started/quick-start-guide.html)
- [CLIP paper](https://arxiv.org/abs/2103.00020)
