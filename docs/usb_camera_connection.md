# USB 카메라 연결과 ROS2 표시

이 문서는 HD USB drive-free UVC 카메라를 Jetson Orin Nano Super에서 ROS2 카메라 소스로 사용하고, Dashboard Connection 탭에서 실시간 화면으로 확인하는 기준 절차다.

## 운영 원칙

- 카메라 장치는 `ros2-camera-driver` 컨테이너가 단독으로 연다.
- Dashboard server는 `/dev/video*`를 직접 열지 않는다.
- Dashboard는 `ros2-camera-bridge`가 ROS2 `/camera/image_raw`를 구독해 WebSocket으로 변환한 JPEG 프레임만 표시한다.
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
| 활용 topic | `/camera/image_raw`, `/camera/camera_info` |

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
  /dev/video* -> ROS2 /camera/image_raw, /camera/camera_info

ros2-camera-bridge
  ROS2 /camera/image_raw -> ws://127.0.0.1:8770/ws/camera
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

WebSocket bridge는 source와 output 해상도를 모두 보낸다. 정상 상태에서는 Dashboard caption의 `source` 값이 선택한 캡처 해상도와 일치해야 한다. 일치하지 않으면 UVC 장치가 요청 모드를 거부했거나 아직 재오픈 중인 상태다.

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
ros2 topic hz /camera/image_raw
ros2 topic echo /camera/camera_info --once
```

정상 기준:

- `/camera/image_raw`가 publish된다.
- `/camera/camera_info.header.frame_id`는 `camera_link_optical`이다.
- Dashboard Connection 탭의 Camera 상태가 `수신 중`으로 바뀐다.
- Camera readout의 `Display FPS`는 누적 프레임 수가 아니라 최근 WebSocket 표시 속도다.

## 주의사항

- 여러 프로세스가 동시에 `/dev/video*`를 열면 프레임 수신이 불안정해질 수 있다.
- VLC, fswebcam, OpenCV 단독 테스트는 ROS2 driver를 끈 상태에서만 수행한다.
- 장시간 1080p 30 fps 사용 시 USB 대역폭과 Jetson CPU 사용량을 확인한다.
