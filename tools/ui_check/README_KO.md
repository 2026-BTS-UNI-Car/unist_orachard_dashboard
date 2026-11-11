# 화면 자동 점검 도구 (ui_check)

대시보드 화면(static 폴더)을 모의 서버로 띄우고, 화면 없는 Chrome으로 모든 경로를 여러 화면 크기에서 열어
배치가 깨진 곳을 수치로 재는 도구다. UI를 고칠 때마다 돌려서 기준선보다 나빠진 곳이 없는지 확인한다.

- static 폴더는 읽기만 한다. 결과는 `--out`으로 준 폴더에만 쓴다.
- 서버는 `127.0.0.1`의 빈 포트에만 붙고, 실행이 끝나면 서버와 브라우저를 모두 끝낸다.
- 브라우저가 `127.0.0.1` 밖으로 보내는 요청은 막고 그 수를 센다.
- Jetson, Docker, 실제 센서, 실제 서버(`dashboard_server`)는 쓰지 않는다.

## 필요한 것

- Python 3.10 이상 (표준 라이브러리만 쓰는 부분: 모의 서버, 비교, 시험)
- `playwright` 파이썬 패키지 (`run_checks.py`만 씀. 이 도구를 만든 PC에는 1.63.0이 깔려 있었다)
- 이 PC에 깔린 Chrome (`--browser-channel chrome`이 기본값. Playwright 전용 브라우저를 따로 내려받지 않는다)

## 파일

| 파일 | 하는 일 |
| --- | --- |
| `run_checks.py` | 모의 서버와 Chrome을 띄워 재고 캡처한다. 실행 결과 JSON 하나를 쓴다 |
| `compare.py` | 실행 결과를 고정 규칙(`rules.json`)과 기준선에 견주어 통과/실패를 정한다 |
| `summarize.py` | 실행 결과 JSON을 표로 보여 준다 |
| `mock_server.py`, `mock_data.py` | 모의 서버와 지어낸 응답 자료(empty, stress) |
| `page_probe.js` | 화면 안에서 도는 측정 코드 |
| `ui_metrics.py`, `rules_engine.py` | 수치 계산과 규칙 판정(순수 함수) |
| `rules.json` | 고정 규칙과 기준선 비교 설정 |
| `tests/` | 단위 시험과, 흠을 일부러 넣은 화면(`tests/fixtures/`) |
| `baseline/ui_check.json` | UI 개편 전의 기록(기준선 브랜치 37181f1에서 잰 값). 캡처는 레포에 넣지 않는다 |

## 돌리는 법

명령은 이 도구 폴더에서 친다. 아래에서 `<static>`은 점검할 static 폴더(예: 저장소의 `static`), `<out>`은 결과 폴더다.
`<out>`은 static 폴더 밖이어야 한다(안쪽이면 거절한다).

```
# 1) 재기: 경로 9개 × 화면 5종 × 자료 2종 = 90쪽, 2분쯤 걸린다
python run_checks.py --static <static> --out <out>

# 2) 판정: 고정 규칙 + 기준선 비교. 끝내는 값 0 = 통과, 1 = 실패, 2 = 파일·인자 오류
python compare.py --run <out>/ui_check.json --baseline baseline/ui_check.json

# 3) 표로 보기
python summarize.py <out>/ui_check.json
python summarize.py <out>/ui_check.json --pages --dataset stress --viewport 390x844 --viewport 820x1180 --viewport 1440x900

# 4) 단위 시험 (브라우저 없이 돈다)
python -B -m unittest

# 5) 측정 코드 자체의 시험 (Chrome을 띄운다. 흠을 넣은 화면에서 수치가 맞게 나오는지 본다)
#    PowerShell:  $env:UI_CHECK_BROWSER_TESTS = "1"; python -B -m unittest tests.test_browser_probe
#    bash:        UI_CHECK_BROWSER_TESTS=1 python -B -m unittest tests.test_browser_probe

# 6) 모의 서버만 띄워 브라우저로 직접 보기 (Ctrl+C로 끝냄)
python mock_server.py --static <static> --dataset stress
```

자주 쓰는 인자(`run_checks.py`):

| 인자 | 뜻 |
| --- | --- |
| `--dataset empty\|stress\|both` | 모의 자료 묶음. 기본 both |
| `--route rover-control` | 점검할 경로(여러 번 가능). 기본은 9개 전부. `/` 대신 `overview`라고 적는다 |
| `--viewport 390x844` | 화면 크기(여러 번 가능). 기본 390x844, 844x390, 820x1180, 1180x820, 1440x900 |
| `--stop-selector "#id"` | STOP 조작 요소를 찾는 선택자(여러 번 가능, 앞의 것이 우선) |
| `--no-screenshots` | 캡처 없이 수치만 |
| `--no-resize-probe` | 화면을 돌린 뒤 캔버스가 따라오는지 보는 검사 생략 |
| `--concurrency 3` | 동시에 여는 쪽 수 |
| `--label "설명"` | 결과 JSON에 적어 둘 글 |

Git Bash에서는 `/rover-control`처럼 `/`로 시작하는 인자가 윈도 경로로 바뀐다. `--route rover-control`처럼 앞의 `/`를 빼고 적는다.

새 화면에서 경로가 바뀌면 `--route drive --route map …`처럼 준다. 준 경로는 모의 서버도 `index.html`로 답한다.
실제 서버의 경로 목록(`dashboard_server/app.py`의 `APP_ROUTES`)과 화면의 경로 목록을 따로 맞춰야 하는 것은 그대로다.

## 무엇을 재는가

쪽 하나는 (자료 묶음, 경로, 화면 크기)다. 화면 크기는 짧은 변이 820px 이하이면 터치 기기로 흉내 낸다
(`820x1180:desktop`처럼 바꿀 수 있다). 화소 배율은 항상 1이다.

| 수치(`metrics`) | 뜻 | 근거 목록(`details`) |
| --- | --- | --- |
| `horizontal_overflow_px` | 문서 가로 넘침(scrollWidth − clientWidth) | `document` |
| `outside_viewport_elements` | 화면 오른쪽(또는 왼쪽) 밖으로 나간 요소 수(가장 안쪽 요소만) | `outside_viewport` |
| `overflowing_elements` | 내용이 자기 상자 밖으로 가로로 넘친 요소 수(카드 밖으로 나간 글 등) | `overflowing_elements` |
| `overflowing_elements_vertical` | 세로로 넘친 요소 수(참고용) | `overflowing_elements_vertical` |
| `clipped_texts` | 넘친 부분을 숨기는 상자에 글이나 버튼이 잘린 곳 수 | `clipped_texts`(잘린 쪽은 `culprits`) |
| `ellipsis_truncations` | 말줄임(…)이나 줄 수 제한으로 줄인 곳 수. 의도한 줄임이라 흠으로 세지 않는다 | `ellipsis_truncations` |
| `overlaps` | 조작 요소(버튼·입력·링크·선택 상자)끼리 겹친 쌍 수. 부모·자식과 같은 label 안은 뺀다 | `overlaps` |
| `overlaps_pinned_vs_flow` | 화면에 붙은 막대와 그 밑으로 지나가는 내용의 겹침(참고용) | `overlaps_pinned_vs_flow` |
| `small_targets` / `interactive_total` | 누름 영역이 44×44px보다 작은 조작 요소 수 / 보이는 조작 요소 수 | `small_targets` |
| `tiny_targets` | 그중 24px보다 작은 것 | |
| `canvases` / `canvas_mismatches` | 보이는 캔버스 수 / 표시 크기와 (버퍼 크기 ÷ 화소 배율)이 2px 넘게 다른 캔버스 수 | `canvases`, `canvas_mismatches` |
| `canvas_stale_after_resize` | 화면을 돌린 뒤 새 표시 크기를 따라오지 않은 캔버스 수 | `canvas_resize_probe` |
| `stop_visible`, `stop_top_px` | STOP 조작 요소가 보이는지, 문서 위에서부터의 위치 | `stop`, `stop_candidates` |
| `stop_in_first_viewport` | 페이지를 열자마자 스크롤 없이 STOP이 온전히 보이는지 | |
| `stop_always_in_viewport` | 스크롤 위치 5곳(위, 1/4, 가운데, 3/4, 아래) 모두에서 화면 안인지 | `stop_scroll_samples` |
| `page_height_px`, `screens` | 쪽 높이와 화면 수(높이 ÷ 화면 높이) | |
| `console_errors`, `page_errors` | 여는 동안의 콘솔 오류, 스크립트 오류 | 같은 이름 |
| `failed_requests`, `http_errors` | 실패한 요청, 4xx·5xx 응답 | 같은 이름 |
| `blocked_external_requests` | 127.0.0.1 밖으로 나가려다 막힌 요청 | `blocked_external` |
| `broken_images` | 보이는데 그림이 없는 `img`(naturalWidth 0, 주소 없는 것 포함) | `broken_images` |
| `style_*` | 그라디언트·흐림 요소 수, 그림자·글자 크기 종류 수, 굵은 글 비율(참고용) | `style` |

세부 규칙:

- 숨긴 요소, 닫힌 접힘 상자 안, 잘라 내는 조상 때문에 전혀 안 보이는 요소는 뺀다.
  닫힌 서랍 메뉴처럼 화면 왼쪽 밖에 있는 것도 뺀다.
- 체크 상자와 라디오는 감싼 `label` 전체를 누름 영역으로 본다. 문장 속 링크는 `small_inline_links`로 따로 센다.
- 누를 수 없게 해 둔 요소(`pointer-events: none`)는 조작 요소에서 뺀다. 꺼진(disabled) 버튼은 센다.
- STOP은 `#roverStopButton`, `[data-ui-check~='stop']`, `[data-rover-drive='stop']` 순서로 찾고,
  없으면 글자가 `STOP`·`정지`·`비상 정지`인 버튼을 찾는다. 보이는 첫 후보를 잰다.
  새 화면에서 STOP의 id가 바뀌면 `--stop-selector`로 알려 주거나 요소에 `data-ui-check="stop"`을 단다.
- 캔버스 돌려 보기: 정지 상태 측정과 캡처가 끝난 뒤, API 응답을 붙잡아 두고 1.3초 기다렸다가 화면의 너비와 높이를
  맞바꾸고 0.6초 뒤에 다시 잰다. 자료가 올 때마다 다시 그리는 캔버스는 언젠가 따라오므로, 자료를 멈춘 상태에서
  곧바로 따라오는지(틀 크기를 감시하는지)를 보기 위해서다.
- 측정은 페이지를 연 뒤 문서 크기와 요소 수가 0.25초 간격으로 세 번 같아질 때까지(최소 1.5초, 최대 6초) 기다렸다가 한다.
- 갓 띄운 브라우저의 첫 쪽은 느려서 사건 순서가 달라질 수 있어, 재지 않는 쪽을 한 번 먼저 연다.

## 모의 자료

| 묶음 | 내용 |
| --- | --- |
| `empty` | 응답은 오지만 연결된 것이 없다. 서비스·센서·컨테이너·기록·문서 0개, 기록 서비스 연결 없음 |
| `stress` | 공백 없는 99~110자 장치 경로, 긴 호스트 이름·SSID·컨테이너 이름, 컨테이너 40개, 기록 30개, 문서 14개, 디스크 6개, 코어 12개, 아주 큰 수 |

값은 모두 지어낸 것이다. 주소는 문서 예시용 대역만, 위치는 (0.000100, 0.000200)만 쓴다.

웹소켓: 네 경로(`/ws/lidar`, `/ws/camera`, `/ws/imu`, `/ws/gps`) 모두 연결은 받는다.
`stress`에서는 IMU 표본을 0.2초마다, GPS 표본을 1초마다 보낸다. 나머지는 아무것도 보내지 않는다.

자료 없이 남는 패널(실행 결과 JSON의 `mock.panels_without_data`에도 적힌다):

- 두 묶음 공통: LiDAR 극좌표(스캔 대기 화면), 카메라 영상, GPS 지도 타일(오프라인 지도 묶음 `/maps/*`가 없음),
  실시간 점유 격자 지도(셀 없음), 기록 재생 패널(기록을 고르지 않음), 닫힌 접힘 상자 안의 관찰용 지도·작업 목록.
- `empty`만: IMU 자세, GPS 수치, 컨테이너·문서·수행계획·기록 목록.

LiDAR 스캔을 일부러 보내지 않는다. 스캔이 계속 오면 캔버스가 그때마다 다시 그려져, 틀 크기를 따라가지 않는 문제가 가려지기 때문이다.
점이 찍힌 모습을 눈으로 보고 싶으면 `python mock_server.py --static <static> --dataset stress --ws-feed lidar --ws-feed imu --ws-feed gps`로 띄운다.

## 결과 읽는 법

`<out>/ui_check.json` (schema `ubts.ui_check.run`, version 1)

- 맨 위: 실행 시각, static 폴더 지문(`static_fingerprint`), 저장소 커밋(`static_git_head`, 커밋 안 된 변경은 반영하지 않음), Chrome 판,
  경로·화면 크기·자료 묶음, 모의 서버가 받은 요청 수(`mock.requests`).
  `mock.requests.<묶음>.unmocked`에 무언가 있으면 화면이 모의 응답이 없는 API를 부른 것이다. `mock_data.py`에 응답을 더한다.
- `summary.by_dataset_viewport`: 자료 묶음 · 화면 크기별 합계.
- `pages[]`: 쪽마다 `key`(`stress|/rover-control|390x844` 꼴), `metrics`, `details`, `screenshots`.
  열지 못한 쪽은 `error`가 있고 `metrics`가 null이다.

`<out>/shots/`: `{묶음}_{경로}_{화면}_first.png`(첫 화면), `…_full.jpg`(전체 쪽. `--full-format png`로 바꿀 수 있다). `/`의 이름은 `overview`다.

`compare.py`는 실패 건을 `[실패] stress /rover-control 390x844 · 규칙 no-overlap(…): 값 2 · 조건 <= 0`처럼
쪽 · 화면 크기 · 규칙을 적어 보여 준다. `[참고]`는 `--verbose`를 줘야 한 건씩 나온다. `--json <파일>`로 판정을 파일로 남길 수 있다.

## 규칙 (`rules.json`)

- `fixed_rules`: 수치가 지켜야 할 조건. `routes`·`viewports`·`datasets`로 대상을 좁힌다(`*` 쓸 수 있음, 없으면 전부).
  `mode`가 `fail`이면 어길 때 실패, `report`면 알리기만 한다.
- `regression.metrics`: 기준선과 같은 쪽끼리 견준다. `better`가 `lower`면 값이 커질 때(허용 `tolerance`, `tolerance_ratio`),
  `true`면 참이던 값이 거짓이 될 때 나빠진 것으로 본다.
- 기준선에 있던 쪽이 없어지면 실패(`missing_page_mode`), 새 쪽은 알리기만 한다(`new_page_mode`).

지금 main이 이미 어기는 규칙(가로 넘침 0, 넘친 글 0, 잘린 글 0, 누름 영역 44px, 캔버스 일치, 돌린 뒤 캔버스 따라오기, STOP 첫 화면, STOP 늘 화면 안, 깨진 그림 0)은 `report`로 두었다.
지금도 지키는 규칙(조작 요소 겹침 0, 콘솔·스크립트 오류 0, 요청 실패 0, 4xx·5xx 응답 0, 바깥 요청 0, 측정 코드 정상)은 `fail`이다.
새 배치가 들어온 화면부터 해당 규칙을 `fail`로 바꾼다. 예: STOP 규칙의 `routes`에 새 경로를 적고 `mode`를 `fail`로.
한 화면에 들어가야 하는 화면은 `{"id": "drive-one-screen", "metric": "screens", "op": "<=", "value": 1, "routes": ["/drive"], "viewports": ["1440x900", "1180x820", "844x390"], "mode": "fail"}`처럼 더한다.
`python compare.py … --strict`는 `report`도 실패로 세어, 목표까지 남은 것을 한 번에 보여 준다.

## 기준선을 일부러 바꿀 때

기준선은 저절로 바뀌지 않는다. 화면을 의도해서 바꿔 수치가 달라졌을 때(경로를 합치거나 없앨 때 포함)만 새로 만든다.

1. `python run_checks.py --static <static> --out <새 기준선 폴더> --label "무엇을 바꾼 뒤의 기준선인지"`
2. `python compare.py --run <새 기준선 폴더>/ui_check.json --baseline <옛 기준선>/ui_check.json --verbose`로 달라진 곳을 모두 읽는다.
   나빠진 수치가 있으면 그것이 의도한 것인지 캡처로 확인한다.
3. 확인한 뒤 옛 기준선 폴더를 새 폴더로 바꾸고, 바꾼 까닭을 커밋 글에 적는다. 옛 기준선은 git 기록에 남는다.

`baseline/ui_check.json`은 UI 개편 전의 기록이다(기준선 브랜치 37181f1에서 잰 값이고 main 3372139에서 잰 값과 같았다). 개편 앞뒤 비교표의 '앞'으로 쓰므로 지우지 않는다.

- `ui_check.json`: 90쪽의 수치와 근거 목록
- `summary_overview_ko.md`, `summary_pages_ko.md`: `summarize.py --out`으로 뽑은 표(합계, 쪽별)
- `shots/`: 캡처 180장(쪽마다 첫 화면 PNG와 전체 쪽 JPEG)

## 한계

- 뒤쪽은 모의 서버다. 실제 센서, 실제 지연, 실제 서버의 오류 응답은 보지 않는다.
- 웹소켓 패널 일부는 비어 있다(위 목록). 카메라 영상, 지도 타일, 격자 지도 셀이 있을 때의 모습은 재지 않는다.
- 페이지를 연 직후의 상태만 잰다. 버튼을 눌러야 나오는 상태(기록 선택, 접힘 상자 열기, 서랍 메뉴, 조종 활성화)는 재지 않는다.
- 캔버스 안에 그린 글과 도형의 겹침(예: LiDAR 빈 화면 글)은 화소라서 보지 못한다. 캡처로 본다.
- 빈 칸이 크게 남는 배치, 줄 맞춤, 격자 칸 수 같은 보기 좋음은 재지 못한다. 캡처로 본다.
- 터치 동작(누른 채 움직이기, 두 손가락), 키보드 입력, 조종 명령 순서, 초점 표시, 명암비는 재지 않는다.
- 화소 배율 1에서만 잰다. 화면이 배율을 1.5로 제한해 그리는 것과 실제 휴대폰(배율 2~3)에서의 선명도는 보지 않는다.
- 휴대폰 크기에서 가로 넘침이 있으면 Chrome이 화면을 줄여 보여 준다. 수치는 줄이기 전의 CSS px 기준이다.
- 지금 main에서는 휴대폰 폭 LiDAR 캔버스의 불일치가 사건 순서(자료 도착과 창 크기 사건)에 달려 있다.
  브라우저를 데운 뒤에는 되풀이해도 같은 값이 나왔지만, 캔버스가 틀 크기를 감시하게 고치기 전까지는 달라질 수 있다.
- 다크 테마만 잰다.
- Chrome 한 종류로만 잰다. Safari·Firefox의 차이는 보지 않는다.
