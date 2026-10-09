# 2026-10-10 receiver 만료 복구 설치·LiDAR 최적화 ARM 검토

Jetson의 관찰 서비스에 receiver 만료 복구를 설치했다. nearest 최적화는 ARM 성능 결과가 혼합돼 OFF로 보류했다. **GPS 통합 지도와 실제 모터 자율주행은 미완료다.** 바퀴 전원 OFF라는 사용자 설명에 따라 실제 자동 모터 명령과 장착·제동 보정을 변경하지 않았다. 요청한2시간은 초과했다.

- 이미지: `sha256:2f4515f3b6e0be15d4ad611a00991eba80910e4fd14af402563a7a502bffd34d`
- 패키지: `68bd1239327ca8701004fd284484223b1ddfabd9105ace933ee8ead9ec1ed5a7`
- source inventory: `44ee2ce17a85f04ff2932060f743e491116734c9948726675c0a140fd2587b69`

## 설치 내용과 검증 범위

기존0.30/0.25m 대응점 게이트·nearest query·fit 계산을 유지한다. 범위를 제한하는 nearest 후보는 수치 동일성이 통과했으나 합성 full-path p95 OFF/ON/ON/OFF74.744/75.799/81.502/74.954ms로 개선되지 않아 설치하지 않았다. 원본SIM18개 비교도 전체 p95가 혼합됐다. 센서 SOURCE250ms/RAW200ms, IMU bracket60ms와20Hz driver는 그대로다.

receiver consume 시작의 기존 비교 시각에서 선택된 causal IMU의 RAW만 양으로 만료되면, 전체 원본·수치 그룹·현재 문맥·owned capability·Stop 검사를 통과한 경우에 한해 해당 계산을 버리고 새로운 원본 두 스캔과 IMU를 기다린다. SOURCE 만료·미래·손상·clock 변경·Stop·이미 기록된 fault는 자동 해제하지 않는다. 기존 최종 consume 이후 만료 복구도 유지한다. 생성자 기본은OFF이며 완전한 패키지 검증 뒤에만 다섯 복구 옵션을 전달한다. nearest 옵션False는 constructor에 전달하지 않으며 구버전 호환을 유지한다.

독립 receiver 회귀146개는PASS했다. ROOT 통합 시험은 stream119개와 실제 생성자·원본 NDJSON wiring9개가 각각PASS했다. 서로 중복되는 suite를 고유 테스트 총합으로 더하지 않는다.

ARM 비교의 상세 범위와 숫자는 `ARM_PROOF_BINDING.json`에 보존한다. 원본 SIM CDR18개와 합성 fixture의 결과 동일성을 검증했으며, 선택된 SIM 입력을10월3일 실차 지형이나 전체 지도 정확도 합격으로 표시하지 않는다. 이전 소유 staging 권한 실패와 SciPy 비유한 직접 query의 플랫폼별 예외 차이도 보존한다. 비유한 실제 scan 입력은 기존 원본 validation/scan_points에서 차단·unknown 처리하며 operative NN 수치 소스는 그대로다.

## 실제 입력 시험

60회 상태 조회에서 정합195회를 관측했다. 지속 native 입력 판정은 **False**이다. 최초 실패는 `ValueError: physical_shadow_final_fence_failed:final_physical_native_receipt_deadline`이다. 판정에는 같은 owner/session/generation/PID의 최소200새 정합·25초 지속 진행·원본 clock/age·최대2초 진행 간격·정상 종료를 요구한다. API 실행·정리 통과와 지도·실차 정확도는 서로 다른 결과다.

새 receiver 만료 대기는 실제1회 발생했다. 정합146 시점의 원본 IMU RAW202.190ms/SOURCE205.277ms에서 계산을 폐기하고 동일 세션의 정상 상태 조회에서 정합193까지 증가를 확인했다. 최종 정합 총수는195였다. 그 다음 최종 scalar 검사에서 causal IMU RAW200.107ms/SOURCE202.835ms로 중단됐다. 이 최종 검사 단계의 exact positive RAW 만료 처리는 아직 별도 수정 중이다. numeric11.985ms/prepare24.704ms였고, IMU gap 대기와 post-consumed 대기는 모두0회였다.

적용 좌우0, exit0, kill없음, queue0, reader/pipe/owner 종료와 최종 inputNone을 확인했다. 기존13서비스와 저장된 지도470스캔을 유지했다. 현재 정합이 평면 laser 상대 움직임을 계산하는 것만으로 검증된 body속도나 자율주행 실행 권한을 만들지 않는다.

## GPS 및 기록 재점검

실제 GPS 원본 Fix24개와 receiver24개를 수신했고, 원본 CDR·GID·연속 seq·정상 Stop을 확인했다. 모두 `no_fix`/위성0이었다. 새 date/time/resolved/fraction/timeaccuracy 필드는 존재하지만 reported tAcc는 약0.274초이며 측정 clock/association은 미검증이다. 위치 factor0을 유지한다. 이 단일12초 미만 관찰 후 전용 프로세스/컨테이너를 정리했다.

전체10월3일7기록을 다시 읽어 기존 UTC 중복 제거와 iTOW 포함 후보를 비교했다. 양쪽 독립 epoch는 모두3,127개였다. 이 기록에서 기존 UTC 방식의 위치 손실을 입증하지 못했다. 높은 수신 주기를 위한12개 pure 시험은 통과했지만 현재 개선 효과가 관측되지 않아 후보만 보관했다. 기존 두 기록의 host−receiver UTC 약−7,880초 관측과 전체 원본 시각/ID는 유지한다.

## 남은 작업

- [ ] 최종 scalar 검사의 정확한 양의 causal IMU RAW 만료를 fault 전에 계산 폐기로 처리하고 모든 다른 검사·Stop을 유지한다.
- [ ] GPS 원본 측정 epoch/quality·clock·독립 geometry를 완성 지도 generation에 연결한다. no_fix를 만들어 보정하지 않는다.
- [ ] native 누적 지도와 지역별 원본 cell age, 실시간 웹 표시·다중클라이언트·중단을 연결하고 지속 시험한다.
- [ ] 관측된 열끝·다음열 경로 proposal을 실제 입력에 연결한다. 미관측 영역으로 마지막열을 단정하지 않는다.
- [ ] 전체10월3일·9월10일 지형/offset/가지/벽·새 과수원과 현재 차체 방향·기울기·drift/진동/제동을 검증한다.
- [ ] 실차 모터 자율주행은 별도 물리 검증이 필요하다. 전원OFF 상태로 제동거리·실제 속도 검증은 완료할 수 없다.

재개 포인터: `native-navigation-20261007/current-native-strict-nearest-run.txt`. 이전 실패와 검증은 [v10](NATIVE_POSTCONSUMED_RECOVERY_PROGRESS_20261010.md)에 보존한다. GitHub Actions 없이 직접 SSH로 설치했다.
