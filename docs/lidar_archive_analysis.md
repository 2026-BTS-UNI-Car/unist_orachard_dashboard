# LiDAR Archive 분석과 재생

Last updated: 2026-08-22

## Current v10 contract (normative)

- Orchard map algorithm: `orchard_metric_anchor_semantic_fusion_local_frenet_v10`.
- Cache identity is the algorithm plus `cache_revision=v10_rep103_robust_tracks_unique_semantics_row_model_native_input_r3` and timeline mtime. A missing or mismatched revision or source/native camera-input contract is stale even when the algorithm name is still v10; `--force` always recomputes.
- Camera association uses common ROS header stamps, preserves original archive order, and performs bounded one-to-one maximum-cardinality/minimum-cost matching with a 250 ms offline gate. Relative timeline time is an archive-wide compatibility fallback only. Header regressions/duplicates are explicit diagnostics and prevent navigation readiness.
- Fusion positions remain LiDAR metric anchors. Camera evidence changes semantic support only; calibrated and uncalibrated camera tracks are never averaged together, and uncalibrated monocular landmarks are not promoted to the metric fusion map.
- Global landmarks are deterministic robust static tracks with one observation per frame. Centerlines combine trajectory-segmented global landmark rows with a separate time-local bilateral LiDAR corridor. Both channels require longitudinally diverse bilateral evidence and a 3--7 m row gap; invalid evidence returns an empty fail-closed centerline.
- Only stabilized overlay detections are eligible for semantics. Low-trust stable pending detections require a temporal track identity and are capped to semantic-only support of an existing LiDAR anchor. Raw rejected detections are unused.
- `ok` means the map computation completed. `readiness.data_sufficiency_ok` and `readiness.navigation_ready` are separate and must not be inferred from `ok`.

The remaining July 10 material is a historical v8 operations snapshot. Any later references to a v8 cache key, 78-degree FOV, global start-to-end centerline axis, fixed landmark cap, or camera-weighted metric position are non-normative and must not be used for current deployment decisions.

## Legacy v8 snapshot (historical, non-normative)

## 1. Archive 구조

```text
runtime/recordings/<recording-id>/
  metadata.json
  timeline.json
  preview_scan.json
  analysis.json
  video.mp4               # video-only archive의 960px/30fps streaming proxy
  video_original.mov      # byte-identical 원본, video-only archive에만 존재
  rosbag2/                 # 기록 설정에 따라 존재
```

`timeline.json`은 web playback preview다. 원본 재처리와 논문 분석은 가능하면 `rosbag2`를 기준으로 한다.

```json
{
  "lidar": {"frames": [{"t_s": 0.038, "points": [], "overlay": {}}]},
  "camera": {"frames": [{"t_s": 0.041, "data": "<jpeg>", "overlay": {}}]},
  "sync": {"time_base": "seconds_since_recording_start"}
}
```

## 2. 최신 분석 revision

- LiDAR overlay: `polar_gap_cluster_wall_context_tree_ood_predictive_tracks_v6`
- Camera overlay: `local_orchard_vision_ai_worker_v4 / orchard_trunk_yolo11_trt_clip_ood_v1`
- Orchard map: `orchard_three_layer_weighted_centerline_v8`
- Odometry: `scan_context_distance_field_coarse_to_fine_v1`

오래된 revision은 재생 요청 때 구간별로 임시 재분석하지 않는다. Recording 전체 순서로 precompute해 tracking state가 segment 경계에서 끊기지 않게 한다.

## 3. Precompute 순서

```bash
docker exec unist-orachard-dashboard \
  python /app/scripts/precompute_archive_camera_overlays.py \
  --recording-id <id>

docker exec unist-orachard-dashboard \
  python /app/scripts/precompute_archive_lidar_overlays.py \
  --recording-id <id>

docker exec unist-orachard-dashboard \
  python /app/scripts/precompute_orchard_maps.py \
  --recording-id <id>
```

Camera 또는 LiDAR timeline이 바뀌면 map cache mtime도 stale이 되므로 map을 마지막에 생성한다.

## 4. Streaming playback

UI는 4초 segment만 요청한다.

```text
/api/recordings/<id>/timeline?start_s=0&duration_s=4
```

최적화:

- compact JSON
- `Accept-Encoding: gzip`
- 최신 camera/LiDAR overlay 재사용
- parsed timeline LRU cache 2개
- browser segment/object URL cache
- 현재 segment 재생 중 다음 두 segment prefetch
- seek 시 필요한 segment만 load

2026-07-10 Jetson warm API:

| Archive | Latency | Wire payload |
| --- | ---: | ---: |
| `20260708T053333Z_both` | 약 105 ms | 약 493 KiB |
| `20260708T054036Z_both` | 약 122 ms | 약 583 KiB |

Public Cloudflare archive 1 첫 segment는 약 0.90초였다. 4초 playback보다 충분히 빠르며 prefetch buffer를 유지한다.

### Video-only archive

`sample_IMG_4469_camera`, `sample_IMG_4471_camera`는 Jetson에서 다음 방식으로 가져왔다.

```bash
docker exec unist-orachard-vision-ai \
  python /app/scripts/import_video_archive.py \
  --input /app/runtime/imports/sample_video/IMG_4469.mov \
  --recording-id sample_IMG_4469_camera \
  --sample-hz 4
```

- 원본 MOV는 hard-link 또는 exact copy로 보존하고 SHA-256을 metadata에 기록한다.
- UI용 `video.mp4`는 긴 변 960 px, 30 fps, H.264 CRF 25, 최대 1.5 Mbps, fast-start proxy다.
- `/api/recordings/<id>/video`는 `HEAD`, single HTTP byte range와 `206 Content-Range`를 지원한다.
- 영상 픽셀은 MP4 Range stream으로 받고 timeline segment는 4 Hz AI contour만 받는다. Native archive segment에서는 중복 JPEG `data`를 제거한다.
- 원본 60 fps 영상과 TensorRT AI overlay는 같은 `t_s` scrubber를 공유한다.

Jetson 검증에서 4초 overlay payload는 약 31-32 KiB, warm 응답은 약 17 ms였다. 공개 browser startup은 133-134 ms, 3.2초 구간의 실재생 진행은 3.21초, 영상/재생바 오차는 12-14 ms, seek 오차는 0이었다.

## 5. 재생 동기화

Scrubber 시간 `t`에서 LiDAR와 camera 각각 가장 가까운 frame을 선택한다. `동시 재생`을 끄면 camera가 독립적으로 갱신될 수 있지만 기본은 같은 time base다.

상단 scrubber와 Orchard Map scrubber는 같은 state를 사용한다. 페이지를 나갔다 돌아와도 archive 자체는 immutable하고 segment cache는 다시 채운다.

Map canvas는 start-to-end world vector를 화면 위쪽 축으로 회전한다. 따라서 시작점은 하단, 종점은 상단에 놓이며 rover heading도 같은 회전 행렬을 사용한다. 두 운영 archive에서 시작점은 canvas 높이의 79.6%, 73.2%에 표시됐고 heading vector와 pointer의 각도 오차는 browser pixel 검증에서 사실상 0이었다.

## 6. Orchard map cache

```text
runtime/orchard_analysis/<recording-id>_orchard_map.json
```

Cache는 timeline보다 새롭고 map algorithm이 정확히 v8일 때만 사용한다. 그렇지 않으면 precompute 또는 첫 API 요청에서 다시 계산한다. Map 계산은 scan matching 때문에 약 45-57초가 걸리므로 CI/CD에서 미리 생성한다.

Map API:

```text
/api/recordings/<id>/orchard-map
```

응답에는 `maps.lidar`, `maps.camera`, `maps.fusion`, `path`, `frames[].scan_match`, `pose_confidence`, `turn_event`, `lidar_ood`가 포함된다.

## 7. Download

```text
/api/recordings/<id>/download
```

첫 요청은 archive를 gzip level 1로 stream하면서 cache file을 동시에 만든다. 다음 요청은 cache를 직접 전송한다. `HEAD`는 tar 생성 없이 header만 확인한다.

```bash
curl -I https://bts.sarihub.com/api/recordings/<id>/download
```

## 8. 검증

```bash
python3 scripts/validate_dashboard_ui.py \
  --base-url https://bts.sarihub.com \
  --recording-id 20260708T053333Z_both

python3 scripts/validate_video_archive_ui.py \
  --base-url https://bts.sarihub.com \
  --recording-id sample_IMG_4469_camera \
  --output-dir runtime/analysis/video_archive_IMG_4469
```

검사 항목:

- archive camera frame load
- camera AI error banner 부재
- LiDAR/camera overlay와 세 map canvas nonblank
- playback progress
- 두 scrubber sync
- map rover pose 변화
- desktop/mobile overflow
- browser console error

이 smoke test는 rover command를 보내지 않는다.

## 9. 좌표와 한계

Live와 archive는 `lidar_bridge_xy_v1`을 공유한다. Raw point와 overlay에 별도 transform을 적용하지 않는다. Legacy coordinate frame이 없는 archive만 compatibility conversion 대상이다.

현재 두 archive에는 wheel odometry, IMU, `/tf`가 없어 path는 scan-derived pseudo-odometry다. 다음 기록에서 이 topic들을 함께 저장해야 drift와 pose confidence를 정량 검증할 수 있다.
