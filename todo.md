# Rover Control Orchard Overlay TODO

Last updated: 2026-07-09

## 2026-07-09 Offline Vision AI Worker 목표

- [x] 사용자가 제공한 Hugging Face/Roboflow token을 repo 밖 `/root/.env`에 저장하고, git 추적 대상이 아님을 확인한다.
- [x] 공개 모델 후보를 archive frame으로 실제 비교한다.
  - [x] Roboflow `tree-trunk-detection-369pz-vm3hn/2`: YOLO11 instance segmentation, contour mask 제공, 현재 best hosted baseline.
  - [x] Roboflow `orchard-trunk-yolov5s/3`: bbox-only, confidence 낮고 mask가 없어 보조 후보로 격하.
  - [x] Roboflow `trees-and-trunks/1`: bbox-only, edge false positive가 많아 비채택.
  - [x] Roboflow `object-detection-isoz2/1`: archive sample에서 유효 prediction 없음.
  - [x] HF SegFormer ADE20K: tree/grass/wall/building semantic gate로는 유용하지만 trunk instance segmentation 용도는 아님.
- [x] API key를 Jetson dashboard container에 넣지 않는 구조로 정리한다.
- [x] AI 처리는 별도 local `vision-ai-worker` container로 분리한다.
- [x] Dashboard camera analysis는 `ORCHARD_VISION_AI_URL`이 있으면 local worker 결과만 사용하고, 실패 시 OpenCV fallback 없이 explicit AI error를 반환한다.
- [x] Local worker는 Ultralytics YOLO `.pt/.onnx/.engine` segmentation 모델을 읽어 `bbox`, `confidence`, `contour`, `line`, `label` schema로 반환한다.
- [x] Local worker는 여러 갈래 밑둥을 하나의 root group으로 묶을 수 있도록 `root_group_id`, `stem_count`를 반환한다.
- [x] Local worker는 optional SegFormer local model directory를 semantic tree support로 사용할 수 있다.
- [x] 모델 준비 스크립트는 HF model snapshot과 YOLO base/fine-tuned model export 경로를 문서화한다.
- [x] Notion `39726f49473a8088bb40d99517eb6eae`에는 최종 보고서 형태로 모델별 결과, 선택 이유, 계층형 구조, offline 배포, 수동 작업 방법을 정리한다.
- [x] Local worker health endpoint는 모델이 없어도 JSON으로 상태를 반환한다.
- [x] Python tests, docs API smoke, `git diff --check`를 통과한다.

## 2026-07-09 Camera AI-only 정책 TODO

- [x] 모델별 contact sheet를 `docs/assets/orchard_perception_models/`에 보고서용 이미지로 보존한다.
- [x] Notion 보고서에 각 모델의 실제 결과 이미지를 첨부한다.
- [x] Camera vision entrypoint에서 OpenCV fallback을 제거한다.
- [x] `ORCHARD_VISION_AI_URL`이 없으면 `vision_ai_url_required` 오류를 반환한다.
- [x] Local AI worker가 응답하지 않으면 `vision_ai_worker_unavailable` 오류를 반환한다.
- [x] UI camera overlay에 `Camera AI unavailable` 오류를 표시한다.
- [x] AI-only 정책 회귀 테스트를 추가한다.

## 2026-07-09 AI worker live 장애 복구 TODO

- [x] 원인 1: `ORCHARD_VISION_AI_URL` 기본값이 비어 있어 dashboard가 AI worker를 호출하지 못하던 문제를 수정한다.
- [x] 원인 2: `vision-ai-worker`가 docker compose profile 뒤에 있어 일반 `docker-compose up -d --build`에서 기동되지 않던 문제를 수정한다.
- [x] Jetson Roboflow base image의 기존 entrypoint가 자체 서버를 띄우며 worker command를 무시하던 문제를 `ENTRYPOINT []`로 수정한다.
- [x] Jetson ONNXRuntime이 TensorRT/CUDA provider를 우선 잡아 crash하던 문제를 `ONNXRUNTIME_EXECUTION_PROVIDERS=CPUExecutionProvider`로 우회한다.
- [x] Roboflow 모델 `tree-trunk-detection-369pz-vm3hn/2`가 CPU provider에서 1회 로딩되는 것을 확인한다.
- [x] 개발 머신에서 `weights.onnx`, `environment.json`, `class_names.txt`, `model_type.json` 오프라인 cache를 생성한다.
- [x] `scripts/prepare_roboflow_offline_cache.py`로 Roboflow API key를 Jetson에 저장하지 않고 cache artifact만 재생성할 수 있게 한다.
- [x] `docker-compose 1.29.2`와 Docker 29의 `ContainerConfig` 충돌 시 dashboard-managed container만 제거하고 재시도하는 fallback을 배포 스크립트에 추가한다.
- [x] API key 없이 Roboflow cache만으로 정식 `unist-orachard-vision-ai` worker `/health`가 `ok: true`를 반환하는지 확인한다.
- [x] Jetson cloudflared SSH `websocket: bad handshake` 복구 후 dashboard 잔여 컨테이너를 정리하고 `unist-orachard-dashboard`, `unist-orachard-vision-ai`를 재기동한다.
- [x] Dashboard live API에서 `Camera AI unavailable / ORCHARD_VISION_AI_URL is not configured` 상태가 더 이상 worker 설정 문제로 발생하지 않는지 확인한다.
- [x] Public `/api/health`, Jetson 내부 `/api/health`, worker `/health`가 모두 정상임을 확인한다.
- [x] `20260708T053333Z_both`와 `20260708T054036Z_both` archive segment에서 `roboflow_inference_local_cache` camera overlay가 반환되는 것을 확인한다.
- [x] 실제 archive frame과 local worker contour overlay 검증 이미지를 `docs/assets/orchard_perception_models/local_roboflow_worker_archive_verification.jpg`로 저장한다.
- [x] 현재 공개 baseline이 놓치는 hard false-negative frame을 기록하고 fine-tuning target으로 분리한다.

## 2026-07-09 Local Vision AI worker 최종 확인

- [x] `Dockerfile.vision_ai`에서 Jetson Roboflow base image의 기본 entrypoint를 무효화한다.
- [x] `docker-compose.yml`에서 `vision-ai-worker`를 profile 밖으로 빼서 일반 `docker compose up -d --build`에 포함한다.
- [x] `USE_INFERENCE_MODELS=False`, `MODEL_CACHE_DIR=/app/runtime/roboflow_cache`로 API key 없이 local cache를 강제한다.
- [x] `ORCHARD_VISION_CONFIDENCE=0.2`, dashboard timeout 6초로 CPU inference 환경을 반영한다.
- [x] `CameraOverlayStabilizer`가 local Roboflow trunk detections를 단일 frame에서도 과도하게 숨기지 않도록 confidence 기준을 worker threshold와 맞춘다.
- [x] `algorithm + revision`으로 archive camera overlay cache freshness를 판정해 오래된 OpenCV/stale overlay를 재사용하지 않게 한다.
- [x] 문서: `docs/orchard_vision_ai_local_worker.md`를 추가하고 docs 탭 catalog에 등록한다.
- [x] 문서: `docs/orchard_perception_final_report.md`의 offline 배포 절차를 실제 local cache worker 방식으로 갱신한다.

## 남은 연구 작업

- [ ] `20260708T054036Z_both` 38.5s처럼 현재 공개 Roboflow baseline이 놓치는 frame을 hard example로 모아 fine-tuning dataset에 포함한다.
- [ ] 사람 하반신, 기둥, 벽, 테이블 다리, 플라스틱 edge를 hard negative class로 충분히 annotation한다.
- [ ] 직접 fine-tuned YOLO11 segmentation weight를 TensorRT FP16 engine으로 export해 `ORCHARD_VISION_BACKEND=ultralytics` 경로로 교체 검증한다.

## 완료 조건

- [x] Connections, Rover Control, Archive에서 perception mode가 같은 의미로 동작한다.
- [x] `2D Objects` 선택 시 LiDAR에만 wall/object/track overlay가 표시되고 camera overlay는 사라진다.
- [x] `Camera` 선택 시 camera 영상에만 tree bbox overlay가 표시되고 LiDAR overlay는 사라진다.
- [x] `Fusion Trees` 선택 시 LiDAR와 camera 양쪽에 overlay가 표시된다.
- [x] Archive `Fusion Trees` LiDAR overlay는 현재 frame LiDAR orchard 후보를 anchor로 쓰고, orchard map final tree는 matching/confidence 보조로 사용한다.
- [x] Camera는 `Camera`, `Camera+Overlay`, `Overlay` 표시 모드를 지원한다.
- [x] Camera bbox에는 tree probability가 겹침이 적은 방식으로 표시된다.
- [x] LiDAR fusion tree overlay는 tree probability를 글자 겹침이 적거나 색상 차이로 표시한다.
- [x] Archive orchard map은 재생 timeline에 맞춰 현재 rover 위치와 heading을 움직여 표시한다.
- [x] Docs에는 파란 원 후보 수와 지도 tree 수가 달라지는 원리 및 수정 후 표시 원리를 설명한다.
- [x] 논문용 과수 인식 알고리즘 보고서를 docs에 정리한다.
- [x] Notion `알고리즘 정리` 페이지에 보고서를 반영하거나, 인증 문제로 실패하면 실패 사유와 대체 산출물을 남긴다.
- [x] `20260708T053333Z_both`, `20260708T054036Z_both` 아카이브로 재분석/검증한다.
- [x] Python tests와 static contract를 통과한다. JS runtime은 로컬에 없어 Jetson/browser 배포 후 확인한다.
- [x] Jetson에 배포하고 내부 API로 확인한다.

## 작업 로그

- [x] 공통 camera analysis module 초안 추가: `dashboard_server/camera_analysis.py`
- [x] Camera analysis를 live camera bridge, data recorder, archive segment API에 연결하고 검증
- [x] Frontend camera overlay canvas와 mode control 구현
- [x] Frontend perception별 LiDAR/Camera overlay 표시 규칙 구현
- [x] Archive final fused tree 역투영 구현 후, 2026-07-09에 frame-anchored fusion으로 보정
- [x] Orchard map current rover marker 구현
- [x] Docs/보고서 업데이트
- [x] Notion 업데이트: 최초 시도는 OAuth authorization required로 실패했으나, 2026-07-08 재인증 후 `39726f49473a8088bb40d99517eb6eae` 페이지에 보고서 반영 완료.
- [x] 테스트 및 Jetson 배포

## Jetson 확인 결과

- [x] `http://127.0.0.1:8080/api/health` OK
- [x] `20260708T053333Z_both` segment camera overlay: 4 frames, 23 detections
- [x] `20260708T054036Z_both` segment camera overlay: 4 frames, 14 detections
- [x] `20260708T053333Z_both/orchard-map`: 18 final trees
- [x] `20260708T054036Z_both/orchard-map`: 38 final trees
- [x] Dashboard image includes OpenCV headless so old archive camera overlays can be backfilled.

## 2026-07-08 추가 디버깅 TODO

- [x] Archive LiDAR raw point와 Fusion/2D overlay가 같은 좌표 보정(transform)을 공유하는지 확인한다.
- [x] Archive LiDAR overlay 좌우반전/회전 불일치를 실제 `20260708T053333Z_both`, `20260708T054036Z_both` frame으로 재현하고 수정한다.
- [x] Orchard map canvas에서 클릭/드래그로 playback scrubber를 움직일 수 있게 한다.
- [x] Rover Control camera stream 영역이 panel 밖으로 넘치지 않게 max/min size와 object-fit/layout을 수정한다.
- [x] Camera tree extraction을 실제 archive frame으로 디버깅해 trunk/canopy false positive를 줄인다.
- [x] Camera overlay는 frame 전체 box 또는 바닥/그림자 box를 줄이고 실제 trunk/near-tree 위주로 표시한다.
- [x] Notion page `39726f49473a8088bb40d99517eb6eae`에 알고리즘 보고서를 다시 정리한다.
- [x] 로컬 테스트와 Jetson 내부 API/서비스 확인을 다시 수행한다.

## 2026-07-08 추가 디버깅 결과

- [x] Camera algorithm을 `opencv_bark_mask_trunk_line_orchard_tree_boxes_v2`로 교체했다.
- [x] `20260708T053333Z_both` first 2s camera frames 4개에서 11 detections, max bbox area 9.1% 확인.
- [x] `20260708T054036Z_both` first 2s camera frames 4개에서 29 detections, max bbox area 10.4% 확인.
- [x] Local orchard map v4 재계산: 1번 archive 18 trees, 2번 archive 38 trees 확인.
- [x] Archive playback segment를 12s로 늘리고, 다음 2개 segment prefetch와 camera analysis LRU cache를 추가했다.
- [x] Notion `알고리즘 정리` 페이지에 2026-07-08 디버깅 보고서를 추가하고 fetch로 반영을 확인했다.
- [x] Local tests: `python3 -m unittest tests.test_lidar_analysis tests.test_api`, `python3 -m py_compile ...`, `git diff --check` 통과.
- [x] Jetson deploy: `dashboard`, `ros2-camera-bridge`, `ros2-data-recorder` rebuild/restart 후 dashboard healthy 확인.
- [x] Jetson API: `20260708T053333Z_both` v4 map 18 trees, `20260708T054036Z_both` v4 map 38 trees, cached map 재조회 0.05s 내외 확인.

## 2026-07-09 추가 디버깅 TODO

- [x] 과수 위치 지도 안에도 archive playback scrubber를 추가하고 상단 scrubber와 동기화한다.
- [x] Archive Fusion Trees LiDAR overlay가 final map projection만 따라가며 raw point와 어긋나는 문제를 frame-anchored fusion으로 수정한다.
- [x] Current LiDAR frame orchard 후보의 overlay center와 nearest raw point 거리 검증: median 1-4cm 수준 확인.
- [x] Camera overlay를 bbox 표시에서 contour/line outline 표시로 변경한다.
- [x] Camera overlay temporal stabilizer를 live camera bridge, archive segment backfill, data recorder에 적용한다.
- [x] Camera algorithm을 `opencv_bark_mask_trunk_contour_stabilized_v3`로 올리고 기존 v2 overlay는 backfill 시 재분석되게 한다.
- [x] v3 기준 first 2s camera frames 4개 재검증: 1번 archive 5 detections/max 6.2%, 2번 archive 18 detections/max 8.1%, bbox render 0개.
- [x] Local tests를 다시 수행한다.
- [x] Jetson 배포/API 확인을 다시 수행한다. 2026-07-09 `10.64.151.247`로 배포 완료.

## 2026-07-09 연구 방향 수정 TODO

- [x] `/api/recordings/{id}/orchard-map`이 `maps.lidar`, `maps.camera`, `maps.fusion` 세 layer를 반환하도록 변경한다.
- [x] LiDAR-only 지도는 LiDAR trunk-like candidate만 누적하도록 분리한다.
- [x] Camera-only 지도는 camera trunk contour/line detection을 단안 기하로 ground plane에 근사 투영하도록 구현한다.
- [x] Weighted Fusion 지도는 LiDAR tree와 camera tree를 world frame에서 matching하고 confidence 기반 평균가중치로 병합한다.
- [x] 각 layer에 행간 중심선 estimator를 적용한다.
- [x] Fusion 중심선은 LiDAR 중심선과 camera 중심선을 직접 평균내지 않고, 평균가중치로 병합된 tree map에서 다시 계산한다.
- [x] Archive UI에서 LiDAR-only, Camera-only, Weighted Fusion 지도 3개를 동시에 표시한다.
- [x] 세 지도는 같은 재생바, 줌, 드래그 view를 공유하고 timeline에 맞춰 rover marker를 움직인다.
- [x] `20260708T053333Z_both`, `20260708T054036Z_both` 로컬 재처리로 layer별 tree count/centerline point 수를 확인한다.
- [x] Docs에는 대시보드 UI 설명보다 지도 생성/weighted fusion/centerline 수식 중심으로 정리한다.
- [x] Notion `39726f49473a8088bb40d99517eb6eae`에 기술 보고서 중심으로 다시 정리한다.
- [x] Jetson에 배포하고 v5 orchard-map API와 browser 지도 렌더링을 확인한다. API는 `orchard_three_layer_weighted_centerline_v5`와 `maps.lidar/camera/fusion` 확인, browser HTML/static에는 3개 지도 canvas 반영 확인.

## 2026-07-09 Jetson 배포 확인 결과

- [x] 대상: `unist@10.64.151.247`, workspace `/home/unist/workspace/unist_orachard_dashboard`
- [x] `dashboard`, `ros2-camera-bridge`, `ros2-data-recorder` docker-compose rebuild/restart 완료.
- [x] `http://10.64.151.247:8080/api/health` OK.
- [x] `/api/connection`: LiDAR bridge alive/scan live, camera bridge alive/frame live, data recorder idle 확인.
- [x] `20260708T053333Z_both/orchard-map`: v5, fusion 35 trees, LiDAR/Camera/Fusion centerline OK.
- [x] `20260708T054036Z_both/orchard-map`: v5, fusion 38 trees, LiDAR/Camera/Fusion centerline OK.
- [x] Archive segment camera overlay: `opencv_bark_mask_trunk_contour_stabilized_v3`, contour/line shape 확인.
- [x] Browser static smoke: `roverOrchardMapLidarCanvas`, `roverOrchardMapCameraCanvas`, `roverOrchardMapFusionCanvas`, `Fusion PNG` markup 확인.

## 2026-07-09 v7 오탐 억제 TODO

- [x] GitHub Actions CI/CD workflow 존재 여부와 trigger 조건 확인: smoke는 push/PR, deploy는 main push/workflow_dispatch에서만 실행된다.
- [x] 현재 Jetson 반영은 uncommitted code를 SSH로 직접 배포한 것이므로 GitHub Actions deploy를 타지 않았음을 확인했다.
- [x] LiDAR algorithm을 `polar_gap_cluster_line_fit_fragment_merge_predictive_tracks_v5`로 올리고 wall proximity suppression을 추가했다.
- [x] 벽 바로 옆 compact fragment가 orchard tree로 남지 않는 regression test를 추가했다.
- [x] Camera algorithm을 `opencv_orchard_scene_trunk_semantic_gate_v4`로 올리고 orchard scene gate, canopy support gate, line-only false positive gate를 추가했다.
- [x] `20260708T053333Z_both` 전체 camera frames 재분석: 93 frames, 55 detections, max 4/frame.
- [x] `20260708T054036Z_both` 전체 camera frames 재분석: 125 frames, 137 detections, max 5/frame.
- [x] `20260708T053333Z_both` LiDAR wall-suppressed candidates 84개, v7 map LiDAR 5 / Camera 19 / Fusion 5 trees 확인.
- [x] `20260708T054036Z_both` LiDAR wall-suppressed candidates 106개, v7 map LiDAR 7 / Camera 42 / Fusion 12 trees 확인.
- [x] v7에서 1번 archive fusion centerline이 0인 이유를 docs에 기록했다: 보수적 wall suppression 후 양쪽 row 조건이 부족하다.
- [x] SegFormer/ADE20K 기반 semantic segmentation을 바로 Docker runtime에 넣지 않고, 별도 inference container 또는 TensorRT fine-tuning 단계로 분리하는 연구 계획을 docs에 기록했다.
- [ ] Jetson `10.64.151.247` v7 배포/API 확인: Docker build 중 SSH 연결이 끊긴 뒤 SSH/HTTP가 timeout이라 보드 복구 후 재시도해야 한다.
- [ ] SegFormer-B0 ADE20K 또는 orchard-specific fine-tuned segmentation worker를 Jetson GPU runtime에서 별도 container로 검증한다.
- [ ] 수동 annotation GT를 만들어 v7 LiDAR wall suppression과 camera v4 gate의 precision/recall을 계산한다.
