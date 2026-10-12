# 1080p 원본 MJPEG 통과 녹화와 실제 fps (2026-10-13)

**범위**
- 실제 Jetson 카메라 드라이버(`unist-orachard-ros2-camera-driver`)와 녹화기 미디어 후처리(`unist-orachard-ros2-data-recorder`).
- 실제 모터 출력·조종 활성화는 하지 않았다.
- 시험 녹화는 rover bridge가 disarmed·정지 상태임을 확인한 뒤 했다.

## 1. 원인

10/10 현장 기록 6개의 토픽별 빈도:

| 토픽 | 빈도 | 의미 |
|---|---|---|
| `/camera/recording/jpeg` | 16.2–20.3Hz | 원본 녹화 |
| `/camera/image_jpeg` | 19.2–23.6Hz | preview, latest-only라 캡처 수 이하 |

- **캡처 자체가 30fps를 못 냈다.** capture thread가 프레임마다 전체 JPEG decode와 180° 회전을 했다. ARM 실측으로 decode·회전·q90 재인코딩에 약 46ms가 걸렸다.
- **녹화는 캡처보다도 적었다.** 녹화용 q90 재인코딩 스레드(큐 4)가 따라가지 못해 프레임을 버렸다.

## 2. 변경

커밋은 `86f7e6a`, `7e49a48`, `09ed393`이다.

**드라이버**
- `CAP_PROP_CONVERT_RGB=0`으로 카메라가 만든 원본 JPEG 바이트를 그대로 기록한다. 형식은 `jpeg; original_camera_mjpeg; rotation=180; width=1920; height=1080`이다.
- 회전은 픽셀 처리가 아니라 metadata로 전달하고, 영상 생성 단계에서 한 번 적용한다.
- preview와 raw만 별도 스레드에서 decode한다.
- capture는 blocking read가 속도를 정해 V4L2 버퍼에 밀린 프레임을 비운다.
- header stamp는 V4L2 버퍼 시각을 ROS 시간으로 옮긴 값이다.

**같은 녹화 안의 형식 일관성**
- passthrough를 요청한 세션에서 fallback이 일어나도, 녹화 프레임은 회전하지 않은 채 `reencoded_bgr_q90; rotation=180`으로 남긴다.
- 그래서 녹화 도중 모드가 바뀌어도 방향은 같고, 내보내기(export)는 payload 종류가 섞인 녹화를 하나로 처리한다.
- 방향이 실제로 바뀐 녹화는 여전히 거부한다.

**fallback 판정**
- backend가 JPEG를 통과시키지 못한다는 증거만 센다. 읽기 실패, 잘림, 채움 바이트는 증거로 치지 않는다.
- 3회 누적되면 fallback으로 바뀌고, 유효 프레임 60개가 이어지면 증거를 지운다.
- fallback은 300초 뒤 재시도하며, 설정 파일을 쓰면 즉시 해제된다.

**영상 생성(녹화기)**
- PTS는 header stamp를 쓴다. 전 구간에서 1ms 이상 증가하고 bag 수신 시각과 1초 이내일 때만 쓰고, 아니면 수신 시각을 쓴다.
- 같은 ms에 들어온 수신은 다음 ms로 옮기고 그 횟수를 manifest에 남긴다. 이 경우 때문에 녹화 전체 영상 생성이 실패하던 문제를 없앴다.
- 프레임을 추가하거나 버리지 않는다.

**JPEG 검사**
- SOS까지 marker를 확인한다.
- EOI 뒤의 0x00/0xFF 채움 바이트는 잘라내고 그 수를 센다. 실제 카메라는 대부분의 프레임에 이 채움을 붙인다.

## 3. 설치(Jetson)

| 서비스 | 이전 이미지 | 새 이미지 | 방법 | ARM 테스트 |
|---|---|---|---|---|
| 녹화기 | `bd48a6a1…` | `37411184…` | compose override 1개(기존 방식), 녹화 idle 확인, 백업·자동 롤백 | 13개 모듈 통과(skip 13) |
| 카메라 드라이버 | `c48fbd9e…` | `2493fbe1…` | 같은 방식 | 카메라 57개 통과 |

- 녹화기 1차 시도는 ARM 테스트 단계에서 중단했다(교체 전). 이 이미지에 없는 `add_low_bandwidth_variant.py`를 쓰는 테스트가 목록에 들어 있었기 때문이다.
- 카메라 드라이버 1차 시도도 같은 이유로 중단했다. 이번에는 브리지 이미지용 `test_camera_policy`가 목록에 있었다.
- 두 경우 모두 목록만 고친 뒤 재설치했다.

**롤백**
- `~/ubts-releases/recorder-media-pts-20261013/INSTALL_RESULT.json`
- `~/ubts-releases/camera-mjpeg-passthrough-20261013/INSTALL_RESULT.json`
- 각 파일의 `rollback_command`를 쓴다(override 없는 기존 compose). 기존 `/app/scripts` 백업도 같은 위치에 있다.
- 카메라 이미지 교체·롤백은 녹화 idle일 때만 한다. 기존 이미지와 새 이미지는 녹화 방향 규칙이 다르다.

## 4. 실제 확인

**설치 직후 상태**
- `mode=passthrough`, `convert_rgb_disabled=true`.
- 장치 읽기 29.8Hz, 무효 프레임 0.
- 타임스탬프 출처 `v4l2_buffer_monotonic`, 버퍼 나이 약 37ms.
- preview 1280×720, 22–23Hz. latest-only이며 초당 약 7개를 덮어쓴다.
- raw 4.8Hz.

**정지 시험 녹화** `20261012T220056Z_both`(40.7초)

| 항목 | 결과 |
|---|---|
| bag 정합성 | 끝까지 읽은 계수가 metadata와 일치 |
| 원본 JPEG | 1,096장, header 기준 **29.7fps** |
| 프레임 간격 | p50 32ms, p99 36ms, 50ms 초과 3회, 최대 68ms |
| 무결성 | 연속 동일 payload 0, SOI/EOI 위반 0, 표본 전부 1920×1080 |
| 영상 생성 | 완료(1,096 프레임) |
| 회전 | 원본을 180° 회전한 쪽과 상관 0.85(그대로 0.38 반대) → 정확히 한 번 적용 |

- 카메라 드라이버 CPU는 약 1.45–1.55코어로, 이전 약 1.7코어보다 조금 줄었다. 1280 preview를 위해 프레임마다 전체 decode를 하기 때문이다. `CAMERA_PREVIEW_DECODE_REDUCTION=2`로 preview를 960폭으로 낮추면 더 줄일 수 있다.

## 5. 남은 확인

- 현장 주행 중 fps. 밝기·장면에 따른 카메라 자체 전달률과 노출 때문에 fps가 낮아지는지 여부.
- 몇 분 길이 녹화의 영상 생성.
- `/camera/image_raw`(6.2MB)는 Fast DDS 512KB 공유메모리 한계 때문에 일부 손실된다. 이 동작은 원래부터 있었고 transport 설정은 바꾸지 않았다.
- 녹화 중 시스템 시계가 step되면(예: GNSS 시각 동기) header·수신 시각이 함께 이동해 영상에 공백이 생긴다. 시각 동기 서비스는 녹화·제어·매핑 중에는 step하지 않도록 조건을 걸어 두었다.
