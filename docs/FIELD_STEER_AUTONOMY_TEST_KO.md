# 현장 첫 조향 시험과 첫 자율 시험 (ESP32-S3 차량)

- 작성: 2026-11-16. 대상 차량: ESP32-S3(펌웨어 `ABS-HOLD-8-REBOUND-LIMITS`, BTS7960 3개), Jetson 의 ESP32 조종 브리지(`unist-orachard-ros2-esp32-bridge`)와 자율 실행기(`unist-orachard-row-executor`, 설치 묶음 [`deploy/jetson_row_executor`](../deploy/jetson_row_executor/README_KO.md)).
- 이 문서의 시험은 사용자가 현장에서 한다. 원격으로 모터를 돌리거나 조종 활성화(Arm)를 하지 않는다. **조향 전원은 조정 쪽이 요청할 때만 켠다.**
- 숫자는 자리값이거나 2026-11-16 보드 시험 값이다. SIM 결과(자리값 기반 SIM 결과)는 현장 성능이 아니다. 이 시험이 처음으로 실제 값을 준다.

## 0. 공통 안전

| 항목 | 내용 |
| --- | --- |
| 비상정지 | 물리 전원 차단을 손 닿는 곳에 둔다. 화면 STOP 은 그 다음이다. 브리지 프로세스가 죽으면 펌웨어는 마지막 구동을 계속한다(펌웨어에 구동 시간 제한 없음) |
| 사람 | 차 옆에 한 사람(비상정지), 화면에 한 사람. 화면 쪽이 '시작'을 누르기 전에 소리 내어 알린다 |
| 화면이 멈추게 하는 것 | STOP 버튼, Esc, 탭 숨김·브라우저 닫기(자율 heartbeat 1.0 s 기한), 와이파이 끊김(수동 조종 입력 기한 0.75 s) |
| 그만두는 조건 | 예상하지 않은 움직임, 조향이 반대로 감, 바퀴가 끝에 부딪힘, 화면의 상태가 갱신되지 않음 → 비상정지, 기록, 조정 쪽에 알림 |
| 기록 | 대시보드 녹화(가능하면), 실행기 기록 `~/ubts-row-executor/log/`, 브리지 상태 `~/workspace/unist_orachard_dashboard/runtime/rover_bridge_status.json` |

## 1. 첫 조향 시험 (바퀴 든 상태, 수동 조종만)

실행기는 SHADOW 그대로다(차를 움직이지 않는다). 브리지 쪽 시험 순서는 [`deploy/jetson_esp32_bridge/README_KO.md`](../deploy/jetson_esp32_bridge/README_KO.md) 5절과 같고, 여기서는 실행기가 쓸 값을 함께 잰다.

1. 차를 받침에 올려 네 바퀴를 띄운다. 앞바퀴가 끝까지 돌 공간이 있는지 본다.
2. 전원을 넣기 전 화면 확인: Rover Control 의 조향 패널이 '조향 장치 연결됨', 조종 꺼짐(disarmed). 자율 칸(실행기 연결 뒤)은 SHADOW, `run_state` idle, LiDAR·IMU 나이가 1 s 미만.
3. **조정 쪽 요청이 있을 때** 조향 전원을 켠다.
4. 조종 활성화 → 구동 속도 상한을 10~15 % 로 낮춘다.
5. 극성(B0): 끌기 막대를 왼쪽 5° 로 끈다 → 앞바퀴가 **차의 왼쪽**(뒤에서 볼 때 왼쪽)으로 도는지 본다. 화면의 측정각도 왼쪽(+)이어야 한다. 반대면 바로 STOP 하고 알린다(그 뒤 시험은 하지 않는다).
6. 0점(F1): 명령 0° 에서 앞바퀴가 차체 중심선과 나란한지 본다. 어긋난 각도를 잰다(직선자 + 휴대폰 각도계, 바퀴마다).
7. 눈금과 범위(B2/B3): 명령 왼쪽 10°, 왼쪽 20°, 오른쪽 10°, 오른쪽 20° 에서 **왼쪽·오른쪽 앞바퀴 각도를 각각** 잰다. 왼쪽 끝 → 오른쪽 끝은 20° 씩 두 번에 간다(펌웨어 한 목표의 1초 보정 한도). 실행기 설정의 `steer_esp32_mark_deg`(지금 17.831, SIM 가정)를 이 값으로 바꾼다.
8. 조향 속도(B5): 0 → 왼쪽 20° 와 20° → 0 에 걸린 시간을 잰다(화면의 측정각이 목표 1° 안에 들 때까지; 2026-11-16 보드 시험은 0 → 20 이 3.3~3.6 s).
9. 구동 방향: W 를 짧게 눌렀다 뗀다 → 뒷바퀴 두 개가 앞으로 도는지 본다. 한쪽이 뒤로 돌면 브리지를 그쪽 `R` 로 다시 설치한다(조정 쪽). 맞으면 브리지를 `--direction-verified` 로 다시 설치한다(조정 쪽). 이것 없이는 실행기 Arm 이 `drive_direction_unverified` 로 거절된다.
10. 멈춤 확인: 바퀴가 도는 동안 STOP, Esc, 탭 숨김, 브라우저 닫기, 와이파이 끊기 각각에서 0.75 s 안에 서는지 본다.
11. 조향 전원을 끄고, 잰 값(표 1)을 조정 쪽에 보낸다.

표 1. 첫 조향 시험에서 적을 것

| 기호 | 무엇 | 실행기 설정 |
| --- | --- | --- |
| B0 | 왼쪽 명령에 왼쪽으로 도는가 | `steer_polarity_checked` |
| F1 | 명령 0° 의 바퀴 각(바퀴마다) | `steer_zero_offset_deg`(±1.5° 안이어야 GO) |
| B2/B3 | 명령 ±10°, ±20° 의 바퀴 각(바퀴마다), 기계 끝 각 | `steer_esp32_mark_deg`, `steer_range_measured`, `steer_wheel_soft_margin_deg` |
| B5 | 0 → 20° , 20° → 0 시간 | `steer_measured_rate_deg_s`, `steer_rate_limit_deg_s`(측정의 0.8 배 이하) |
| B4 | 조향 펄스 PWM | 펌웨어 고정값 100/255. `steer_min_pwm_pct` 에 펌웨어 값을 적을지 결정 |
| 구동 방향 | 모터 2·3 의 앞 방향 | 브리지 `--left-forward/--right-forward`, `--direction-verified` |

## 2. 그림자 주행 (자율 전, 차는 사람이 몬다)

실행기는 SHADOW 로 둔다. 실제 열에서 실행기가 통로를 잡는지 본다.

1. 열 시작에 차를 놓는다. 화면 자율 칸에서 Start(그림자 run) 를 누른다. Arm 은 SHADOW 에서 거절된다(`motor_output_not_allowed`, 정상).
2. 사람이 수동 조종으로 열 가운데를 천천히(0.2~0.3 m/s) 끝까지 간다.
3. 자율 칸의 횡오차·방향 오차·통로 신뢰도와 제안 조향을 본다. 열 끝에서 실행기가 끝을 알아보는지 본다.
4. 끝나면 End. 기록(`~/ubts-row-executor/log/`)을 조정 쪽에 보낸다.

## 3. 구동 측정 (땅 위, 수동 조종)

자율 MOTOR 의 설정 검사가 요구하는 구동 값. 곧은 열이나 평지에서 사람이 몬다(녹화 켬).

| 기호 | 무엇 | 실행기 설정 |
| --- | --- | --- |
| C2 | 서 있는 차가 막 움직이기 시작하는 구동 % | `breakaway_duty_pct` |
| C3 | 구동 25~60 % 에서 일정해진 속도(실측이 거의 없는 구간) | `v_measured_m_s`, 현장 조정 파일의 `speed_table` |
| C7 | 0.3~0.4 m/s 에서 구동 0 을 준 뒤 멈출 때까지 거리 | `measured_stop_distance_m` |

## 4. 결정할 것 (사용자)

1. 실행기 조향 GO 문턱 `steer_rate_min_deg_s`(지금 8 °/s, 자리값). 보드의 측정 조향 속도는 약 5.1 °/s(등가각, SIM 가정의 눈금)라 지금 문턱으로는 MOTOR 가 거절된다. SIM 재측정은 4.0 °/s 로 낮춘 설정을 문서로 남기고 썼다(`PREREG_STEER_FOLLOW_REALDIMS_KO.md`).
2. 펌웨어 구동 감시: 패치(`runs/esp32_bridge_20261116/firmware_watchdog/`)를 개발자가 보드에 넣을지. 넣지 않으면 실행기 설정 `steer_esp32_require_fw_watchdog` 을 `false` 로 적어야 Arm 이 된다. 그때는 브리지 프로세스가 죽는 경우를 물리 비상정지와 옆 사람이 막는다.
3. 첫 자율의 목표 속도(현장 조정 `target_speed_m_s`, 0.2~0.3 권함)와 브리지 구동 PWM 상한.

## 5. 첫 자율 시험 (MOTOR)

조건: 1~4 절이 끝났고, 조정 쪽이 MOTOR 설정을 만들어 PC 에서 설정 검사(`feasibility` ok)를 통과시켰다.

1. 실행기를 MOTOR 로 바꾼다(Jetson, 조정 쪽 안내): `python3 install_row_executor.py --apply --mode motor --config-file ~/ubts-row-executor/config.motor.json`. 사전 점검과 준비 확인을 통과해야 바뀐다.
2. 차를 열 시작, 열 가운데에 똑바로 놓는다. 비상정지를 든 사람이 차 옆에 선다. 앞 10 m 에 사람이 없는지 본다.
3. 화면(Rover Control 자율 칸): Start → Arm. 화면이 '자율 주행 시작'을 켤 때까지 기다린다(`go_ready`: 입력 1 s 신선, 조향 상태 신선, 차 정지, 통로 잡음). 막히면 `go_blockers` 를 읽는다.
4. 소리 내어 알리고 '자율 주행 시작'을 **한 번** 누른다. 화면을 계속 보이게 둔다(heartbeat). 실행기가 먼저 서 있는 채로 조향을 맞춘 뒤 출발한다.
5. 지켜볼 것: 조향이 열 가운데로 돌아오는지, 속도가 목표 근처인지, 열 끝에서 스스로 서는지. 화면에 멈춤 까닭(`stop_reason`, `stop_detail`)이 나오면 적는다: `steering_not_following`(조향이 목표를 2 s 넘게 3° 넘게 못 따라감), `steering_ineffective`(자이로와 조향각이 안 맞음), `steering_feedback_stale`, `operator_link_lost`(화면 끊김), `speed_cap_below_min` 등.
6. 멈추기: STOP 또는 비상정지. 다시 가려면 확인(ack) → Start → Arm → 시작. 자동으로 다시 출발하지 않는다.
7. 끝나면 실행기를 SHADOW 로 되돌린다: `python3 install_row_executor.py --apply`(기본 SHADOW 설정) 또는 `--rollback`. 기록을 조정 쪽에 보낸다.

## 6. 참고

- 실행기와 ESP32 브리지 연결(어댑터), 안전 규칙과 남는 틈: 시뮬레이션 레포 `docs/steering/EXECUTOR_ESP32_KO.md`.
- 운영자 API(시작·Arm·go·heartbeat·STOP): 시뮬레이션 레포 `docs/steering/OPERATOR_API_KO.md`.
- ESP32 브리지의 부호와 구동 안전: [`esp_rover_control.md`](esp_rover_control.md) 1절.
