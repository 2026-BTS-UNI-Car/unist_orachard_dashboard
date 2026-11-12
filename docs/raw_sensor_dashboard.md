# 원본 센서 대시보드

이 버전은 실제 Jetson에서 사용하는 대시보드의 AI·fusion tree 처리를 제거한다.
카메라는 원본 프리뷰, LiDAR는 LaserScan 점, IMU/GPS는 기존 센서 측정값을 표시한다.
학습 모델, TensorRT/YOLO/CLIP worker, tree bearings, 객체/줄기 overlay,
fusion map API·UI 및 과거 archive의 AI 재계산 경로를 제거했다.

유지되는 기능: 실제 센서 드라이버, ESP USB 단일 소유 제어, arm/lease/ticket/TTL/STOP,
GPS 오프라인 지도, 센서 원본 IMU 표시, 원본 archive 조회/재생/다운로드, recorder.
IMU 소프트웨어 회전·녹화 시작 영점·gyro bias 보정과 수동 영점은 제거했다.
`/imu/data`는 `/imu/data_raw` 메시지를 수정 없이 전달하는 호환 토픽이다.
과거 archive의 보정 데이터는 기록 당시 값과 설명으로 계속 재생한다.
기존 archive의 overlay 필드는 응답에서 제외하며 원본 파일을 수정하거나 삭제하지 않는다.
오프라인 지도 및 기존 녹화는 runtime/data 디렉터리에 그대로 보존한다.

실차 기본 모드는 `live`이다. simulator 표시·가상 제어기는 실차에 적용하지 않는다.
별도 orchard-rover 저장소는 배포된 커밋을 복제한 뒤 격리된 simulation backend를 추가한다.
실차 UI의 PWM %는 측정된 바퀴 속도가 아니다.

배포는 소스·이미지 복구 지점을 먼저 남긴 뒤 dashboard, ros2-camera-bridge,
ros2-lidar-bridge, ros2-data-recorder만 교체한다. vision-ai-worker는 중지하고 재시작 정책을 해제한다.
센서 드라이버·ESP motor bridge·GPS·IMU bridge·기존 occupancy 서비스는 재시작하지 않는다.
차량 주행과 새 녹화를 실행하지 않고 센서 수신·HTTP·WebSocket·기존 archive를 확인한다.
배포 결과와 복구 경로는 [`RAW_DEPLOYMENT.md`](https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/blob/archive-before-cleanup-20261113/RAW_DEPLOYMENT.md)(보존 태그) 및 Jetson의 private checkpoint에 기록한다.
