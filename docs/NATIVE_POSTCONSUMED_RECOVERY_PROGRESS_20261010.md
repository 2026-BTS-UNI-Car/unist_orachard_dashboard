# 2026-10-10 최종 IMU 검사·기록 GPS 시각 점검

Jetson의 센서 관찰 서비스에 v10을 설치했다. **GPS 통합 지도와 실제 모터 자율주행은 미완료다.** 자동 모터 명령과 센서 장착·제동 보정을 변경하지 않았다. 요청한2시간은 초과했다.

이미지: `sha256:21fb797577b24339e6af5391e41a1b58848c9bb323cc6d79e99bd7a212ac6dc6`. 패키지: `d24a38bafde135b70db87c50e39303f4d6d82c2267ffeef3f769a45587ab57f1`. source inventory: `80c60df047f1d1b8ffd66a1de984d1b5238ce4f93ca32c6e3df149d1fd2bd644`.

## 설치한 변경과 실제 시험

수치 계산 뒤 receiver consume이 성공한 다음의 정확한 최종 association에서, 원본 SOURCE는 유효하지만 선택된 lower IMU의 RAW가200ms를 넘으면 그 계산을 버리고 새 두 스캔·실제 IMU를 기다린다. 전체 원본 bytes·CDR decode·수치 group·clock·source·Stop 검사 뒤에만 이 분기가 허용된다. proposal·pending·capability는 폐기하고 이전 진단·버전·Header 순서는 보존한다. 일반 fault나 이미 중단된 receiver는 해제하지 않는다.

새 factory 옵션은 기본OFF이며 완전한 패키지 검증 뒤에만 전달한다. 기존 세 ingress 옵션과 factory 옵션을 분리했고 구버전 호환을 유지했다. TTL SOURCE250ms/RAW200ms, IMU bracket60ms와20Hz driver는 그대로다.

고정 후보128개, 독립 복사본61개, 새 ROOT stream100개와 실제 생성자·수치 NDJSON wiring3개가 각각PASS했다. suite의 중복을 더해 고유 테스트 총합으로 표시하지 않는다. ARM overlay 빌드100.0초, 기본 비실행·원본 CPP 바이너리 재사용·source 검사가 통과했다.

실제 API60회 조회에서 정합182회까지 진행했다. IMU 간격65.195ms의 실제 gap 대기가1회 발생했고 같은 세션에서 정합이98→105→115회로 계속 증가했다. 최종 consumed-RAW 대기는0회여서 새 분기의 실제 발동은 이 시험에서 검증하지 못했다.

이후 receiver consume 내부에서 causal IMU RAW200.563ms/SOURCE202.282ms로 실패했다. 이 지점은 이번 변경의 **이전 단계**이며 이미 receiver fault가 유지된다. 수치 fit16.404ms/prepare23.420ms/commit 초기2.963ms/receiver consume2.875ms였다. 지속 입력 판정은False다. 서로 다른 시험의 정상 구간을 합산하거나 오류 없는 구간만으로 합격하지 않는다.

API 실행·종료 검증은 통과했다. 적용 좌우0, exit0, kill없음, queue0, reader·pipe·owner 정리와 입력None을 직접 확인했다. 기존13서비스 설정/PID/시작 시각 및 저장된 지도470스캔을 유지했다. 전역 지도 정확도·다중 클라이언트·물리 자율주행 합격으로 확대하지 않는다.

## 10월3일 전체7기록 GPS 시각·짝 재점검

기존 전체 센서 inventory와 gzip 원본 SHA를 대조하고 각 파일을 끝까지 읽었다. NavSatFix6,229개와 receiver6,207개 중 정확한 공유 publication stamp 짝은6,185개였다. 그 짝의 위도·경도·고도 값 불일치는0개였다. 동일 iTOW 반복은3,080회다. PVT와 HPPOSLLH가 같은 측정 epoch를 전달하는 구조이므로 서로 독립적인 위치 제약으로 누적할 수 없다.

| 기록 | receiver 수 | 공유 stamp 짝 | 반복 iTOW | publication−원본 GPS UTC 중앙값(s) |
|---|---:|---:|---:|---:|
| 20261003T072238Z_both | 1498 | 1492 | 742 | -7879.950 |
| 20261003T072329Z_both | 178 | 178 | 89 | 0.023 |
| 20261003T072530Z_both | 1015 | 1011 | 504 | 0.022 |
| 20261003T073551Z_both | 1576 | 1573 | 784 | -7879.970 |
| 20261003T080351Z_both | 482 | 481 | 240 | -0.057 |
| 20261003T080909Z_both | 404 | 402 | 199 | -0.057 |
| 20261003T081934Z_both | 1054 | 1048 | 522 | -0.096 |

072238과073551의 host publication 시각은 원본 receiver UTC text보다 약7,880초 이르다. 녹화 ID 순서와 GPS에 기록된 시각이 어긋나는 근거다. 이 표는 관측 차이를 기록한 것이며, GPS 측정 시각의 보정값·clock certificate로 사용하지 않는다. 원본 센서 stamp나 녹화 ID를 변경하지 않았다.

전체 receiver 기록에서 date/time/full-resolved/time-accuracy/fraction metadata가 없고 UTC text는 정수 초다. 수신기 보고 hAcc는3.0169–9.592m였다. 기존 기록으로 GPS의 정확한 측정 시간과 cm급 RTK를 검증했다고 표시하지 않는다. 기록은 host 센서 시각에서 상대 정합을 유지하며 GPS는 고유 epoch의 느슨한 위치 정보로 구분해야 한다.

별도 원본 GNSS producer의 ARM 컴파일·기본 실행·1,010개 parser self-test도 통과했다. 원본 두 ROS topic을 읽는 준비 단계이며 아직 실제 GNSS factor·완성 지도 owner 연결이나 설치 합격이 아니다. 최초 scratch 실행 권한 실패와 수정된 비실행 시험을 모두 보존했다.

## 남은 작업

- [ ] receiver consume 내부의 정확한 양의 시간 만료를 fault 이전에 계산 폐기로 처리하고 모든 다른 오류·Stop은 유지한다.
- [ ] 기존 정합 게이트를 유지하는 nearest 탐색 최적화를 실제 ARM 및 지속 센서 입력에서 검증한다.
- [ ] 실제 GNSS 원본 수신·측정 epoch/quality/clock·독립 geometry를 완성 지도 generation에 연결한다.
- [ ] 전체10월3일·9월10일 지도 지형/offset/가지·행 끝·벽·마지막 행과 새 과수원을 검증한다.
- [ ] 관측된 headland/다음 행의 경로·주행 속도 및 현재 차체 drift/진동/제동을 검증한다. 바퀴 전원OFF로 물리 제동을 검증할 수 없다.

재개 포인터: `native-navigation-20261007/current-native-postconsumed-recovery-run.txt`. 이전 실패와 검증은 [v9](NATIVE_INPUT_RECOVERY_PROGRESS_20261010.md)에 보존한다. GitHub Actions 없이 직접 SSH로 설치했다.
