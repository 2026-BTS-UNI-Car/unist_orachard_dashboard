# USB 카메라 연결과 ROS2 표시

이 문서는 HD USB drive-free UVC 카메라를 Jetson Orin Nano Super에서 ROS2 카메라 소스로 사용하고, Dashboard Connection 탭에서 실시간 화면으로 확인하는 기준 절차다.

## 운영 원칙

- 카메라 장치는 `ros2-camera-driver` 컨테이너가 단독으로 연다.
- Dashboard server는 `/dev/video*`를 직접 열지 않는다.
- Dashboard는 `ros2-camera-bridge`가 ROS2 `/camera/image_jpeg`를 구독해 재인코딩 없이 binary WebSocket으로 전달한 프레임만 표시한다.
- `/camera/image_raw`는 source-native AI와 archive preview용으로 5 Hz latest-only 발행한다. 실시간 표시는 raw DDS 대역폭과 분리한다.
- Connection 탭의 해상도/FPS 선택은 `runtime/camera_settings.json`에 저장되고, `ros2-camera-driver`가 실제 UVC 캡처 모드로 적용한다.

## 제품 기준 사양

| 항목 | 기준 |
|---|---|
| 인터페이스 | USB 2.0, UVC compliant |
| 최대 출력 | 1920x1080, 30 fps |
| 기본 캡처 | 1280x720, 60 fps 또는 1920x1080, 30 fps |
| 센서 | CMOS |
| 포커스 | 고정 포커스 |
| Linux 장치 | `/dev/video0`, `/dev/video1` 등 |
| 활용 topic | `/camera/image_jpeg`, `/camera/image_raw`, `/camera/camera_info` |

## Jetson 인식 확인

```bash
lsusb
ls -l /dev/video*
ls -l /dev/v4l/by-id 2>/dev/null || true
```

정상이라면 `Microdia USB 2.0 Camera`, `USB Video Class`, `UVC Camera` 계열 이름과 `/dev/video*` 장치가 보인다.

## Docker 구성

Compose는 두 카메라 서비스를 실행한다.

```text
ros2-camera-driver
  /dev/video* -> ROS2 /camera/image_jpeg, /camera/preview/camera_info (720p live preview)
              -> ROS2 /camera/image_raw (source-native AI/archive, 5 Hz)
              -> ROS2 /camera/camera_info

ros2-camera-bridge
  ROS2 /camera/image_jpeg -> binary-v1/latest-ack WebSocket
  ROS2 /camera/image_raw  -> source-native AI worker
  ws://127.0.0.1:8770/ws/camera -> Dashboard proxy
```

Dashboard는 `/ws/camera` 요청을 local camera bridge로 프록시한다. 브라우저가 `/connection` 페이지를 벗어나면 카메라 WebSocket은 자동으로 닫힌다.

## 해상도와 FPS

`ros2-camera-driver`는 카메라가 열린 뒤 아래 명령과 같은 방식으로 실제 UVC 모드를 읽고 `runtime/camera_driver_status.json`에 기록한다.

```bash
v4l2-ctl --list-formats-ext -d /dev/video0
```

Dashboard Connection 탭의 해상도와 FPS 선택지는 이 상태 파일의 `supported_view_modes` 값을 우선 사용한다. 카메라가 아직 `/dev/video*`까지 올라오지 않은 경우에는 Windows에서 확인한 제품 기준 기본값을 임시로 보여주고, 실제 모드 확인 대기 상태로 남는다.

현재 fallback catalog는 다음 기준이다.

| 선택지 | 실제 해상도 | FPS |
|---|---:|---:|
| 1080p 16:9 | 1920x1080 | 30 |
| 720p 16:9 | 1280x720 | 60 |
| 768p 4:3 | 1024x768 | 30 |
| 600p 4:3 | 800x600 | 60 |
| 480p 4:3 | 640x480 | 30 |
| 240p 4:3 | 320x240 | 30 |
| 1024p 5:4 | 1280x1024 | 30 |

선택을 바꾸면 Dashboard가 `/api/camera/settings`로 `runtime/camera_settings.json`을 갱신한다. `ros2-camera-driver`는 이 파일 변경을 감지해 카메라를 닫고 새 width/height/fps/fourcc로 다시 연다.

WebSocket bridge는 표시용 압축 프레임과 캡처 상태를 분리한다. 기본 `CAMERA_COMPRESSED_PREVIEW_WIDTH=1280`이므로 1920x1080 캡처에서도 실시간 프레임은 1280x720이다(아래 MJPEG 원본 경로의 `auto` 축소 디코드도 이 요청 폭 아래로 내리지 않는다). `compressed_preview_width`는 요청 폭이고 실제 발행 크기는 status의 `preview.effective_width/effective_height`에서 확인한다. 실제 UVC 캡처 크기는 `runtime/camera_driver_status.json`의 `actual`에서 확인한다. AI는 축소 프리뷰가 아닌 `/camera/image_raw`의 선택된 캡처 해상도를 사용한다.

브라우저/WebSocket output은 표시용 프리뷰다. 기본 AI 추론은 `ORCHARD_VISION_INFERENCE_WIDTH=0`으로 source/native frame을 사용하고, recorder는 별도의 `RECORDER_CAMERA_PREVIEW_WIDTH=640`으로 저장 JPEG만 축소한다. 따라서 화면이나 archive 용량을 줄이기 위해 프리뷰 폭을 바꿔도 AI 입력 해상도는 바뀌지 않는다. 추론 입력은 camera bridge가 단독 소유하므로 폭을 제한하려면 `ORCHARD_VISION_INFERENCE_WIDTH`만 명시적으로 설정한다. recorder는 공유된 bridge 결과를 sensor stamp로 매칭하며 독립 추론 설정을 갖지 않는다.

## MJPEG 원본 녹화 경로 (2026-10-13)

`CAMERA_MJPEG_PASSTHROUGH=1`(기본, `--no-mjpeg-passthrough`로 끔)이고 fourcc가 `MJPG`이면 드라이버는 `CAP_PROP_CONVERT_RGB=0`으로 카메라가 보낸 원본 JPEG를 받는다. 캡처 thread는 디코드·회전·재인코딩을 하지 않고 마커 헤더만 검사한 뒤 원본 바이트를 `/camera/recording/jpeg`에 발행한다. 검사는 SOI, 지원되는 SOF(0/1/2/9/10) 1개, 스캔 성분이 쓰는 DQT, 버퍼 안에 끝나는 SOS 헤더, SOS 이전의 SOI/EOI/예약 마커 거부, 마지막 EOI다(Huffman 표가 없는 Motion-JPEG는 허용). 마지막 EOI 뒤의 `0x00`/`0xFF` 채움은 EOI가 SOS 헤더 뒤에 있을 때만 잘라내고(`stage_counters.jpeg_fill_trimmed`), 잘린 버퍼나 다른 꼬리 바이트는 그대로 거부한다. open 때 첫 유효 프레임 하나만 캡처 thread에서 전체 디코드해 SOF 크기를 확인한다(요청 크기와 다른 SOF는 디코드하지 않고 거부).

### 녹화 방향 계약: 세션당 하나

- passthrough 세션(`CAMERA_MJPEG_PASSTHROUGH=1`)의 녹화 토픽은 경로와 무관하게 **센서 방향 픽셀 + 선언된 회전**이다. 원본: `jpeg; original_camera_mjpeg; rotation=180; width=1920; height=1080`. 디코드 fallback(같은 세션): 회전하지 않은 프레임을 `NativeCameraEncoder` q90으로 인코딩하고 `jpeg; reencoded_bgr_q90; rotation=180; width=1920; height=1080`. 프리뷰/raw만 회전한다. 따라서 녹화 중 passthrough↔fallback 전환은 payload 출처만 바꾸고 방향은 바꾸지 않는다. fourcc를 `MJPG` 이외로 바꾼 설정도 같은 계약(fallback, `fourcc_*_not_mjpg`)을 쓴다.
- `CAMERA_MJPEG_PASSTHROUGH=0`이면 기존 legacy 동작 그대로다: 회전된 픽셀을 q90 재인코딩, format `bgr8; jpeg compressed bgr8; width=..; height=..`(회전 표시 없음), header stamp는 발행 시각.
- `export_recording_camera.py`는 format의 `rotation=0|180`을 manifest `rotation_deg`로 옮기고 `build_adaptive_recording.py`가 180이면 `hflip,vflip`을 적용한다. 같은 회전·크기 안의 payload 전환은 하나의 녹화로 내보내고 manifest에 `camera_payload`(`mixed` 가능), `camera_payload_frames`(종류별 프레임 수), `camera_payload_segments`를 남긴다. `original_camera_codec_preserved`는 모든 프레임이 카메라 원본 JPEG일 때만 true다. 회전 표시가 없는 이전 녹화는 rotation 0(이미 회전된 픽셀)이고, 녹화 안에서 회전이 실제로 바뀌면(예: 녹화 중 카메라 드라이버 이미지 설치/롤백으로 legacy↔신규가 섞임) `camera_mode_changed_inside_recording`으로 거부한다. **카메라 드라이버 이미지 설치·롤백은 녹화가 idle일 때 한다.**

### fallback 판단

fallback은 "백엔드가 JPEG를 그대로 넘기지 못한다"는 증거만 센다: open 때 버퍼가 uint8 1행이 아님(예: YUYV `HxWx2`, RGB24 원시 `HxWx3`), open의 3회 read 모두 SOI 없음, 실행 중 재open을 일으킨 5회 실패 중 과반이 잘못된 passthrough 버퍼, SOI는 있지만 유효 프레임이 없는 open이 5회 연속(1건). read 실패와 잘린/채움 JPEG 자체는 세지 않는다. 증거 3건이면 세션 fallback이 걸리고(`mjpeg_passthrough.fallback`), 유효 프레임 60개 연속이면 증거가 0으로 돌아간다. fallback은 300초 뒤의 다음 재open에서 passthrough를 다시 시도하고(1건만 더 나오면 즉시 다시 fallback), 카메라 설정 파일이 다시 쓰이면(Dashboard에서 같은 설정을 다시 저장해도) 즉시 해제되며 fallback 캡처 중이면 바로 재open한다. open 때 OpenCV가 MJPG 대신 다른 palette를 고르면(`backend_fourcc_*_not_mjpg`) 또는 `CONVERT_RGB=0`을 거부하면(`backend_rejected_convert_rgb_0`) 그 open만 디코드 캡처로 연다. fallback 디코드 캡처에서 BGR(`HxWx3` uint8)이 아닌 프레임은 read 실패로 센다.

### 캡처 페이싱과 header stamp

passthrough에서는 cadence slot이 read를 막지 않고 blocking V4L2 read가 획득 속도를 정한다. 꺼낸 버퍼의 driver timestamp(`CAP_PROP_POS_MSEC`, uvcvideo 기본 `CLOCK_MONOTONIC`)가 1.5 프레임 주기보다 오래됐으면 큐에 쌓였던 것이므로 같은 tick에서 최대 4회까지 더 읽어 backlog를 비운다. 프레임 복제나 생성은 없다. header stamp는 그 버퍼 timestamp를 `ROS time - time.monotonic()` offset(매 프레임 측정)으로 ROS 시각에 옮긴 값이고, 값이 없거나 나이가 -5 ms~2 s 밖이거나 증가하지 않으면 read 완료 시각을 쓴다. 출처는 status `capture_timestamp`(`source`, `sources` 누적, `buffer_age_ms`, `untrusted_reason`)와 `capture_pacing`에 있다. fallback 디코드 캡처는 기존 cadence slot을 유지하되 같은 timestamp 규칙을 쓴다. **Jetson 실기에서 `capture_timestamp.source=v4l2_buffer_monotonic`과 `buffer_age_ms`가 수십 ms인지 먼저 확인한다.**

export/build의 PTS는 header stamp가 녹화 전체에서 1 ms 이상 간격으로 증가하고 bag 수신 시각과 1 s 이내로 일치하면 header stamp를 쓰고(`frame_timing=recorded_camera_header_stamp_VFR`), 아니면 bag 수신 시각을 쓴다. 수신 시각 두 개가 같은 ms에 들어오면 녹화 전체를 실패시키지 않고 뒤 프레임을 다음 ms로 옮기며 `pts_adjusted_frames`/`pts_max_adjustment_s`(export manifest `presentation_timing`, `adaptive_video.json`의 `vfr_input`)에 남긴다. 타임라인 정렬(`timeline_offset_s`)은 계속 bag 수신 시각을 쓴다.

### 프리뷰 축소 디코드

프리뷰(`/camera/image_jpeg`)와 raw(`/camera/image_raw`)는 latest-only worker에서 원본 JPEG를 디코드한다. `CAMERA_PREVIEW_DECODE_REDUCTION=auto|1|2|4`의 기본 auto는 디코드 폭이 요청 프리뷰 폭(`CAMERA_COMPRESSED_PREVIEW_WIDTH`) 이상인 가장 큰 libjpeg 축소율을 고른다: 1920 원본 + 1280 요청이면 1(전체 디코드 후 1280x720으로 resize), 3840 + 1280이면 2, 1920 + 960/640이면 2. 전체 디코드+resize는 1/2 축소 디코드보다 프레임당 CPU가 약 2배 든다(x86 OpenCV 4.13, 343 KB 1080p JPEG: 17.6 ms 대 9.0 ms; 회전·인코딩도 큰 이미지 기준이며 Jetson ARM 수치는 실측 필요). 따라서 CPU가 부족하면 `CAMERA_PREVIEW_DECODE_REDUCTION=2`(960x540) 또는 4를 명시하거나 프리뷰 폭을 낮춘다. 이 값은 compose 환경 변수로 전달돼야 한다. worker가 늦으면 프리뷰만 덮어쓰기(`preview_overwritten`)되고 녹화 발행에는 영향이 없다. raw는 `CAMERA_RAW_PUBLISH_FPS` 주기에만 전체 디코드·회전하고, 축소율 1이면 그 디코드를 재사용한다.

### 상태 필드

`camera_driver_status.json`의 `mode`(닫히면 null), `mjpeg_passthrough.active`(열린 캡처가 원본 JPEG를 녹화할 때만 true), `recording_payload`(`original_camera_mjpeg`/`reencoded_bgr_q90`/`legacy_reencoded_bgr_jpeg`/null), `recording_orientation`, `recording_rotation_deg`, `fallback`, `fallback_evidence_streak`/`fallback_evidence_reasons`, `valid_run`, `stage_counters`(device_reads, passthrough_frames, invalid_frames(+`reasons`), recording_published(+bytes), preview_decoded/published/overwritten, raw_published, jpeg_fill_trimmed의 누적값과 약 5초 rate)로 단계별 처리량을 확인한다. 실제 카메라의 장시간 기록 FPS는 별도 현장 측정이 필요하다.

### Fast DDS 전송 한계(기존 동작, 변경 없음)

Iron 기본 Fast DDS(2.10)의 SHM segment는 participant당 512 KiB이고 카메라 publisher는 BEST_EFFORT/KEEP_LAST(1)이다. 한 샘플이 segment보다 크면 recorder가 읽기 전에 조각이 재사용되어 샘플 전체가 사라지고, driver의 `recording_published`는 발행 호출만 센다. 6.2 MB `/camera/image_raw`는 이미 이 때문에 bag에 일부만 남는다(10/10 기록에서 `camera_info` 대비 18-29%). passthrough 원본 프레임은 Jetson probe에서 108-141 KB라 이 영역 밖이지만, 약 500 KB 이상 프레임은 손실될 수 있다. 전송 설정은 이번 변경에서 바꾸지 않았다. 현장 확인 때는 bag의 `/camera/recording/jpeg` 수와 같은 구간의 `recording_published.total`을 비교한다.

camera AI worker는 5 Hz의 latest-frame/coalescing 방식이다. worker가 200 ms보다 늦어져도 과거 frame queue를 순서대로 처리하지 않고 가장 최근 frame으로 교체하며, 누락 수와 실제 추론 입력 크기는 `runtime/camera_bridge_status.json`의 `vision_publisher`에서 확인한다. 실시간 표시 스트림은 이 AI 주기와 독립적으로 약 15 Hz까지 동작한다.

## 실시간 전송 프로토콜

2026-08-27부터 실시간 카메라는 다음 계약을 사용한다.

- ROS2 `sensor_msgs/CompressedImage` JPEG를 브리지에서 다시 인코딩하지 않는다. `/camera/preview/camera_info`는 720p 프리뷰 크기와 짝을 이루며, source-native `/camera/camera_info`와 구분한다.
- WebSocket 한 메시지는 `UBCF` magic, protocol version, compact JSON metadata, JPEG bytes 순서인 `binary-v1`이다.
- base64 JSON 대비 약 33%의 인코딩 팽창과 매 프레임 data URL 생성을 제거한다.
- `latest-ack`은 최대 두 프레임만 전송 중으로 허용한다. 브라우저는 이전 object URL을 즉시 폐기하고 최신 `<img>` 슬롯으로 교체하므로 오래된 프레임 backlog가 쌓이지 않는다.
- 상태 JSON은 초당 한 번만 보내며, JPEG WebSocket compression은 끈다.

Jetson 1920x1080 캡처, 1280x720 표시 프리뷰, quality 70에서 측정한 결과:

| 경로 | 표시 수신률 | 평균 JPEG packet | 대역폭 | p95 frame gap |
|---|---:|---:|---:|---:|
| Jetson 직접 LAN | 14.91 fps | 37.06 KiB | 4.50 Mbps | 100.6 ms |
| `bts.sarihub.com` Cloudflare | 12.56 fps | 37.24 KiB | 3.53 Mbps | 145.0 ms |

동일한 공용 경로에서 1080p JPEG + 1-credit decode ACK는 4.15 fps였으므로, 표시 프리뷰 분리와 `latest-ack`을 함께 적용해야 한다.

CameraInfo는 단순히 마지막 메시지를 재사용하지 않는다. `camera_info_image_header_integrity_v2` 계약이 image/CameraInfo의 `frame_id`, native width/height, sensor stamp gate, monotonic clock을 모두 검증하며, 하나라도 불일치하면 operator calibration flag가 켜져 있어도 `bearing_valid=false`로 닫힌다. status의 provenance reason과 regression count가 배포 smoke gate에도 포함된다.

ROS2 driver의 초기 기본값은 Compose 환경변수 `CAMERA_WIDTH`, `CAMERA_HEIGHT`, `CAMERA_FPS`, `CAMERA_FOURCC`로 정한다. 런타임 변경은 `CAMERA_SETTINGS_FILE`의 JSON이 우선한다.

## 자동 재인식

`camera_ros2_driver.py`는 `CAMERA_DEVICE=auto`일 때 `/dev/v4l/by-id/*Camera*`, `/dev/v4l/by-id/*camera*`, `/dev/v4l/by-id/*USB*`, `/dev/video*` 순서로 장치를 찾는다. 프레임 읽기가 연속으로 실패하면 카메라를 닫고 다시 스캔한다.

카메라를 뽑았다가 다시 꽂은 뒤에도 컨테이너가 살아 있으면 자동으로 다시 열린다. USB 장치는 보이지만 `/dev/video*`가 생기지 않는 경우에는 드라이버가 주기적으로 USB device reset을 시도하고, 몇 차례마다 USB authorized cycle로 재열거를 시도한다. 실패가 반복되면 복구 간격은 최대 120초까지 늘어난다. 실패 상태와 복구 횟수는 `camera_driver_status.json`에 남긴다.

현재 확인된 실패 예시는 `lsusb`에는 `0c45:6362 Microdia USB 2.0 Camera`가 보이지만 커널 로그에 `Failed to query UVC probe control` 및 `Failed to initialize the device`가 남고 `/dev/video*`가 생성되지 않는 상태다. 이 경우 Dashboard는 카메라를 직접 열지 않고 `USB 감지 · 비디오 노드 대기`로 표시한다.

```bash
docker compose logs -f ros2-camera-driver
docker compose restart ros2-camera-driver ros2-camera-bridge
dmesg -T | grep -Ei 'uvc|video|camera'
```

## ROS2 확인 명령

```bash
ros2 topic list | grep camera
ros2 topic hz /camera/image_jpeg
ros2 topic hz /camera/image_raw
ros2 topic echo /camera/camera_info --once
```

정상 기준:

- `/camera/image_jpeg`가 표시 속도로 publish되고 `/camera/image_raw`가 AI/기록 속도로 publish된다.
- `/camera/camera_info.header.frame_id`는 `camera_link_optical`이다.
- Dashboard Connection 탭의 Camera 상태가 `수신 중`으로 바뀐다.
- Camera readout의 `Display FPS`는 누적 프레임 수가 아니라 최근 WebSocket 표시 속도다.

## 주의사항

- 여러 프로세스가 동시에 `/dev/video*`를 열면 프레임 수신이 불안정해질 수 있다.
- VLC, fswebcam, OpenCV 단독 테스트는 ROS2 driver를 끈 상태에서만 수행한다.
- 장시간 1080p 30 fps 사용 시 USB 대역폭과 Jetson CPU 사용량을 확인한다.
