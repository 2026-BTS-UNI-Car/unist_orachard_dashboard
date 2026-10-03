# 엔코더 없는 실차 자율주행 적용 순서

10월3일 자료에는 PWM 요청/보고와 raw LiDAR·IMU·GPS가 있지만 휠 회전수나 검증된 차체 속도는 없다. PWM은 전기 명령이며 odometry가 아니다. 카메라는 영상 표시/장면 검토만 사용한다. 실제 모터 자동 출력은 계속 보류한다.

## 상태 추정과 지도

1. raw LaserScan10Hz와 IMU20Hz를 원본 header stamp로 짝지어 SE(2) 상대 운동을 구한다. IMU 상대 회전·충격은 정합 품질을 검토하는 자료다. 미측정 장착 회전을 임의의 yaw offset이나 보정 quaternion으로 대체하지 않는다. 실제 IMU 표시/원본 기록은 변경하지 않는다.
2. 관측 면의 방향 다양성, overlap, 잔차, 여러 초기값의 상충을 검사한다. 가지가 scan plane에 들어오거나 차체가 흔들려 강체 정합이 불가능하면 해당 scan으로 free 공간을 확장하지 않는다. 나무 기둥으로 분류되지 않은 가지·벽도 실제 장애물로 남긴다. 반복 관측은 보조 기준이지 잘못된 정합을 허용하는 근거가 아니다.
3. 승인된 local pose로 타일 OGM을 갱신한다. 지도 화면의 축소는 원래 셀을 삭제하지 않는다. 불확실한 긴 공백은 분리된 submap이며 차체가 정지했다고 가정하거나 GPS 좌표 사이에 직선을 그어 연결하지 않는다. 재위치 추정은 원본 scan의 충분한 기하 검증으로 승인해야 한다.
4. GPS는 epoch 중복을 제거하고 원본 host stamp에 맞춘 정확도 가중 위치 제약으로 사용한다. 현재 hAcc3~10m/비RTK 자료는 row 폭 수준의 조향 기준이 아니다. 차체 yaw와 GNSS course를 동일하게 취급하지 않는다. 짧거나 정지한 구간은 ENU heading alignment가 관측되지 않은 상태로 둔다. GPS 보정으로 과거 pose가 바뀌면 저장된 scan으로 해당 map을 다시 생성한다.

## 실제 차체 운동을 이용한 제어

관측된 pose 변화로 body v/yaw-rate와 지연을 추정한다. 휠 속도나 바퀴별 slip을 관측했다고 표시하지 않는다. reference를 lane center/heading/안전한 body 속도로 주고 출력은 좌·우 PWM이다. heading/yaw-rate 오차의 제한된 PI 보상, 출력 saturation/anti-windup, 변경 속도 제한을 함께 적용한다. equal PWM에도 나타나는 직진 편향은 센서 오차와 구분해 반복 구간에서 확인한다.

피봇은 전진과 별도의 breakaway·출력응답·진동 profile을 쓴다. 현재 안정 pivot은3구간뿐이어서 속도곡선/제동곡선을 확정하지 않는다. SIM `actuator_response` 계수는 stress 가정이다. 현재 보고 PWM→body speed의 고정 변환으로 실차를 제어하지 않는다. 센서 confidence나 통신 freshness가 부족하면 자동 출력0/lease 해제 상태를 요구한다. 출력0은 측정된 정지나 안전한 제동거리와 같지 않다.

## 처음 보는 과수원과 마지막 열

- 원본 orchard의 row 개수/위치/길이나 Gazebo truth를 사용하지 않는다. 지속 관측되는 좌·우 장애물 경계와 통과 가능한 공간으로 local corridor를 찾는다. scan 단면에 나타난 가지 때문에 한쪽 row가 갑자기 사라진 경우는 종료로 결정하지 않는다.
- row 끝 후보는 양측 지지의 지속적 감소, 끝 너머의 관측된 headland 공간, 충분한 실제 이동 baseline을 함께 확인한다. 알려진 빈 공간과 아직 관측하지 못한 공간을 구분한다. 단일 scan이나 GPS 한 점으로 마지막 지점을 확정하지 않는다.
- headland 회전은 전체 차체 swept footprint와 제동 여유를 통과하는 관측된 free 공간에서만 계획한다. 건너편 벽/가지/차량이 그 공간을 차지하면 정지 후보이며, 벽을 row 끝으로 오인해 회전하지 않는다.
- 다음 row를 양측에서 재관측하지 못하면 탐색 범위를 제한한다. 마지막 row와 센서 blackout이 구분되지 않으면 `end_unconfirmed`/정지 상태를 표시한다. 반복 관측으로 다음 통로 부재와 막힌 경계를 확인했을 때만 `orchard_end_confirmed`를 부여한다. 새로운 과수원 모두에서 확실한 검출을 이미 달성했다는 주장을 하지 않는다.
- 60cm 한 평면의 LiDAR가 측정하지 못하는 높이의 장애물은 grid에 없을 수 있다. 실제 footprint/센서 장착 회전/지면과 가지에 대한 관측 검증이 필요한 이유이며, 카메라 AI를 몰래 다시 추가하는 것으로 해결하지 않는다.

## 검증과 적용 게이트

먼저7개 전체 기록의 approved/rejected pose, GNSS 제약 전후 map, 시간별 OGM을 재현한다. 다음은 사람이 측정한 거리·방향과 정지/피봇/제동 반복 구간으로 운동·기하 오차를 평가하는 단계다. 측정 기준 없이 accepted scan 수만으로 정확도를 인증하지 않는다.

SIM은 weak-turn/높은 pivot deadband/좌우 비대칭/가변 slip/진동/IMU·LiDAR dropout/GPS jump·비RTK profile을 각각 시험한다. 설정이 다른 새 과수원, 마지막열, 건너편 벽, 가지 단면, sensor blackout을 포함한다. encoderless runtime은 wheel odometry와 planted row metadata를 배제했는지 증거를 확인한 뒤 채택한다. 현재 live SIM의 legacy wheel 기반 경로가 이 검증을 대신하지 않는다.

그 후에도 실차 초기 적용은 출력 없는 shadow observer/경로 추천→제동 계수 검증→낮은 출력에서 사람이 중단 가능한 시험 순서다. 사용자에게 실차 자동 구동을 다시 허용받기 전에는 실제 PWM 자동 명령을 보내지 않는다. raw 입력/요청 PWM/보고된 핀 값/추정 body 운동/confidence/상태 전이를 함께 기록하여 요청과 실제 관측을 구분한다.
