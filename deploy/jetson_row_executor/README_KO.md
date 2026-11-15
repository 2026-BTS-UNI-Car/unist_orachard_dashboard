# Jetson 자율 실행기(열 추종) 설치 묶음

- 작성: 2026-11-16. 대상: Jetson Orin Nano(arm64), ESP32-S3 조종 브리지(`ROVER_ESP_KIND=esp32_abs_hold`)가 이미 설치된 차.
- 실행기: 시뮬레이션 레포 `unist_orchard_simulation`의 `tools/row_executor`(한 커밋을 바이트 그대로). ESP32 브리지와 말하는 방법(어댑터)은 그 레포의 `docs/steering/EXECUTOR_ESP32_KO.md`.
- 기본은 **SHADOW**다: 실행기가 `/scan`, `/imu/data_raw`를 읽고 열 추종 제안만 계산한다. 조종 소켓(`/run/ubts-rover`)을 컨테이너에 **넣지도 않으므로** 브리지에 닿을 수 없다. 차를 움직이는 MOTOR 모드는 현장 측정과 결정이 끝난 뒤 사람이 차 옆에 있을 때만 쓴다(7절).
- 현장 시험 순서(첫 조향 시험, 첫 자율 시험): [`docs/FIELD_STEER_AUTONOMY_TEST_KO.md`](../../docs/FIELD_STEER_AUTONOMY_TEST_KO.md).
- 수치는 자리값이거나 2026-11-16 보드 시험에서 잰 값이다. SIM 결과는 "자리값 기반 SIM 결과"이며 현장 성능이 아니다.

## 1. 무엇이 어디에 생기나

| 것 | 위치 | 내용 |
| --- | --- | --- |
| 컨테이너 | `unist-orachard-row-executor` | 이미 있는 ROS Iron 이미지 `sha256:a59689051b2e…`(Python 3.10.12, numpy 1.21.5, scipy 1.8.0, rclpy; native 관찰 서비스가 쓰는 이미지). 빌드·pull 없음. network·IPC host(센서 컨테이너와 같음), `ROS_DOMAIN_ID`는 LiDAR 드라이버 컨테이너 값, 메모리 1 GiB, restart unless-stopped |
| 실행기 파일 | `~/ubts-row-executor/releases/<시각>_<커밋>/row_executor/` → 컨테이너 `/opt/ubts/row_executor`(읽기 전용) | 레포 파일 그대로. 시작할 때 실행기가 `SHA256SUMS`와 vendor 목록을 다시 확인한다 |
| 설정 | `…/releases/<시각>_<커밋>/config.json` → `/etc/row_executor/config.json`(읽기 전용) | 기본 `config.vehicle_esp32_shadow.json`(front_steer, 단일열, SHADOW, ESP32 브리지, 운영자 API `127.0.0.1:8097`) |
| 현장 조정 값 | `~/ubts-row-executor/state/field_tuning.json` → `/var/lib/row_executor` | 없을 때만 패키지 기본값을 복사한다. 이미 있으면 그대로 둔다(조정한 값을 덮어쓰지 않는다) |
| 기록 | `~/ubts-row-executor/log/` → `/var/log/row_executor` | 실행기 JSONL(최대 1000 MB), ROS 로그 |
| 토큰과 연결 파일 | `<대시보드 runtime>/row_executor/operator_token.txt`, `dashboard_link.json` | 실행기가 시작할 때마다 새 토큰을 쓴다(0600). 연결 파일은 설치기가 실행기 준비 확인 뒤에 쓴다. 대시보드는 `/app/runtime/row_executor/`에서 읽는다 |
| 영수증 | `~/ubts-releases/row-executor-kit/<시각>_<커밋>/RECEIPT.json` | 경로, SHA-256, 컨테이너·이미지 ID, 단계별 시각, 확인 결과. 환경 변수는 이름만. 토큰은 쓰지 않는다 |

- 운영자 API는 `127.0.0.1`에만 묶인다. 같은 Jetson의 대시보드 서버만 닿는다(휴대폰이나 노트북은 대시보드 화면을 거친다).
- 토큰은 화면·로그·영수증에 나오지 않는다(`--no-print-token`). 설치기는 확인할 때 Docker API로 토큰을 읽어 메모리에서만 쓴다.

## 2. 지키는 것

- 쓰지 않는 것: docker compose, build, pull, prune, 컨테이너·이미지·볼륨 지우기, 조종 소켓에 보내는 명령, 모터·Arm 명령, 펌웨어 쓰기.
- 옛 실행기 컨테이너(다시 설치할 때)는 멈추고 `unist-orachard-row-executor-kept-<시각>`(restart no)로 남긴다. 실패한 새 컨테이너는 `-failed-<시각>`, 되돌린 것은 `-rolledback-<시각>`로 남긴다. 사전 점검 컨테이너(`-kit-preflight-<시각>`)도 남긴다.
- 대시보드·브리지·센서 컨테이너는 건드리지 않는다. 대시보드 runtime 폴더에는 `row_executor/` 하위 폴더만 만든다.

## 3. 묶음 만들기 (PC)

대시보드 레포 작업 트리에서:

```sh
python deploy/jetson_row_executor/make_executor_kit.py \
  --sim-repo D:/Develop/UBTS/unist_orchard_simulation --sim-commit <시뮬레이션 커밋> \
  --dash-commit <이 레포 커밋> --out <레포 밖 폴더>
```

- 두 커밋에서 `git cat-file`로 읽는다(작업 트리의 수정은 들어가지 않는다). 같은 커밋이면 같은 바이트의 `.tar.gz`가 나온다. 출력이 있으면 덮어쓰지 않는다.
- `payload/row_executor/`는 `tools/row_executor`의 모든 파일(시험 포함: 실행기의 `SHA256SUMS`가 모두를 적는다), `payload/config/`는 차량 설정, `MANIFEST.json`은 두 커밋, 파일 SHA-256, 실행 이미지.

## 4. Jetson에서 점검 (바꾸는 것 없음)

```sh
scp ubts-row-executor-kit-<sim12>-<dash12>.tar.gz unist@jetson-ssh.sarihub.com:~/ubts-releases/
ssh unist@jetson-ssh.sarihub.com
cd ~/ubts-releases && tar xzf ubts-row-executor-kit-<sim12>-<dash12>.tar.gz && cd ubts-row-executor-kit-<sim12>-<dash12>
sha256sum -c SHA256SUMS
python3 install_row_executor.py            # 점검만. 마지막 줄 '점검 통과' 또는 '설치 불가: <까닭>'
```

점검 항목: 묶음 파일과 패키지 `SHA256SUMS`, 설정(front_steer·ESP32·loopback·`motor_output_enabled`가 모드와 맞음), 실행 이미지(arm64, ROS Iron), 컨테이너 이름, 대시보드 runtime 폴더, ROS 도메인, `127.0.0.1:8097`이 비었는지. 출력의 `plan`에 만들 컨테이너의 명령과 마운트가 있다(SHADOW에서는 `/run/ubts-rover` 마운트와 `--motor-output`이 없다).

## 5. 설치 (`--apply`)

```sh
python3 install_row_executor.py --apply
```

순서: 파일 준비 → 사전 점검 컨테이너(network none; 실행기 import, `SHA256SUMS`, vendor, 설정, 루프 생성, rclpy) → 새 컨테이너 → 시작 → 준비 확인(운영자 API가 답하고 `run_state` idle, 모드 SHADOW, 3 s 동안 실행기가 받은 `/scan` 5 Hz 이상·IMU 15 Hz 이상; run 이 없을 때는 실행기가 센서 나이를 내지 않아 받은 메시지 수로 본다) → 연결 파일. 준비 확인에 실패하면 새 컨테이너를 멈춰 남기고 연결 파일을 쓰지 않으며 옛 실행기를 되살린다.

설치 뒤 읽기 확인:

```sh
docker ps --filter name=unist-orachard-row-executor --format '{{.Names}} {{.Status}}'
docker logs --tail 20 unist-orachard-row-executor        # 'row executor running: mode=SHADOW', 토큰 없음
curl -s http://127.0.0.1:8080/api/executor/status | python3 -m json.tool | head -40   # 대시보드가 연결 파일을 읽을 때
```

- 대시보드 화면: Rover Control 의 자율 칸에 실행기 상태(`run_state` idle, SHADOW)가 보인다. LiDAR·IMU 나이는 run 을 연 뒤에 나온다. 이것은 연결 파일을 읽는 대시보드 판(`dashboard_server/executor_proxy.py`의 `LinkedExecutor`, 2026-11-16 브랜치 `jetson-autonomy-20261116`)이 설치되어 있어야 한다. 그 전 판은 '실행기 설정 없음'을 보인다(실행기는 그래도 SHADOW로 돈다).
- SHADOW 에서 화면의 Start 는 그림자 run 을 연다(제안 기록만, 차는 실행기와 상관없이 수동 조종). Arm 은 `motor_output_not_allowed`로 거절된다.

## 6. 되돌리기 (`--rollback`)

```sh
python3 install_row_executor.py --rollback               # 마지막 설치(LATEST_RECEIPT)
```

실행기를 멈추고 `-rolledback-<시각>`(restart no)로 남기며, 연결 파일을 `dashboard_link.json.rolledback-<시각>`으로 옮긴다(대시보드는 '설정 없음'). 그 설치가 옛 실행기를 남겼으면 이름, restart 정책, 돌던 상태를 되살린다. 결과는 영수증 폴더의 `ROLLBACK_<시각>.json`.

## 7. MOTOR 모드 (현장 시험 날에만)

MOTOR 모드는 차를 움직일 수 있는 실행기다. 그래도 운영자가 대시보드에서 Start → Arm → '자율 주행 시작'(go)을 누르기 전에는 구동 0 말고 아무것도 보내지 않고, 화면이 보이는 동안의 heartbeat(1.0 s 기한)가 끊기면 선다. 설치 전 조건(모두 [`docs/FIELD_STEER_AUTONOMY_TEST_KO.md`](../../docs/FIELD_STEER_AUTONOMY_TEST_KO.md)의 측정과 결정):

1. 브리지가 구동 방향 확인을 마쳤다(`ROVER_ESP32_DIRECTION_VERIFIED=1`, 브리지 묶음의 `--direction-verified`). 아니면 실행기 Arm 이 `drive_direction_unverified`로 거절된다.
2. 실행기 설정에 현장 값이 들어갔다: 구동 C2·C3·C7, 조향 B0(극성)·B2/B3(범위, `steer_esp32_mark_deg`)·B4·F1, 측정 조향 속도와 GO 문턱 결정(`steer_rate_min_deg_s`), `motor_output_enabled: true`. 실행기의 설정 검사(`feasibility` ok)를 지나야 사전 점검을 통과한다.
3. 펌웨어 구동 감시(watchdog) 패치가 들어갔거나(`steer_esp32_require_fw_watchdog: true` 그대로), 들어가지 않았다면 그 위험(브리지 프로세스가 죽으면 마지막 구동이 계속됨)을 받아들인다는 결정과 함께 `false`로 적고 물리 비상정지와 사람이 차 옆에 있다.

```sh
python3 install_row_executor.py --apply --mode motor --config-file ~/ubts-row-executor/config.motor.json
```

시험이 끝나면 SHADOW 로 되돌린다: `python3 install_row_executor.py --apply`(기본 SHADOW 설정) 또는 `--rollback`.

## 8. 남는 위험

- ESP32 펌웨어에는 구동 시간 제한이 없다. 실행기가 죽으면 브리지의 입력 기한(0.75 s)이 STOP 한다(가짜 보드 시험으로 확인). 브리지 프로세스가 SIGKILL 이나 멈춤으로 죽고 보드가 USB 전원을 받으면 마지막 구동이 계속 돈다. 펌웨어 감시 패치 전까지는 물리 비상정지가 마지막 수단이다.
- 조향 명령 눈금(명령 20 = 바퀴 몇 도)은 아직 재지 않았다. 설정의 `steer_esp32_mark_deg` 17.831 은 SIM 가정(기계 한계 − 1°)이다.
- 실행기의 열 추종은 SIM 에서만 쟀다(자리값 기반 SIM 결과). 실차의 조향 응답(정지 중 약 5 °/s, 펄스 유지)과 구동 응답은 현장에서 처음 본다.

## 9. 확인한 것 (2026-11-16)

- 시험(PC, 가짜 Docker·가짜 호스트): `tests/test_jetson_row_executor_kit.py` 14개. 점검이 아무것도 바꾸지 않음, 거절(이미지 없음, 포트 사용 중, 모드와 설정이 다름, loopback 아님, 묶음 변조, 패키지 SHA256SUMS 어긋남), SHADOW 설치(명령·마운트·토큰·연결 파일·조정 파일 보존), 센서가 없으면 연결 파일 없이 자동 되돌림, 옛 실행기 보존과 복원, 사전 점검 실패 시 바뀐 것 없음, MOTOR 모드의 마운트, 되돌리기, 묶음 만들기, 도구에 지우기·prune·build·compose·subprocess 없음. 연결 파일 읽기: `tests/test_executor_proxy.py`(Linux 경로 시험 포함).
- 실행기와 ESP32 브리지의 끝단 시험(가짜 펌웨어, 시뮬레이션 레포 `tools/row_executor/tests/test_esp32_executor_*.py`): Windows Python 3.13 과 WSL Python 3.10 + numpy 1.21.5 + scipy 1.8.0(Jetson 과 같은 판)에서 통과.
- Jetson 설치(SHADOW, 시뮬레이션 a6630c6 + 이 레포 8c891b2 묶음): 첫 시도는 준비 확인 규칙의 잘못(run 이 없을 때 센서 나이는 늘 비어 있음)으로 60 s 뒤 스스로 되돌렸다(`unist-orachard-row-executor-failed-20261115T181822Z`로 남음, 그 사이 실행기는 `/scan` 약 10 Hz, IMU 약 32 Hz 를 받고 있었다). 고친 묶음으로 설치됨: 사전 점검 Python 3.10.12 / numpy 1.21.5 / scipy 1.8.0, `SHA256SUMS`·vendor 통과, `motor_allowed` false. 읽기 확인: `run_state` idle, 모드 SHADOW, 5 s 동안 스캔 51개(10.1 Hz)·IMU 167개(33 Hz) 처리, 버린 스캔 0, 놓친 틱 0, 루프 약 1.1 ms, CPU 약 15 %, 메모리 53 MiB, 마운트에 `/run/ubts-rover` 없음, 로그에 토큰 없음.
- 대시보드: 그때 설치된 대시보드(feb4600)는 연결 파일을 읽지 않아 `/api/executor/status`가 `executor_not_configured`였다. 이 레포의 `executor_proxy.py`(연결 파일 읽기)를 그 대시보드 컨테이너 안에서 파일을 바꾸지 않고 한 번 실행해 보니 연결 파일과 토큰으로 실행기에 닿았다(`configured` true, idle, SHADOW). 화면에 보이려면 이 판의 대시보드를 대시보드 묶음으로 설치해야 한다.
