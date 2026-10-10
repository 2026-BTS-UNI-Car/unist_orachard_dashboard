# 2026-10-10 원본 GPS 수신·IMU 연결 수정

Jetson에 원본 GPS CDR 수신, quality 검사, 실제로 지도에 반영된 pose·scan 이력 보관을 설치했습니다. 지도 frame digest는 같은 결과를 유지하면서 거리 배열을 한 번에 pack하도록 변경했습니다. 실제 자동 모터 출력은 OFF입니다.

첫 설치에서 제가 `/imu/data`의 GID를 원본 수신기에 넣어 시작이 실패했습니다. 수신기의 계약과 C++ 소스는 `/imu/data_raw`를 사용합니다. 원본 BNO085 드라이버의 GID로 다시 연결한 뒤 실제 120초 시험을 끝냈고 입력·스레드·리소스 종료를 확인했습니다. 실패한 첫 시험과 수정 후 시험을 모두 보관했습니다.

수정 후 실제 결과:

- LiDAR 1,183개, 원본 IMU 2,351개, GPS data/fix 각각 232개 수신. 원본 전달 계수와 DONE 검사가 일치하며 수신 오류는 없었습니다.
- GPS 232쌍 모두 NoFix였습니다. 유효 GPS 보정 인자는 0개이며 GPS 통합 지도가 완성된 상태가 아닙니다.
- 지도 version 131, cell 2,079개를 보관했습니다. epoch는 하나였고 지도 worker 오류는 없었습니다.
- 지도 version이 33개 연속 표본에서 변하지 않았고, 신선한 주행 입력의 진행 간격은 최대 약 37초였습니다. 실시간 처리 기준을 통과하지 못했습니다.
- 마지막 지도 작업은 CPU 약 140ms, wall 약 285ms였습니다. 한 작업의 값이며 전체 처리 성능 개선을 입증하지 않습니다.
- 기존 13개 서비스의 설정·시작 시각·PID를 유지했습니다. 잠깐 올렸던 진단 컴파일 메모리는 512MiB로 복구했고 자동 모터 명령은 보내지 않았습니다.

설치 image: `sha256:ada20126b430ed21e861a538c30dfd6691c6f76ccb9e1de8dbfed91671b9c493`.

현재 원본 IMU 토픽은 `/imu/data_raw`입니다. 다음 재배포 때에도 `OBSERVER_CONTRACT.json`의 토픽을 기준으로 GID를 조회해야 합니다. `/imu/data`는 recorder가 제공하는 다른 입력이므로 대체해서 연결하면 안 됩니다. 재시작 후 GID 변경에 대한 자동 재연결은 아직 구현되지 않았습니다.

다음 작업은 원본 CDR의 중복 해석 제거와 같은 native commit에서 제어 입력을 만드는 연결입니다. 중복 해석 제거 후보 14개, 제어 입력 연결 후보 28개, 실행 중 GPS/pose 이력 조회 후보 26개의 테스트가 통과했지만 이 세 후보는 아직 실제 runtime에 적용하지 않았습니다. 측정된 차체 방향·장착 변환·제동 profile과 실제 주행 검증도 남아 있습니다.

실차 자율주행, GPS 보정이 반영된 실시간 occupancy, 전체 10월 3일/9월 10일 주행 정확도 검증은 미완료입니다. 시뮬레이션 runtime은 이번 변경으로 교체하지 않았습니다. 카메라·PWM·휠 엔코더·simulation truth를 native 위치 추정 입력으로 사용하지 않았습니다.

원본 증거는 로컬 `orchard-realdata-audit/20261003-field/native-navigation-20261007/native-gnss-latency-root-20261010`에 있습니다. `CURRENT_CHECKPOINT.json`과 `probe-original-IMU`가 수정 후 상태를 가리킵니다.
