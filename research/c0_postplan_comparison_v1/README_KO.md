# C0 post-plan scalar comparison 후보 v1

현 Jetson에 적용된 source를 기준으로 만든 별도 후보다. 여섯 Python 제품 source와 map source manifest가 들어 있다. 원 Git 제품 경로, 기존 frozen source, 원시 기록, SDK/kernel/물리 package/ZeroDrive/STOP/TTL은 수정하지 않았다. 이 폴더를 만들면서 Jetson 설치·SSH·추가 NoMotor 관측은 하지 않았다.

## 동작과 기본값

full poll getter와 planner 입력은 그대로다. post-plan 재검사에서만 명시적인 `comparison_provider`를 사용할 수 있다. 기본값 None은 원 legacy full getter 경로다. app의 `UBTS_NATIVE_CONTROL_COMPARISON_V1` 기본값은 `0`이다. Root가 **dashboard와 native stream 두 역할의 새 source pin을 함께 확인한 release에서만** `1`을 명시한다. 구 서버의 unsupported op, malformed reply, None, 중간 Stop/owner 교체는 None/WAIT이며 full getter 자동 재시도나 fallback이 없다.

compact 응답에는 context/scan sequence·frame/원 scan·IMU·motion endpoint 및 같은 ingress에서 읽은 현재 SOURCE/RAW clock만 있다. 센서 ranges/IMU vectors/모션 값/좌표/PWM은 없다. 내부 응답의 원 절대 시각을 제품 status/로그에 추가하지 않는다. 4 KiB 누적 recv 상한, 고정 0.6초 deadline, 단일 요청, false authority 세 값을 확인한다.

server는 기존 proposal_input과 같은 adapter lock, 5개 feature binding, stopping/fault/proposal-None 조건 및 최신 row의 SOURCE <250 ms / 모든 RAW receipt <200 ms를 유지한다. 두 실제 adapter(`stream_rows`와 독립 `stream_rows_map`)에 byte/AST가 같은 compact getter를 넣었다. 기존 두 full getter AST는 그대로다. 서비스는 compact read 전후 현재 owner session/generation/resource/adapter가 같은지 확인하고, lifecycle lock과 adapter lock의 새로운 중첩을 만들지 않는다.

소비자는 exact context/frame/frontier, 같은 sequence의 모든 scalar endpoint 동일성, 새 sequence의 scan 원 stamp/receipt 증가와 motion.previous >= planned scan, causal IMU 및 같은 IMU stamp의 receipt 재기록 금지를 확인한다. **계획한 원 row**를 원 observation validate/250 ms TTL에 다시 검사한다. 최신 row가 fresh라고 원 계획의 stamp/receipt를 교체하지 않는다. 따라서 최신 server row RAW110 ms와 원 계획 RAW210 ms 조합은 원 관측 정책대로 가능하고, 원 계획이250 ms 초과면 최신이 fresh여도 거부한다. 이는 원 server RAW200 ms를 observation RAW200 ms로 바꾼 것이 아니다.

원 process/validate/expired/wait_input/run/stop/preference/latched STOP/lease/publication fence는 AST 동일성 및 관련 시험으로 보존했다. 새 생성 경로도 실제 worker와 두 adapter class 및 서비스 helper를 호출해 검증한다. 초기 다섯 파일 초안의 MapRows 누락과 시험 harness 실패는 DRAFT_ERRATA_KO.md에 남겼다.

## 검증과 미검증

`python -B test_comparison.py`: 생성 회귀16개, 실제 IO/SDK/ROS/모터/스레드 실행 없음. 기존 `tests/test_autonomy_observation.py`23개를 overlay에 연결해 한 번 실행했고 전부 통과했다(소프트웨어 unit thread만 사용). GENERATED_RESULT/EXISTING_RESULT에 정확 시험 source SHA와 결과를 보존한다. 이는 Jetson runtime/성능 또는 freshness PASS의 증거가 아니다.

원 실제25초는 planner115회, full getter poll152회/평균37.451 ms, post-plan115회/평균45.467 ms, plan+검증115회/평균35.467 ms였다. warm trace81개 중 age 미표시12개다. 30은 성공 게시된 planner snapshot의 누적 처리수 최대이며 게시 횟수가 아니다. factory13표본은 owner 결합 sampler 오류로 대표성이 없고 병목 주장에 쓰지 않는다.

range 복사/JSON 크기 한 번을 줄일 가능성만 있다. adapter lock, serial UDS 대기, planner 비용은 남는다. 실제 지연 감소/age 개선은 새 정확 owner-bound25초 측정 전에는 UNKNOWN이다. 새 source를 배포한 뒤에도 원 NoMotor/age/owner/Stop 기준을 그대로 평가해야 한다.

## Root 적용 준비 경계

SOURCE_PINS.json은 정확 현재 deployed source 기준과 후보 SHA를 기록한다. 6 Python+NATIVE_MAP_SOURCE만 새 image에 overlay한다. map manifest는 `files_sha256.stream_rows_map.py` 한 값만 바뀌었고 service literal이 새 manifest SHA를 가리킨다. stream BUILD_PROVENANCE는 실제 old parent 값이5334…인 것을 검증하고 **fresh derived image 안에서만** `files_sha256.stream_rows.py` 한 값을 새 후보로 재결합한다. `rebind_stream_provenance.py`는 기본 OFF이며 CPP binary/observer contract/physical package 및 다른 source를 함께 검증한다. 현재 실행 container/원 metadata를 직접 수정하지 않는다.

Root fresh image에서 기존 field10 preflight에 더해 다음을 실행한다. 두 source 역할이 모두 성공하기 전 dashboard enable을 하지 않는다. 이 author는 아래 image preflight를 실제 image에서 실행하지 않았다.

```text
python -B /kit/run_role_preflight.py --root-import-only --role dashboard
python -B /kit/run_role_preflight.py --root-import-only --role stream
```

해당 image에 기존 모듈/물리 package의 정확 PYTHONPATH를 유지한다. stream preflight는 실제 두 adapter class를 import해 generated scalar로 service dispatch와 Stop 거부를 확인하며 ROS node/socket/원센서/actuator는 열지 않는다. preflight 이후에도 Root의 exact image/source/76개 보호 파일, idle/disarmed/recorder idle, no C0 owner, 기존 런타임 Docker API 동일성·rollback 보존 검사가 필요하다. compose 사용·자동 retry·한 역할만 enable·카메라/원센서 clock 재설정은 하지 않는다.

현재 Git 작업의 root product 파일과 deployed source는 WS relay/배포된 원 IMU xyz 전달 등의 이미 설치된 차이가 있어 동일하다고 가정할 수 없다. 그래서 공개 branch에는 이 immutable overlay 연구 폴더를 추가하며 Git HEAD 전체를 실행 image로 간주하지 않는다. 카메라 JS/index와 기존 physical package는 실제 parent image에서 그대로 보존해야 한다.
