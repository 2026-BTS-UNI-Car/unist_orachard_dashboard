# 2026-10-10 최종 IMU 만료 복구 설치와 지도 작업 분리

Jetson에 기존 정합 계산의 마지막 causal IMU RAW 만료를 처리하는 v13 복구를 설치했다. **전체 GPS·IMU·LiDAR 지도와 실제 모터 자율주행은 미완료다.** 요청한 2시간은 초과했다. 바퀴 전원 OFF 상태라는 사용자 설명을 유지하며 자동 모터 명령과 장착·제동 보정을 변경하지 않았다.

- 설치 이미지: `sha256:fb50f6ac102b08c2a18adf91e2d2a26db3b31f970ed71f7d86830c5858053f31`
- 패키지: `4de382364c247de7b3ad05e819b2272e1de50a57d0d9e1c0ffe7d2e12ac85cf4`
- source inventory: `629ee490bba4514fdb405939aa7b82713130a5a3c7be71771b16d96d9d3f73b1`
- 설치 CID: `6ffd150e110d7ce69f2d28e959914ad35cd63d4d15d58d85bd9553997ca13703`

## 실제 설치와 관찰

기존 최종 비교 시각에서 선택된 원본 causal IMU의 RAW만 양으로 200ms 이상 만료되고 SOURCE는 250ms 이내인 경우를 정확한 typed WAIT로 처리한다. 모든 원본 그룹·SHA·수치·capability·현재 context·Stop을 검증하고 해당 계산과 proposal을 폐기한다. 기존 Header 순서와 이미 누적된 정합 이력은 유지하며 새 원본 두 스캔과 IMU를 기다린다. SOURCE 만료·미래 시각·손상·변조·Stop·이미 기록된 fault는 자동 해제하지 않는다. 건강한 경로에는 새 clock/SHA/decode 호출을 추가하지 않았다. 기존 SOURCE250ms/RAW200ms/bracket60ms와 fit 계산은 그대로다.

생성자는 기본 OFF다. 완전한 패키지 검증을 통과한 loader만 최종 복구 옵션을 전달한다. nearest 최적화는 이전 ARM 성능 결과가 혼합돼 OFF다. CLI/RPC/environment로 이 기능을 우회해서 켜는 경로는 없다.

후보 시험은 stream127·실제 wiring11·package164가 각각 PASS/0skip했다. ROOT에서 설치 원본을 다시 시험해 stream127·복사된 wiring 전체 suite108가 PASS/0skip했다. wiring 전체에는 상속된 package 회귀가 포함되므로 전부 실제 wiring으로 세지 않는다.

ARM 기본 비활성 동작·패키지·실행 파일·7개 loader 옵션을 확인한 뒤 직접 SSH로 대기 상태 설치했다. 컴파일러를 실행하지 않고 기존 검증된 C++ 수신 실행 파일을 재사용했다. 기존 서비스 13개를 유지했다.

실제 입력을 60회 조회한 결과 정합은 343회였다. 지속 native 입력 판정은 **False**이며 최초 실패는 `ValueError: physical_shadow_final_fence_failed:stale_causal_imu_receipt`이다. 판정은 같은 owner/session/generation/PID에서 새 정합200회·25초 지속·원본 age·진행 간격2초 이내·정상 종료를 요구한다. 단일 API 실행 또는 정리 통과를 지도 정확도 합격으로 표현하지 않는다.

적용 좌우0, 정상 exit0, kill없음, queue0, reader/pipe/owner 종료와 최종 inputNone을 확인했다. 저장된 기존 지도470스캔은 유지했다. 상세 원본 결과는 재개 폴더의 `ACTUAL_API_R12_CORRECTED_PROBE.stdout.json`과 `OBSERVED_INSTALL_PROGRESS.json`에 보관한다.

## 지도와 자율주행의 다음 연결

정확히 게시된 원본 factory commit을 지도에 연결하는 후보는 ROOT 독립63시험에 통과했다. 합성 원본 CDR와 실제 SciPy 시험이며 실제 지형 정확도 합격은 아니다. 현재 후보의 3,200-beam 동기 지도 작업은 로컬 측정 약88~129ms이고 타일 첫 패킹은 약53ms였다. 이것을 센서 입력 스레드에서 수행하면 입력 만료를 유발할 수 있다.

따라서 원본 정합 게시 경계에서 immutable 지도 입력을 보관하고 별도 제한된 지도 작업자로 전달하는 새 후보를 작업 중이다. 화면용 누적 지도는 원본 stamp를 유지한 역사 자료이며, 주행 proposal은 독립적으로 최신 cell과 pose를 확인한다. 기존 물리 gate를 렌더링 마감 시각으로 우회하거나 원본 시각을 갱신하지 않는다. 중단과 입력 공백에는 기존 지도를 유지하며, 독립 정합으로 이전 지형과 연결되기 전까지 임의 위치를 이어 붙이지 않는다.

웹 타일은 완성 generation만 원자적으로 게시하고 느린 클라이언트가 여러 페이지를 받는 동안 같은 snapshot을 유지하도록 제한된 lease/cache를 구성 중이다. 이것은 아직 실제 대시보드에 설치되지 않았다. 기존 화면을 새 origin으로 교체하는 동작을 허용하지 않고 명시적인 새 지도 요청만 초기화를 허용할 예정이다.

## 남은 작업

- [ ] 누적 지도 계산 분리·원본 keyframe 재연결·타일 lease/cache·웹 Stop/새 지도·다중 클라이언트의 실제 연결과 지속 시험.
- [ ] valid GNSS epoch/quality·원본 측정 clock·geometry를 지도 generation에 연결. 현재 관측 GPS는 no_fix/위성0이므로 위치 factor0 유지.
- [ ] 전체10월3일과9월10일 기록의 실제 지형, 센서 offset, 가지, 벽, 열끝·다음열·마지막열과 새 과수원 검증.
- [ ] 현재 차체 방향·기울기·encoderless 속도·drift/진동·제동과 실제 모터 자율주행 검증. 전원OFF만으로 실측 제동 검증을 완료할 수 없다.

재개 포인터: `native-navigation-20261007/current-native-final-scalar-run.txt`. 이전 실패와 설치 결과는 [receiver 복구](NATIVE_COMBINED_RECOVERY_PROGRESS_20261010.md)에 보존한다. GitHub Actions 대신 직접 SSH로 설치했다.
