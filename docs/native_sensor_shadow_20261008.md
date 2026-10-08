# Actual native sensor shadow — 2026-10-08

현재 Jetson에는 초기 패키지 26개 파일을 소스로 설치했고, 별도 SciPy/Release 수신기 이미지의 기본 검사를 통과했습니다. 첫 실제 통합 진단은 2.725초에 `imu_not_causally_available`로 중단됐습니다. 기존 13개 서비스와 470-scan 지도를 보존했으며, 이는 통합 처리 성공이 아닙니다.

후속 V3는 계산에 필요한 원본 IMU 보간 샘플과 최신 샘플만 묶어 사용합니다. 불필요한 과거 샘플이 계산 중 보관 경계를 넘어 처리를 중단하는 문제를 수정했으며, 원본 시간과 250ms/200ms 제한은 유지합니다. Factory 28개·runner 17개·portable package 4개 검사를 통과했습니다. 새 SOURCE SHA는 `caec4f76da7356fb6eb03887949b5be56017b81d3603e4ef240fd5d11f53f842`입니다. V3의 실제 설치 및 15초 통합 처리 성공은 별도 결과가 필요합니다.

대시보드 위치 추정의 회전 방향 검사도 수정했습니다. 충분한 원본 gyro 구간이 있을 때 그 방향과 어긋나는 기하 fallback을 거부합니다. 기본 모듈 6개 검사를 통과했으며, 이 변경의 실제 WEB 배포와 지도 정확도 검증은 아직 수행하지 않았습니다.

새 관측 전용 패키지는 [deploy/native_sensor_shadow](../deploy/native_sensor_shadow/README.md)에 있습니다. 기존 대시보드·센서 드라이버·모터 제어 서비스를 수정하거나 자동 실행하지 않습니다. 실제 자율주행 시작은 계속 차단되어 있으며 제안·적용 출력은 항상 HOLD/0입니다.

실제 Jetson에서 원본 CDR 관측자는15초 동안 LiDAR150개(각3240ray), IMU300개를 수신했습니다. 같은 native callback의 실제16-byte publisher GID, 원본 header와 CLOCK_MONOTONIC_RAW 수신시각을 보존했습니다. 모든150scan의 마지막 beam 시각이 callback 이후로 나오지는 않았습니다. 이는 관측자의 수신 검증이며 새 Python 처리기의 설치·실시간 처리 성공이나 주행 정확도 검증은 아닙니다.

소스 패키지는 독립적인13개 런타임 파일, 원본 observer 빌드 소스, runner와 Dockerfile을 포함합니다. 실제 ROS Iron SDK에는 NumPy가 있으나 SciPy가 없어, 검토된 immutable 이미지를 기반으로 새 파생 이미지에만 SciPy를 설치합니다. 기존 실행 컨테이너나 Jetson 호스트에는 설치하지 않습니다. 운영자는 실제 binary SHA와 현재 GID, 원래13role·센서·기록·470지도 보존 조건을 검증한 뒤 별도 관측 컨테이너에서 실행해야 합니다.

처리기는 원본 CDR과 causal IMU로 LiDAR 센서 평면의 상대 이동·회전을 진단합니다. 실제 IMU REP145, 센서 장착·기울기, body 좌표 속도, rolling scan deskew, 제동 특성은 아직 검증되지 않았습니다. 휠 엔코더·PWM·카메라·Gazebo truth를 위치추정에 사용하지 않습니다. GPS도 현재 fix가 없어 위치 권한을 대신하지 않습니다.

현재 로컬 검증은 factory28개·runner17개와 portable package4개의 pure/mock 테스트 통과입니다. 설치된 fresh callback 처리·fit·최종 freshness fence의 실제15초 성공은 별도 결과로 기록해야 합니다. 실패·불연속·지연을 시각이나 GID 변경으로 숨기지 않으며, 부분 원본과 원인을 남기고 HOLD합니다.

무결성 검증은 raw-byte SHA에 기반합니다. 패키지 내부 `.gitattributes`가 원래 CRLF를 포함한 파일을 보존하므로 복제 뒤 임의 LF 정규화를 하지 마세요. 기본 CLI는 계획만 출력합니다. 상세 설치·검증·명시적 관측 인자는 패키지 README에 있습니다.
