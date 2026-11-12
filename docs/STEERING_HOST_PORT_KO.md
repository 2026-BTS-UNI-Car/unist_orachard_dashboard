# 조향 호스트 코드 이식 기록 (D-11)

- 작성: 2026-11-13 (묶음 S4b, 브랜치 `screens-20261113`).
- 무엇: 앞바퀴 조향 차량의 호스트 브리지 V3(대시보드 → ESP V3 직렬)와 그 시험을 시뮬레이션 레포에서 이 레포로 옮겼다.
  이 레포가 원본 위치가 되는 첫걸음이다. 시뮬레이션 레포의 `dashboard/scripts/` 사본은 다음 단계(D-13)에서 이 레포를 따라가게 바꾼다.
- 출처: `unist_orchard_simulation` main `506eff394817e95013d7cbd2644d8ce6a3fc52a0`, 폴더 `dashboard/scripts/`.
  설명서는 같은 레포의 `docs/steering/HOST_BRIDGE_V3_KO.md`(요청·오류·래치·시간 규칙)와 `ESP_PROTOCOL_V3_DRAFT_KO.md`(V3 규격)다.
- 실기 ESP·USB·Jetson에서는 돌리지 않았다. 시험은 손으로 쓴 가짜 V3 MCU와 메모리 전송으로 한다. 값은 자리값이다.

## 1. 옮긴 파일

소스는 바이트 그대로다. 시험은 import 경로만 바꿨다(`scripts.test_steer_support` → `tests.test_steer_support`, `test_steer_link.py`의 소스 폴더 경로, 주석의 실행 폴더).

| 이 레포 | 출처 파일(마지막 변경 커밋) | SHA-256 앞 16자(출처 = 이 레포) |
| --- | --- | --- |
| `scripts/esp_motor_v3.py` | `dashboard/scripts/esp_motor_v3.py` (41fb153) | dc24b9f5b4a978ef |
| `scripts/steer_control_core.py` | `dashboard/scripts/steer_control_core.py` (41fb153) | 4c612f5323fe7686 |
| `scripts/steer_bridge_core.py` | `dashboard/scripts/steer_bridge_core.py` (03c6fe7) | f12d7ced08325e43 |
| `scripts/steer_rover_bridge.py` | `dashboard/scripts/steer_rover_bridge.py` (41fb153) | bcd661a6bbc29ba3 |
| `tests/test_steer_support.py` | `dashboard/scripts/test_steer_support.py` (41fb153) | 22716584798f18b6 |

| 이 레포(경로만 고침) | 출처 파일(마지막 변경 커밋) |
| --- | --- |
| `tests/test_steer_codec.py` | `dashboard/scripts/test_steer_codec.py` (41fb153) |
| `tests/test_steer_link.py` | `dashboard/scripts/test_steer_link.py` (41fb153) |
| `tests/test_steer_control_core.py` | `dashboard/scripts/test_steer_control_core.py` (41fb153) |
| `tests/test_steer_bridge_core.py` | `dashboard/scripts/test_steer_bridge_core.py` (03c6fe7) |
| `tests/test_steer_rover_bridge.py` | `dashboard/scripts/test_steer_rover_bridge.py` (41fb153) |
| `tests/test_steer_bridge_equivalence.py` | `dashboard/scripts/test_steer_bridge_equivalence.py` (41fb153) |

조향 코드가 기대는 기존 파일(설계 2.4, A26)은 두 레포에서 SHA-256이 같다: `scripts/rover_control_core.py` 5cc86b226e880406,
`scripts/rover_control_bridge.py` c0a9945ee6d0cfaf, `scripts/esp_motor.py` 3ff12d0d3f60e942, `scripts/esp_wheel_controller.py` d31e12eda589bb22,
`dashboard_server/rover_api.py` c69f3573ab3966e5, `dashboard_server/motor_pwm.py` 60ac85934bb978fa. 이 파일들은 고치지 않았다.

## 2. 기본은 꺼짐

- `docker-compose.yml`의 기본 브리지는 그대로 V2(`ros2-esp-rover-bridge`, `scripts.rover_control_bridge`)다.
- 조향 브리지는 선택 프로필 `steer`의 `ros2-esp-steer-bridge`다. `docker compose --profile steer ...`로만 뜬다. V2와 같은 제어 소켓과 직렬 장치를 쓰므로 V2를 먼저 멈춘다(소켓 옆 잠금이 두 번째 브리지를 거절한다).
  호스트 프로필 JSON(고정 `cal_id`)은 레포 밖 파일을 `UBTS_STEER_HOST_PROFILE_FILE`로 준다.
- V3 펌웨어가 없으면 모든 arm이 `firmware_v3_required`로 거절된다. 옛 차동 화면(`vehicle_profile` 없음)은 arm이 `steer_profile_required`로, left/right 명령은 `differential_command_rejected`로 거절된다.
- `Dockerfile.ros2`에 조향 파일 4개를 복사하는 줄을 더했다(기존 서비스의 동작은 같다).

## 3. 확인 (2026-11-13)

| 확인 | 결과 |
| --- | --- |
| `docker compose -f docker-compose.yml config` 출력 | 이식 전과 바이트까지 같다(553줄) |
| `docker compose --profile steer config --services` | `ros2-esp-steer-bridge`가 더해진다 |
| Windows Python 3.13 `python -B -m unittest discover -s tests -p "test_steer_*.py"` | 127개 통과, 10개 건너뜀(Linux 전용: fcntl, pty, Unix 소켓) |
| WSL Python 3.10.12 같은 명령 | 127개 통과 |
| 기반 이미지 `ubts-steer-sim:wf4c` Python 3.12.3(`--network none`, 읽기 전용 마운트) | 127개 통과 |

모든 결과는 자리값 기반 SIM 결과이고 현장 검증이 아니다.
