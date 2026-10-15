<!-- ROOT_NATIVE897_CLOSED_CURRENT_OCT15 -->

## 현재 목표와 실행 순서 — 2026-10-15

**ACTIVE / 미완료.** 실제 2D RPLIDAR S2·raw IMU 원본으로 끝까지 이어지는 지도와 다시 실행할 수 있는 로컬 시뮬레이션 대시보드를 완성한다. 아래는 최신 상태이며 뒤의 오래된 체크포인트는 당시의 결과다. 현재 소스·실행 상태는 `D:\Develop\UBTS\codex-resume-20261013\CURRENT_CHECKPOINT.json`을 함께 따른다.

### 완료해 확인한 범위

- [x] 기존 짧은 원본 후보 V32: scan300+IMU597 입력, EOF897·readback, 마지막 native 작업 COMPLETE, RAW/WIRE/PENDING0, 두 HTTP 조회·실제 headed 브라우저의 native/overview 지도 표시·정상 종료·자원 정리. 새 수정 후보의 재검증과는 구분한다.
- [x] 고정28MiB 저장 한도의 실제 C0 SDK 시험: 자연 BEGIN 용량 거부 → 정확한 COMMITTED 과거5MiB 삭제 → 같은 RAW 저장·실제 ACK → RAW0·정상 종료. 합성 셀 시험이며 원본 센서·지형 정확도 검증은 아니다.
- [x] 추가로 발견한 미완료 요청 문제 재현 및 수정: 기존 일반 ArchiveLive BEGIN 거부 뒤 preowned 실제 ACK가 와도 live_outcomes1이 남았다. 최소 두 헤더 수정 뒤 동일 실제 시험은 live0·RAW0·native_closed·close를 통과했다. 저장소 보호 참조·64chunk 해시·정확한5MiB 삭제도 별도 읽기 전용 검증을 통과했다. 기존 전체 RESULT의 검증기 실패는 그대로 보존했다. `ROOT_ACTUAL_PREOWNED_FIX_COMMON_AGGREGATE_JOINED_POSITIVE_20261015.json`.
- [x] 원본 처리용 새 Node 소스8270·ELF0ce26의 실제 빌드, SDK97/C0 불변, 기본 실행 OFF, 현재 COMMON 환경 ldd·기본 OFF·정확한 자원 정리. 센서 실행 성공으로 해석하지 않는다.
- [x] 원본897개 입력 통계와 기존 ROS ledger 해시 일치. LiDAR3240beam/scan, 유효 범위 안72.05%, 범위30m를 넘는 유한값99,312개, IMU 최대 메시지 간격109ms. 입력과 소스 불변·기존 작업의 정확한 컨테이너 종료/부재도 별도 검증했다. 기존 잘못된 Docker 부재 판정 결과는 보존했다. 장착 축·시계 정렬·지도 정확도는 이 통계로 보정하지 않는다.
- [x] 최신 작은 증거와 현재 체크포인트 NAS 복사·해시 확인. 최신19개 소스822파일/9.93MB도 별도 NAS ZIP 검증(SHA109f0b27). SDK·원본·전체 실행 환경 복구를 보장하는 백업은 아니다.

### 다음 작업 — 이 순서로 진행

- [x] **1. 새 짧은 원본 후보 V5:** 새 Node0ce26와 Stop 후 공간 정리5파일·요청 해제·이전 캐시 해제·GUI 진단을 결합해 실제 원본897개를 끝까지 처리했다. 마지막 native26/snapshot10 COMPLETE, RAW0·정상 종료, 두 HTTP 클라이언트·실제 headed 브라우저의 native/overview 픽셀·정확한 자원 정리를 확인했다. 최종 overview는2.96초/작업 스레드CPU0.70초였다. 화면에 방사형 자유 공간과 번짐이 보여 지도 정확도는 미검증이다. `ROOT_NEW_SHORT_V5_CLOSED_JOINED_POSITIVE_20261015.json`.
- [ ] **2. 전체 원본과 최종 화면:** 15,638개 센서/17,671개 보존 입력을 끝까지 처리한다. 이전 V31은 native752 COMPLETE·RAW0·정상 종료였으나36.1M셀 최종 overview가 기존30초 내 준비되지 않았다. 최신 V33은 EOF 일치 뒤 native633 DEFERRED·Stop 후 저장 대기로 실패했다. 캐시만으로 해결되지 않은 경로에 새 저장/요청 해제 수정을 검증한다. 제한 시간을 늘려 통과시키지 않는다. **현재 V6 전체 원본 실행 중:** sourcece5d/wrapperc013, Root session39867. V5 짧은 시험의 정확한 결과를 재확인한 뒤 기존550/900/30초·RPC3·512MiB 한도로 실행했다. 종료 후 결과를 확인하기 전에는 통과로 표시하지 않는다.
- [ ] **3. 연속성·정확도:** 전체 SDK callback과 최적화 tail, 정합·원거리 겹침·진행 방향·Stop 뒤 지도 유지·재생 일시정지·사용자 확대 유지·여러 클라이언트를 구분해 확인한다. IMU↔LiDAR identity TF는 미측정 가정이다. GPS는 원본 시각·품질이 검증된 경우에만 사용하고, 휠/가상truth/PWM/카메라는 native 위치 추정 입력으로 쓰지 않는다. 현재 Node는 capture를 닫은 뒤 destructor에서 trajectory를 끝내며 RunFinalOptimization은 호출하지 않는다. 따라서 현재 마지막 COMPLETE가 최종 최적화 tail까지 포함한다는 증거는 없다. 실제 SDK ingress/post-Add/skip 계측은 별도 새 SDK 소스로 준비하며 현재 C0 실행에 섞지 않는다.
- [ ] **4. 시뮬레이션 통합 Stop:** V8은 실제 동일 요청 ZERO ACK→SDK Stop→MAPPING_STOPPED→RAW0→정상 C0 종료를 확인했으나 마지막 owner ZERO100ms 검사에 실패했다. 종료 중복 STOP/tick 수정은 준비했다. V9은 먼저 전체 callback drain333ms 지연으로 기존100ms 검사에 실패해 종료 수정을 검증하지 못했다. 333ms가 단일 callback 시간인지는 모른다. 새 숫자 진단은 SIM/owner cgroup을 관찰하며 기준을 바꾸지 않는다.
- [ ] **5. 로컬 제품 실행과 성능:** 반복 Start/Stop·작업 상태/중단·실시간 우선순위·유한 저장소·여러 기기 접속·실제 로컬 시작 명령을 검증한다. 카메라1080p/Q70은 확인했지만 전체 센서 SIM의 decoded25.64fps/최근27.28fps는30fps 목표 미달이다. 느린 회선용 기본 전송 한도는 유지한다.
- [ ] **6. 마감:** 소프트웨어 완료와 현장 필요 항목을 구분하여 TODO·CLAUDE.md·증거·GitHub·NAS를 갱신한다. 10/10·10/03 전체 및9/10 장착 차이, GPS 품질·시각, 현장 제동·저속 제어·오프라인 GNSS 시간·원본 SSD 백업은 관측 범위에 맞게 기록한다.

### 작업 경계와 재개

실제 모터 출력·arm·펌웨어·장착 보정·새 Jetson 배포·SSH는 이번 계속 작업에서 하지 않는다. Jetson 설치 정보는 과거 관찰이며 현재 접속 확인이 아니다. Root만 실제 Docker/SDK/ROS/Chrome/컴파일/Git/NAS 작업을 수행하며 무거운 시험은 직렬로 실행한다. 병렬 작업은 소스와 종료된 증거 검토만 맡긴다. 원본·실패 결과·기존 한도·소유권과 신선도 검사는 보존한다.

최신 증거: `ROOT_WHOLE33_EOF_POSITIVE_PRESSURE_DRAIN_NEGATIVE_20261015.json`, `ROOT_JOINT9_CLOSED_FRESHNESS_NEGATIVE_20261015.json`, `ROOT_ACTUAL_PREOWNED_FIX_COMMON_AGGREGATE_JOINED_POSITIVE_20261015.json`. 새 짧은 V5는 통과했고 전체 V6는 실행 중이다. 최종 상태는 CURRENT_CHECKPOINT.json을 따른다.
<!-- /ROOT_NATIVE897_CLOSED_CURRENT_OCT15 -->

<!-- ROOT_ORDERED_GOAL_20261015_1930 -->
## 계속 진행할 목표와 최신 체크포인트 — 2026-10-15

목표는 ACTIVE다. 완료 기준은 원본 기록 끝까지 이어지는 2D LiDAR·raw IMU 지도, 마지막 지도까지 같은 대시보드에 표시, 유한 작업과 명시적 Stop, 재실행 가능한 로컬 시뮬레이션이다. 아래 순서로 진행하며 실패 결과도 보존한다.

- [ ] **1. 원본 마지막 지도 표시:** 새 native 실행 파일을 빌드했다. 짧은 원본 897개 입력의 EOF·readback은 확인했지만 공유 파일의 metadata 읽기 `errno61`로 종료되어 브라우저는 시작되지 않았다. 해당 읽기만 기존 제한 안에서 재시도하고 브라우저 PID 종료 오류를 수정한 새 소스로 재검증한다.
- [x] **가상 플러그인 첫 정지 응답:** 실제 native ZERO ACK 6.38ms, 네 바퀴의 명령값 0과 응답 일치 확인. 차량 정지거리·실차 검증이나 전체 시험 완료를 의미하지 않는다.
- [ ] **2. 가상 제어와 센서 유지:** 2초 상태 유지와 전체 정상 종료는 아직 미완료다. 최신 V5는 native peer가 연결되기 전 소유자가 종료됐다. 원인 `native_kernel_peer_not_bound`와 이전 시험을 보존했다. 가상 주행 출력·조종 활성화는 하지 않았다.
- [x] **전체 센서 화면:** V31에서 지도·카메라·LiDAR·raw IMU·가상 GPS의 일곱 브라우저 검증과 native RAW/WIRE/PENDING 0, 사용한 실행 자원 종료 확인.
- [ ] **3. 카메라 목표와 상시 로컬 실행:** 1080p·Q70, 표시 decoded 25.64fps / 최근 27.28fps. 30fps 목표와 반복 실행·통합 Stop은 아직 미완료다.
- [ ] **4. GPS 및 현장 검증:** 품질·시각·좌표 증거가 있는 GPS만 연결한다. 현재 Jetson 상태를 새로 확인하거나 신규 배포·실제 모터 출력·펌웨어·장착 보정을 수행하지 않았다.

체크포인트: `ROOT_LATEST_GOAL_CHECKPOINT_20261015_1930.json`. 아래 항목들은 당시의 이력이며 최신 완료 상태는 이 절을 기준으로 한다.
<!-- /ROOT_ORDERED_GOAL_20261015_1930 -->

<!-- ROOT_V31_CLOSED_POSITIVE_OCT15 -->
## 최신 완료 결과와 다음 목표 — 2026-10-15

목표 상태는 ACTIVE다. 로컬 V31 통합 실행에서 native 지도 엔진 정상 종료, RAW/WIRE/PENDING 0, 같은 지도와 카메라·LiDAR·raw IMU·가상 GPS의 브라우저 표시, 단일 PNG 검증 파일 저장, 사용한 컨테이너·네트워크·브라우저 종료를 확인했다. 수정한 watchdog 기본 OFF와 실제 worker 카메라 픽셀은 이 짧은 시뮬레이션 실행에서 검증됐다. 이전 V27 등의 실패를 성공으로 바꾸지 않았다.

- [x] 새 지도 통신 경로와 카메라 worker의 전체 센서 브라우저 검증: `ROOT_V31_CLOSED_CHECKPOINT_20261015.json`.
- [x] Gazebo 8.15 가상 모터 플러그인 새 빌드, 기본 OFF·라이브러리 연결·빌드 정리 확인. 가상 모터 출력과 이동은 아직 실행하지 않았다.
- [ ] 카메라 전달 성능: 실제 브라우저 decoded 25.64fps / visible RAF 25.93fps. 기본 제한은 유지하고 로컬 SIM 상세 보기에서만 12MB/s 상한을 실제 확인했다. 1080p·JPEG Q70은 유지했으며 30fps 목표는 아직 미완료다. ACK·전송 대기·프레임 크기를 분석한다.
- [ ] 가상 모터 플러그인의 실제 zero ACK, 정지 전달과 이동 검증.
- [ ] 전체 원본 기록의 마지막 지도까지 브라우저에 표시하는 수명 처리와 상시 로컬 실행.
- [ ] 검증된 GPS 시각·품질 연결 및 실차/현장 검증. 실제 Jetson 신규 배포·모터 출력은 수행하지 않았다.

현재 완료 결과는 짧은 로컬 실행의 기능 검증이다. 모든 SDK callback, 최종 최적화 지도, 실제 지형 정확도나 실차 자율주행 완료를 의미하지 않는다. 아래 체크포인트는 당시 결과로 보존한다.
<!-- /ROOT_V31_CLOSED_POSITIVE_OCT15 -->

<!-- ROOT_V30_CLOSED_POSITIVE_OCT15 -->
## 최신 완료 결과와 다음 목표 — 2026-10-15

목표 상태는 ACTIVE다. 로컬 V30 통합 실행에서 native 지도 엔진 정상 종료, RAW/WIRE/PENDING 0, 같은 지도와 카메라·LiDAR·raw IMU·가상 GPS의 브라우저 표시, 단일 PNG 검증 파일 저장, 사용한 컨테이너·네트워크·브라우저 종료를 확인했다. 수정한 watchdog 기본 OFF와 실제 worker 카메라 픽셀은 이 짧은 시뮬레이션 실행에서 검증됐다. 이전 V27 등의 실패를 성공으로 바꾸지 않았다.

- [x] 새 지도 통신 경로와 카메라 worker의 전체 센서 브라우저 검증: `ROOT_V30_CLOSED_CHECKPOINT_20261015.json`.
- [x] Gazebo 8.15 가상 모터 플러그인 새 빌드, 기본 OFF·라이브러리 연결·빌드 정리 확인. 가상 모터 출력과 이동은 아직 실행하지 않았다.
- [ ] 카메라 전달 성능: 실제 브라우저 decoded 13.40fps / visible RAF 13.41fps. 1080p·JPEG Q70은 유지했으며 30fps 목표는 아직 미완료다. ACK·전송 대기·프레임 크기를 분석한다.
- [ ] 가상 모터 플러그인의 실제 zero ACK, 정지 전달과 이동 검증.
- [ ] 전체 원본 기록의 마지막 지도까지 브라우저에 표시하는 수명 처리와 상시 로컬 실행.
- [ ] 검증된 GPS 시각·품질 연결 및 실차/현장 검증. 실제 Jetson 신규 배포·모터 출력은 수행하지 않았다.

현재 완료 결과는 짧은 로컬 실행의 기능 검증이다. 모든 SDK callback, 최종 최적화 지도, 실제 지형 정확도나 실차 자율주행 완료를 의미하지 않는다. 아래 체크포인트는 당시 결과로 보존한다.
<!-- /ROOT_V30_CLOSED_POSITIVE_OCT15 -->

<!-- ROOT_V29_CLOSED_POSITIVE_OCT15 -->
## 최신 완료 결과와 다음 목표 — 2026-10-15

목표 상태는 ACTIVE다. 로컬 V29 통합 실행에서 native 지도 엔진 정상 종료, RAW/WIRE/PENDING 0, 같은 지도와 카메라·LiDAR·raw IMU·가상 GPS의 브라우저 표시, 단일 PNG 검증 파일 저장, 사용한 컨테이너·네트워크·브라우저 종료를 확인했다. 수정한 watchdog 기본 OFF와 실제 worker 카메라 픽셀은 이 짧은 시뮬레이션 실행에서 검증됐다. 이전 V27 등의 실패를 성공으로 바꾸지 않았다.

- [x] 새 지도 통신 경로와 카메라 worker의 전체 센서 브라우저 검증: `ROOT_V29_CLOSED_CHECKPOINT_20261015.json`.
- [x] Gazebo 8.15 가상 모터 플러그인 새 빌드, 기본 OFF·라이브러리 연결·빌드 정리 확인. 가상 모터 출력과 이동은 아직 실행하지 않았다.
- [ ] 카메라 전달 성능: 실제 브라우저 decoded 11.91fps / visible RAF 11.14fps. 1080p·JPEG Q70은 유지했으며 30fps 목표는 아직 미완료다. ACK·전송 대기·프레임 크기를 분석한다.
- [ ] 가상 모터 플러그인의 실제 zero ACK, 정지 전달과 이동 검증.
- [ ] 전체 원본 기록의 마지막 지도까지 브라우저에 표시하는 수명 처리와 상시 로컬 실행.
- [ ] 검증된 GPS 시각·품질 연결 및 실차/현장 검증. 실제 Jetson 신규 배포·모터 출력은 수행하지 않았다.

현재 완료 결과는 짧은 로컬 실행의 기능 검증이다. 모든 SDK callback, 최종 최적화 지도, 실제 지형 정확도나 실차 자율주행 완료를 의미하지 않는다. 아래 체크포인트는 당시 결과로 보존한다.
<!-- /ROOT_V29_CLOSED_POSITIVE_OCT15 -->

<!-- ROOT_STOP_CORE_WORKER_CLOSED_OCT15 -->
## 현재 목표와 최신 검증 — 2026-10-15

목표는 실제 2D LiDAR·IMU 지도와 로컬 전체 센서 시뮬레이션의 안정적인 연결이다. 목표 상태는 ACTIVE다.

- [x] 새 시뮬레이션 Stop의 핵심 경로: 실제 native 정지·비동기 close·backend 종료 0·RAW/WIRE/PENDING 0·같은 지도 30초 유지 확인. `ACTUAL_STOP_CLOSE_V3_CORE_OBSERVATION.json` 참조.
- [ ] 전체 Stop 구성은 카메라 생산자 진단 파일 누락으로 아직 완료가 아니다. 앞선 실패 기록을 보존했다.
- [ ] 최우선: RPC마다 실행하던 traceback watchdog을 기본 OFF로 전환하고 새 원본 소스·실행 검증. V27에서 ACK 이후 watchdog 취소 대기가 다음 RPC 진입을 막은 증거가 있다.
- [ ] 브라우저 worker는 V27에서 40초 제한에 걸리고 최종 다운로드가 없었다. FPS·화질 성공으로 간주하지 않고 bootstrap/worker/export의 유한 오류 정보를 추가한다.
- [ ] 단일 만료형 가상 모터 명령 소유자와 zero ACK를 먼저 검증한 뒤 시뮬레이션 이동·지도 연속성을 확인한다.
- [ ] 이후 상시 로컬 실행, 전체 기록 최종 지도 표시, 카메라 전달 성능, 검증된 GPS 연결과 최종 문서를 완료한다.

카메라 원본 생산은 약 30fps지만 브라우저 30fps는 아직 검증되지 않았다. 실제 Jetson 신규 배포·모터 출력·조종 활성화는 수행하지 않았다. 아래의 이전 실패와 체크포인트는 당시 결과로 남긴다.
<!-- /ROOT_STOP_CORE_WORKER_CLOSED_OCT15 -->

<!-- ROOT_SIM_STOP_CLOSE_NEGATIVE_OCT15 -->
## 현재 작업: 시뮬레이션 정지 종료 오류 수정

목표는 ACTIVE다. 새 Stop 실행 파일을 빌드하고 같은 로컬 이미지에서 의존성과 기본 OFF를 확인했다. 실제 시뮬레이션 실행에서 대시보드 정지 요청, native 엔진 정지·Node 종료, RAW/WIRE/PENDING 0, canonical MAPPING_STOPPED, 정지 후 같은 지도 30초 유지가 관찰됐다. 그러나 마지막 `close` 통신에서 `owned_provider_EOF`가 발생해 actor 정상 종료·최종 accounting 검증과 전체 실행은 **실패**했다. 성공으로 바꾸거나 새 주행을 허용하지 않는다.

- [x] 실패 원본과 정지 단계 증거, helper/network/browser 종료를 별도로 보존: `ROOT_STOP_CLOSED_FAILURE_20261015.json`.
- [ ] 현재 우선 작업: 기존 RPC·owner·actor 제한 안에서 정리 준비와 완료 상태를 비동기로 처리하고, 새 실행으로 정상 종료를 확인한다.
- [ ] 이후 가상 차량 이동·상시 운영과 카메라 worker 전달을 검증한다. 브라우저 카메라 30fps·GPS 통합·실차 적용은 아직 완료되지 않았다.

GitHub와 NAS에는 로컬 preview 진입점과 V24/V25 결과 문서를 반영했다. 실제 Jetson 신규 설치와 실제 모터 출력은 수행하지 않았다.
<!-- /ROOT_SIM_STOP_CLOSE_NEGATIVE_OCT15 -->

<!-- ROOT_OCT15_V24_CLOSED_CHECKPOINT -->
## 2026-10-15: V24/V25 closed checkpoint and next goal

The goal remains ACTIVE. Latest V25 SIM4CPU native factory and all seven browser claims passed, with RAW accounting zero and owned processes/containers/network absent. Its native runtime closed four jobs at `native-19-19` COMPLETE. The diagnostic remains **partial/negative** solely because the initial display counter was absent; the strict Q70 graph passed. No old failure was relabelled.

V24's native factory passed: five jobs, last `native-25-25` COMPLETE, SDK RAW accounting zero, actor exit 0 and frozen sources unchanged. V24's browser trial remains **negative**: two final-current-native claims failed. Three valid same-owner observations (native3/9/18) and the retained visible canvas remain evidence from that attempt. V23's browser PASS also remains historical positive evidence.

The V24 strict Q70 graph readback passed in 2.071 s. Baseline2CPU measured 96.38% quota utilization and 191 of 200 periods throttled over 20.7659 s. V25 SIM4CPU measured 56.09% utilization and zero of 201 periods throttled over 20.7619 s. Producer rates improved to Gazebo 29.81 fps and ROS compressed 28.95 fps, while WebSocket delivery was 10.16 fps and browser decode 10.20 fps. These independently sampled rates are not a synchronous end-to-end ratio; achieved browser 30 fps remains unqualified.

The saved32-tile directory-FD reuse focus passed byte parity and four guards, but reuse was slower (0.10885/0.11201 s versus baseline 0.09423/0.08004 s). It stays default OFF and is not promoted as an optimization.

- [x] Preserve V25 native/browser PASS, diagnostic partial, V24 browser negative and all older evidence separately.
- [ ] Next: investigate the remaining camera delivery/consumer bottleneck and robust initial diagnostic counter, without changing native authority or claiming browser30.
- [ ] Complete portable local start/run/Stop and SIM movement, whole-original final GUI, and live writer/overview performance with measured evidence. Source-published preview remains runtime-unqualified until its own actual run.
- [ ] Keep GPS clock/quality and physical accuracy, ARM, deployment and hardware validation as separate unfinished goals.

Only `/scan` and `/imu/data_raw` feed native localization. Camera/GPS are display-only here. No motor/ARM/firmware/mount-calibration or original-record mutation occurred in this checkpoint. Old board/installation/status entries below are observations from their recorded dates, not current deployment verification. CURRENT/RESEARCH/GOAL retain their history and record V24 as a distinct latest attempt.
<!-- /ROOT_OCT15_V24_CLOSED_CHECKPOINT -->

Later Root closed result: V25 SIM4CPU native and all seven product-browser checks passed with RAW0 and owned cleanup. Strict JPEGQ70 graph readback passed.20.7619s quota window: GZ29.814/ROS28.947fps,56.09percent quota use,zero throttle. Browser10.201decodedfps;30fps remains open. Diagnostic overall false because display baseline counter was missing; no display rate fabricated. (SHA256 75b6dc093d4e83bbbc7b1985367e110d28c6c2da2fb66e1cc0233659a8241936).


# 매핑 작업 목표와 검증 체크포인트 — 2026-10-15

<!-- ROOT_LATEST_GOALS_OCT15 -->
## 현재 목표와 재개 순서 — 2026-10-15 (2026-10-14T16:12:36.303570+00:00)

목표는 ACTIVE이며 전체 완료가 아니다. V23에서 실제 시뮬레이션 센서→C0 SDK→같은 단일 저장 작업자→제품 브라우저 화면의 실행 검증이 통과했다. 아래 목록이 현재 상태이며 이후 절은 과거 실행 기록이다. native 위치 추정 입력은 `/scan`·`/imu/data_raw`이며 카메라·가상 GPS는 표시용이다.

- [x] **V23 실제 factory·native 종료:** 새 common recipe `a1ed8207…`·fresh actor `68f50a80…`·C0 SDK `c0c419c9…`, factory `aa5fb298…`·wrapper `9e9cd038…`·GUI V7 `73c1aa86…`를 연결했다. 실제 작업 5개, 마지막 `native-25-25` COMPLETE, RAW/WIRE/PENDING 0·actor 종료 0, helper/network/browser process 정리가 통과했다. **C0 GC_STATE는 PENDING**이므로 모든 GC가 완료됐다고 표시하지 않는다. 완료 참조 정리는 4개·참조 정리 대기 0이었다.
- [x] **V23 실제 제품 브라우저:** 일반 Chrome의 약 30초 관찰에서 1080p JPEG·LiDAR·raw IMU·가상 GPS·기존 네 WebSocket·같은 client2 native canvas·current COMPLETE 관계의 7개 검사가 모두 통과했다. 실제 native sequence 19·snapshot 4와 known cell 65,683개를 화면에 표시했다. 페이지 JSON과 1,042,417B PNG 증거를 보존했다.
- [x] **영상 전달과 제한된 parameter 확인:** 카메라 decode 8.353845fps·visible RAF 7.300728fps, LiDAR 4.791518/s·IMU 18.998202/s·가상 GPS 4.659130/s를 관찰했다. 이전 V18 decode 0.826446fps와 관찰 창이 달라 원인 비교는 별도다. 실제 parameter get에서 JPEG quality 70과 QoS `sensor_data`를 읽었지만 node/topic/format/list 발견 검사 일부가 실패해 **전체 configuration 검증은 미완료**다.
- [x] **common 환경 소스·컴파일 검증:** 1,216개 의존 파일·105 binding·11 operator, 저장 tile 32개·434,176셀·14 partial edge와 4개 chunk의 scalar/vector bit 일치가 통과했다. 새 컴파일 actor는 651,264B이며 기존 68f actor와 바이트가 같고 SDK 97파일은 보존했다. 해당 별도 byte/산술 검사를 actual runtime 성공과 구분해 기록한다.
- [x] 전체 원본 V13의 선택 센서 15,638개·custody 17,671행 EOF와 마지막 `native-719-719` COMPLETE·RAW/WIRE/PENDING 0·actor 종료 0을 확인했다. 저장된 snapshot 15의 36,222,708셀 overview는 별도 읽기 전용 profile에서 12.0979678초에 완료됐다. V13 최종 current overview/HTTP는 미확인으로 전체 harness 실패를 보존한다.
- [ ] **1순위: 로컬 실행·시작·시뮬레이션 이동 흐름.** 반복 실행 가능한 local start/run/Stop을 마련하고 시뮬레이션 이동 중 native 센서 지도와 같은 화면 관계를 검증한다. 실제 모터·조종 활성화와 구분하고 native 입력 범위를 유지한다.
- [ ] **중요 목표: 실제 카메라 30fps.** V23의 factory 종료 시점 최근 ROS 수신은 약 30.19fps였지만 브라우저 decode는 약 8.35fps이며 실제 30fps는 UNQUALIFIED이다. source/Gazebo 성능·bridge·ROS·WebSocket·decode·표시를 같은 관찰 창에서 대조한다. 일부 순간 수신률을 전체 30fps 성공으로 간주하지 않는다.
- [ ] **전체 최종 표시와 live 우선 처리.** 원본 최종 overview·공유 HTTP·브라우저를 함께 확인하고 live core loop·vector 셀 검사·저장/표시 지연을 줄인다. 큰 전체 요약은 live 영역 갱신을 막지 않도록 defer하고 기존 지도·수동 확대·Stop을 보존한다. 남은 GC 상태와 운영 화면·재시작 복구·검증 코드 공개를 마무리한다.
- [ ] **parameter readback 강화:** 실제 quality 70·QoS getter 성공을 보존하면서 짧은 발견 창에서 실패한 node/topic/format/list 검사를 안정적으로 대조한다. source 설정값과 실제 전체 configuration 확인을 구분한다.
- [ ] **별도 목표: GPS 품질·물리 하드웨어.** 원본 측정 시각·freshness·공분산/불확실성을 검증한 GPS만 위치 정합 입력으로 연결한다. ARM·현장 정확도·GNSS/RTC/USB SSD·장착 보정·실제 자율주행은 별도 검증한다.

새 Jetson 배포·실제 모터 출력·조종 활성화·펌웨어·장착 보정은 수행하지 않았다. 자율주행 출력은 OFF/HOLD이다. 재개 제어 파일은 `CURRENT_CHECKPOINT.json`, `RESEARCH_PROGRESS_LATEST.json`, `GOAL_TARGETS_CURRENT.json`, `V23_ACTUAL_POSITIVE_CHECKPOINT_20261015.json`이다. 새 factory/wrapper frozen source 백업은 `V23_FACTORY_WRAPPER_SOURCE_BUNDLE_20261015.json`에서 대조한다.

<!-- /ROOT_LATEST_GOALS_OCT15 -->

## 과거 실행 기록 — 각 기록 시점의 상태

아래 체크포인트는 당시의 관찰과 실패를 보존한 이력이다. 현재 작업 순서는 위 목록을 따른다.

<!-- ROOT_V23_PREVIOUS_GOALS_HISTORY -->
## 이전 목표 기록 — V23 결과 반영 전

아래 목록은 당시의 미완료 상태와 실패를 보존한 기록이다. 현재 순서는 위 최신 목록을 따른다.

## 현재 목표와 재개 순서 — 2026-10-15 (2026-10-14T15:23:35.487671+00:00)

목표는 ACTIVE이며 전체 완료가 아니다. 실제 2D LiDAR·IMU 기반 지도를 원본 기록 끝까지 생성하고, 로컬 시뮬레이션의 같은 단일 저장 작업자에 연결해 대시보드에서 계속 표시한다. 아래 목록이 현재 상태이며 이후 절은 과거 실행 기록이다. native 위치 추정 입력은 `/scan`·`/imu/data_raw`이다.

- [x] **전체 원본 V13 native 처리·종료:** 선택 센서 15,638개와 custody 17,671행의 EOF·수신 바이트/순서가 정확히 일치했다. 마지막 `native-719-719` COMPLETE, 완료 작업 15개·snapshot 15, 실제 참조 정리 85개·정리 대기 0, RAW/WIRE/PENDING 0·actor 종료 0, helper/network 정리를 확인했다. 모든 SDK callback·최종 최적화 tail·물리 지형 정확도는 별도 미검증이다.
- [ ] **V13 전체 GUI 완료:** 최종 current overview를 native 종료 전에 확인하지 못해 HTTP는 시도되지 않았다. V13 전체 harness는 실패로 보존한다. native 처리 성공과 화면 성공을 구분한다.
- [x] **종료된 V13 저장 지도 성능 관찰:** snapshot 15의 36,222,708셀·2,396 source row·18 overview tile을 12.0979678초에 처리했다(Root 전체 관찰 15.0926306초). 기존 30초 제한을 통과했지만 저장된 자료의 읽기 전용 phase 관찰이며 실제 SDK·native runtime·브라우저 실행은 없었다. 기존 focused 검사는 반복하지 않았다.
- [x] **새 후보의 실패 보존:** V21은 8.62초에 native JPEG profile v1/v2 schema guard 불일치로 중단했다. ROS·Node·브라우저는 시작되지 않았고 helper/network는 정리됐다. 카메라 속도나 UI 실패를 검증한 실행은 아니다.
- [x] **후속 소스 준비:** 제품 GUI V6, native ROI의 overview/fragment 독립 갱신, JPEG kit V2의 수정된 remap·품질 parameter 후보를 frozen source로 보존했다. 이 후보들의 실제 제품 화면·frame/parameter 검증은 아직 완료가 아니다.
- [x] 이전 원본 30초 V12·prefetch·두 HTTP 클라이언트와 실제 ROI 브라우저 V4에서 독립 자동 갱신·수동 확대 상태·선택 영역 유지가 통과했다. V17의 여섯 센서·1080p native runtime 종료와 실제 소프트웨어 Stop·기존 canvas 보존 결과도 보존한다. 이 결과를 새 제품 GUI 후보 검증으로 간주하지 않는다.
- [ ] **1순위: 실제 live 성능과 셀 검증 비용.** 새 phase 관찰을 근거로 live 저장·읽기·vector cell validation 비용을 줄이고 같은 작업자의 지연·대기·중지와 결과 일치를 확인한다. 저장 자료 profile 성공만으로 live 처리나 GUI 성능 완료를 표시하지 않는다.
- [ ] **2순위: V22 실제 제품 UI·영상·parameter.** 최소 schema guard 수정 후보의 actual 실행을 확인하고 native canvas·같은 current COMPLETE 관계·1080p frame·remap·품질 parameter를 함께 검증한다. GUI V6와 native ROI fragment 독립성은 source-only 상태이며 actual 검증이 남아 있다. JPEG 전달 개선·실제 30fps는 UNQUALIFIED이다.
- [ ] **3순위: 전체 원본 최종 GUI와 운영 화면·공개.** 최종 overview·두 HTTP 클라이언트·실제 브라우저를 함께 확인하고 Stop 완료 문구·여러 기기 접근·재시작 복구를 검증한다. 확정된 결과에 맞춰 TODO·체크포인트·GitHub·NAS를 동기화한다.
- [ ] **별도 목표: GPS 품질·물리 하드웨어.** 원본 측정 시각·freshness·공분산/불확실성을 검증한 GPS만 위치 정합 입력으로 연결한다. ARM·현장 물리 정확도·GNSS/RTC/USB SSD·장착 보정·실제 자율주행은 별도 검증한다.

이번 후보의 새 Jetson 배포·실제 모터 출력·조종 활성화·펌웨어·장착 보정은 수행하지 않았다. 자율주행 출력은 OFF/HOLD이다. 재개 제어 파일은 `RESEARCH_PROGRESS_LATEST.json`, `GOAL_TARGETS_CURRENT.json`, `ROOT_CURRENT_PROGRESS_20261015.json`이며 새 frozen source 백업은 `OCT15_UI_CAMERA_PHASE_SOURCE_BUNDLE.json`에서 대조한다.
<!-- /ROOT_V23_PREVIOUS_GOALS_HISTORY -->

<!-- ROOT_WHOLE_NATIVE_CLOSED_OCT14 -->
원본 지도 처리 완료 확인 (2026-10-14T12:48:06.298225+00:00): V11에서 원본 17,671 custody 행과 선택 센서 15,638개(5,243 scan·10,395 IMU)의 EOF·별도 수신 바이트/순서를 대조했다. 마지막 실제 지도 작업 `native-740-740` COMPLETE, 실제 SDK RAW/WIRE/PENDING 0, actor 종료 0, 정리 후 helper/network 부재를 확인했다. 공간 정리 후 실제 작업 완료로 복구했다. 각 SDK sensor callback 계수·최종 최적화 tail 전체·물리 지형 정확도는 아직 별도 미검증이다.

- [x] 원본 입력 EOF와 마지막 실제 SDK 지도 작업 완료·영구 저장·대기 데이터 0·정상 종료.
- [x] 실제 FINISH 공간 부족→동일 RAW 새 세션 재전송→정리·ACK·정상 종료의 별도 읽기 전용 대조 통과. micro 보고서 변수 오류와 첫 observer 오류는 보존했다.
- [ ] **현재 1순위: 큰 지도의 표시 속도.** 최종 전체 지도 요약 또는 HTTP 확인이 실패해 V11 전체 harness는 실패로 유지한다. 저장 지도 완료와 표시 완료를 합치지 않는다.
- [ ] **현재 2순위: 시뮬레이션 전체 센서 대시보드.** native 지도 정상 종료는 통과했다. 1080p·30fps 요청 설정의 실제 카메라, LiDAR·IMU·가상 GPS·동일 작업자 지도 표시를 실행 중이다.
- [ ] **현재 3순위: 실제 브라우저 갱신 검증.** V3 시험에서는 새 지도 46→47의 수동 확대 좌표가 정확히 유지됐지만 예상 wheel 좌표의 차이로 전체 시험 실패. 실제 이벤트 좌표를 기록하는 별도 V4 시험 후보를 준비했다. 실제 화면 증거는 보존했다.

목표 ACTIVE. 이번 후보의 Jetson 배포·모터·조종 활성화·펌웨어·장착 보정은 수행하지 않았다.
<!-- /ROOT_WHOLE_NATIVE_CLOSED_OCT14 -->

<!-- ROOT_SIM_NATIVE_CLOSED_OCT14 -->
실행 체크포인트 (2026-10-14T12:16:21.859324+00:00): 시뮬레이션 V9의 실제 센서→C0 SDK→단일 저장 작업자 경로가 종료 0으로 끝났다. 실제 native 지도 작업 6개, 마지막 `native-22-22` COMPLETE, 최종 RAW/WIRE/PENDING 0, 현재 지도·완료 작업 관계 및 helper/network 부재를 확인했다. 공개 대시보드 전체 센서 화면이나 새 브라우저 렌더링 완료를 뜻하지 않는다. 각 센서 callback 개수는 SDK에 계수가 없어 미검증이다.

- [x] 공통 실행 환경에서 실제 시뮬레이션 SDK 지도 생성·영구 저장·최종 대기량 0·정상 종료.
- [ ] 실제 브라우저와 전체 센서 대시보드 연결. 지도 영역 자동 갱신·수동 확대 유지 확인.
- [ ] 긴 원본 지도 완료. 재시도 micro V3은 실제 공간 부족→정리→같은 RAW의 새 세션 재전송→영구 ACK·RAW 0까지 진행했으나 종료 시 폐기된 이전 세션 기록이 남아 실패했다. 종료 검사를 완화하지 않고 폐기된 비영구 전송 상태만 정리하도록 수정한다. V2/V3 실패 증거는 그대로 보존한다.

Jetson 새 배포·실제 모터·조종 활성화·펌웨어·장착 보정은 수행하지 않았다. 목표는 ACTIVE다.
<!-- /ROOT_SIM_NATIVE_CLOSED_OCT14 -->

<!-- ROOT_FINISH_RETRY_COMMON_IMAGE_OCT14 -->
최근 확인 (2026-10-14T12:03:24.044700+00:00): V9 긴 기록 실행은 센서 EOF 전에 중단했다. 실제 오류는 공간 부족으로 FINISH가 DEFER된 뒤 이미 닫힌 전송 세션을 다시 FINISH한 것이며, C++가 원본 RAW를 유지한 채 새 세션으로 재전송하도록 수정한 후보를 새로 컴파일·검증한다. 실패 결과와 이전 지도를 보존했다. VIEW 상태 경합의 별도 Linux 검사는 통과했다.

- [ ] **즉시 목표 1:** FINISH 재시도 후보를 새 C++로 컴파일하고, 실제 공간 부족→정리→같은 RAW 재전송→영구 ACK를 확인한다. 이후 긴 원본의 EOF·최종 지도·RAW 0·정상 종료를 검증한다.
- [x] **시뮬레이션 준비:** 공통 이미지의 필수 라이브러리와 기본 비활성 시작 검사 통과. 기존 이미지는 보존했다.
- [ ] **즉시 목표 2:** 공통 이미지의 실제 센서→SDK 지도 실행. V7은 실제 시작 후 `map_builder.lua` 누락으로 실패했으며 누락 설정을 보완한다. 센서 생성 수를 SDK 수신 성공으로 간주하지 않는다.
- [x] **화면 후보 검사:** 전체 지도 요약과 영역 갱신을 분리한 JS 검사 8개 통과.
- [ ] **즉시 목표 3:** 위 화면 후보의 실제 공유 작업자·브라우저 연결, 전체 센서 대시보드, Stop 상태·확대 유지 확인.

이 세 목표는 모두 완료가 아니다. Jetson 새 배포·실제 모터·조종 활성화·펌웨어·장착 보정은 수행하지 않았다. TODO와 실패 증거를 보존하며 이어간다.
<!-- /ROOT_FINISH_RETRY_COMMON_IMAGE_OCT14 -->

추가 실행 결과 (2026-10-14T10:54:28.707284+00:00): 최대 본체 시험의 별도 읽기 전용 대조 통과. 큰 저장 지도 계산은 25.5→16.8초, 결과 값 일치 및 16개 오류 처리 검사 통과. 다만 전체 기록 동시 처리에서는 지도 요약 제한을 다시 넘었고, V8 actor는 마지막 작업 `native-760-760`의 상태를 실패로 판단해 종료 1이었다. 원본 센서 EOF는 정확히 확인했지만 최종 RAW 0·정상 종료·전체 지도 완료는 미검증이다. 실행 중 VIEW 작업에 들어온 저장 압력 알림의 상태 경합을 수정 중이다. SIM V5는 초기 준비 후 Node 생성까지 진행했지만 native 지도 작업이 없어 실패했다. 센서 관찰 수치는 SIM 측 관찰이며 실제 C0 입력 수치가 아니다. 동일 ROS 실행 환경의 필수 의존성을 준비한다.

현재 근거: `native-maximum-body-incoming-pressure-micro-v3/output/RESULT.json`, `native-C0-served-browser-stop-v1/CLOSED_BROWSER_STOP_JOIN.json`, `fullfield-C0-integrated-v6r2/POSTCLOSED_PARTIAL_OUTCOME.json`, `RESEARCH_PROGRESS_LATEST.json`. 실차 모터·조종 활성화·펌웨어·장착 보정·원본 변경 및 새 Jetson 설치는 하지 않았다.

<!-- ROOT_STOP_AND_OVERVIEW_OCT14 -->
후속 체크포인트 (2026-10-14T08:51:09.037307+00:00): 전체 목표는 ACTIVE이다.

- [x] 전체 원본 V4에서 저장 압력 복구를 직접 관찰했다. 실제 공간 회수 후 4회 재시도했고 마지막 수락 작업 `native-870-870`은 완료됐다. 전체 센서 EOF도 통과했다.
- [ ] V4의 전체 성공은 아니다. 약 3,365만 셀의 overview가 준비 시간 안에 완료되지 않아 검증 도구가 종료했다. 최종 RAW 0·정상 actor 종료·모든 Node callback·최종 최적화 tail은 이 실행에서 증명하지 못했다.
- [x] 기본 비활성 고속 셀 검증 후보: 555개 비교, 실제 지도 본체 4개의 471개 청크 일치. 초기 로딩 0.46초. 전체 처리 성능 개선을 뜻하지 않는다.
- [x] 현재 보존 구조의 고속 overview 후보: 좌표·셀 값·저장 결과·Stop 보존에 대한 3개 검증 통과. V6r2 전체 실행에서 센서 EOF와 마지막 수락 작업 완료는 통과했지만 전체 지도 요약의 준비 제한은 통과하지 못했다. 수신 중 용량 부족 알림·정리와 큰 지도 계산을 추가 보완한다. 기존 HELLO 3초·overview 30초·저장 512MiB 한도는 유지한다.
- [x] 로컬 실제 C0 SDK Stop: 대시보드 client 1 요청, client 2 제어 거절, 실제 Runtime 정지 → Node 종료 → RAW/WIRE/PENDING 0 → canonical MapStop → 정상 종료 및 남은 지도 데이터 대조 통과. 15개 별도 단위 검증도 통과했다. 의도적으로 원본 일부만 재생한 Stop 시험이며 전체 원본 EOF 시험은 아니다.
- [ ] Stop 후 같은 브라우저의 canvas가 유지되는 실제 화면 검증과 큰 backlog에서의 Stop 지연 검증은 남아 있다.
- [x] SIM 시간 지원 새 C++ 프로그램 빌드 통과. 새 ELF `44a5dc71dbcf7916e7432948048bc874c97c120df88b74bc298ec6cc033ac8a3`, C0 SDK 링크 검증. 아직 시뮬레이터·SDK runtime·웹 센서 화면 실행 성공은 아니다.
- [ ] V6r2 전체 재생 결과 확정 후 시뮬레이터와 새 SDK 프로그램, 공유 지도·센서 대시보드 연결을 실행 검증한다.

재개 위치: `RESEARCH_PROGRESS_LATEST.json`, `fullfield-C0-integrated-v4/POSTCLOSED_PARTIAL_OUTCOME.json`, `native-C0-software-stop-probe-v1/POSTCLOSED_HANDOFF.json`, `sim-C0-actor-build-v1/output/COMPILE_RESULT.json`. 이 로컬 후보를 Jetson에 새로 설치하지 않았으며 실제 모터·조종 활성화·펌웨어·장착 보정은 수행하지 않았다.
<!-- /ROOT_STOP_AND_OVERVIEW_OCT14 -->


<!-- ROOT_WHOLEFIELD_OCT14 -->
전체 원본 재생 체크포인트 (2026-10-14T08:10:42.065464+00:00): 목표는 실제 2D LiDAR·IMU 기반 연속 지도, 단일 저장 작업자와 공유 웹 화면, 안전한 Stop·유한 저장소, 로컬 시뮬레이션 연결을 실행 검증하는 것이다. 목표는 ACTIVE이며 완료가 아니다.

- [x] 전체 원본 17,671행의 custody와 선택한 15,638개의 센서 CDR(5,243 scan·10,395 IMU)을 EOF까지 재생하고 별도 DDS 수신의 순서·바이트를 대조했다. 모든 SDK callback이나 최종 지도 완료를 뜻하지 않는다.
- [x] 실제 C0 SDK 지도 snapshot 8을 같은 저장 작업자의 웹 API에서 받아 격리된 Chrome으로 overview와 원본 해상도 선택 영역을 렌더링했다. 별도 DB·합성 지도 이미지를 사용하지 않았다.
- [x] 이전 GC가 SQL 작업자를 오래 점유하던 문제를 수정하고 독립적인 3개 검증을 통과했다. 긴 재생은 이어 진행했지만 다음 저장 압력 문제를 드러냈다.
- [ ] 전체 지도 완료: V3은 native-308-308에서 DEFERRED/disk_byte_budget로 멈췄다. 센서 EOF 성공과 지도 실패를 별도로 보존했다.
- [ ] 저장 압력 중에도 안전한 GC를 실행하고 실제 공간 회수 이후에만 제한된 재시도를 허용한다. 종료된 저장소 분석에서 보존 데이터 추정치 약 282MiB는 512MiB 한도보다 작아, 무조건 용량을 늘리지 않는다. 미확정 결과·원본·현재 지도는 보존한다.
- [ ] 기본 비활성 NumPy 셀 검증 후보의 정확도·오류 순서·시작 지연을 별도로 검증한다. 후보 작성만으로 실시간 성능 개선을 주장하지 않는다.
- [ ] 수정 후 전체 원본 재검증, 실제 SDK Stop, 로컬 시뮬레이션 공통 지도·센서 UI를 순서대로 실행한다. GPS는 원본 측정 시각 검증이 부족해 위치 정합 입력이 여전히 비활성이다.

재개 증거: `fullfield-C0-integrated-v3/POSTCLOSED_PARTIAL_OUTCOME.json`, `RESEARCH_PROGRESS_LATEST.json`. 이번 후보는 Jetson에 새로 설치하지 않았고, 실제 모터 출력·조종 활성화·펌웨어 변경·장착 보정도 하지 않았다.
<!-- /ROOT_WHOLEFIELD_OCT14 -->

갱신 시각(UTC): 2026-10-14T06:02:16.402823+00:00

목표는 휠 엔코더 없이 실제 2D LiDAR·IMU로 연속 지도를 만들고, 같은 저장 작업자의 지도 데이터를 로컬 시뮬레이션 대시보드에서 표시하는 것이다. GPS는 원본 시각과 품질이 검증된 경우에만 연결한다. 원본 녹화, 기존 서비스, 미검증 실차 모터 제어는 이 로컬 검증과 구분한다. 전체 목표는 진행 중이다.

## 이번에 직접 확인한 범위

- 30초 원본의 300개 LiDAR scan과 597개 IMU 메시지를 재직렬화·시각 변경 없이 실제 SDK Node/Grid로 보냈다. 897개 EOF와 별도 DDS 수신 바이트 대조가 통과했다. Node의 모든 callback 처리와 최종 최적화 tail까지 증명한 결과는 아니다.
- 지도 셀을 단일 SQLite/CAS 저장소에 저장하고 실제 C++ 요청·확인 응답·해시·최종 지도 관계를 대조했다. 마지막 지도 작업 완료, RAW/wire/pending 0, 실행 프로세스·테스트 컨테이너·전용 네트워크 정리를 확인했다.
- Windows `127.0.0.1:18891`에서 두 HTTP 클라이언트의 접속을 확인했다. 격리된 Chrome 실행에서는 같은 저장 작업자의 overview와 원본 해상도 지도 셀 124,531개가 렌더링됐다. 별도 DB 복제나 미리 만든 지도 이미지를 표시한 결과가 아니다. 증거 이미지 SHA256: `02f940ab98dfcdf56328df797a1be98f5310047127d91cdbc79a65bfd25a8c4f`.
- 새 C0 SDK로 별도 C++ 프로그램을 빌드해 합성 Frame 세 개를 실제 Runtime과 저장 작업자에 전달했다. 현재·이전 Frame은 보존되고, 오래된 Frame 하나만 원본 요청·확인 응답과 일치하는 두 단계 COMMITTED 사건으로 참조 회수됐다. 실제 센서나 파일 삭제 시험은 아니다. SDK SHA256: `c0c419c9b0be1ae019551835ef08960701d7b78b24bbea796ab449af1e4dc5fa`.

## 실패 결과와 보완

상태 파일의 교체 순간을 exists/stat/read로 나눠 읽던 검증 스크립트를 단일 읽기와 기존 시간 제한으로 보완했다. 브라우저 검증이 HTTP 응답에 없는 비공개 접속 URL까지 요구하던 오류도 수정했다. 후속 SQL 대조에서는 거절된 DEFERRED 요청과 실제 수락된 요청을 함께 세던 조건을 바로잡았다. 기존 실패 결과를 덮어쓰지 않고, 종료된 저장소에 대한 읽기 전용 대조로 실제 수락 요청과 지도 관계를 별도로 검증했다.

C0 참조 회수 프로그램 자체는 종료 0이었으나, 검증 스크립트가 정상 진단 stderr를 전부 거절해 전체 실행 결과는 실패로 남겼다. 실제 130개 진단 줄의 순서, 원본 요청·확인 응답·COMMITTED 행, 최신·이전 지도 보존을 읽기 전용으로 재확인했다. 동일한 성공 실행을 다시 빌드하거나 실행하지 않았다.

## 남은 소프트웨어 작업

- [ ] 사용자 확대·드래그 및 선택 영역을 새 지도 갱신 뒤에도 유지하고, 작업 진행 상태를 공유 UI에서 확인한다.
- [ ] 대시보드의 지도 Stop 요청을 실제 C++ Runtime 정지와 연결하고, 이미 저장된 화면을 유지한다. 현재 확인한 화면은 읽기 전용이며 Stop 버튼은 비활성이다.
- [ ] 정확한 COMMITTED 참조 회수와 독립적인 읽기·현재 지도·진행 작업의 보존 조건으로 오래된 CAS 파일을 회수한다. 아직 파일 삭제나 장시간 용량 유지까지 검증되지 않았다.
- [ ] 유한 스트리밍 MCAP/원본 ledger 입력으로 전체 현장 기록을 EOF까지 재생하고 지도 연속성·지연·CPU·메모리·디스크를 검증한다. 짧은 원본 결과를 전체 기록 결과로 간주하지 않는다.
- [ ] 새 SDK의 실제 관찰 시각, 원본 GNSS 측정 시각·품질, 시뮬레이션과 대시보드 공통 연결을 완성한다. 전달 시각을 센서 측정 시각으로 대체하지 않는다.

실차 이동 시험, 장착 방향·기울기 보정, ARM 장시간 성능, 전류·전압·제동 검증, 실외 GNSS·RTC·USB 원본 백업은 별도 하드웨어 항목이다. 이 결과로 실차 자율주행을 검증했다고 표시하지 않는다. 이번 로컬 후보를 Jetson에 새로 설치하지 않았다.

## 재개 위치

- SSD 작업 루트: `D:\Develop\UBTS\codex-resume-20261013`
- 목표: `GOAL_TARGETS_CURRENT.json`
- 최신 진행: `RESEARCH_PROGRESS_LATEST.json`, `CURRENT_CHECKPOINT.json`
- 원본 짧은 재생: `native-sdk-node-assembly-v9/POSTCLOSED_HANDOFF.json`
- 브라우저 및 종료 후 대조: `same-owner-browser-postclosed-readback-v1/POSTCLOSED_HANDOFF.json`
- C0 참조 회수: `native-retirement-backend-probe-v1/READONLY_POSTCLOSED_HANDOFF.json`

비공개 접속 토큰·실행 argv·원본 센서 자료는 이 공개 문서에 넣지 않는다. Jetson 대시보드는 오래된 Compose로 재생성하지 않는다. 실차 상태는 현재 시점의 직접 조회 없이 과거 점검 결과로 단정하지 않는다.
