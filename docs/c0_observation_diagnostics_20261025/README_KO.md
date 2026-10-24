# C0 실시간 관측의 처리·게시·지연 분리

이 후보는 소스와 작은 소프트웨어 시험만 완료했다. 실제 보드 설치·관측 Start·센서/모터/STOP 실행은 하지 않았다. 생산 성능 향상이나 지연 원인 확정을 주장하지 않는다.

기준은 실제 배포에서 읽은 autonomy_observation.py SHA `344733...`와 field10 release r2다. 로컬 native_observation_client.py는 해당 snapshot과 달랐으므로 현재 소스라고 가정하지 않았다. snapshot/후속 installer 계보는 확인했지만 설치 전에 Root가 현재 CID·이미지·소스 SHA를 다시 확인해야 한다.

기존 25초 probe는 현재 planner의 처리계수를 읽지 않고, **모든 postcheck를 통과해 게시된 current_decision.field10 계수**만 읽었다. 따라서 12.166초에 32, 20.603초에 76으로 보이는 변화는 실제 입력 처리가 한 번에 몰렸다는 증거가 아니다. 기존 source는 planner가 처리한 뒤 post-plan input getter가 None이면 결과를 버리고 age도 None으로 표시한다. source가 입력을 거부한 것이므로 이를 원 센서 메시지 누락이나 단순 warmup으로 단정할 수 없다. getter None을 fresh로 바꾸거나 오래된 sensor stamp를 새로 쓰지 않는다.

별도의 표시 결함은 재현했다. 아직 preference가 반영된 결과가 한 번도 게시되지 않아 worker version이 None인 동안 status는 None과 현재 version을 비교해 PREFERENCE_CHANGED를 표시했다. 25초 probe의 그 문구 10회는 모두 첫 정상 게시 전이었다. 새 후보는 이 경우 WAIT_RESULT/원래 WAIT를 표시하며, 실제 게시 버전이 바뀐 경우의 PREFERENCE_CHANGED와 기존 결과 폐기 guard는 그대로 유지한다.

실제 overlay는 **autonomy_observation.py 한 파일**이다. poll getter·post-plan getter·planner 단계의 상대 host monotonic 실행 시간과 None 횟수, 실제 planner 반환 시점의 제한된 field10 계수, 게시 성공 sequence를 분리한다. 메타데이터만 추가하며 source의 scan/IMU/header/context, 비교 시계, TTL, 신선도, Stop/Arm/control 경로를 변경하지 않는다. 수치의 단계는 AFTER_PLAN_BEFORE_POSTCHECK_NOT_PUBLICATION이며 실제 제어 자격이 아니다. 모든 계수는 고정 key/상한을 갖고 시간 역행·과도한 계측치는 invalid로만 남긴다.

원 source의 validate·expired·wait_input·Stop·lease lifecycle 메서드 19개가 AST로 동일하다. 변경을 역적용하면 원 source bytes가 정확히 복원된다. 새 generated 10개와 기존 관련 시험 23개(실제 소프트웨어 thread, 장치/ROS/SDK IO 없음)가 통과했다. 단위 시험은 getter None·양의 age 만료·다른 context·진짜 preference 변경·취소를 여전히 fail-closed로 확인한다.

`EXPECTED_API.json`은 현재 보드에 한 파일만 반영할 때 필요한 before/after SHA와 유지할 카메라 revision/API shape다. 설치와 추가 NoMotor 25초 측정은 Root의 별도 유한 supervisor가 담당한다. 기존 camera app.js·1080 원본 전송·config·TTL은 유지해야 한다. 다음 실제 측정에서 계획 시간이 큰지, 두 번째 getter 왕복이 큰지, producer가 실제 None을 반환하는지 분리한 뒤 성능 수정을 결정한다. 현재 계측만으로 TTL 연장이나 누락 제외를 정당화하지 않는다.
