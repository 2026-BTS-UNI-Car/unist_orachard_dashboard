# 연구 성과 지표

수행계획서의 핵심 목표는 GPS/RTK가 약한 과수원 환경에서 Vision-LiDAR 센서 신뢰도를 동적으로 조절해 행간 중심선을 안정적으로 추정하고 경로추종 성능을 확보하는 것입니다. 대시보드는 이 목표를 관찰할 수 있는 지표 위주로 구성합니다.

## 자율주행 안정성

- `centerline_error_m`: 추정 중심선과 기준 중심선 사이 평균 오차
- `path_tracking_error_m`: 로버 위치와 목표 경로 사이 횡방향 오차
- `rows_completed`: 완료한 과수 행간 수
- `distance_m`: 누적 주행 거리
- `estop`: 비상정지 여부

## 센서 신뢰도

- `lidar_reliability`: LiDAR 기반 과수열/줄기 검출 신뢰도
- `vision_reliability`: RGB 기반 주행 가능 영역 또는 과수열 보조 검출 신뢰도
- `lidar_weight`, `vision_weight`: 동적 가중치 결과
- `row_detection_count`: 현재 프레임 또는 구간에서 검출된 유효 행/줄기 수

## 지도와 환경 인식

- `map_coverage_percent`: 주행 구간 대비 지도화된 영역 비율
- `trees`: LiDAR 또는 융합 인식 기반 과수 위치
- `clutter`: 잡초, 지주대, 가지, 오검출 후보
- `centerline`: 현재 선택된 행간 중심선
- `route`: 목표 주행 경로

## 시스템 운용성

- CPU, 메모리, 디스크, 온도
- 센서 topic rate
- 배터리 잔량
- MAVLink 또는 장거리 telemetry 연결 상태
- ROS2 bridge heartbeat

## 실험 로그로 남길 항목

- 실험 회차 ID, 날짜, 장소, 날씨, 노면 상태
- 센서 calibration 버전
- 알고리즘 파라미터 버전
- 평균/최대 중심선 오차
- 평균/최대 경로추종 오차
- 비상정지 횟수
- 주행 실패 구간과 원인
- 센서별 신뢰도 변화 그래프
