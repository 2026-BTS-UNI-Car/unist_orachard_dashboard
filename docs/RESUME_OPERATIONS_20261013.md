# 재개 운영 작업 기록 (2026-10-13)

2026-10-12 밤–10-13 새벽 KST에 사용자 승인 범위로 Jetson에서 수행한 작업이다. 실제 모터 출력·펌웨어 변경·장착 보정·자율주행은 하지 않았다. 설치는 모두 “실행 중 이미지 위에 검토한 파일만 덮는 overlay 이미지”이며 이전 이미지·컨테이너와 되돌리는 방법을 보관했다.

## 1. LAN SSH 연결 불가 원인(읽기 전용 진단)

Jetson 방화벽 문제가 아니었다. PC는 `U+Net0588_5G`(게이트웨이 MAC `…:05:89`), Jetson은 `U+Net0584_5G`(게이트웨이 MAC `…:05:85`)로 **서로 다른 공유기**에 있고 둘 다 기본 대역 192.168.219.0/24를 쓴다. PC 쪽 192.168.219.104는 무작위 MAC 장치(`aa:8a:3c:…`)로 ping만 응답하고 TCP를 거부했다. Jetson ufw는 inactive, sshd는 0.0.0.0:22. 설정은 바꾸지 않았다. LAN으로 쓰려면 PC를 Jetson과 같은 SSID에 연결하고 호스트 키 지문 `SHA256:MBW0bMaJHakDA4yfvqJgrqUNa8hWuf+RmkJpwFUh/EI`를 확인한다. Cloudflare 경로는 약 0.14–0.3MB/s이고 Jetson 쪽 QUIC 재연결로 자주 끊긴다.

## 2. 완료된 영상 후처리 중간 파일 정리

- 대상: 작업이 `complete`인 7개 기록(10/10 진단 1개 + 현장 6개).
- 삭제 전 검증(일회성 컨테이너, 녹화 폴더 읽기 전용): 큐·작업 완료 상태, 설치된 HLS 자산 해시, 화질별 init+첫/마지막 세그먼트·240p 전체·`native.mp4` 앞뒤 실제 디코딩, 원본 bag/metadata가 인코딩 당시와 동일.
- 삭제한 것(모두 내용으로 증명): `source.zip`(설치 manifest의 `camera_export_sha256`과 일치, 프레임 수=MCAP 원본 카메라 메시지 수, export의 원본 해시=현재 원본), `package/`의 HLS 복제본(설치본과 바이트 동일), `package/frames/*.jpg`(각 파일이 원본 JPEG 프레임 해시와 동일), `native_frames.mkv`(패킷=원본 프레임+마지막 프레임 1회 반복).
- 남긴 것: `encoder.log`(용도 미확인), `progress.json`, `source_sha256.json`, 작업 로그, 035952의 중단 작업 폴더(1.8GB, 완료 작업이 아니라 범위 밖), 원본 MCAP·센서/PWM·지도·설치된 영상.
- 결과: 95.36GB 삭제, 건너뜀 0, 여유 37GB → 132.5GB(사용률 93% → 73%). 각 작업 폴더에 근거 `cleanup.json`.
- 근거 보관: Jetson `~/workspace/ubts-dashboard-backups/20261013-media-cleanup/`(VERIFY v1–v4, DELETION_RESULT), PC `D:/Develop/UBTS/resume-audit-20261013/media-cleanup/`.
- 검증 방법 이력: v1의 HLS `-ss` seek 디코딩은 ffmpeg 4.4 fMP4에서 거짓 오류(partial file/NAL)를 냈고, v3는 ffprobe 출력 절단으로 mkv를 잘못 거부했다. 둘 다 고친 v4로만 삭제했다.
- 코드(`scripts/recording_postprocess.py`): 설치·검증이 끝난 작업은 위와 같은 내용 증명이 된 파일만 지우고 `cleanup.json`을 남긴다. 실패·중단 작업은 그대로 두며, 이후 같은 기록의 작업이 성공하면 그 이전 실패·중단 작업 중 **내용 증명이 되는 것만**(완전한 export, 원본 프레임 복사본) 지운다. 부분 export·부분 인코딩·미상 파일은 남기고 크기를 기록한다. 실행 중인 작업은 건드리지 않는다.

## 3. native 관찰 서비스 재시작 반복

- 원인: 10/10 11:45의 `observe.sock`이 영구 host 디렉터리에 남아 bind `EADDRINUSE`, 재시작 1,785회.
- 운영 조치: 이 경로를 쓰는 컨테이너(서비스 자신, 대시보드는 읽기 전용 클라이언트)와 host listener 부재를 확인하고, 같은 inode·socket·`connect()` 거부를 확인한 뒤 그 파일 하나만 삭제 → 즉시 idle 복구.
- 코드(`deploy/native_sensor_stream/native_observation_service.py`): 형제 lock 파일 `observe.sock.lock`의 exclusive flock을 서비스 수명 동안 보유. 기존 경로는 lock 보유 + 같은 socket inode + 연결 거부일 때만 제거한다. 살아 있는 listener, socket이 아닌 경로, 판정 불가, 다른 owner의 lock은 건드리지 않는다. 정상 종료 시 자기 inode만 지우고 lock을 푼다.
- 설치: 이미지 `sha256:b54eeee4…`(부모 `ed7e0e3c…`, 서비스 파일 `9bc9efa5…`), 컨테이너 `fedda161…`. 기존 Config/HostConfig를 Engine API로 그대로 복사(이미지만 변경). 기존 컨테이너는 `ubts-native-sensor-observation-retained-socket-recovery-20261013T012410`(restart=no)로 보존.
- 실기 검증: ARM 테스트 7개 통과, 정상 재시작(복구 불필요) → idle, SIGKILL 후 남은 socket을 lock 증명으로 복구 → idle, 최종 `unless-stopped`·idle·`owned=false`·`runtime_authority=false`. 관찰 Start는 하지 않았다.

## 4. 녹화 내구성 설치와 녹화기 수정

- 설치 이미지 `sha256:bd48a6a1…`(부모 `54a5b97d…`), 컨테이너 `f57b3460…`. 덮은 파일: `data_recorder_service.py`, `mapping_sensor_writer.py`, `recording_durability.py`, `inspect_interrupted_recording.py`, `recording_postprocess.py`, `gnss_time_sync.py`, `recording_clock_audit.py`. 기존 compose 파일 14개에 override 1개만 추가(이전 배포 절차와 동일).
- 롤백: `~/ubts-releases/recorder-durability-cleanup-clock-20261013/INSTALL_RESULT.json`의 `rollback_command`(override 없는 기존 compose 목록으로 recreate), 기존 `/app/scripts` 백업 `backup-20261013T012058/`.
- ARM 테스트 110개 통과(skip 15). 웹 `TelemetryProvider` 테스트는 녹화기 이미지 안의 오래된 웹 모듈 대상이라 제외했다. 1·2차 시도는 테스트 경로 문제로 교체 전에 안전 중단됐다.
- 추가 수정: (a) 정지 시 mapping sidecar를 먼저 닫아 metadata를 한 번만 기록(기존 HEAD에서 실패하던 마감 테스트 통과), (b) `/gps/data` 구독 depth 1→10(같은 epoch의 NAV-PVT/HPPOSLLH 중 하나가 덮어써져 PVT·UTC 근거가 빠지던 문제), (c) `gps.jsonl`에서 NAV-PVT에 별도 0.2초 레인.
- 테스트 기록 `20261012T163005Z_both`(정지 상태, 139초): 정상 Stop·archived, bag EOF 계수 일치, 원본 JPEG 표본 197장 모두 1920×1080(28.6fps), LiDAR 9.8Hz·IMU 19.9Hz, `motor.jsonl` 270행(핀 8개, disarmed), `gps.jsonl` PVT 137/HPPOSLLH 137, 사이드카 fsync 274회 오류 없음, mapping sidecar 완료. 영상 후처리 결과는 아래 6절.
- 범위 밖: 갑작스러운 전원 차단·저장장치 고장 보장, rosbag 자체 readproof, 자동 복구(정책상 모두 false).

## 5. 소프트웨어 시계 대책

- 원인: 현장 무네트워크 부팅에서 RTC가 시간을 유지하지 못해 timesyncd가 마지막 저장 시각을 복원 → 10/10 녹화 시 호스트가 약 10,595초 늦었다.
- 서비스 `ubts-gnss-time-sync`(녹화기 이미지, `scripts/gnss_time_sync.py`, `--mode step`, 권한은 기본+`SYS_TIME`): 커널이 미동기(`STA_UNSYNC`)일 때만, 유효·완전 해석·tAcc≤50ms인 NAV-PVT epoch 5개 이상이 0.1초 안에서 일치하고 그 사이 호스트 시계 점프가 없을 때, 오차 2초 이상이면 **녹화기 idle·rover 비활성·매핑 없음·native owner 없음**을 결정 시점과 실행 직전에 다시 확인한 뒤 한 번 step한다. 녹화·조종·매핑 중에는 보류, 10분 cooldown, 부팅당 3회, 실행 후 잔여 오차 확인. 상태 `runtime/clock_sync/status.json`, 이벤트 `events.jsonl`.
- 현재 상태: NTP 동기 중이라 `action=none`, 실내 NoFix라 GNSS UTC 무효(epoch 0)를 정상 판정. **실제 step 동작은 네트워크 없는 현장 부팅에서 아직 검증하지 않았다.** NTP가 나중에 연결될 때의 timesyncd 동작은 바꾸지 않았다(root 설정 변경 없음).
- 과거 기록: 원본 이름·metadata·timestamp는 그대로 두고 `recording_clock.json`(표시 전용, 근거 포함)을 `035952`·`040927`·`042200`·`045553`·`051127`에 추가(offset 10,594.97–10,595.13초, 분산 ≤0.042초). `042301`은 시작 시점 GNSS 보정이 이미 있어 생략, `024924`·`042151`은 유효 UTC가 없어 거부. 작성 전후 원본 해시 동일 확인, 녹화 목록이 보정 시각을 `gnss_estimated`로 표시.
- RTC 배터리는 하드웨어 확인 후 별도 결정.

## 6. 테스트 기록 영상 후처리

자동 큐가 정지 직후 작업을 받아 `complete`(1080p 3,939프레임, 138초 영상 인코딩 1,013초 ≈ 7.4배). 새 정리 코드가 완료 직후 내용 증명된 중간 파일 4,129개(1.04GB)를 지우고 `encoder.log`·`progress.json`·`source_sha256.json`·`cleanup.json`만 남겼다. 5개 화질 모두 init+첫/마지막 세그먼트 디코딩 정상. 테스트 스크립트 1차 실행은 Stop 요청을 GET으로 보내는 버그로 녹화가 계속됐고, 즉시 POST `/stop`으로 정상 종료한 뒤 검증 전용 모드로 같은 기록을 확인했다.
