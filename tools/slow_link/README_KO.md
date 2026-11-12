# 느린 통신 시험대 (slow_link)

차의 2.4 GHz 무선 AP에 휴대폰이나 노트북으로 붙었을 때처럼 느리고 가끔 끊기는 연결을 PC에서 흉내 내고,
그 위에서 대시보드를 실제로 띄워 자료가 얼마나 늦게, 얼마나 많이 오가는지 재는 도구다.
10월에 만든 측정 하네스를 정리해 옮기고 두 가지 측정을 더했다:
**조종 명령과 STOP의 왕복 시간(측정 A)**, **처음 열기와 다시 열기의 무게(측정 B)**.

- 모든 프로세스는 `127.0.0.1`에만 붙는다. Jetson, SSH, Docker, 직렬 장치, 모터는 쓰지 않는다.
- 조종 요청은 **기록만 하는 가짜 제어 끝점**(`control_endpoint.py`)으로 간다. 모터 대신 받은 것을 파일에 적는다.
  조종권·표·입력 만료는 제품의 `scripts/rover_control_core.py`의 `RoverControl` 클래스가 제 시간 값 그대로 판단한다.
- 재는 대상은 작업 트리가 아니라 **커밋 하나를 `git archive`로 내보낸 사본**이다(임시 폴더). 커밋 안 된 변경은 재지 않는다.
  커밋 해시는 모든 결과 파일(`run.json`, `SUMMARY.json`)에 들어간다.
- 결과는 실행마다 **새 폴더**에 쓴다. 이미 있는 결과를 지우거나 덮어쓰지 않는다. 레포 안과 `_archive` 안에는 쓰지 않는다.
- GNSS: 기록에서 상대 거리(동·북 m)만 읽어 지어낸 원점에 더한다. 기록의 좌표는 읽지도 쓰지도 않는다.

## 필요한 것

- Python 3.11 이상. 시험대 스택에는 `numpy`, `websockets`(12), `opencv-python`, `psutil`이 필요하다.
  이 PC에는 `%LOCALAPPDATA%/ubts-uiperf-venv`(Python 3.13)가 있다. 윈도에서 래스터 커널을 빌드하려면 `ziglang`도 있어야 한다(없으면 대시보드는 numpy 경로로 돈다).
  단위 시험은 표준 라이브러리만 쓴다.
- Node 22 이상 (전역 `fetch`와 `WebSocket`을 쓴다)
- 이 PC에 깔린 Chrome (`SLOW_LINK_CHROME`이나 설정의 `chrome`으로 다른 경로를 줄 수 있다)
- 재생할 기록 추출본. 레포에 넣지 않는다. 경로를 JSON 파일로 준다:

```json
{"lidar_npz": "D:/.../lidar/20261010T042301Z_both.scans.npz",
 "drive_json": "D:/.../drive-extract/20261010T042301Z_both.drive.json.gz",
 "camera_dir": "C:/Users/.../ubts-uiperf-run/camera720"}
```

`lidar_npz`는 스캔(3240빔, 약 10 Hz), `drive_json`은 IMU·GPS, `camera_dir`은 1280x720 JPEG 프레임 폴더다.
카메라 원본(1920x1080) 경로를 위한 프레임은 `camera_dir`의 프레임을 키워 실제 카메라 JPEG 평균 크기(약 112 kB,
`docs/CAMERA_ORIGINAL1080_PREVIEW.md`)에 가깝게 다시 압축해 `<work>/inputs/`에 한 번 만든다. 화질은 실제보다 무르고, 바이트 크기만 맞췄다.

## 돌리는 법

명령은 이 도구 폴더에서 친다. `<py>`는 위 패키지가 깔린 파이썬이다.

```
# 시나리오 하나, 프로필 하나
<py> -B run_bench.py --scenario drive --profile bad --source HEAD --inputs <inputs.json> --out-root <결과 폴더>

# 프로필 4개를 차례로 (each 새 폴더)
<py> -B run_bench.py --scenario drive --profile all --source baseline-installed-20261025 --inputs <inputs.json> --out-root <결과 폴더>

# 같은 조건을 세 번 (실행 사이 흔들림 보기)
<py> -B run_bench.py --scenario drive --profile normal --source HEAD --repeat 3 --inputs <inputs.json> --out-root <결과 폴더>

# 표로 보기 (결과 폴더 하나 또는 여럿, 또는 그 위 폴더)
python summarize.py <결과 폴더>
python summarize.py <결과 폴더> --markdown
python summarize.py <결과 폴더> --series <실행 폴더 이름>     # 시간에 따라 늘어나는 것이 있는지

# 단위 시험 (브라우저 없이, 표준 라이브러리만)
python -B -m unittest

# 실제로 한 번 돌려 보는 시험 (Chrome을 띄운다, 약 1분 반)
#   PowerShell: $env:SLOW_LINK_BROWSER_TESTS = "1"; $env:SLOW_LINK_INPUTS = "<inputs.json>"; $env:SLOW_LINK_PYTHON = "<py>"
#               python -B -m unittest tests.test_browser_bench
#   bash:       SLOW_LINK_BROWSER_TESTS=1 SLOW_LINK_INPUTS=<inputs.json> SLOW_LINK_PYTHON=<py> python -B -m unittest tests.test_browser_bench
```

끝내는 값: 모든 실행이 끝까지 돌면 0, 실패한 실행이 있으면 1(실패한 실행도 폴더와 `FAILED.json`이 남는다).
실행이 끝난 뒤 시험대 포트가 열려 있으면 거기서 멈춘다.

## 받아들인 값과 견주기 (`compare.py`)

고치기 전의 핵심 수치를 `accepted/baseline.json`에 적어 두고, 고친 뒤의 실행을 거기에 견준다. 목표는 `targets.json`에 있다
(기준 측정, 목표의 까닭, 허용 폭의 근거는 `docs/slow_link.md`). 아래 명령은 레포 맨 위에서 친다. 표준 라이브러리만 쓴다.

```
# 1) 고친 커밋을 잰다 (위의 run_bench.py). 그 결과 폴더를 받아들인 값과 견준다
python -B tools/slow_link/compare.py --run <결과 폴더>
python -B tools/slow_link/compare.py --run <결과 폴더들이 든 폴더>          # --profile all로 잰 네 실행을 한 번에

# 2) 목표표의 목표를 만족하는지 본다 (--run 없이 치면 받아들인 값 자체로)
python -B tools/slow_link/compare.py --targets --run <결과 폴더>

# 3) 의도한 변경으로 수치가 바뀌었으면, 확인한 뒤 그 실행을 새 받아들인 값으로 쓴다
python -B tools/slow_link/compare.py --accept <결과 폴더> [<결과 폴더> ...]

# 문서의 목표표를 다시 만든다 (targets.json을 고친 뒤 docs/slow_link.md의 두 표시 줄 사이에 붙인다)
python -B tools/slow_link/compare.py --targets-table
```

**견주기.** 실행의 시나리오와 프로필로 칸을 찾는다(예: `drive/normal`). 수치마다 한 줄: 받아들인 값, 이번 값, 변화, 허용 폭, 판정.

| 판정 | 뜻 | 끝내는 값에 주는 영향 |
| --- | --- | --- |
| 같음 | 변화가 허용 폭 안 | 없음 |
| 좋아짐 | 허용 폭을 넘어 좋아짐 | 없음 (받아들일지는 사람이 정한다) |
| 나빠짐 | 허용 폭을 넘어 나빠짐 | 1 |
| 없음 | 받아들인 값은 있는데 이번 실행에 수치가 없다 (화면의 이름이 바뀌어 계측이 비었을 때 등) | 1 (`--allow-missing`이면 없음) |
| 새 수치 | 이번 실행에만 있다 | 없음 |

끝내는 값: 0 = 나빠지거나 없어진 수치가 없다, 1 = 있다, 2 = 견줄 수 없다(실패한 실행, PC가 멈춘 실행, 받아들인 값이 없는 칸, 잰 조건이 다른 실행, 읽을 수 없는 파일).
`--json <파일>`을 주면 같은 판정을 JSON으로도 쓴다(판정 이름은 better, same, worse, missing, new).

- 허용 폭은 `accepted/baseline.json`의 `tolerances`에 수치마다 있다: max(`abs`, `rel` × 받아들인 값). 멈춤이 있는 프로필(`bad`, `very_bad`)은 `stall_rel`, `stall_abs`를 쓴다.
  **잠정 값이다.** 칸마다 실행이 한 번뿐이라, 같은 코드의 반복 측정이 쌓이면 좁힌다. 이 값만은 파일에서 손으로 고쳐도 된다(`--accept`가 지우지 않는다. `--reset-tolerances`면 도구의 기본값으로 돌아간다).
- `mode`가 `report`인 수치(지금은 `control_presend_max` 하나)는 나빠져도 알리기만 하고 실패로 세지 않는다. `--strict`를 주면 센다.
- 잰 조건(프로필의 속도·지연·대기열·멈춤, 시나리오의 실시간 길이 등, 화면 수)이 받아들인 값을 잴 때와 다르면 견주지 않고 2로 끝난다. `--ignore-conditions`로 그래도 견줄 수 있다.
- `--cell drive/normal`처럼 칸을 직접 정할 수 있다(다른 프로필의 값에 견주어 볼 때).
- 요약 파일(`SUMMARY.json`) 하나만 줘도 된다. 결과 폴더를 주면 옛 요약에 없는 것(PC 멈춤, 멈춤 뒤 밀린 메시지 수)을 폴더의 `cpu.jsonl`·`RAW.json`에서 다시 읽는다. 폴더에는 아무것도 쓰지 않는다.

**받아들이기(`--accept`).** 준 실행들의 수치로 그 칸만 바꿔 쓴다. 다른 칸과 허용 폭은 그대로 둔다. 칸마다 실행 하나만 줄 수 있고, 실패했거나 PC가 멈춘 실행은 받지 않는다.
`--replace-all`을 주면 준 실행에 없는 칸은 지운다. 파일에는 실행 폴더의 이름, 커밋, 잰 시각, 잰 조건, 수치만 들어가고 폴더의 경로는 들어가지 않는다.
받아들인 값을 바꾸는 커밋에는 왜 바뀌었는지(어느 변경의 결과인지) 적는다. 지금 파일은 다음 명령으로 만들었다(`<r>`은 2026-11-12 측정의 결과 폴더).

```
python -B tools/slow_link/compare.py --replace-all --accept <r>/20261112T042334Z_drive_good_d962013 <r>/20261112T042607Z_drive_normal_d962013 \
    <r>/20261112T042849Z_drive_bad_d962013 <r>/20261112T043211Z_drive_very_bad_d962013 <r>/20261112T045534Z_drive_map_bad_d962013 \
    <r>/20261112T050929Z_hidden_normal_d962013 <r>/20261112T054908Z_session_expiry_normal_d962013_rerun <r>/20261112T051208Z_quick_none_d962013_unshaped
```

**목표(`--targets`).** `targets.json`의 목표마다 이번 값과 만족 여부를 찍는다. 목표의 `kind`가 `keep`(이미 만족하는 지킬 값)인데 깨졌거나 수치가 없으면 1로 끝난다.
`reach`(뒤 과제가 닿을 값)는 못 미쳐도 0이다. `--strict`를 주면 못 미친 목표가 하나라도 있을 때 1이다(모든 최적화가 끝난 뒤의 확인용).
`targets.json`의 `today`는 목표를 정한 날의 받아들인 값이고, 단위 시험이 두 파일과 문서의 표가 서로 같은지 본다.

자주 쓰는 인자:

| 인자 | 뜻 |
| --- | --- |
| `--source` | 잴 커밋·태그·브랜치. `git archive`로 내보낸다 |
| `--profile` | `good`, `normal`, `bad`, `very_bad`, `none`(중계기 없이 바로), `all`(네 프로필 차례로) |
| `--repeat N` | 같은 조건을 N번 |
| `--live-s` | 시나리오의 실시간 구간 길이를 바꾼다 |
| `--no-drive` | 조종 연습을 빼고 잰다 |
| `--clients 2` | 같은 프로필의 연결 두 개로 화면 두 개를 연다 (각자 병목) |
| `--network-profile` | 화면의 전송 품질 선택(`auto` 등). 주지 않으면 화면의 기본값 |
| `--queue-bytes` | 프로필의 병목 대기열 크기를 바꾼다 |
| `--port-offset` | 모든 포트를 이만큼 옮긴다 (기본 28080, 28765, 28770, 28775, 28778, 28790, 28795, 29100) |
| `--label` | 결과 폴더 이름 끝에 붙일 글 |
| `--keep-scratch` | 내보낸 소스 사본을 지우지 않고 남긴다 |
| `--chrome-arg=<플래그>` | Chrome에 플래그를 더한다(여러 번 가능, `run.json`에 남는다). 예: `--chrome-arg=--disable-gpu` |

## 시나리오

모두 주행 화면(`/rover-control`)에서 잰다.

| 이름 | 하는 일 |
| --- | --- |
| `drive` | 처음 열기(빈 캐시) → 다시 열기(같은 탭) → 2분 실시간. 실시간 동안 조종 연습을 되풀이한다 |
| `drive_map` | 처음 열기 → 10분 실시간, 조종 연습 + 실시간 격자 지도 그리기 켬. 시간이 지나며 늘어나는 것을 본다 |
| `load` | 처음 열기와 다시 열기만 |
| `hidden` | 1분 보이고, 탭을 숨긴 채 1분 |
| `session_expiry` | 대시보드 세션 만료를 50초로 줄이고 150초 (스트림이 끊기는지) |
| `quick` | `drive`의 짧은 판(40초), 다시 열기 없음. 고친 뒤 빨리 볼 때 |

조종 연습 한 번(`run_bench.py`의 `DRIVE_CYCLE`): 화면의 "조종 활성화" 버튼을 누르고 → W 키를 5초 누르고 → 놓고 1초 → 화면의 STOP 버튼 → 2초.
누르는 것은 화면 자신의 버튼과 키라서, 요청은 화면이 평소에 보내는 대로 간다(키를 누르고 있는 동안 140 ms마다 `/api/rover/command`, STOP은 `/api/rover/stop`).

## 연결 프로필 (`profiles.json`)

| 이름 | 속도 | 왕복 지연 | 대기열 | 멈춤 |
| --- | --- | --- | --- | --- |
| `good` | 10 Mbit/s | 10 ms | 256 KiB | 없음 |
| `normal` | 2 Mbit/s | 40 ms | 64 KiB | 없음 |
| `bad` | 500 kbit/s | 150 ms | 16 KiB | 20초마다 1초 (처음은 10초에) |
| `very_bad` | 150 kbit/s | 300 ms | 8 KiB | 20초마다 2초 (처음은 10초에) |

**모두 가정한 값이다.** 실제 차의 AP 연결은 아직 재 보지 않았다. 대기열은 그 속도로 약 0.25초어치로 잡았다.
실제 AP의 대기열은 이보다 클 수 있고, 대기열이 크면 같은 속도에서 지연이 더 길어진다(`--queue-bytes`로 바꿔 볼 수 있다).

중계기(`link_emulator.py`)가 하는 일: 화면 하나(클라이언트 하나)마다 오르내리는 방향에 병목 하나씩을 둔다. 그 화면의 모든 TCP 연결(HTTP, WebSocket)이
그 병목 하나를 같이 쓴다. 정한 속도로 1460바이트씩 내보내고, 방향마다 왕복 지연의 절반을 더하고, 새 TCP 연결마다 왕복 한 번을 더 기다린다.
대기열이 차면 보내는 쪽을 기다리게 한다(버리지 않는다). 멈춤 동안에는 어느 방향으로도 아무것도 지나가지 않는다.
중계기의 시험(`tests/test_link_emulator.py`)은 이것이 실제로 나오는지 잰다: 속도 ±5 %(150 kbit/s, 2 Mbit/s, 10 Mbit/s), 왕복 지연 −5 ms~+30 ms,
새 연결 −10 ms~+60 ms, 멈춤 300 ms가 양방향에서 각각 240~380 ms의 공백으로 세 번, 큰 전송 뒤의 작은 요청이 0.45초 안에 통과.

## 결과 폴더

`<out-root>/<UTC 시각>_<시나리오>_<프로필>_<커밋 7자>[_<label>]/`

| 파일 | 내용 |
| --- | --- |
| `run.json` | 시나리오·프로필 설정, 잰 커밋(전체 해시와 폴더별 tree 해시), 입력, 포트, 리스너 주인, 끝난 뒤 정리 결과 |
| `SUMMARY.json` | `analyze.py`의 요약 (아래 수치). `python analyze.py <폴더>`로 다시 만들 수 있다 |
| `RAW.json` | 브라우저 쪽 원자료 (요청 목록, 도착·그린 시각, 조종 요청, 화면 상태) |
| `control_events.jsonl` | 가짜 제어 끝점이 받은 요청, 핵심 상태 변화(조종 해제 이유), 모터 대신 적은 호출 |
| `control_endpoint.json` | 끝점의 pid·포트와 쓰인 시간 값(제품 클래스에서 읽은 값) |
| `link.json` | 중계기 통계: 초마다 지나간 바이트, 최대 대기열, 멈춤 시각 |
| `cpu.jsonl` | 초마다 프로세스별 CPU·메모리 (x86 PC 값) |
| `stall_*.jsonl` | 프로세스가 20 ms 잠을 깨지 못한 최대 시간 (초마다) |
| `bridge_*.json` | 재생 브리지가 보낸 수와 늦음 |
| `logs/` | 각 프로세스의 출력 |
| `live_c0.png` | 실시간 구간 끝의 화면 |
| `FAILED.json` | 실패했을 때만. 무엇에서 멈췄는지 |

DevTools 명령마다 답 기한(60초)이 있다. 시험 탭의 렌더러가 죽거나 탭이 닫히면 실행은 그 자리에서 끝나고,
그때까지 잰 것과 함께 이유가 `RAW.json`·`SUMMARY.json`의 `lost_tabs`와 `error`에 남는다.

## 수치의 뜻

시험대의 모든 프로세스와 브라우저는 한 PC의 같은 시계를 쓴다. 재생 브리지는 메시지를 보내는 순간의 시각을 메시지에 찍는다.

**실시간 구간** (`live`, 보통 2분):

| 수치 | 뜻 |
| --- | --- |
| 카메라 fps, 지연 p50/p95 | 화면이 실제로 해독해 보인 그림 수/초, 그리고 (해독이 끝난 시각 − 브리지가 그림을 낸 시각). 해독 전에 새 그림에 밀려 버려진 그림은 `camera_superseded`로 따로 센다 |
| 카메라 px | 화면이 받은 그림의 가로 크기 |
| LiDAR Hz, 지연 | 화면이 다시 그린 스캔 수/초, (그린 시각 − 브리지가 스캔을 낸 시각) |
| IMU Hz, 지연 | 자세 그림을 다시 그린 수/초, (그린 시각 − 표본 시각) |
| GPS 지연 | 위치 글자가 바뀐 시각 − 그 GPS 표본이 브리지에 들어온 시각 |
| kB/s (스트림) | 화면이 받은 메시지 크기의 합/초 (WebSocket 틀 머리는 빼고) |
| 상태 kB/s, req/s | 실시간 지도와 조종을 뺀 모든 `/api/*` 요청 (텔레메트리, 기록 상태, 자율주행 상태 등) |
| 지도 kB/s, req/s | `/api/live/map*` 요청. `drive_map`에서는 응답 크기가 시간에 따라 어떻게 변하는지도 (`--series`) |
| link down/up kbit/s, % of rate | 중계기가 실제로 내려보낸/올려보낸 양과 프로필 속도에 대한 비율 |
| after_stall (멈춤이 있는 프로필) | 멈춤이 끝난 뒤 통로마다: 첫 메시지까지의 시간과 그 메시지의 나이, 밀린 메시지 수(`backlog_messages`: 멈춤이 끝난 뒤에 닿았지만 끝나기 전에 만들어진 것), 새 자료가 닿기까지(`fresh_after_ms`: 멈춤이 끝난 뒤에 만들어진 첫 메시지). 다음 멈춤 전까지, 길어야 15초를 본다. 그 안에 새 자료가 없던 멈춤은 `not_fresh_within_horizon`에 센다 |

**측정 A — 조종과 STOP** (조종 연습이 있는 시나리오):

| 수치 | 뜻 |
| --- | --- |
| cmd rt p50/p95/max | 화면이 `fetch()`를 부른 때부터 답의 본문을 다 읽은 때까지 (`/api/rover/command`) |
| pre-send | `fetch()`를 부른 때부터 요청이 브라우저를 떠난 때까지(연결을 기다린 시간). 브라우저 자신의 Resource Timing `requestStart` |
| uplink | `fetch()`부터 가짜 제어 끝점이 요청을 받은 때까지 (분해: 새 연결 + 오르는 쪽 + 대시보드 처리) |
| period | 키를 누르고 있는 동안 끝점이 받은 명령 사이의 간격. 화면은 140 ms마다 보내려 하지만 앞 요청의 답이 와야 다음을 보낸다 |
| gaps>TTL, input expired | 명령 간격이 입력 만료(0.75초)를 넘은 횟수, 끝점의 조종 코어가 실제로 입력 만료로 세운 횟수 |
| lost while held | W를 누르고 있는 동안 화면이 조종권을 잃은 조종 연습의 수 |
| STOP→endpoint | STOP 버튼을 누른 때부터 끝점이 STOP을 받은 때까지. **차가 서는 데 필요한 것은 이 시간이다** |
| STOP answer | STOP 버튼부터 화면이 답을 받은 때까지 (화면에 "정지 요청 접수"가 뜨는 시각) |

**측정 B — 처음 열기와 다시 열기** (`load`):

| 수치 | 뜻 |
| --- | --- |
| req, kB | 열기 동안 오간 요청 수와 바이트(압축 뒤, 머리글 포함). 페이지 파일(HTML, CSS, 스크립트, 그림, 글꼴, 지도 파일)은 열기가 끝날 때까지 시작된 것 모두, API 요청은 화면을 쓸 수 있고 load 사건이 날 때까지 시작된 것만 센다(그 뒤는 평소의 갱신) |
| usable s | 화면의 스크립트가 끝까지 돌고 조종 버튼이 있는 때 (첫 텔레메트리 요청이 나간 때) |
| load event s | 브라우저의 load 사건 |
| all files s | 쓸 수 있고 load 사건이 난 뒤, 받고 있는 페이지 파일이 1.5초 동안 없게 된 때. 120초 안에 그렇게 되지 않으면 비우고(`-`), 그때 받고 있던 파일을 `open` 수와 `still_open` 목록에 적는다 |
| camera s | 첫 카메라 그림이 보인 때 |
| on network, 304 | 다시 열기에서 네트워크에 나간 요청 수와 그중 "바뀌지 않음(304)" 답 |
| file body kB | 다시 열기에서 본문까지 다시 받은 페이지 파일의 바이트 |

요청 목록은 브라우저 자신의 네트워크 사건(DevTools `Network`)에서 온다. `blob:`·`data:` 주소(카메라 그림)는 네트워크가 아니라서 세지 않는다.
웹 워커가 받는 파일(지도 라이브러리의 `maplibre-gl-worker.mjs`)은 응답 머리글까지만 보이고 끝난 것과 바이트는 워커 쪽에 알려진다.
이름이 `worker.js`·`worker.mjs`로 끝나는 스크립트가 그렇게 머리글만 보이면 2초 뒤부터 기다리지 않고 `completed_elsewhere`에 적으며,
그 본문 바이트(압축 뒤 약 6 kB)는 합계에 빠진다. 느린 연결에서는 큰 파일도 페이지 쪽 응답 사건이 다 받은 뒤에야 오므로,
"응답 사건이 아직 없음"만으로 다른 곳에서 끝났다고 보지 않는다.

모든 p95와 max는 **그 실행 한 번**의 값이다. 같은 조건을 여러 번 돌리지 않은 수치는 관찰 하나로 읽는다.

## 파일

| 파일 | 하는 일 |
| --- | --- |
| `run_bench.py` | 진입 명령. 내보내기, 입력 준비, 스택, 브라우저, 요약을 한 번에 |
| `link_emulator.py` | TCP 연결 흉내 중계기 (속도, 지연, 대기열, 주기적 멈춤) |
| `stack.py` | 가짜 제어 끝점, 재생 브리지 3개, 대시보드, 중계기, CPU 표본기를 띄우고 끈다. 리스너의 주인을 확인한다 |
| `replay_bridges.py` | 기록을 소스의 실제 브리지 코드(`scripts/*_ws_bridge.py`)로 내보낸다. ROS 구독만 바꿨다 |
| `control_endpoint.py` | 기록만 하는 가짜 조종 끝점 (실제 `RoverControl` 클래스와 제품 브리지의 요청 처리 순서) |
| `shim/sitecustomize.py` | 시험대 프로세스에만 실리는 준비: 대시보드의 `rover_api`가 유닉스 소켓 대신 127.0.0.1의 끝점에 붙게 하는 소켓, 지도 발행 시각 표시, 멈춤 측정 |
| `shim/win/` | 윈도에서 리눅스용 대시보드가 import되게 하는 대역 `fcntl`, `termios` (호출하면 실패) |
| `bench_inputs.py` | 소스 내보내기(`git archive`), 빈 오프라인 지도, 1080 카메라 프레임, 래스터 커널 빌드 |
| `browser_runner.mjs` | 화면 없는 Chrome을 DevTools 프로토콜로 몬다. 열기·다시 열기·실시간·조종 연습 |
| `page_instrument.js` | 페이지에 미리 넣는 계측. 관찰과 얇은 감싸기만 하고 메시지를 바꾸지 않는다 |
| `analyze.py`, `summarize.py` | 실행 하나의 요약, 여러 실행의 표 |
| `compare.py` | 받아들인 값과 견주기, 목표 확인, 받아들인 값 다시 만들기 |
| `accepted/baseline.json` | 받아들인 값(칸별 핵심 수치)과 허용 폭 |
| `targets.json` | 목표표(`docs/slow_link.md`의 표가 여기서 나온다) |
| `profiles.json` | 연결 프로필 |
| `tests/` | 단위 시험(브라우저 없음)과 선택 시험 `test_browser_bench.py` |

## 대시보드 쪽에 기대는 것 (화면을 크게 바꾸면 확인)

계측은 화면의 다음 이름에 기댄다. 바뀌면 그 수치가 빈다(오류가 아니라 `-`로 나온다).

- 전역 함수: `drawLidarMapOnCanvas`, `displayBinaryCameraFrame`, `acknowledgeCameraFrame`, `drawImuAttitude`, 전역 `state`(`state.roverControl.lease`, `state.lastTelemetryPoll`, `state.telemetry`)
- 요소: `#roverArmButton`, `#roverStopButton`, `#roverCommandStatus`, `#roverGpsPosition`, `#liveMapStart`, `#liveMapCanvas`, `#roverImuCanvas`, `#roverLidarCanvas`
- 모듈: `/static/metric-map.mjs`의 `MetricMap` (지도 그리기)
- 서버: `ROVER_CONTROL_SOCKET` 환경 변수와 `dashboard_server.rover_api`의 `socket` 사용, `dashboard_server.source_live_mapping.SourceLiveMapping._publish`

## 한계

- **x86 PC에서 잰 값이다.** Jetson(ARM)의 CPU 시간, 특히 대시보드 안의 지도 계산은 더 크다. 휴대폰 브라우저의 해독 시간도 다르다.
- **TCP 위에서 흉내 낸다.** 패킷 손실은 멈춤으로만 나타난다. 재전송 뒤 TCP가 속도를 줄이는 것, 무선의 속도 변화, AP가 연결별로 공평하게 나누는 것은 없다.
  병목은 화면 하나에 하나인 선입선출 대기열이다.
- **재생한 자료다.** LiDAR·IMU·GPS는 10/10 오후 기록 하나를 되풀이한다. 카메라는 10/03 720p 프레임을 30 fps로 내고, 원본(1080) 경로에는 그것을 키운 프레임을 낸다.
  실제 카메라 원본과 크기는 비슷하지만 같은 그림은 아니다.
- 대시보드의 Docker, 시스템 상태, 녹화기는 없다(빈 값이나 가짜 대기 상태). 그래서 텔레메트리 응답은 실제 Jetson보다 작을 수 있다.
- 조종 끝점은 실제 브리지의 요청 처리를 따라 했지만 ESP 직렬 왕복은 없다. 끝점이 받은 시각까지가 대시보드 쪽 몫이다.
- 브라우저는 Chrome 하나, 화면 크기 1366x900(바꿀 수 있다)이다. 휴대폰의 절전, 탭 전환, 화면 꺼짐은 흉내 내지 않는다.
- 화면 없는 Chrome은 실제 GPU 대신 소프트웨어로 그린다. 1080p 카메라 그림을 초당 약 10장 보이는 동안 브라우저 메모리가
  0.7~0.8 GB까지 늘었다가 멈췄다(기본은 GPU 프로세스, `--disable-gpu`면 렌더러에). 측정값은 두 경우가 같았다.
  휴대폰에서 같은지는 재 보지 않았다.
- 시험대는 PC가 잠들거나 멈추면 그 동안을 잴 수 없다. `cpu.jsonl`(초마다)과 `stall_*.jsonl`(프로세스가 깨어난 늦음)에 공백이나
  큰 값이 있으면 그 실행은 쓰지 않는다. 시험 탭을 잃은 한 실행과 시간 예산을 넘긴 한 실행이 모두 PC가 멈춘 때와 겹쳤다.
- 같은 조건을 여러 번 돌리지 않은 수치는 관찰 하나다. 실행 사이의 흔들림은 `--repeat`로 따로 잰다.
