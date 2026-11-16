# Jetson ESP32-S3 조종 브리지 설치 묶음

차의 옛 ESP8266 보드를 빼고 ESP32-S3 보드(BTS7960 3개: 1 조향, 2 왼쪽 구동, 3 오른쪽 구동)를 달았다(2026-11-16).
옛 조종 브리지 컨테이너(`unist-orachard-ros2-esp-rover-bridge`)는 없는 옛 장치(CP2102)를 가리켜 시작하지 못한다(Exited 255).
이 묶음은 그 역할을 새 컨테이너 `unist-orachard-ros2-esp32-bridge`에 넘긴다. 브리지 코드는 `scripts/esp32_steer_bridge.py`
(`ROVER_ESP_KIND=esp32_abs_hold`), 설명은 [`docs/esp_rover_control.md`](../../docs/esp_rover_control.md).

- 작성: 2026-11-16. **아직 Jetson 에 설치하지 않았다.** 설치는 조정 쪽이 정한다.
- 명령은 Jetson 에서 실행한다. Python 3 표준 라이브러리만 쓴다(Ubuntu 22.04 의 Python 3.10).

| 파일 | 하는 일 | 어디서 |
| --- | --- | --- |
| `install_esp32_bridge.py` | 점검(기본), `--apply` 로 설치 | Jetson |
| `rollback_esp32_bridge.py` | 점검(기본), `--apply` 로 되돌리기 | Jetson |
| `make_bridge_kit.py` | 레포의 한 커밋에서 묶음 만들기 | PC |
| `MANIFEST.json`, `payload/scripts/*.py`, `SHA256SUMS` | 묶음 내용 | `make_bridge_kit.py` 가 만듦 |

## 1. 하는 일과 하지 않는 일

하는 일(`--apply`):

1. 점검: 묶음 파일 SHA-256, Docker, 옛 브리지 컨테이너와 그 이미지, ESP32 장치(by-id 링크 → `/dev/ttyACM*` 문자 장치, 주 번호 166,
   udev `ID_SERIAL` 이 `Espressif_USB_JTAG_serial_debug_unit_30:30:F9:3C:D8:60`), 제어 소켓 잠금(`/run/ubts-rover/bridge.lock`)이 비어 있는지
   (다른 브리지가 돌지 않는지), 대시보드가 같은 제어 소켓 폴더와 runtime 폴더를 쓰는지.
2. 미리 확인: 옛 브리지 이미지로 만든 짧은 컨테이너(네트워크 없음, 장치·마운트 없음)에 새 파일을 넣고 브리지 모듈을 import 해 본다. 그 컨테이너는 지우지 않고 멈춘 채 둔다.
3. 새 컨테이너: 옛 브리지와 **같은 이미지**(빌드·pull 없음), 같은 마운트(runtime, `/run/ubts-rover`, `/run/lock`, `/run/udev` 읽기 전용),
   host 네트워크·IPC, 보안 옵션, 로그 설정. 명령은 `source /opt/ros/iron/setup.bash && exec python3 -m scripts.esp_rover_bridge`
   (`exec` 이라 `docker stop` 의 SIGTERM 이 브리지에 닿고 브리지가 STOP 을 보낸다. 정지 유예 10초). 새 파일 5개
   (`esp32_abs_hold.py`, `esp32_steer_bridge.py`, `esp_rover_bridge.py`, `steer_control_core.py`, `rover_control_core.py`)를 컨테이너의 `/app/scripts` 에
   넣고 다시 읽어 SHA-256 을 맞춘다. 이 단계까지 옛 컨테이너는 건드리지 않는다.
4. 옛 컨테이너: 돌고 있으면 멈추고, 재시작 정책을 `no` 로 바꾸고, 이름을 `unist-orachard-ros2-esp-rover-bridge-kept-<시각>` 으로 바꿔 **보존**한다.
5. 새 컨테이너 시작, 준비 확인(최대 40초): 컨테이너가 돌고, 상태 파일(`runtime/rover_bridge_status.json`)이 `esp_kind: esp32_abs_hold`,
   `connected: true`, `armed: false` 로 새로 쓰이고, (이 사용자가 열 수 있으면) 제어 소켓이 `vehicle_profile: front_steer` 로 답한다.
6. 4 이후 어디서든 실패하면 자동으로 되돌린다: 새 컨테이너를 멈추고(STOP 을 보냄) `-failed-<시각>` 으로 이름을 바꿔 두고(재시작 `no`),
   옛 컨테이너의 이름과 재시작 정책을 돌려 놓는다. 결과는 영수증 JSON.

장치 연결 방식: 기본 `--device-mode by-id`. 새 컨테이너의 장치는 by-id 경로로 적는다. Docker 는 컨테이너를 시작할 때마다 이 링크를 풀어
그때의 노드(지금 `ttyACM1`)를 넣는다(재부팅으로 GNSS 와 ESP32 의 ttyACM 번호가 바뀌어도 맞는 장치). 그리고 브리지는 열 때마다
`/run/udev/data/c166:N` 의 `ID_SERIAL` 을 다시 확인해 다른 장치(예: u-blox GNSS)면 열지 않는다. `--device-mode node` 는 지금 노드를 고정한다(권하지 않음).

하지 않는 일: `docker compose`(Jetson 의 compose 파일은 옛 이미지를 가리킨다), build, pull, prune, 컨테이너·이미지·볼륨 삭제,
펌웨어 쓰기, 모터 출력, 조종 활성화(arm). 대시보드 컨테이너와 다른 ROS 컨테이너는 바꾸지 않는다(대시보드는 [`../jetson_dashboard/`](../jetson_dashboard/README_KO.md) 묶음).

## 2. 안전

- 설치 중과 아무도 조종 활성화를 하지 않는 동안 브리지가 보드에 쓰는 것은 `STOP`, `STATUS`(초당 10번), `2 S`·`3 S`(초당 1번)뿐이다.
  예외는 사람이 대시보드의 **조향 보정** 버튼을 눌렀을 때다(8절): 그때는 `STOP` 뒤 `CAL …` 줄만 쓴다(모터 2·3 명령 없음).
- **USB 포트를 열면 ESP32 가 다시 켜질 수 있다**(2026-11-16 한 번 관측). 다시 켜지면 모든 출력이 꺼진다. 브리지는 열자마자 STOP 을 보내고,
  READY 배너(또는 3초) 뒤 STOP 과 STATUS 의 답을 확인하고서야 준비 상태가 된다.
- 원본 펌웨어(ABS-HOLD-8-REBOUND-LIMITS)의 **구동 모터 2·3 에는 시간 제한이 없었다.** 2026-11-16 구동 감시 패치(300 ms, FW 줄 `DRIVE_WD=300`)를 보드에 썼고 브리지는 구동 중 `HB`를 보낸다. 호스트도 겹으로 막는다: 입력 기한 0.75초(조종권 규칙)와 별도의 구동 TTL,
  STOP 이 확인될 때까지 다시 보내기, 정지 중 1초마다 0 다시 보내기, SIGTERM·SIGINT·SIGHUP·atexit 에서 STOP.
- **남는 위험**: 브리지 프로세스가 SIGKILL·멈춤·Jetson 전원 문제로 죽었는데 보드가 계속 전원을 받으면 마지막 구동 명령이 계속 돈다
  (PC 시험에서 확인: `tests/test_esp32_bridge_linux.py`). 이 위험은 2026-11-16 보드에 쓴 펌웨어 감시가 0.3 s 로 줄인다(실제 보드에서는 아직 재지 않음). 물리 비상정지는 그대로 마지막 수단이다.
  `docker kill`, `docker stop -t 0` 을 쓰지 않는다.
- 구동 방향(2·3 의 F 가 앞인지)은 아직 확인하지 않았다(`ROVER_ESP32_DIRECTION_VERIFIED=0`). 첫 시험은 바퀴를 들고 한다(5절).
- 구동 PWM 상한은 기본 153(60 %, 화면의 속도 상한과 같음). `--max-drive-pwm` 으로 낮출 수 있다.
  **2026-11-16 지금 설치: `--max-drive-pwm 0`**(사용자 결정, 첫 조향 시험 동안 구동 바퀴가 돌지 않게). 구동 방향 시험 전에 되돌리기(`rollback_esp32_bridge.py --apply`) 뒤
  기본값으로 다시 설치한다(같은 이름이면 설치기가 `new_name_in_use`로 거절하므로).

## 3. 준비

- 차는 멈춰 있고, 바퀴는 땅에서 들거나 차를 고정한다. 대시보드 조종 비활성.
- ESP32 보드가 Jetson USB 에 꽂혀 있고 `ls -l /dev/serial/by-id/` 에 `usb-Espressif_USB_JTAG_serial_debug_unit_30:30:F9:3C:D8:60-if00 -> ../../ttyACM1` 이 보인다.
- Docker 를 쓸 수 있는 사용자(`docker ps` 가 되면 그대로, 아니면 `sudo`). 제어 소켓 응답 확인은 root 만 할 수 있으므로 `sudo` 로 실행하면 확인이 하나 더 된다.
- 화면에서 조향 패널을 쓰려면 같은 커밋의 대시보드 묶음도 설치한다([`../jetson_dashboard/README_KO.md`](../jetson_dashboard/README_KO.md)).
  대시보드는 `DASHBOARD_VEHICLE` 을 바꾸지 않아도 브리지가 `vehicle_profile: front_steer` 를 내면 Rover Control 의 주행 제어를 조향 패널로 바꾼다.
  순서는 브리지 → 대시보드, 또는 대시보드 → 브리지 어느 쪽도 된다(옛 대시보드는 조향 패널이 없어 차동 패드만 보이고, 그 패드의 left/right 명령은 새 브리지가 거절하고 정지한다).

## 4. 순서

```sh
mkdir -p ~/ubts-releases && cd ~/ubts-releases
tar xzf ubts-esp32-bridge-kit-<id>.tar.gz
cd ubts-esp32-bridge-kit-<id>
sha256sum -c SHA256SUMS                        # 모두 OK
python3 install_esp32_bridge.py                # 점검만. 끝 줄 '점검 통과' 면 설치 가능 (종료 코드 0, 아니면 2)
sudo python3 install_esp32_bridge.py --apply   # 설치. 끝 줄 '결과: installed'
```

확인(차는 움직이지 않는다):

```sh
docker ps --format '{{.Names}}\t{{.Status}}' | grep esp
docker logs --tail 20 unist-orachard-ros2-esp32-bridge
python3 -c "import json;d=json.load(open('/home/unist/workspace/unist_orachard_dashboard/runtime/rover_bridge_status.json'));print(d['esp_kind'],d['connected'],d['armed'],d['firmware'],d['steering']['command_equiv_deg'])"
```

대시보드 Rover Control: '조향 장치 연결됨', 목표/측정의 측정값이 보드의 `COMMAND_EQUIV` 와 같은 쪽(왼쪽 +, 오른쪽 −)인지 본다. 조종 활성화는 하지 않는다.

되돌리기:

```sh
python3 rollback_esp32_bridge.py               # 점검
sudo python3 rollback_esp32_bridge.py --apply  # 새 컨테이너 멈춤(STOP), 옛 컨테이너 이름·재시작 정책 복원(시작은 --start-old 일 때만)
```

설정을 바꾸려면(예: 구동 방향) 되돌린 뒤 옵션을 붙여 다시 설치한다. 되돌리면 새 컨테이너의 이름이 `-rolledback-<시각>` 으로 바뀌므로 같은 이름으로 다시 설치할 수 있다.

| 옵션 | 기본 | 뜻 |
| --- | --- | --- |
| `--device` | by-id 경로(위) | ESP32 by-id 경로. ttyACM 을 훑어 고르지 않는다 |
| `--identity` | `Espressif_USB_JTAG_serial_debug_unit_30:30:F9:3C:D8:60` | 기대하는 udev ID_SERIAL |
| `--device-mode` | `by-id` | `node` 는 지금 노드를 고정(권하지 않음) |
| `--max-drive-pwm` | 153 | 구동 PWM 상한(0~255) |
| `--left-forward`, `--right-forward` | `F` | 모터 2·3 에서 앞으로 가는 방향(바퀴 든 시험 뒤 정함) |
| `--direction-verified` | 꺼짐 | 방향을 현장에서 확인한 뒤에만 |
| `--allow-esp-absent` | 꺼짐 | ESP32 가 꽂혀 있지 않아도 브리지만 띄움(브리지는 2초마다 다시 열기를 시도) |
| `--ready-timeout` | 40 | 준비 확인 시간(초) |

## 5. 현장 첫 시험(사용자, 조정 쪽이 알릴 때)

1. 바퀴를 든다. 물리 비상정지(전원 차단)를 손 닿는 곳에 둔다.
2. 대시보드 Rover Control → 조종 활성화 → 속도 상한을 10~15 % 로 낮춘다.
3. W 를 짧게 눌렀다 뗀다. 구동 바퀴 두 개(모터 2 왼쪽, 3 오른쪽)가 앞으로 도는지 본다. 한쪽이 뒤로 돌면 그쪽 옵션을 `R` 로 바꿔 다시 설치한다.
4. A·D(끝까지)와 끌기 막대로 조향을 본다. A 는 왼쪽, D 는 오른쪽, 놓으면 가운데로. 왼쪽 끝 → 오른쪽 끝은 20° 씩 두 번에 나눠 간다(펌웨어의 한 목표 1초 보정 한도 때문).
5. STOP, Esc, 탭 숨김, 브라우저 닫기, 와이파이 끊기 각각에서 바퀴가 서는지 본다(0.75초 안).
6. 결과를 조정 쪽에 알린다. 방향이 맞으면 `--direction-verified` 로 다시 설치한다.

## 6. 문제 생겼을 때

| 화면의 말 | 뜻 | 할 일 |
| --- | --- | --- |
| `device_missing` | by-id 링크가 없음 | USB 연결 확인, `ls -l /dev/serial/by-id/` |
| `udev_identity_mismatch` | 링크가 다른 장치를 가리킴 | 장치 확인. 다른 보드면 `--identity` 를 조정 쪽과 정함 |
| `control_socket_in_use` | 다른 브리지가 제어 소켓을 잡고 있음 | `docker ps` 로 어떤 브리지인지 확인. 두 브리지를 같이 돌리지 않는다 |
| `new_name_in_use` | 새 이름의 컨테이너가 이미 있음 | 이미 설치됨. 되돌리기 뒤 다시 |
| `preflight_import_failed` | 이 이미지에서 새 파일이 import 되지 않음. 바뀐 것 없음 | 영수증의 `preflight_log_tail` 을 조정 쪽에 보냄 |
| `ready_timeout` | 새 브리지가 준비되지 않음(보드 답 없음 등). 자동으로 되돌림 | 영수증의 `ready`, `new_log_tail` 을 조정 쪽에 보냄 |
| 경고 `ModemManager runs` | ModemManager 가 새 ttyACM 을 건드릴 수 있음 | 2026-11-16 점검에서 보임. 브리지는 TIOCEXCL 로 포트를 혼자 쓴다. 문제가 보이면 조정 쪽에 알림 |

영수증: `~/ubts-releases/esp32-bridge-kit/<UTC 시각>_<커밋 12자>/RECEIPT.json`(`LATEST_RECEIPT` 가 가리킴). 환경변수는 이름만 적는다.
Docker 에 남는 것(지우지 않음): `...-kept-<시각>`(옛 브리지), `...-kit-preflight-<시각>`(미리 확인), 실패했다면 `...-failed-<시각>`, 되돌렸다면 `...-rolledback-<시각>`.

## 7. 확인한 것

- 시험(PC, 가짜 Docker·가짜 호스트): `tests/test_jetson_esp32_bridge_kit.py` 8개. 점검이 아무것도 바꾸지 않음, 거절(장치 없음, 다른 장치, 잠금, 이름, 옛 컨테이너 없음,
  묶음 변조), 설치(옛 컨테이너 보존·재시작 no, 같은 이미지·마운트, by-id 장치, `exec`, ESP32 설정, 파일 SHA-256), ESP 없음 → 자동 되돌림,
  미리 확인 실패 → 바뀐 것 없음, 되돌리기, 도구에 삭제·prune·pull·build·compose·subprocess 없음.
- Jetson 읽기 전용 점검(2026-11-16, 파일을 쓰지 않고 메모리에서 `run_checks` 만 실행): 막는 것 없음. 옛 브리지 Exited(255, 옛 장치 없음),
  이미지 `fa63d0d9…`(arm64), 새 이름 비어 있음, by-id → `/dev/ttyACM1`(166:1), ID_SERIAL 일치, `/run/ubts-rover` 잠금 비어 있음,
  대시보드는 같은 제어 소켓과 runtime 폴더를 씀, `DASHBOARD_VEHICLE` 없음. 경고: ModemManager 실행 중.
- 설치(`--apply`)는 Jetson 에서 아직 한 번도 하지 않았다.

## 8. 실시간 조향 보정과 펌웨어 업데이트 한 번 (2026-11-16 제안, 아직 쓰지 않음)

좌우 끝은 배터리 전압·바닥·하중에 따라 날마다 다를 수 있어, 보정 표시(RIGHT/CENTER/LEFT)를 보드의 NVS 에 두고 대시보드에서 다시 잰다.
설명은 [`docs/esp_rover_control.md`](../../docs/esp_rover_control.md) 1-4절. 브리지 쪽은 제어 소켓 op `calibrate`
(`find_left`, `find_right`, `set_center`, `apply` {`left_margin_counts`, `right_margin_counts`, 기본 30}, `default`, `read`)이고,
조종 활성화가 꺼져 있고 움직이지 않을 때만 받는다. 진행과 결과는 상태 파일의 `calibration` 칸에 있다.

이 기능은 펌웨어의 `CAL` 명령이 있어야 한다(FW 줄 끝 `CAL_RUNTIME=1`). **지금 보드의 펌웨어에는 없다**: 이 묶음의 브리지는 그때 아무것도 보내지 않고
`calibration_needs_firmware_update` 로 답하며, 화면의 보정 버튼도 그 까닭으로 꺼져 있다. 새 펌웨어는 이름(`ABS-HOLD-8-REBOUND-LIMITS`)을 그대로 두므로
옛 브리지도 새 펌웨어에 그대로 붙는다. 처음 켜면 NVS 가 비어 지금과 같은 기본값(1681 / 1900 / 2180)으로 돈다.

펌웨어 파일(조정 쪽 기록 폴더 `steering-sim-20261108/runs/steer_runtime_cal_20261116/fw/`): 소스 `cal_runtime/`, 앱 `build/BTS7960_Serial_Control.ino.bin`
(315,552 B, sha256 `660a991648ca4c1bd7f1f09edd5c858b679c2b08959c3074ad5e28f417651632`, Arduino-ESP32 2.0.17, FQBN `esp32:esp32:esp32s3:CDCOnBoot=cdc`),
지금 보드와 같은 파티션 표. 되돌리기용 지금 판(구동 감시 판): `runs/fw_watchdog_20261116/build/patched_app.bin`.

**쓰기는 차를 고정하고 한다(사용자, 현장).** esptool 로 쓰는 약 10 초 동안 보드는 리셋·다운로드 모드라 펌웨어가 돌지 않고 모터 드라이버 핀을
소프트웨어가 잡지 못한다(STOP·구동 감시 없음). 그래서 원격으로 쓰지 않는다. 다음 중 하나를 한 뒤에만 쓴다:
차를 받침에 올려 구동 바퀴를 땅에서 띄움, 또는 구동 모터 선(드라이버 2·3 출력)을 뺌, 그리고 사람이 차 옆에서 물리 비상정지를 손에 둠.

Jetson 에서(2026-11-16 감시 판을 쓸 때와 같은 방법, esptool 4.7.0 가상환경 `~/ubts-releases/fw-watchdog-20261116`):

```sh
sudo python3 rollback_esp32_bridge.py --apply           # 1. 브리지가 STOP 을 보내고 포트를 놓는다
esptool.py --chip esp32s3 --port /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_30:30:F9:3C:D8:60-if00 \
  write_flash --flash_mode keep --flash_freq keep --flash_size keep 0x10000 cal_runtime_app.bin   # 2. app0 만. 'Hash of data verified'
python3 read_status.py                                  # 3. STATUS 만 보냄: FW 줄 끝 'CAL_RUNTIME=1', CAL 줄 'STORE=DEFAULT GUARD=1300..2500'
sudo python3 install_esp32_bridge.py --max-drive-pwm 0 --apply   # 4. 이 묶음(새 커밋)의 브리지. 구동 상한은 사용자 결정대로
```

부트로더·파티션·otadata 는 쓰지 않는다. 대시보드도 같은 커밋 묶음으로 설치해야 Rover Control 에 '조향 보정' 칸이 보인다.
되돌리기: 1 → `write_flash … 0x10000 patched_app.bin` → 3(`CAL_RUNTIME` 없음) → 4.

보정 자체(쓰기 뒤, 사용자): 앞바퀴는 실제로 쓰는 조건(땅에 닿은 채)이 좋다. 끝 찾기는 조향 모터만 PWM 100 으로 짧게 밀고 구동 모터는 쓰지 않는다.

1. Rover Control → 주행 제어 → **조향 보정** 칸을 연다. 조종 활성화는 꺼 둔다(켜져 있으면 버튼이 꺼진다).
2. 앞바퀴를 똑바로 둔 채 `지금 위치를 가운데로`(필요할 때만).
3. `왼쪽 끝 찾기` → 끝나면 '왼쪽 끝 (찾은 값)' 이 생긴다. `오른쪽 끝 찾기`도 같다. 가운데 근처에서 시작하면 빠르다. 멈추려면 STOP(어디서든).
4. `적용`(여유 기본 30 count) → 확인 창의 새 값을 보고 승인. 보드에 저장되고, 명령 ±20(A/D 끝)이 새 값이 된다.
5. 조종 활성화 뒤 A·D 로 끝까지 가서 끝 근처에서 서는지, '조향 유지 취소' 가 없는지 본다. 넓어진 보정에서는 브리지가 한 목표를 280 count 이하로 나눠 보낸다.
6. 처음 값으로 돌아가려면 `기본값으로`.
