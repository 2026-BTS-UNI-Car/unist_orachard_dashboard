# 2026-10-10 실차 자율주행 소프트웨어 적용 체크포인트

현재 원본 시각 계측 설치와 후속 IMU gap 시험은 [센서 시각 계측과 지속 처리](NATIVE_ADMISSION_PROGRESS_20261010.md)에 기록했다. 아래 내용은 이전 체크포인트이며 현재 설치 이미지와 구분한다.

현재 관찰 서비스의 추가 최적화와 후속 실제 시험은 [센서 관찰 처리 개선](NATIVE_LATENCY_PROGRESS_20261010.md)에 기록했다. 아래 이미지·시험 결과는 추가 최적화 이전 체크포인트다.

**판단 코드와 Jetson 대시보드 설치를 완료했다. 실제 모터 자율 구동은 미완료이며 관찰 전용으로 유지한다.** 사용자가 보고한 바퀴 전원 OFF는 원격으로 측정한 사실이 아니다. 이번 시험에서 모터 API를 호출하거나 제동·장착 보정값을 변경하지 않았다.

## 적용한 기능

- 전체 2D LiDAR 스캔, 인과 IMU, 연속된 센서 평면 이동 추정으로 양측 과수열 지지·열 중심·방향·전방 반환점을 판단하는 플래너.
- 첫 제안 0, 시간에 따른 지지 확인, 한쪽 열 소실·미관측·불안정한 형상은 HOLD, 추정 전방 공간의 반환점과 역전은 STOP. 마지막 열·회전 공간을 모르면 회전을 제안하지 않는다.
- 순항 선호 0–30%, yaw 보정·감쇠·제안 변화 제한. 실행 중 기존 속도 옵션을 바꾸면 관찰 판단기에 전달한다. 퍼센트는 미교정 선호값이며 측정된 바퀴 속도나 m/s가 아니다.
- 명시적인 관찰 시작/정지, 단일 owner, 세대별 발행 차단, 큐·첫 오류·입력 경과·제안/적용값 확인. 적용값은 항상 좌우 0%다.
- 관찰 TTL 만료 시 오래된 계산을 버리고 0으로 대기한다. 새 정상 입력에서만 연속성 기준을 다시 잡고 첫 출력을 0으로 만든다. 기존 operator/형상 STOP, 미래 시각·시계 역행·출처 변경·손상 오류는 해제하지 않는다.
- 원본 ROS CDR·Header·publisher GID·RAW receipt를 확인하는 별도 센서 서비스. 페이지 접속과 상태 조회는 센서 구독을 시작하지 않는다. 장치·GPIO·모터 소켓을 이 서비스에 제공하지 않는다.

GPS·PWM·카메라·휠 오도메트리·시뮬레이션 truth는 이 플래너의 위치 추정 입력으로 사용하지 않는다. GPS 융합, 차체 속도 측정, 장착·진행방향·제동의 물리 검증이 완료됐다는 뜻이 아니다. 1.22 × 0.70 m 차체와 여유 폭, FF/yaw 계수는 추정치다.

## 실제 적용 및 확인

| 항목 | 확인 결과 |
| --- | --- |
| Jetson WEB | 새 이미지 적용, health healthy |
| 웹 이미지 | `sha256:3d7cd5f794dcf9cbb747040a97d63a7c72e0b2ade95edb99d22d381cf34c488d` |
| native 센서 이미지 | `sha256:c2105ed93c4e48d9ac024d6df7cf6fe7ef09d573fa96cbf92ceb4efae77e62b4` |
| 기존 서비스 | WEB 이외 독립 12개 CID/시작 시각/설정 유지 |
| 기존 지도·속도 설정 | 지도 파일 12개와 설정 바이트 동일, 지도 470 scans·queue 0 유지 |
| 실제 관찰 API | Start/Stop 및 12회 상태 조회 확인, 적용 0·authority false |
| 종료 | 관찰 owner와 센서 owner 해제, readers joined/resources closed 확인 |
| 외부 Cloudflare 경로 | health/observe/JS HTTP 200, JS 배포 해시 일치 |
| rollback | 이전 WEB CID와 이미지 보존, 실제 rollback 실행 시험은 하지 않음 |

대시보드의 **주행 제어 → 현장 시험 준비 → 관찰 시작/정지**에서 사용한다. 기존 자율주행 시작은 검증 대기이며 `/api/autonomy/start`는 계속 409다. 관찰 정지는 모터 비상정지의 검증을 대신하지 않는다.

현재 센서 서비스는 기본적으로 구독 없이 대기하며 재시작 후에도 자동 주행하지 않는다. 마지막 시험의 fault는 상태 화면에 남는다. owner가 정리됐는지 확인한 후 명시적으로 새 관찰을 시작할 수 있다. ROS driver를 재시작하면 publisher GID를 다시 관찰하고 연결해야 하며 오래된 GID를 새 출처로 가장하지 않는다.

## 실제 입력에서 발견한 미해결 문제

마지막 실제 API 시험은 2회 이동 diagnostic 후 `physical_shadow_final_fence_failed:final_physical_native_receipt_deadline`로 입력을 거부했다. 실패 row의 timing을 새로 보존해, 선택된 원본 causal IMU RAW age가 209.29ms로 200ms 제한을 9.29ms 넘었음을 확인했다. 그때 numeric fit은 15.45ms, prepare total은 23.40ms였고 추가 commit association·receiver 검증 비용도 기록했다. 12회 상태 조회 동안 적용 출력은 0이었다. 이 timing은 원본 시각과 gate를 바꾸지 않은 진단이다.

직전 별도 시험에서는 4회 후 `stale_causal_imu_receipt`, 이전 버전에서는 39회 후 `imu_bracket_gap_exceeded`, 다른 시험에서는 3회 후 최종 검증 실패, 또 다른 버전에서는 0회와 `stale_or_future_native_source`가 발생했다. 서로 다른 버전/시험의 성공 구간을 합쳐 지속 실행 성공으로 표시하지 않는다.

이전 버전은 exit -9 강제 종료가 필요했다. 실패 후 worker가 읽기를 멈추면 stdout 역압으로 자식 write가 막힐 수 있는 경로를 수정했다. drain 수정 후 두 실제 시험 모두 exit 0·kill 없음·queue 0·reader/pipe 정리·owner 해제를 확인했다. 첫 입력 오류는 보존했고, 읽어 버린 CDR/DONE을 검증 완료로 승격하지 않아 native protocol qualification은 false다. fault 뒤에도 원본 producer는 명시적인 관찰 정지 전까지 drain을 계속할 수 있으므로 Stop과 owner 해제를 확인해야 한다. 모터 적용값은 계속 0이었다. **지속 native 입력, 부하 중 지연, 실제 위치·진행방향·정지 정확도는 미검증이다.**

별도 C++ 원본 수신 5초 시험은 41 scan·64 IMU, 정상 operator DONE 및 ROS 정리 flag, 전용 컨테이너 삭제와 기존 13개 서비스 유지를 확인했다. 이 짧은 시험에서 LiDAR Header 간격 p95 101.74ms, callback 시점 Header age p95 107.77ms, callback부터 host 완료 행 수신까지 p95 5.94ms였다. IMU 간격 p95 52.91ms·최대 58.01ms, Header age p95 5.43ms였다. 완료 행 지연에는 CDR 가공·stdout·Docker 전송이 포함된다. Python fit은 실행하지 않았으며 ARM 빌드와 동시에 수행한 짧은 표본이므로 장시간 수신·계산 성능의 검증은 아니다.

현재 driver 코드의 기본 IMU 요청은 20Hz이며 명목 간격은 50ms다. 기존 60ms bracket 검사까지 여유가 작다. driver에 50Hz 요청 설정 경로가 있으나 이번에는 driver를 변경하지 않았고 실제 50Hz 달성도 확인하지 않았다. 다음 시험에서 실패 pair의 시간 여유와 FIFO 대기·동일 입력 반복 검증·fit 비용을 분리해 개선해야 한다. TTL을 늘리거나 누락 IMU를 합성해 통과시키지 않는다.

## 기록 재생과 검증 범위

10월 3일 기록 7개의 **첫 120스캔씩**, 총 840스캔만 비교했다. 전체 녹화 검토가 아니다. 원본 JSON Header와 인과 IMU를 보존했고 offline receipt는 합성임을 명시했다. 지도 적분은 0회, HOLD 34회·STOP 806회·비영 제안 0회였다. STOP 수는 최초 latch가 반복된 횟수를 포함한다. 모든 환경 분류·주행 적합성은 판단 불충분이다. 이 재생은 final native 입력 경로의 검증이 아니다.

최신 판단기·관찰·앱/API/UI·client·활성 속도 연결 77개, 기존 body/live/continuous/preflight 37개, 최종 native stream 순수 테스트 60개가 각각 통과했다. fault 후 역압·발행 차단과 실패 timing 보존 회귀를 포함한다. native ARM 빌드·기본 비실행 검사·source/binary inventory와 선택적 Compose 설정 검사도 통과했다. 서로 다른 suite의 중복 여부를 확인하지 않고 고유 테스트 합계를 만들지 않는다.

## 다음 작업

- [ ] 원본 센서가 지속적으로 freshness와 IMU bracket 검사를 통과하도록 입력 지연·실패 메타데이터·요청 주기를 계측한다. 실패 pair의 시간 여유와 정상 ROS 정리까지 확인한다.
- [ ] 10월 3일 전체 기록의 GPS/IMU/LiDAR 시계·잡음·회전·정지·지도 오차 검토와 PWM 응답 분석을 마친다.
- [ ] 9월 10일 과수원 데이터에서 작은 차체와 비일치 센서 위치를 분리해 가지·열 소실·마지막 열·벽·회전 공간 사례를 검증한다.
- [ ] 현재 차량의 센서→차체 진행방향/기울기, 낮은 속도의 좌우 응답·드리프트·진동·제동을 측정한다. 기록 추정치를 실측 보정값으로 승격하지 않는다.
- [ ] GPS 융합·전역 연속 지도·위치 불확실성, encoderless 속도 피드백, 안전한 회전 경로를 통합한다.
- [ ] 별도 검증 후 기존 ESP 단일 제어 owner·deadman·수동 인계를 사용한 실제 자율 출력 경로를 연결하고 시험한다. 현재 모터 출력은 비활성이다.
- [ ] SIM native 입력과 같은 판단기 연동, 장시간/새 환경/다중 클라이언트 검증을 마친다.

소스와 선택적 배포 설정은 `deploy/native_sensor_stream/README.md`, `compose.observe.yaml`에 있다. 기본 Compose에 자동 실행 기능을 추가하지 않았다. GitHub Actions 대신 직접 SSH로 이미지를 빌드·설치했다. 구체적인 비공개 실행 로그·실패 시도·원본 입력 증거·Git 해시는 UBTS 체크포인트에 보존한다.
