# 2026-10-10 지도 중단 수정과 기본 OFF 자율주행 코드 설치

**요청한 2시간 기한을 넘겼고 실차 자율주행과 과수원 임무 완료는 미검증입니다.** 제어 코드 설치를 실제 자율주행 완료로 표시하지 않습니다.

Jetson에 기본 OFF 제어 supervisor와 지도 WAIT 경쟁 수정을 설치했습니다. 지도 생성 중 WAIT가 들어오면 취소된 작업만 버리고 기존 지도와 작업자를 유지합니다. 재현 테스트 2개 통과, 기존 R17에서 동일 회귀 실패를 보존했습니다. 원본 시각, source250ms/RAW200ms/IMU60ms, CDR/GID/소유권/손상/Stop 검사와 좌표 계산은 완화하지 않았습니다.

실제 OBSERVE_ONLY 60초 시험 결과:

- 센서 진단 131건 뒤 `ValueError: native_original_ingress_rejected:stale_or_future_original_callback_RAW`로 입력이 종료됐습니다. 이 원본 callback RAW 검증 오류는 미해결이며 이후 지도 갱신이 멈췄습니다.
- 지도 commit 9→27→33회, 마지막 33회/2041셀. 같은 지도 epoch를 유지했고 지도 adapter fault는 0건입니다. 대시보드에서 마지막 지도 유지와 조종 OFF를 확인했습니다.
- 명시적 Stop 후 owner 반환, reader join, 자원 종료, exit0, kill 없음, queue0, input 없음과 기존 13개 서비스 상태 보존을 확인했습니다.
- 센서·지도 지속 동작 적격 판정은 false입니다. 지형 정확도·주행 안전성·실차 임무 완료 검증이 아닙니다.

현재 native 이미지: `sha256:a78bd32786e01d1c60cc9a5f20b0efec21d9c044ba3fe5afd76a584d968e3897`. WEB 이미지: `sha256:fe9bb6cbbd77ce5bbc30bfa9b1f5cf7ac802d2078bac5bdc01e637bb2352a974`. 실제 supervisor 상태 API implemented=true/defaultOFF=true, Start HTTP409/default_OFF. source generation 및 motor lease·arm/drive/keepalive 호출수 0을 확인했습니다. 실제 모터 비영 출력을 전송하지 않았습니다.

제어 코드는 기존 LiDAR/IMU planner, 별도 검증 BODY 피드백과 명령별 swept-space, 측정 FF+P preview, 기존 수동 lease/ticket/deadman을 잇도록 구현됐습니다. 현재 실제 BODY·장착·제동·비상정지·명령별 swept-space 제공자가 없어 자동 출력은 차단됩니다. HTTP에서 검증 선언이나 보정을 넣어 차단을 우회할 수 없습니다. Stop/수동 전환은 generation 취소와 bridge 우선 정지를 요청합니다. 정지 요청 ACK만으로 실제 제동 완료를 주장하지 않습니다.

최우선 남은 작업은 원본 callback RAW 입력 오류 해결과 지속 센서·지도 처리 검증입니다. native GPS factor는 미연결이며 GNSS 마지막 관측은 NoFix입니다. 10월 3일·9월 10일 전체 지도 정확도, 실제 센서 장착/BODY 속도/제동, 가지·벽·열 끝·다음 열·마지막 열과 새 과수원 검증도 미완료입니다. PWM·휠·truth·카메라를 위치 피드백 대신 쓰지 않습니다. 현재 전진 위주 제어 연결을 완전한 과수원 임무로 표시하지 않습니다.

이전 R17 진단 495건·지도 5회 후 경쟁 오류, WEB 실패·rollback, 기존 체크포인트와 TODO 이력은 보존합니다.
