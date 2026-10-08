# Actual native sensor shadow — 2026-10-08

새 관측 전용 패키지는 [deploy/native_sensor_shadow](../deploy/native_sensor_shadow/README.md)에 있습니다. 기존 대시보드·센서 드라이버·모터 제어 서비스를 수정하거나 자동 실행하지 않습니다. 실제 자율주행 시작은 계속 차단되어 있으며 제안·적용 출력은 항상 HOLD/0입니다.

실제 Jetson에서 원본 CDR 관측자는15초 동안 LiDAR150개(각3240ray), IMU300개를 수신했습니다. 같은 native callback의 실제16-byte publisher GID, 원본 header와 CLOCK_MONOTONIC_RAW 수신시각을 보존했습니다. 모든150scan의 마지막 beam 시각이 callback 이후로 나오지는 않았습니다. 이는 관측자의 수신 검증이며 새 Python 처리기의 설치·실시간 처리 성공이나 주행 정확도 검증은 아닙니다.

소스 패키지는 독립적인13개 런타임 파일, 원본 observer 빌드 소스, runner와 Dockerfile을 포함합니다. 실제 ROS Iron SDK에는 NumPy가 있으나 SciPy가 없어, 검토된 immutable 이미지를 기반으로 새 파생 이미지에만 SciPy를 설치합니다. 기존 실행 컨테이너나 Jetson 호스트에는 설치하지 않습니다. 운영자는 실제 binary SHA와 현재 GID, 원래13role·센서·기록·470지도 보존 조건을 검증한 뒤 별도 관측 컨테이너에서 실행해야 합니다.

처리기는 원본 CDR과 causal IMU로 LiDAR 센서 평면의 상대 이동·회전을 진단합니다. 실제 IMU REP145, 센서 장착·기울기, body 좌표 속도, rolling scan deskew, 제동 특성은 아직 검증되지 않았습니다. 휠 엔코더·PWM·카메라·Gazebo truth를 위치추정에 사용하지 않습니다. GPS도 현재 fix가 없어 위치 권한을 대신하지 않습니다.

현재 로컬 검증은 runner15개와 portable package4개의 pure/mock 테스트 통과입니다. 설치된 fresh callback 처리·fit·최종 freshness fence의 실제15초 성공은 별도 결과로 기록해야 합니다. 실패·불연속·지연을 시각이나 GID 변경으로 숨기지 않으며, 부분 원본과 원인을 남기고 HOLD합니다.

무결성 검증은 raw-byte SHA에 기반합니다. 패키지 내부 `.gitattributes`가 원래 CRLF를 포함한 파일을 보존하므로 복제 뒤 임의 LF 정규화를 하지 마세요. 기본 CLI는 계획만 출력합니다. 상세 설치·검증·명시적 관측 인자는 패키지 README에 있습니다.
