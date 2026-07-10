# LiDAR Archive 분석과 재생

Last updated: 2026-07-10

## 1. Archive 구조

```text
runtime/recordings/<recording-id>/
  metadata.json
  timeline.json
  preview_scan.json
  analysis.json
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

## 5. 재생 동기화

Scrubber 시간 `t`에서 LiDAR와 camera 각각 가장 가까운 frame을 선택한다. `동시 재생`을 끄면 camera가 독립적으로 갱신될 수 있지만 기본은 같은 time base다.

상단 scrubber와 Orchard Map scrubber는 같은 state를 사용한다. 페이지를 나갔다 돌아와도 archive 자체는 immutable하고 segment cache는 다시 채운다.

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
