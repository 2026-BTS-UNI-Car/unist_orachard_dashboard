# 2026-10-10 센서 시각 계측과 지속 처리 검토

현재 v7 설치와 원본 Header 지연 시험은 [후속 계측](NATIVE_IMU_GAP_PROGRESS_20261010.md)에 기록했다. 아래 v6 결과는 이전 체크포인트다.

Jetson에 원본 센서 시각 계측을 설치했다. **지속 입력과 GPS 통합 주행 지도, 실제 자율주행은 미완료다.** 자동 모터 명령과 장착·제동 보정 변경은 수행하지 않았다. 바퀴 전원 OFF는 사용자 설명이며 물리 주행 검증값이 아니다. 이전 최적화와 시험 이력은 [센서 관찰 처리 개선](NATIVE_LATENCY_PROGRESS_20261010.md)에 남긴다.

## 설치와 실제 시험

현재 관찰 이미지: `sha256:0f8a3b7b4e1e8b58020ea5d457d21b86a867ca33ea94cdbc23f28e45dbb46835`.
패키지 HANDOFF: `f372e8ed6f1d81e5dc54990a51c435ffa9e83a65d80a22e3f3d23bc06c50d9ad`.
source inventory: `10ac53502003acd803817d6bd19b5ac4c69bf5e38c7823d2b840d74a28673ceb`.

추가 시각 계측은 실패한 검증에서 이미 사용한 원본 Header·callback RAW·callback SYSTEM·비교 시각을 최대 1024B로 보존한다. 새 시각 샘플이나 시각 재표기 없이 첫 오류를 유지한다. source250ms/RAW200ms/초기 IMU500ms와 원본 CDR SHA·전체 decode/equality·GID·ownership·Stop 검사는 그대로다. v6 revision/parent와 진단 전용 flag를 strict loader에서 확인하며 이전 v3/v5도 원래 조건으로 검증한다.

| 확인 | 관측 결과와 범위 |
| --- | --- |
| 스트림·종료 회귀 | 새 후보 49개 PASS |
| strict loader | 별도 14개 PASS, 실제 선택 패키지·v3·잘못된 parent/flag 포함 |
| 시각 계측 회귀 | 고정된 후보의 기존 59개 PASS 증거와 26 owned/9 external 해시를 재확인; 이번에는 다시 실행하지 않음 |
| ARM 빌드 | 96.8초, C++ binary 재사용, 기본 비실행·source/binary/package 검사 PASS |
| 실제 입력 시험 | 60회 조회, 262 LiDAR/504 IMU, 정상 정합250회 뒤 IMU bracket gap 오류 |
| 종료 | exit0·강제 kill 없음·queue0·readers joined/resources closed·owner 해제 |
| 운영 보존 | 기존13 서비스·설정·시작 시각·지도470 scans 유지, 좌우 적용0 |

첫 실패는 `physical_original_pair_rejected:imu_bracket_gap_exceeded`였다. 기존 bracket 제한은 **60ms**, IMU driver의 명목 주기는20Hz/50ms다. 이번 시각 계측 값이 비어 있는 것은 실패가 startup/CURRENT TTL 게이트가 아닌 IMU 보간 단계에서 발생했기 때문이다. 실패한 bracket의 정확한 간격은 아직 계측하지 않았다. 마지막 정상 쌍의 fit8.067ms/prepare14.837ms/commit6.271ms와 lower IMU RAW age127.791ms는 실패 쌍의 값이나 전체 지연 분포가 아니다. 이전 버전과 다른 시험의 성공 구간을 합치지 않는다. **native protocol qualification은 false다.**

현재 대시보드의 현장 시험 준비 → 관찰 시작/정지는 설치되어 있다. 실제 `/api/autonomy/start`의 검증 대기와 자동 적용0은 유지한다. fault 뒤에는 명시적인 관찰 정지와 owner 해제를 확인한다. 관찰 정지는 모터 비상정지를 대신하지 않는다. 기존 WEB·GPS/IMU/LiDAR/카메라/ESP driver는 이번에 변경하지 않았다. GitHub Actions 대신 직접 SSH를 사용했다.

## 지도 처리 후보와 GPS 연결 검토

Linux64 격리 환경에서 bitmap→signed tile 후보의96회 전체3200-ray 적분, signed cells·전체/차분/초기화 패킷 순서와 캐시 별칭 회귀가 통과했다. 전용 컨테이너를 제거했고 기존40 컨테이너의 설정·PID·시작 시각과 원본 소스를 유지했다. 다만 성능은 혼합이다. 87-tile update p95는18.772→29.921ms로 악화됐고97-tile은20.987→11.890ms로 줄었다. host 변동도 관측되어 일반 성능·실시간 M12 합격으로 확대하지 않는다. **이 후보는 공개 코드나 SIM runtime에 적용하지 않았다.**

기존 전체7기록 감사의 LiDAR32047/IMU63515/GPS data6207을 재사용했다. 원본을 이번에 전량 다시 읽었다는 뜻이 아니다. GPS hAcc3.0169–9.592m는 RTK 센티미터 정확도로 간주할 수 없다. 현재 native 원본 관찰 경로는 LiDAR/IMU 쌍 진단이며 GPS admission과 누적 지도가 아직 없다. 기존 archive의 위치 보정과 live/ordered playback의 speed bridge·innovation prior를 구분해야 한다. GPS를 실패 scan 뒤의 보존 pose에 연결하지 않고 검증된 receiver epoch와 독립 geometry pose를 대응시키는 default-OFF 후보를 준비한다. yaw·휠 오도메트리·PWM·카메라·truth를 위치 증거로 대신하지 않는다.

## 남은 우선 작업

- [ ] 실패한 IMU bracket의 원본 간격과 시각을 계측하고, 정상 데이터 누락만 별도 대기·재기준화할 후보를 검증한다. 기존 fault/Stop 자동 해제나60ms 제한 완화는 하지 않는다.
- [ ] 같은 버전으로 지속 입력·지연·중단·다중 클라이언트와 실제 화면 지연을 검증한다.
- [ ] native GNSS transport/receiver clock·quality·geometry 대응과 완성된 지도 generation의 원자적 교체를 연결한다.
- [ ] 10월3일 전체 지도 오차와9월10일 작은 차체·센서 offset·가지·열 끝·벽을 검증한다. 현재 장착·진행방향·속도·좌우 drift/회전/진동/제동과 마지막열은 미검증이다.

새 private 실행·로그·parent·고정 테스트 증거는 `native-navigation-20261007/current-native-admission-run.txt`가 가리키는 폴더에 저장한다. 이전 실패 기록과 체크포인트는 삭제하지 않는다. `Dockerfile.optimized`는 Jetson에 보존된 v5 ARM 이미지의 변경 없는 C++ observer와 고정 환경을 재사용하는 overlay이며 깨끗한 호스트에서 그 이미지가 없으면 먼저 검토된 기반 이미지가 필요하다.
