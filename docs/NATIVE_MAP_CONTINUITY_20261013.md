# 실시간 native 지도: 세션 장애·공백 연결·처리 속도 개선 (2026-10-13)

**범위**: 실제 Jetson native 관찰 서비스(`deploy/native_sensor_stream/`)의 누적 지도 경로. 실제 모터 출력·조종 활성화·펌웨어 변경·장착 보정은 하지 않았다. 자율주행 출력은 OFF/HOLD. 아래 수치는 **10/10 수동 과수원 기록 6개(약 57분)를 설치된 클래스에 1배속으로 재생한 결과**이며, 실차 주행 중 실시간 검증이나 물리 정확도 인증이 아니다. 정확도 비교 기준인 오프라인 ICP chain은 참조값이지 정답이 아니다.

## 1. 재생 방법

- 도구: private `D:/Develop/UBTS/resume-audit-20261013/native-replay/replay_native.py`.
- 입력: bag의 원본 `/scan`·`/imu/data_raw` CDR. 읽기 전용으로 추출했다(6개 기록 약 883MB).
- 경로: 실제 `PhysicalOriginalIngress`·`PhysicalNativeShadowFactory`·`StreamObserverRows`(map)·`CommittedNativeMapAdapter`·`NativeMapTransport`. 행 형식은 C++ observer와 같다.
- 시계:
  - 신선도 게이트는 실제 `CLOCK_MONOTONIC_RAW`로 그대로 동작한다.
  - ROS 시스템 시계만 기록 시각 기준 가상 시계다.
  - 기록 간격대로 행을 넣는다.
- 환경: 실서비스와 같은 2 CPU·512MiB·단일 스레드 BLAS 컨테이너. Jetson에서 실행했다.
- 장애 처리: 장애가 나면 세션을 닫고 다음 행에서 새 세션을 Start한다. 지도 세션은 유지된다.
- 제외 범위: GNSS는 재생하지 않는다. 웹 전송·브라우저 표시는 재생 범위 밖이며 별도 live probe로 잰다.

## 2. 기존 코드(설치본 v20, 1fb6cea)에서 확인한 문제

| 기록 | 길이 | 성공 fit | 세션 장애 | 지도 발행 | 최장 지도 정지 | 지도 지연 p50 |
|---|---|---|---|---|---|---|
| 035952 | 474s | 69% | 14 (fit 거부 12, IMU 공백 2) | 8 | 467s | 616ms |
| 040927 | 526s | 65% | 10 (6, 4) | 5 | 520s | 629ms |
| 042200 | 55s | 62% | 0 | 3 | 46s | 698ms |
| 042301 | 687s | 69% | 6 (2, 4) | 1 | 684s | 420ms |
| 045553 | 746s | 65% | 55 (48, 7) | 4 | 741s | 828ms |
| 051127 | 905s | 64% | 18 (17, 1) | 7 | 898s | 587ms |

원인은 세 가지다.

1. **정상적인 정합 거부·IMU 공백이 관찰 세션 전체를 종료한다.**
   - `physical_internal_fit_rejected`: 반복적인 나무 열에서는 정합이 거부되거나 모호할 수 있는데, 이것이 정상 결과임에도 세션을 끝낸다.
   - `imu_bracket_gap_exceeded`: IMU 공백이 이미 시간이 지난 상태로 판정되면, 기존 v9 IMU 공백 WAIT가 이 경우를 소유하지 못한다.
2. **공백 뒤 지도 연결이 영구히 실패한다.**
   - WAIT가 나면 지도 연속성이 끊긴다.
   - 끊긴 뒤에는 마지막 발행 keyframe 한 장과 새 scan의 정합만으로 다시 잇는다.
   - 이 정합은 0.8m·30° 탐색 한계와 양방향 겹침 기준을 넘지 못한다. 공백이 계속 커지므로 한 번 실패하면 다시 이어지지 않는다.
3. **지도 처리가 수신 경로를 잠식한다.**
   - 세대마다 셀 전체를 복사·정렬하고(prepare 148ms), 전체 타일을 다시 인코딩한다(100–180ms).
   - 지도 없이 돌리면 수신 p99가 66ms인데, 지도를 켜면 135–295ms로 늘고 나이 만료 WAIT도 많아진다.

## 3. bridge 오프라인 벤치마크(실제 scan 924쌍)

- 대상: 6개 기록에서 공백 0.3–3.0초, 주행 상태(정지·직진·arc·pivot·혼합)별로 뽑은 K→H 쌍.
- 위치: private `bridge-bench/`.
- 기준: 오프라인 ICP 참조.
- 판정: 일치는 0.10m·1.0° 이내, 오수락은 0.25m 또는 2.5° 초과.

| 방식 | 수락/924 | 오수락 | 직진 0.8s | 직진 1.2s | 직진 2.0s |
|---|---|---|---|---|---|
| 기존 keyframe 정합 | 617 | 0 | 18/40 | 0/40 | 0/40 |
| seed 중심 추가(C1) | 640 | 0 | 21/40 | 0/40 | 0/40 |
| seed 중심 + 탐색 한계 확장(C1b, **채택**) | 690 | 0 | 22/40 | 19/40 | 6/40 |
| IMU yaw seed(C2) | 691 | 0 | – | 19/40 | 8/40 |
| correlative submap(C3) | 688 | 0 | – | 18/40 | 7/40 |
| 정답 seed 상한(oracle) | 694 | 0 | – | 19/40 | 7/40 |
| submap 쌍(C5, 미채택) | – | 0 | 4/40 | 1/40 | 0/40 |

- **정답 seed로도 직진 1.2초 이상은 절반 이하만 통과**한다. 한계는 seed가 아니라 단일 scan 겹침 기준이다. 겹침 기준은 오수락을 막는 장치이므로 완화하지 않았다.
- 결론적으로 긴 직진 공백은 bridge로 메울 수 없으며, 공백 자체를 줄여야 한다(4.2).
- 주기적인 나무 열을 흉내 낸 synthetic 장면에서 문제가 하나 더 확인됐다. 실제 이동(1.5m)이 탐색 한계 밖에 있으면, 기존 identity 탐색이 한 나무 간격 어긋난 위치(−0.57m)를 **오수락**할 수 있다. seed 중심 탐색은 실제 이동을 찾는다. 이 동작은 `test_native_map_bridge.py`로 고정했다.

## 4. 변경

### 4.1 지도 처리(commit `991c9d6`)

**셀·전송 처리**
- 지도 상태를 64×64 numpy 타일 copy-on-write로 바꿨다. 세대마다 scan이 닿은 타일만 복사한다.
- 정렬 행은 필요할 때만, owner lock 밖에서 만든다.
- proposal 창은 벡터화했다.
- 광선 래스터가 numpy 셀 배열을 돌려준다(스칼라 기준과 동일한 결과를 테스트로 확인).
- 타일 전송은 상태 바이트에서 개수·경계·점유 경계를 직접 유도한다.
  - 같은 내용의 타일은 검증과 인코딩을 한 번만 한다.
  - owner가 보낸 요약값이 유도값과 다르면 거부한다.
  - 출력은 기존 전체 재생성 경로와 바이트 단위로 같다.

**공백 연결(bridge)**
- 새 capture chain의 첫 seal(공백 직후 첫 scan)을 최신 seal보다 먼저 처리한다.
- keyframe 표면은 보존된 원본 CDR에서 필요할 때만 만든다. geometry digest를 정합 전후로 확인해 봉인한다.
- seed 중심을 둔다.
  - LiDAR fit의 등속 twist를 SE(2) 지수로 외삽한다.
  - 같은 source context이고 공백이 3초 이하일 때만 쓴다.
  - 두 번째 seed grid를 추가하며, 탐색 한계는 그 중심까지 닿는다.
  - 기존 판정 기준과 모호성 판정은 모든 결과에 그대로 적용한다.
- 같은 keyframe에 대한 재시도는 처음 3회 뒤 원본 scan 시각 기준으로 0.5→4초 backoff한다. 시계를 새로 읽지 않는다.
- 지도 용량에 도달하면 명시적 새 지도 전까지 latch한다.
- 단계별 시간을 상태에 공개한다.

**공통**
- 신선도·소유권·Stop·generation fence는 그대로다.

### 4.2 세션 장애의 typed WAIT 전환(commit `c7961f7`, `b0eb7f7`)

새 패키지 revision `native_v21_owned_registration_imu_gap_scan_continuity_wait`. HANDOFF `87859742…`, 부모는 v20 `e6231533…`. 생성자 기본값은 OFF이고 v21 HANDOFF만 켠다.

| 대상 | 처리 |
|---|---|
| 정합 거부 | 정확한 kernel 결과만 대상: `unsupported_or_inconsistent_registration`, `ambiguous_registration`, `insufficient_returns`, `insufficient_observed_surfaces`, 0.6m/π 초과 fit. 거부된 쌍은 아무것도 발행하지 않는다. 이미 모든 게이트를 통과한 최신 scan을 다음 anchor로 유지한다. |
| 시간이 지난 IMU 공백·범위 손실 | v9와 같은 전체 re-anchor. 같은 시계 표본, TTL 변경 없음. |
| scan 연속성 | 누락 scan(간격 >200ms, 수신 순서 유지)은 최신 scan 유지. 보류 쌍 교체는 전체 re-anchor. |

- 다음은 모두 기존처럼 fatal로 남긴다: CDR/SHA/header/GID/context, 시계 domain, 순서 역전, capability/digest/generation/Stop, 예상 밖 수치 예외.
- 소유를 증명할 수 없는 v21 WAIT는 `unowned_native_<label>_wait`로 실패한다.
- 상태 카운터, 연속 횟수(streak), 시간 기록(≤2KB)으로 모든 WAIT를 볼 수 있다.

### 4.3 고정값(pin)과 설치

- 설치(2026-10-13 08:07 KST): native 이미지 `29c89fb1…`.
  - 부모 `b54eeee4…`에 지도 모듈 6개, `stream_rows*`, `stream_runner`, `NATIVE_MAP_SOURCE.json`, 서비스, 패키지 4개 파일을 overlay했다.
  - `BUILD_PROVENANCE.json`은 이미지 안에서 다시 생성했다.
  - ARM 테스트 16개 모듈이 모두 통과했다.
  - 기존 컨테이너는 `ubts-native-sensor-observation-retained-map-continuity-v21-20261013-20261013T080730`으로 보존했다.
  - 롤백: `~/ubts-releases/native-map-v21-20261013/INSTALL_RESULT.json`.

- `NATIVE_MAP_SOURCE.json` SHA `6d0093fd…`. 서비스 고정값도 같이 바꿨다.
- `stream_runner` loader가 v21을 수락한다.
- `Dockerfile.gnss*`의 `PHYSICAL_PACKAGE_SHA`는 `87859742…`.
- `BUILD_PROVENANCE.json`은 설치 overlay 안에서 `build_provenance.py`로 다시 만든다.

## 5. 결과(같은 기록·같은 기준)

### 5.1 재생(6개 기록, 1배속, Jetson 2 CPU·512MiB)

| 기록 | 세션 장애 base3 → cand4 → cand5 | 지도 발행 세대 base3 / cand4 / cand5 | 지도 지연 p50 base3 → cand5 | 최장 정지 base3 / cand5 |
|---|---|---|---|---|
| 035952 | 14 → 14 → **0** | 8 / 56 / 57 | 616 → 128ms | 467 / 459s |
| 040927 | 10 → 9 → **0** | 5 / 38 / 40 | 629 → 87ms | 520 / 517s |
| 042200 | 0 → 0 → 0 | 3 / 49 / 46 | 698 → 84ms | 46 / 46s |
| 042301 | 6 → 4 → **0** | 1 / 46 / 8 | 420 → 122ms | 684 / 683s |
| 045553 | 55 → 54 → **0** | 4 / 25 / 68 | 828 → 156ms | 741 / 735s |
| 051127 | 18 → 18 → **0** | 7 / 124 / 49 | 587 → 89ms | 898 / 897s |

- cand4 = 지도 처리·bridge 개선만, cand5 = cand4 + v21.
- cand5에서는 6개 기록 모두 세션 1개로 끝까지 이어졌다. v21 WAIT 횟수: 정합 거부 121, 시간 지난 IMU 공백 20.
- 단계 시간(cand4·cand5): prepare 30–40ms(기존 148ms), 표시 인코딩 8–10ms(기존 100–180ms).
- 정확도(ICP 참조 대비, 지도가 그려진 구간)
  - 인접 세대 RPE 중앙값 2–8mm.
  - bridge·긴 구간 RPE 최대 5.7cm·0.5°.
  - 가까운/먼 점 smear는 참조 pose와 비슷한 수준(`native-replay/out/ACCURACY_base3_cand4.json`).
- **남은 문제: 연속성.** 재생에서는 한 번 1초 안팎의 직진 공백 bridge가 실패하면, 이후 공백이 커져 기록 끝까지 다시 잇지 못한다.
  - 재생 입력(bag)은 IMU 메시지를 약 1.9% 잃었다. 그래서 IMU 공백 WAIT가 live보다 3배 이상 많다(WAIT 분석: bag 035952 공백 232회 vs 완전 스트림 시뮬레이션 74회).
  - 따라서 재생의 공백 빈도는 실차보다 비관적이다.

### 5.2 진행 중 정합 취소 수정(commit `10bbf67`)

- 재생에서 bridge 정합(ARM 54 seed, 수백 ms) 도중 commit 단계 typed WAIT(`native_fit_not_committed`)가 정합을 취소해 공백이 더 길어지는 현상을 확인했다.
- clean factory의 typed WAIT일 때만 기존 pair WAIT와 같이 진행 중 정합을 보존한다.
- factory 고장·Stop·다른 무효화는 여전히 취소한다.
- cand6(설치본과 동일 코드) 재생 결과는 5.4절에 기록한다.

### 5.3 실차 live 확인(2026-10-13, 정지 상태, 관찰만)

- 설치: native 이미지 `29c89fb1…`(부모 `b54eeee4…`).
- 실행 조건: rover disarmed·정지를 확인한 뒤 관찰 Start만 했다. 180초 뒤 Stop. 모터 명령 없음.
- 측정: 브라우저와 같은 500ms 폴링으로 `/api/autonomy/native-map/tiles`를 읽었다(`map-qualification/live_map_latency_probe.py`).

| 항목 | IMU 20Hz | IMU 33Hz |
|---|---|---|
| scan 대비 fit | 1,476/1,711 (86.3%) | 1,666/1,695 (**98.3%**) |
| 세션 장애 | 0 | 0 |
| IMU 공백 WAIT / 나이 만료 WAIT | 8 / 71 | 0 / 10 |
| priming으로 버린 scan | 76 | 8 |
| 지도 세대(웹에서 확인) | 325 | 349 |
| scan 헤더→웹 수신 p50 / p90 / p99 | 261 / 718 / 1030ms | 248 / 292 / 750ms |
| 웹에서 본 세대 간격 최대 | 2.0s | 1.0s |
| 타일 fetch p50 | 16ms | 18ms |
| 큐 최대(한도 16) | 8 | 9 |

- **IMU 33Hz 전환**: 드라이버 설정만 바꿨다(펌웨어 변경 아님).
  - 방법: `IMU_SAMPLE_HZ=33` compose override 1개(`~/ubts-releases/imu-rate-20261013/`).
  - 결과: 실제 발행률 33.0Hz, 읽기 오류 0, 드라이버 CPU 7.5→11.8%.
  - 롤백: `INSTALL_RESULT.json`의 `rollback_command`.
  - 근거: WAIT 분석 시뮬레이션. 6개 기록 완전 스트림 기준 fit 91.4→99.2%, WAIT 몰림 분당 16→1.5회. 50Hz 이상은 기존 timing 기록 용량 한계(창 >26 IMU) 때문에 보류.
- 웹 지연에는 LiDAR 전달 지연(헤더는 sweep 시작, 약 100ms)과 폴링 대기(최대 500ms)가 포함된다.
- 정지 상태라 이동 중 정합 난이도는 반영되지 않는다. 이동 중 확인은 현장 시험이 필요하다.
- pose ledger는 3분 만에 보존 용량(16MB, 원본 scan 포함)에 도달해 새 행 추가를 멈췄다(`retained_pose_history_capacity`). 지도 표시와는 무관하며, 기존부터 있던 상한이다.

### 5.4 cand6 재생(설치본과 동일)

(재생 완료 후 기록)

## 6. 테스트

- Linux py3.10(Jetson과 같은 numpy 1.21·scipy 1.8): 모든 native suite가 통과한다.
  - 예외는 기존 코드(1fb6cea)에서도 실패하는 오래된 테스트다: `test_stream_source` 3개(GNSS 구독 추가 이전의 observer 소스 기대값), `test_autonomy_observe_api_ui` 2개(node 필요).
  - 신규: `test_native_map_incremental` 6, `test_native_map_bridge` 12, `test_v21_reanchor_waits` 29, `test_v21_reanchor_features` 10.
- 10/10 private 지도 qualification suite(`native-accepted-chain-map`, `native-historical-recovery-wait`): 52/52.
  - 레이아웃 조정은 3건이며 `map-qualification/run_private_suites.py`에 기록했다: idle 판정에 chain-head 슬롯 포함, 지연 생성 geometry의 봉인 확인.

## 7. 남은 한계

- 단일 scan bridge로는 긴 직진 공백(약 1.2초 이상)을 대부분 이을 수 없다. 남은 공백은 원인별로 줄여야 한다(IMU 보고율, re-anchor 정책). 이어지지 않은 구간을 등속·PWM으로 채우지 않는다.
- 지도 용량은 250,000셀이다(0.1m). 긴 주행에서는 용량에 도달할 수 있고, 도달하면 latch되어 새 지도 명령이 필요하다.
- 오프라인 ICP는 참조값이다. 물리 정확도·장착·deskew는 미검증이다.
