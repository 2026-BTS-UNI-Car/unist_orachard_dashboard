# 2026-10-10 원본 센서 누락·시간 만료 처리

Jetson의 관찰 서비스에 v9를 설치했다. **GPS 통합 지도와 실제 모터 자율주행은 아직 미완료다.** 실제 바퀴 전원 OFF는 사용자 설명이며 원격 측정한 값은 아니다. 설치와 시험은 자동 모터 명령 및 장착·제동 보정을 변경하지 않았다.

현재 이미지: `sha256:02e15f1ea0137b10408a15b6b7b9f94b2aa44b28d754f0f848758d5e9f2a4634`.
패키지 HANDOFF: `42e46534f700cacdce08ea9aee64b3fe9667f6bb35a297b99182507d1afd12a1`.
source inventory: `c352304fb2e902595a52b84550a8b9209a5abdd0e5fda3382fac371d57aa57fb`.

## 변경

원본 geometry·clock·GID·source·Stop 검사를 유지한 채, 검증된 양의 시작/후속 스캔 시간 만료와 실제 IMU 간격 누락만 대기로 처리한다. 누락 전 pending·prepared·proposal을 버리고 새 원본 스캔 두 개와 정상 IMU bracket에서 다시 정합한다. 이미 관측한 미승인·만료 Header의 순서도 보존해 역행을 차단한다. 과거 진단은 보존하되 누락 전 입력은 다시 발행하지 않는다.

SOURCE250ms/RAW200ms 및 IMU bracket60ms를 늘리지 않았다. 미래 시각·역행·손상·source 변경·명시적인 Stop은 계속 중단 상태를 유지한다. 이후 factory/association 단계의 만료는 별도 검증 대상이다. 누락된 IMU 보간, Header 재기록, 자동 fault 해제는 하지 않는다.

세 복구 옵션은 생성자 기본 OFF다. 공개 실행기와 서비스는 완전한 패키지 검증이 끝난 경우에만 옵션을 전달한다. RPC·환경 변수·일반 CLI로 활성화하는 경로는 없다. 구버전 패키지 반환값과 호출 방식은 유지한다.

## 검증 범위

고정 후보108개 및 독립 복사본41개 회귀가 통과했다. 실제 수치 정합 version1→2, 시작·후속 만료 이후 새 스캔 쌍 재개, Stop·시각 역행과 변조 차단을 포함한다. 각 suite의 중복을 더해 총 고유 테스트 수로 표시하지 않는다. 실행기·서비스 통합 시험과 ARM 빌드·기본 비실행 검사는 private 체크포인트에 별도 보존한다.

실제 API 시험은60회 상태 조회다. 정상 정합 `123`회, 최초 source 실패 `ValueError: physical_shadow_final_fence_failed:stale_original_causal_imu`. API 실행·정리 검증과 지속 native 입력 검증을 구분한다. 이번 지속 입력 판정: `False`. 이 판정은 전역 지도 정확도나 다중 클라이언트 성능·물리 자율주행 합격을 의미하지 않는다. 시험 결과는 한 세션 단위로 기록하며 서로 다른 시험의 성공 구간을 합산하지 않는다.

이번 실패에서는 최종 consumed association의 선택된 lower IMU가 SOURCE202.865ms로250ms 안에 있었지만 RAW200.780ms로200ms를0.780ms 넘겼다. 수치 fit19.422ms/prepare28.517ms 후의 검사다. 이 실패 지점과 원본 clock·Header·RAW를 보존했다. 초기 시간 만료/IMU gap 대기 기능이 실제 시험에서 발동한 횟수는0이며, 이번 관측으로 실제 gap 이후 재개를 검증했다고 표시하지 않는다. 후기 단계에서 검증된 양의 만료만 계산 폐기로 처리하는 별도 후보를 작성 중이다.

이전 probe의 `실패 없음`만으로 성공을 표시할 수 있는 판정을 수정했다. 데이터0회·진행 없음은 합격하지 않는다. 오류가 발생한 시험도 정리 성공만으로 전체 성공으로 덮어쓰지 않는다.

## 남은 핵심 작업

- [ ] 지속 native 입력에서 후기 association/factory 시간 만료의 정확한 causal IMU age를 검증하고 필요한 처리만 분리한다.
- [ ] GPS의 실제 receiver epoch·quality·원본 clock을 독립 geometry pose 및 완성된 지도 generation에 연결한다. 현재 native stream은 LiDAR/IMU 경로다.
- [ ] 완성된 같은 버전 지도·화면·중단·다중 클라이언트의 지속 처리 및 전체10월3일/9월10일 지형 오차를 검증한다.
- [ ] 열 끝의 외곽 회전 경로와 다음 열 진입·마지막 열 종료 로직을 구현하고 검증한다. 현재 관찰 판단기는 양쪽 열이 확인되는 구간의 전진 제안이며, 출구가 불명확하면 정지한다.
- [ ] 현재 차체 축·장착 기울기·encoderless 속도·drift·진동·제동·새 과수원·가지/벽/마지막 열을 검증한다. 바퀴 전원 OFF 시험으로 물리 제동 성능을 검증했다고 표시하지 않는다.

LiDAR driver가 blocking 스캔 수신 전에 Header를 생성하는 것은 소스에서 확인했다. 관측된360–390ms 지연의 원인별 비중은 아직 확정하지 않았다. 센서 driver·20Hz IMU 및 기존 대시보드 지도를 유지했다. 이전 결과와 실패는 [v7 계측](NATIVE_IMU_GAP_PROGRESS_20261010.md)에 보존한다.

재개 포인터: `native-navigation-20261007/current-native-input-recovery-run.txt`. GitHub Actions 대신 직접 SSH로 설치했다. 요청한2시간은 초과했으며, 검증되지 않은 실제 자율주행을 완료로 표시하지 않는다.
