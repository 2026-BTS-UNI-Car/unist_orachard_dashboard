# 2026-10-10 녹화 저장 보완과 로컬 시뮬레이션 정지 수정

**Jetson의 마지막 직접 확인은 11:58 KST이며, 이후 작업은 로컬에서 진행했다. 아래 새 녹화 코드는 GitHub 소스에 반영하지만 Jetson에는 아직 설치하지 않았다. 실제 자율주행과 지속 실시간 GPS·IMU·LiDAR 통합 지도의 검증은 미완료다.** 전원 종료 전에 설치·검증·보관한 결과는 [전원 종료 체크포인트](NATIVE_POWER_CUT_CHECKPOINT_20261010.md)에 있다.

## 녹화 저장 보완

메타데이터·상태·타임라인·보조 기록 manifest는 같은 디렉터리의 임시 파일을 fsync한 뒤 원자적으로 교체하고 Linux에서는 디렉터리도 fsync한다. GPS·PWM JSONL에는 최대 두 개의 비상속 파일 descriptor를 보유한 별도 worker가 1Hz로 동기화를 요청한다. LiDAR·IMU·GPS 매핑 보조 gzip 기록은 기존 FIFO와 원본 시각을 유지하면서 소유 worker가 주기적으로 flush/fsync한다. 새로운 전체 bag 스캔이나 고주파 해시 작업은 없다.

저장 worker가 종료 제한 안에 끝나지 않으면 참조를 유지하고 새 녹화를 거부한다. 아직 변경되는 gzip을 완성된 파일처럼 hash하지 않는다. 상태의 `mapping_sidecar_cleanup_pending`, `durability.sidecar_worker`와 기록 metadata의 worker 종료·완료·오류로 구분한다. 정상 Stop의 기존 `archived` 표시, 영상/HLS 후처리 enqueue, rosbag 토픽·명령·기존 Stop 제한은 유지했다. `archived`는 기존 종료 표시이며 bag 무결성 검증을 뜻하지 않는다.

| 검증 | 결과와 범위 |
| --- | --- |
| 고정 후보 테스트 | Windows 16 PASS/0 skip; 정상 녹화·영상 enqueue, 저장 오류, blocked worker 소유권, 별도 임시 child kill 포함 |
| 독립 Linux 실행 | 동일 16 PASS/0 skip; 의존 경로만 읽기 전용 mount로 변경, rosbag disabled, 새 tmpfs 출력만 사용 |
| 실제 Linux fsync | tmpfs에 파일·디렉터리 fsync 완료 확인; 실제 Jetson 저장장치의 내구성 증거는 아님 |
| 공개 회귀 테스트 | portable 15 PASS; 원본 AST 비교 1개는 고정 private audit에 보관 |
| 보호 대상 | 기존 로컬 서비스 8개의 이미지·PID·시작 시각·설정 유지; 기존 녹화·실제 장치 접근 없음 |

갑작스러운 OS child 종료 시험에서 동기화한 40개 JSONL 행과 gzip의 온전한 행 prefix는 보존됐다. gzip footer가 없으므로 정상 EOF 검증은 실패했고 metadata는 `recording` 상태 그대로였다. 자동 복구·reindex·이전 기록 변경은 하지 않았다. 1Hz는 동기화 요청 주기이며 최대 데이터 손실 시간이나 kernel I/O 종료 시간을 보장하지 않는다. `rosbag_readproof`, `sudden_power_loss_qualified`, `filesystem_storage_failure_qualified`는 모두 false다.

현재 Jetson에서 정상 종료한 `20261010T024924Z_both`는 별도로 원본 bag을 EOF까지 읽고 메타데이터 계수와 대조했다. 원본 JPEG 193개 모두 1920×1080이었다. 이 8.5초 기록은 새 fsync 코드의 설치 증거나 지속 30fps·모든 센서 공통 구간의 전체 표본 보장으로 확대하지 않는다.

후보 H: `4a6754af958f3b7d1e9a1b46d3a8ccb95da883bb26bdf389c03458c1010ef5ad`.
대상: `scripts/data_recorder_service.py`, `scripts/mapping_sensor_writer.py`, `scripts/recording_durability.py`.
공개 테스트: `python -B tests/test_recorder_durability.py`.

### 중단 기록을 바꾸지 않고 확인하는 도구

`python -B scripts/inspect_interrupted_recording.py --inspect --recording-dir /absolute/path/to/recording`으로 명시적으로 검사한다. `--inspect`가 없으면 파일을 읽지 않는다. metadata의 원래 상태, 온전한 JSONL 행, gzip의 읽을 수 있는 prefix·checksum·footer, bag 구성 파일 존재를 제한된 범위에서 보고한다. 입력·해제된 데이터·행·항목·보고 크기와 cooperative 실행 시간 제한이 있고, symlink·경로 역참조를 거부한다. 제한에 걸려 끝까지 읽지 못한 파일을 손상되었다거나 완성되었다고 단정하지 않는다.

검사기는 원본을 repair/reindex/rename하지 않고 자동 녹화를 재개하지 않는다. bag 내용은 읽지 않으므로 `rosbag_readproof`는 항상 false다. 정상적인 `archived` 표시도 그대로 보고하며 실제 전원 차단 후 복구 합격을 뜻하지 않는다. 읽기 자체로 OS의 접근 시각은 바뀔 수 있다. synthetic Windows 15 PASS/0 skip, 독립 Linux의 portable 15 PASS/0 skip와 실제 symlink 거부·원본 hash 보존을 확인했다. Linux에서 허용되는 Unicode 파일명 길이로만 테스트 fixture를 조정했으며 검사 source는 동일하다. 실제 저장 기록은 이 시험에서 읽지 않았다.

공개 회귀: `python -B tests/test_interrupted_recording.py`.

## 실제 로컬 시뮬레이션에서 확인한 정지 수정

Stop과 진행 중 DDS 콜백이 겹칠 때, 의도적으로 닫은 큐를 잘못된 패킷 오류로 처리하고 외부 owner가 이전 `running` 상태를 기준으로 다시 오류를 만드는 경합을 수정했다. 소유 lifetime의 취소 표시를 먼저 설정하며, 정확한 닫힌 큐의 취소만 구분한다. 패킷 손상·overflow·SDK·ingest의 실제 오류는 Stop 중에도 보존한다. 늦게 도착한 이전 owner 콜백과 commit은 새 owner로 넘기지 않는다. 큐 64개/8MiB, 기존 원본 시계·source freshness·20초 READY·40초 cleanup 제한은 유지한다.

고정 pure 테스트 54 PASS/0 skip 뒤 ROOT가 로컬 SIM owner 하나만 교체했다. 기존 WEB·센서 source·Gazebo·RViz와 다른 서비스 7개는 유지했다. 기존 두 owner 컨테이너는 정지 상태로 보관했고 지도 byte 파일도 교체 전에 별도 보관했다. 기존 메모리 지도를 새 실행으로 자동 이식한 것은 아니다.

같은 19개 경합 회귀를 repo 상대 경로의 portable 테스트와 최소 fake fixture로 공개했다. 테스트 함수·fixture 함수의 AST 동일성과 exact package SHA를 확인했고 19 PASS/0 skip이었다. `python -B -m unittest discover -s tools/tests -p test_sim_native_owner_stop_race.py -v`로 재검증할 수 있다.

- 로컬 대시보드: `http://127.0.0.1:18089`.
- 새 owner: `fa189b79a329135d81bfeafe0f0cc0b4c644877439367d6878e5401d5fce6e2c`.
- 동일 이미지: `sha256:d67b8b92261ea68d89eacbdeb20bbfd9b37c190dc830fb161a1a7b61560a656d`.
- 패키지 H: `f987e2b58114b7d16daad698328a37405b1cdc553df902f4adf98dfea0cb903a`.
- activation H: `23303a2577ec93c9173b5da776e23102e5091c254c70abfcdb2d3063feda3455`.
- 원본 source session·domain·GID·namespace·full Env·8CPU/4GiB profile 유지. 자동 모터 출력 없음.
- 실제 Start 7.521초, 30초/60회 조회 동안 fault 없음. source callback +4,028, pose +263, native 수신·처리 각 +3,552, 완결 batch +502.
- Stop 뒤 `stopped`, fault 없음, 소유 resource 6개 모두 종료, 큐 0, priority 해제. 지도 body 5,327,445B를 새 HTTP 연결의 identity/gzip 두 경로로 받아 SHA 검증.
- 보존한 grid body는 native version 7, known cell 320,519개다. 완결 batch 502개를 서로 다른 502개 지형 갱신으로 해석하지 않는다. body H: `976eb8618090ee8dd6d85d7ca8db13c9ffaa175f20f80f2bc27d99830a6adbef`.

이 결과는 로컬 Cartographer/String 시뮬레이션 경로의 Start·진행·정지·보존을 확인한 것이다. 물리 Jetson CDR 경로의 지속 최신 입력 기준 통과, 지형 정확도, GPS로 보정된 전역 지도 또는 실차 제어 검증을 대신하지 않는다. 변경한 package의 자체 manifest는 동결 당시 pure qualification만 표시하며 위 ROOT 실행 증거는 별도로 보관한다.

## 재개 순서와 남은 작업

1. Jetson이 다시 켜지면 기존 기록을 먼저 읽기 전용으로 검사하고 recorder idle·기존 서비스·디스크 여유를 확인한다. 새 녹화 코드 설치 전에 정확한 기존 파일과 image를 보관한다. 기존 bag을 자동 repair/reindex하거나 `archived`로 바꾸지 않는다.
2. 새 녹화 코드를 직접 SSH로 설치하고 실제 ARM 환경의 파일·디렉터리 fsync 및 정상 1080p 녹화·영상 후처리·Stop을 검증한다. 시험 기록은 별도 ID로 만들며 기존 42개는 보존한다. 재부팅 시 자동 모터/녹화를 시작하지 않는다.
3. 기록 중 전원 차단에 대한 실제 저장장치·bag 복구 검증은 별도 수행한다. 중단 파일 원본을 보관한 뒤 복사본에서만 복구를 시도한다. 장시간 카메라 fps와 각 토픽 표본의 공통 수신 구간을 검증한다.
4. 물리 native 최신 입력의 긴 관측 공백과 처리 지연을 줄인다. 현재 수치 동등 overlap batch 후보는 성능이 일관되게 개선되지 않아 적용하지 않았다. source/RAW/IMU freshness 제한은 완화하지 않는다.
5. 10월 3일 도로·과수원 전체 지도와 9월 10일 다른 차체·장착 offset·가지·벽·마지막 열을 검증한다. GPS NoFix를 유효 위치로 만들거나 PWM·휠 odometry·truth·카메라를 위치 증거로 대신하지 않는다.
6. 현재 장착 방향·기울기·차체 범위·좌우 drift/제자리 회전·진동·제동을 실측하고 제어 binding을 연결한다. 검증되지 않은 물리 자율주행은 완료로 표시하지 않는다.

private 재개 근거는 `native-navigation-20261007/recorder-durability-root-20261010`, `sim-stop-race-root-20261010`와 각 frozen 후보에 보관한다. GitHub Actions는 사용하지 않았다. 실제 배포 상태와 공개 소스 상태를 구분한다.

로컬 SIM의 재시작 정책은 기존대로 `no`다. 새 Start는 source session·domain·GID를 다시 확인해야 한다. PC나 센서 컨테이너가 재시작되어 출처가 바뀌면 이전 activation을 그대로 실행하지 말고 새로운 읽기 전용 discovery 결과로 activation을 다시 생성·검증한다. 이 변경에서 재부팅 후 자동 복구나 기존 메모리 지도의 자동 복원을 검증한 것은 아니다.
