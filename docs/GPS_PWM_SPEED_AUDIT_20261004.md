# GPS 속도와 PWM 추가 분석 · 2026-10-04

7개 기록의 원본 추출 manifest 및 GPS·PWM·raw IMU 해시를 다시 확인했다. MCU reported/requested 핀이 일치하고 2초 이상 유지된 구간에서 첫 1초를 제외했다. GPS는 수신기 epoch를 중복 제거하고 보존된 host source stamp로 맞췄다. 수신기 UTC의 큰 오프셋을 host clock에 덮어쓰지 않았다.

도로 equal-forward 안정 구간의 **구간별 안테나 지상속도 중앙값을 다시 모은 중앙값**은 기록별 약0.73~0.94m/s였다. 과수원은080351에서0.87m/s,080909에서0.54m/s였다. 세 pivot 구간에서는0.15~0.27m/s가 표시되었다. 정지 출력 구간에서도 일부 GPS 속도가0.20m/s였다. 기록 조건·PWM 수준·노면·안테나 레버암·GPS 속도 오차가 달라 이 값으로 바퀴 속도, 차체 전방 속도, slip gain 또는 제동 거리를 확정하지 않는다. 297개 PWM 안정 구간 중 IMU 측정 구간이 확보된296개를 공동 분석했다. 원래 IMU 진단을 유지했다.

기존 디코더는 UBX NAV-PVT의 gSpeed만 보존하고 sAcc/headAcc/velNED를 버렸다. 다음 기록을 위해 `speed_accuracy_m_s`, `course_accuracy_deg`, `velocity_ned_m_s`, `motion_accuracy_source`, UTC valid/resolved·tAcc·nano를 추가했다. HPPOSLLH가 같은 epoch의 위치를 보완할 때도 motion 필드를 유지한다. no-fix에는 motion 정확도/velocity를 유효값으로 제공하지 않고, 2D fix의 vertical velocity는 unknown이다. NMEA에는 없는 속도 정확도를 만들어 넣지 않는다. course는 이동방향이며 차량 yaw로 사용하지 않는다.

필드 위치·단위 근거: [u-blox ZED-F9P Interface Description, NAV-PVT, pp157–158](https://cdn.sparkfun.com/assets/learn_tutorials/8/5/6/ZED-F9P_UBX_NMEA_and_RTCM_protocols.pdf). 수신기 추정 오차 자체도 실측 calibration 보증이 아니다. 과거 파일에 누락된 sAcc는 복구할 수 없다.

raw GPS·위치·궤적·원본 영상은 private 공간에 유지한다. 공개 집계는 `gps_pwm_speed_audit_20261004.json`이다. GPS decoder 회귀14개와 PWM 분석6개 통과; 실제 녹화 서비스 이미지/ROS 환경의 격리 컨테이너에서 JSONL 보존·timestamp·no-fix·manifest5개를 제외 없이 검증했다. 실제 녹화를 시작하지 않았다. Jetson GPS serial owner만 source0df5ccd/image20d54fad로 직접 적용했고 다른12개 서비스와 원본 metadata41개를 보존했다. GPS 브릿지/API에 새 필드가 전달되며 현재 실내 no-fix이다. 실제 web image는 변경하지 않았다. 상세 근거는 `field_followup_deployment_20261004.json`이다.
