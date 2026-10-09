# 2026-10-10 원본 LiDAR 지연과 IMU bracket 계측

현재 v9 복구 설치와 실제 시험은 [후속 기록](NATIVE_INPUT_RECOVERY_PROGRESS_20261010.md)에 있다. 아래 결과는 이전 v7 체크포인트다.

Jetson 관찰 서비스에 v7 계측을 설치했다. **지속 입력, GPS 통합 지도와 실제 자율주행은 미완료다.** 이전 v6 설치와 정상250쌍 뒤 IMU gap 실패는 [이전 시각 계측](NATIVE_ADMISSION_PROGRESS_20261010.md)에 보존한다. 이번 변경은 모터 명령·장착/제동 보정·센서 driver를 변경하지 않았다.

현재 이미지: `sha256:94dd2b79c24b7c8cb02958f5a75edcb56735e678e9a882de5fa01da7ed5d6d06`.
패키지 HANDOFF: `1af864127cf4ab8c0f30f8142f93d32f16bd3d7a3145534008aafd2288b2b883`.
source inventory: `33d211aa25243a9478a75998e941031452eb629775073beef658ecfcf2418897`.

## 변경과 검증

실패한 IMU bracket의 이전/현재 스캔 경계, 원본 IMU Header/RAW receipt, 검증에 사용한 clock과 gap을 최대1024B로 보존한다. 새 clock을 샘플링하지 않고 기존60ms gate와 CDR/ownership/GID/Stop/시각 검사를 유지한다. gap gate 뒤의 수치군 검증을 통과한 증거는 아니므로 `numeric_groups_validated=False`를 명시한다. v7 revision/parent와 두 timing-only flag를 strict loader에서 확인한다.

고정 후보67개와 실행기·로더66개 시험이 각각 skip 없이 통과했다. ARM 빌드83.8초, 동일 C++ binary 재사용 및 기본 비실행/source/binary/package 검사를 확인했다. 기존13서비스의 구성·시작시각·PID와 저장 지도470 scans/queue0을 유지했다. 이전99cd sidecar는 보존했다. SIM runtime은 변경하지 않았다.

## 실제 세 차례 시험

각 시험은 별도60회 상태 조회다. 성공 구간을 합산해 지속 처리로 표시하지 않는다.

| 시험 | 정상 정합 | 첫 실패와 측정 범위 |
| --- | ---: | --- |
| 첫 시험 | 0 | startup Header age422.779ms, Header→callback388.662ms; callback RAW age34.056ms/SYSTEM age34.118ms. 원본 Header만250ms 제한 초과 |
| 재시험 | 0 | 마지막 association의 `stale_original_causal_imu`; fit20.872ms/prepare31.688ms. 정확한 causal IMU age는 이 실패 지점에서 아직 미측정 |
| 별도 gap 포착 시도 | 2 | CURRENT Header age373.630ms, Header→callback363.634ms; callback RAW age9.981ms/SYSTEM age9.996ms. 원본 Header만250ms 제한 초과 |

첫 시험과 마지막 시험은 모든 측정 age가 양수이고 Header가 callback보다 앞섰다. 수신 callback에 도달했을 때 Header가 이미 오래됐다는 관측이다. driver·DDS 중 지연 원인은 별도 소스 검토가 필요하며 계산 속도만으로 해결된다고 주장하지 않는다. 이번 v7 시험에서는 IMU gap보다 다른 시간 검증이 먼저 실패해 gap 수치는 아직 없다. 기존 v6의250쌍 뒤 gap 실패를 이번 버전의 검증으로 합치지 않는다.

세 시험 모두 적용 좌우0·authority false, exit0·강제kill 없음·queue0·readers joined/resources closed·owner 해제를 확인했다. protocol qualification은false다. 대시보드의 관찰 시작/정지는 설치되어 있으나 실제 자율주행 시작은 검증 대기다. fault 뒤 명시적인 관찰 정지와 owner 해제를 확인한다.

## 다음 수정

- [ ] 완전한 원본 geometry·endpoint IMU group·clock/source/GID 검증 후 데이터 누락만 typed WAIT으로 처리한다. old pending/owned/prepared/proposal을 폐기하고 새 원본 스캔2개와 정상 IMU bracket으로 다시 기준을 잡는다. gap을 넘는 보간과 Stop/fault 자동 해제는 금지한다.
- [ ] 검증된 양의 startup/CURRENT 시간 만료도 stale 원본을 버리고 대기한다. 미래/역행·clock jump·손상·출처 변경은 계속 latched하며250/200ms 및60ms 한계를 늘리지 않는다. later factory/association 만료는 별도로 검토한다.
- [ ] LiDAR publisher Header 생성과 observer QoS의 지연 원인을 read-only로 검토하고 반복 fit/fallback 시간을 개선한다.
- [ ] default-OFF GNSS 위치 factor와 독립 geometry witness 후보를 실제 receiver clock/quality 경로 및 완성된 지도 generation commit에 연결한다. 현재 후보 합성 테스트는 실제 GPS/전역 지도 정확도의 합격이 아니다.

최신 private 실행 포인터는 `native-navigation-20261007/current-native-imu-gap-run.txt`다. 원래 실패·세 시험·빌드·설치·고정 후보·Git/CAS 체크포인트를 보존한다. GitHub Actions를 쓰지 않고 직접 SSH를 사용했다. 현재 driver20Hz, SOURCE250ms/RAW200ms, IMU60ms는 그대로다.
