# GPS 신호와 녹화 상태 표시

2026-10-01: deployed to the real Jetson from source `9e5ffd056e486be149c488ac1a939216ee405f3c`. The GPS driver, GPS bridge and web images are `9738ed34`, `b79baa57` and `7f5ea0aa` respectively. [Deployment evidence](jetson_gps_reception_recording_deployment.json) records 39 native ARM tests, fresh independent NAV-SAT/NAV-SIG epochs and the recorder API freshness contract. The observed receiver remains `no_fix` with 0 satellites used and fresh empty diagnostic reports; this does not imply an antenna-power diagnosis.

Checkpoint `20261001-015431-gps-reception-9e5ffd056e48` retains the original source bytes/metadata, per-role Compose chains and `rollback-*.json`. The pinned `deploy_gps_reception_002.py rollback` phase requires prepared digest `f2d1c91bac0f16789e06a113c41daa919819c8d89f6539ecbd965d9998ae17d7`, disarmed/idle state and unchanged owned resources. Defaults for the three image tags were persisted, with the original tag targets backed up. Historical explicit overrides still refer to older images. No rollback was needed; no recorder restart, new recording, motor actuation or receiver configuration write occurred. Other 13 containers and 32 archive metadata entries were preserved.

GPS 화면은 **위치 계산에 사용된 위성**, **검출된 신호**, **추적 중인 신호**를 구분한다. 신호를 추적해도 위치 fix가 없으면 좌표를 유효하게 표시하지 않는다. NAV-SIG의 여러 주파수 신호 개수와 NAV-SAT의 위성 개수도 합치지 않는다. quality 2~7은 검출, 4~7은 잠금/추적으로 집계하고 목록에 있는 탐색 행만으로 검출 수를 늘리지 않는다.

기존 GPS 드라이버 한 개가 5초마다 빈 NAV-SAT/NAV-SIG 조회를 보낸다. 짧게 나뉜 쓰기는 같은 한 묶음을 이어 보내며 대기열을 쌓지 않는다. 설정·출력 주기·Flash·리셋 쓰기는 없다. 중복 iTOW는 진단의 나이를 갱신하지 않는다. 12초 이상 지난 자료, 연결 끊김, 미보고는 0개로 만들지 않고 미확인으로 표시한다. 진단 수신으로 위치 표본의 나이도 갱신하지 않는다. 수신기 형식은 [u-blox HPG 1.51 Interface Description](https://content.u-blox.com/sites/default/files/documents/u-blox-F9-HPG-1.51_InterfaceDescription_UBXDOC-963802114-13124.pdf)을 따른다.

녹화 상태는 recorder API의 최신 응답을 사용한다. API 연결이 끊긴 뒤 남은 event 상태 파일은 `last_known` 과거 자료로만 반환하며 현재 기록 여부는 `unknown`이다. UI는 요청 시작부터 지난 단조 시간을 포함해 응답의 나이를 계산하고 5초를 넘으면 상태 미확인으로 표시한다. GET은 4초 뒤 취소하며, 시작/중지 후 늦게 도착한 GET이나 일반 telemetry가 최신 상태를 덮어쓰지 못한다. 미확인 상태에서는 시작을 막고 중지 요청은 가능하게 유지한다. recorder나 원본 녹화 자료의 저장 형식은 변경하지 않았다.

검증: 격리 ROS Iron Linux 44개 검사, 독립 WSL GPS/녹화 상태 37개 검사, GPS UI 모델·녹화 UI의 지연/중단/재접속 경쟁 조건을 확인했다. [검사 근거](gps_reception_recording_validation.json). 실제 안테나 위치 fix, 실차 주행, 새 실제 녹화, WAN 성능 검증을 뜻하지 않는다.
