현재 설치·검증 범위: [최종 IMU 만료 복구와 지도 작업 분리](../../docs/NATIVE_FINAL_SCALAR_RECOVERY_PROGRESS_20261010.md). GPS 통합 지도와 실차 모터 자율주행은 미완료입니다.

현재 Jetson에 설치된 비동기 지도 생성·웹 표시와 남은 오류는 [설치 검증 기록](../../docs/NATIVE_ASYNC_MAP_INSTALL_20261010.md)에 정리했습니다. 아래 내용은 이전 체크포인트의 기록입니다.

# Native 센서 관찰 서비스

현재 v9 센서 시간 만료·누락 처리는 [설치와 시험](../../docs/NATIVE_INPUT_RECOVERY_PROGRESS_20261010.md)에 있다. 아래 내용은 이전 설치 시점의 기록이다.

현재 v7 원본 LiDAR/IMU 계측의 설치·시험은 [후속 기록](../../docs/NATIVE_IMU_GAP_PROGRESS_20261010.md)에 있다. 아래 내용은 이전 설치 시점의 기록이다.

현재 v6 원본 시각 계측의 설치·시험·제한은 [진행 기록](../../docs/NATIVE_ADMISSION_PROGRESS_20261010.md)에 있다. 아래 이전 이미지·결과와 구분한다.

이 서비스는 원본 LiDAR·IMU를 읽고 주행 **제안**을 확인하는 관찰용 서비스입니다. 휠 전원을 OFF로 유지합니다. 모터 명령, 운전 lease, PWM 출력, 자율주행 활성화 기능은 연결하지 않습니다. `OBSERVE_ONLY`에서 좌·우 제안은 각각 0–30%이고 실제 적용값은 항상 0%입니다. 비율은 미교정 선호값이며 측정된 속도(m/s)가 아닙니다.

## 시작과 종료

- 기본 `native_observation_service.py` 실행은 계획만 출력합니다. `--serve`를 명시하면 로컬 Unix 소켓을 열고 **idle**로 대기합니다. 이 단계에서는 센서 구독을 시작하지 않습니다.
- 기존 대시보드 세션에서 **관찰 시작**을 누르면 별도 native 센서 owner와 제안 계산을 시작합니다. 속도 옵션은 0–30% 범위여야 합니다. 상태 조회나 페이지 접속만으로 시작하지 않습니다.
- **관찰 정지**는 제안 발행을 먼저 억제하고 센서 프로세스·파이프·worker의 종료를 확인합니다. 이는 휠 비상정지와 별개이며, 모터 정지 검증을 대신하지 않습니다. 정지 요청이 곧 종료 완료를 뜻하지 않습니다. `owned=false`와 fault/정리 상태를 확인합니다.
- 준비 실패·입력 변경·용량 초과는 fault로 중단합니다. 자동 재시작은 없습니다. 원인을 확인하고 이전 owner 정리가 끝난 뒤 명시적으로 다시 시작합니다.

현재 ROOT 확인 범위는 실제 WEB 및 native 서비스 설치, **IDLE 기본 미구독**, ARM 기본 비활성 audit, 관찰 API Start/Stop, 외부 HTTP·JS 배포 확인입니다. focused pure 77개와 native pure 60개가 통과했습니다. 실제 입력은 causal IMU freshness 검증에서 실패한 구간이 있습니다. GUI 브라우저 조작, 지속 관찰, 물리 주행·제동 검증은 완료하지 않았습니다.

관찰 API는 `GET /api/autonomy/observe`, `POST /api/autonomy/observe/start`, `POST /api/autonomy/observe/stop`입니다. Start/Stop은 기존 세션과 동일 Origin을 사용하며 센서값·모터값을 요청 본문에 받지 않습니다. 서비스의 로컬 소켓은 `status/start/stop/input`만 받습니다. 기존 지도와 기록 API는 별개입니다. native 소켓이 설정되지 않은 WebSocket 입력은 원본 native 증거로 승격하지 않고 HOLD합니다.

## 무엇을 판단하는가

원본 전체 2D 스캔, 인과 순서에 맞는 IMU, 연속된 LiDAR 평면 이동 추정만 사용합니다. 휠 오도메트리·PWM·카메라·truth·GPS는 위치 추정 입력으로 사용하지 않습니다. CDR 바이트와 원본 Header, 토픽 타입, publisher GID, 관찰 프로세스·세션·RAW 시계 경계와 freshness를 확인합니다. 프로세스 준비 제한은 15초이며 준비 전에 Stop한 경우 정리 완료와 데이터 준비 여부를 분리합니다.

플래너는 양쪽 지지점이 시간에 걸쳐 유지될 때만 열 중심과 방향을 추정합니다. 첫 출력은 0이고, 한쪽 지지 소실·분기·불안정한 형상·전방 미관측은 HOLD입니다. 추정 전방 공간 안의 반환점과 이동 역전 등은 STOP을 latch합니다. 마지막 열이나 출구를 모르는데 회전하지 않습니다. 명시적인 새 관찰 reset 전에는 latch를 해제하지 않습니다.

원본 비유한 range가 JSON `null`로 표현되면 원본 native 표식이 확인된 경로에서만 **미관측**으로 처리합니다. slot 순서와 원본 CDR 증거는 유지하며 빈 공간으로 채우지 않습니다. 후방 미관측은 유효한 전방·측면 판단 전체를 폐기하지 않지만 전방 미관측은 계속 HOLD합니다.

원본 센서 확인은 장착·물리 검증과 다릅니다. 센서→차체 변환, 진행방향, 타이밍, 모터 deadzone·좌우 응답, 제동거리·제동시간은 이 플래너에서 교정 완료로 선언하지 않습니다. 차체 1.22 × 0.70 m와 여유 폭, FF·slew·yaw P damping은 보수적인 **추정치**입니다. LiDAR 평면 속도는 검증된 차체 속도가 아닙니다. GPS 수신기 품질·시계·extrinsics 검증이나 GNSS 융합도 완료된 것으로 취급하지 않습니다. 기존 `EncoderlessDrivePreview`의 별도 qualification은 변경하지 않습니다.

## 상태와 용량

`PROPOSE_FORWARD`는 재생·관찰 제안이며 실행 허가가 아닙니다. `HOLD`/`STOP` 사유, 수신·처리 진행, queue와 첫 fault, owner 정리를 함께 확인합니다. freshness 검사에는 원본 stamp/receipt를 사용하며 조회 시각으로 원본 시간을 갱신하지 않습니다.

제안 관찰 단계의 일반적인 **양의 TTL 만료**는 `WAIT_STALE_INPUT`/HOLD/0으로 대기합니다. 계산 도중 만료된 제안과 이전 trajectory도 발행하지 않습니다. 최신 입력 getter가 `None`을 반환하면 `WAIT_INPUT`/0으로 대기하며, 이미 stale 대기 중이면 그 이유를 유지합니다. 같은 오래된 sequence로 재개하지 않습니다. 원본 context·시각·순서 검사를 통과한 **더 새로운 신선한 sequence**에서만 guarded reanchor로 이전 pair 기준과 형상 지속성을 다시 시작하고 첫 출력 0과 시간적 지지 조건을 다시 확인합니다. 원본 시각을 바꾸거나 빠진 motion/IMU를 만들지 않습니다.

이 회복은 operator/manual takeover/geometric STOP이나 이미 latch된 fault를 해제하지 않습니다. 미래 시각·시계 역전·source/context 변경·손상된 입력은 계속 실패로 처리합니다. 또한 **원본 native admission의 stale/future 및 IMU bracket gap은 여전히 fault**입니다. 관찰 계산의 대기 동작을 원본 수신 gate 완화로 해석하지 않습니다.

원본 stream의 각 CDR은 최대 256 KiB, 각 행은 최대 532,480 B입니다. 처리 queue는 16행, stderr 보존은 최근 64 KiB로 제한합니다. 제안 관찰 queue는 8개, evidence는 최근 128개입니다. lifetime 4,096-sample 종료는 적용하지 않지만 용량 초과를 조용히 건너뛰지 않습니다. 큰 입력·순서 위반·GID 변경·원본 native admission 실패·예기치 않은 EOF는 fault가 됩니다. 이 수치는 전체 메모리 사용량의 보증이 아닙니다. 원본 행을 무제한 보관하거나 자동 재생하지 않습니다.

첫 fault 이후에는 admission·제안 발행을 차단하고 stdout을 제한된 크기로 EOF까지 읽어 버립니다. 실패 worker의 큐가 꽉 차도 원본 프로세스의 write와 SIGTERM 처리가 막히지 않게 합니다. 마지막 실제 시험에서 exit 0, 강제 kill 없음, reader/pipe 정리와 owner 해제를 확인했습니다. 읽어 버린 행은 검증된 입력이나 archive가 아니며 native protocol qualification은 계속 false입니다. 첫 fault와 drain 바이트·행 수를 상태에 남깁니다.

fault가 나면 판단 발행은 중단되지만 원본 구독 프로세스는 **관찰 정지** 요청 전까지 drain을 계속할 수 있습니다. fault 뒤에도 반드시 정지를 누르고 `owned=false`를 확인합니다. 실패 계산에서 이미 측정한 timing은 `factory_failure_timing`에 최대 2048B로 보존하고 실패 row 상태를 별도로 표시합니다. 이 값은 실패 시점에 측정된 진단이며 원본 시각이나 TTL, 성공 여부를 바꾸지 않습니다.

## 기존 기록의 제한된 재생 결과

10월 3일 기록 7개의 **첫 120스캔씩**, 총 840스캔을 원본 JSON Header와 하위 시각의 인과 IMU로 재생했습니다. `ContinuousMapping(draw=False)`로 연속 센서 평면 이동만 얻었으며 지도 적분은 0회입니다. 합성 receipt는 offline 재생용으로 별도 표시했습니다. 이 추출본에는 원본 CDR·GID·RAW 증거가 없으며 native 경로로 가장하지 않았습니다. 플래너 재생 허용은 명시적 opt-in이고 실제 앱의 기본값은 OFF입니다.

| 기록 시작 시각(UTC) | HOLD | STOP | 비영 제안 |
| --- | ---: | ---: | ---: |
| 07:22:38 | 1 | 119 | 0 |
| 07:23:29 | 2 | 118 | 0 |
| 07:25:30 | 9 | 111 | 0 |
| 07:35:51 | 1 | 119 | 0 |
| 08:03:51 | 3 | 117 | 0 |
| 08:09:09 | 3 | 117 | 0 |
| 08:19:34 | 15 | 105 | 0 |
| 합계 | 34 | 806 | 0 |

사유 집계는 이동/IMU 부족 32, 추정 전방 공간 반환점 238, 이동 역전 또는 회전 branch 불명 568, 양쪽 열 지지 불명 1, 전방 미관측 1입니다. STOP 횟수에는 **첫 latch 사유의 반복**이 포함되므로 독립 장애물·역전 사건 수가 아닙니다. 모든 기록의 결론은 판단 불충분입니다. 비영 제안 0건은 실차 장애물 인식이나 안전한 제동·자율주행 검증 성공을 뜻하지 않습니다. 읽은 원본 prefix와 원본 파일 stat/manifest를 확인했으며 압축 기록 전체의 SHA를 재계산하지 않았습니다.

## 선택적 Compose 연결 예시

아래는 기존 배포에 검토 후 추가하는 예시입니다. 이미 검증된 **stream 서비스 이미지**와 현재 센서 publisher의 서로 다른 16-byte GID(소문자 hex 32자)를 공급합니다. 센서 재시작으로 GID가 바뀌면 재확인해야 합니다. 이미지 build provenance는 서비스가 시작 때 확인합니다. 예시를 적용하는 것만으로 주행이 허가되지 않으며 이 문서는 배포 완료 증거가 아닙니다.

```yaml
services:
  native-sensor-observer:
    profiles: [native-observe]  # 기본 Compose 시작 대상에서 제외
    image: "${NATIVE_STREAM_IMAGE:?검증된 기존 stream 이미지를 지정}"
    restart: "no"
    user: "0:0"
    network_mode: host
    ipc: host                 # 기존 실제 ROS 센서의 IPC topology 유지
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    pids_limit: 64
    cpus: "2.0"
    mem_limit: 1g
    environment:
      ROS_DOMAIN_ID: "0"       # 현재 서비스가 허용하는 실제 Iron domain
      UBTS_MAP_FIT_WORKERS: "1"
      ROS_LOG_DIR: /tmp/ubts-native-stream/log
    tmpfs:
      - /tmp:rw,nosuid,nodev,size=64m,mode=0700
    volumes:
      - "${NATIVE_STREAM_SOCKET_DIR:-/home/unist/ubts-native-stream-socket}:/run/ubts-native-stream"
    command:
      - /usr/bin/python3
      - -B
      - /opt/ubts-native-stream/native_observation_service.py
      - --serve
      - --socket-path
      - "${NATIVE_STREAM_SOCKET:-/run/ubts-native-stream/observe.sock}"
      - --scan-gid
      - "${NATIVE_SCAN_GID:?현재 scan publisher GID를 지정}"
      - --imu-gid
      - "${NATIVE_IMU_GID:?현재 IMU publisher GID를 지정}"

  dashboard:
    # 기존 image/volumes/environment 및 다른 설정은 유지하고 항목만 추가
    environment:
      UBTS_NATIVE_STREAM_SOCKET: "${NATIVE_STREAM_SOCKET:-/run/ubts-native-stream/observe.sock}"
    volumes:
      - "${NATIVE_STREAM_SOCKET_DIR:-/home/unist/ubts-native-stream-socket}:/run/ubts-native-stream:ro"
```

서비스가 허용하는 소켓은 컨테이너 안의 `/run/ubts-native-stream/observe.sock` 한 개입니다. host source는 사용자 홈의 `/home/unist/ubts-native-stream-socket` 또는 이미 준비된 동등한 디렉터리이며 host `/run` 생성이나 sudo를 요구하지 않습니다. 서비스 UID0의 생성 권한과 대시보드의 접근 권한이 확인된 디렉터리를 두 컨테이너에 동일하게 연결합니다. 기존 원본 센서 SHM·다른 owner의 소켓 권한을 바꾸거나 기존 소켓을 지우지 않습니다. host network/IPC는 기존 ROS 입력에 접근하기 위한 설정이며 새로운 센서 드라이버·TF publisher·모터 장치나 제어 소켓을 추가하지 않습니다. 대시보드에 이 소켓이 연결되어 있어야 관찰 Start/Stop이 native 서비스로 전달됩니다. 프로필을 명시해 서비스를 실행한 후에도 상태는 idle이며, 관찰 시작은 별도 사용자 동작입니다.

## 검증한 선택적 처리 최적화

원본 CDR·ownership·시계 경계를 유지하는 typed digest와 정확히 같은 endpoint의 surface 캐시를 별도 `optimized-package`에 제공한다. 기본 `Dockerfile.stream`의 legacy 패키지를 보존한다. 새 `Dockerfile.optimized`는 검증한 cached ARM binary를 재사용하고 명시적인 패키지 SHA를 요구한다. 설치·실패 이력과 검사 명령은 [추가 처리 개선](../../docs/NATIVE_LATENCY_PROGRESS_20261010.md)을 참고한다. 실제 지속 입력과 모터 자율 구동은 아직 합격하지 않았다.
