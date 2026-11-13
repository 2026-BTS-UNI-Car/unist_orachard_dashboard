# 차의 무선 AP 점검표 (NET-12)

차에 붙은 Jetson 의 무선 AP(`BTS_Jetson`)에 휴대폰이나 노트북으로 붙어 대시보드를 쓸 때, 연결을 좋게 하려고 현장에서 볼 것과
연결 기록을 남겨 대시보드의 연결 등급과 느린 통신 시험대를 실제 값에 맞추는 방법이다.

- 설정을 바꾸는 일은 사용자가 현장에서 한다. 이 문서의 어느 명령도 차를 움직이지 않는다.
- 대시보드 설치 묶음(`deploy/jetson_dashboard/`)은 AP 설정을 바꾸지 않는다. AP 는 호스트의 `ubts-network-mode.service`(`deploy/offline_ap/`)가 맡는다.
- AP 전환·접속 주소·설치는 [jetson_offline_access.md](jetson_offline_access.md)와 [../deploy/offline_ap/README.md](../deploy/offline_ap/README.md)에 있다.
- 시험대의 프로필과 목표는 [slow_link.md](slow_link.md), 연결 등급은 `static/app/04-link-grade.js` 머리 설명에 있다.

## 1. 지금 설정 (코드 기준)

| 항목 | 값 | 어디서 정하나 |
| --- | --- | --- |
| SSID, 보안 | `BTS_Jetson`(NetworkManager 프로필 이름 `ubts-network-mode-ap`), WPA2-Personal(암호는 보드의 `/etc/ubts-offline-ap/wpa2.pass`, 레포에 없음) | `deploy/offline_ap/ubts_network_mode.py` (`UBTS_AP_SSID`) |
| 대역, 채널 | 2.4 GHz(`band=bg`), **채널 6 고정** | 같은 파일의 AP 프로필(`channel=6`, 두 곳) |
| 무선 절전 | 끔(`powersave=2`) | 같은 파일 |
| 주소 | `10.77.0.1/24`, 대시보드 `http://10.77.0.1` | 같은 파일, `deploy/offline_ap/` |
| 무선 장치 | `wlP1p1s0` 하나. Wi-Fi 접속과 AP 를 동시에 하지 못한다 | `UBTS_WIFI_INTERFACE` |
| 채널 폭, 송신 출력 | 레포에서 정하지 않는다(NetworkManager·드라이버 기본값). 2026-08-27 확인 때 채널 6 으로 검색됐다 | 현장에서 3절 명령으로 읽는다 |

**주의: 채널을 손으로 바꾸면 되돌아간다.** 네트워크 전환 서비스는 AP 프로필을 쓸 때마다 대역이 `bg`, 채널이 6 이 아니면
6 으로 다시 고친다(`ensure_ap_profile`). 채널을 바꾸려면 이 파일의 두 곳을 고치고 서비스를 다시 설치해야 한다(코드 변경, 조정 쪽에 알림).

## 2. 현장 점검표

차를 세운 상태에서 위에서부터 차례로 본다. 결과(시각, 장소, 값)는 종이나 메모에 적고, 연결 기록(4절)을 같이 켜 둔다.

| 번호 | 볼 것 | 어떻게 | 좋은 상태 | 아닐 때 |
| --- | --- | --- | --- | --- |
| A1 | 주변 AP 와 채널 | 휴대폰의 Wi-Fi 분석 앱(채널 그래프)으로 2.4 GHz 를 본다. 차 옆과 열 끝 두 곳 | 채널 6 근처(4~8)에 센 AP 가 없다 | 1, 6, 11 중 가장 한가한 채널을 적는다. 바꾸는 것은 1절 주의대로 코드 변경 |
| A2 | 대역 | 2.4 GHz 유지 | 나무 사이에서는 2.4 GHz 가 5 GHz 보다 멀리 간다 | 5 GHz 로 바꾸지 않는다(이 PHY 가 5 GHz AP 를 되는지도 확인 안 함) |
| A3 | 채널 폭 | Jetson 에서 `iw dev wlP1p1s0 info` 의 `width` | 20 MHz | 40 MHz 면 이웃 채널과 겹친다. 적어 두고 조정 쪽에 알림 |
| A4 | 송신 출력, 나라 설정 | `iw dev wlP1p1s0 info` 의 `txpower`, `iw reg get` 의 `country` | 나라가 `KR`, 출력이 그 나라의 한도 안에서 가장 큰 값 | 값만 적는다. 출력을 바꾸는 명령은 쓰지 않는다 |
| A5 | 안테나 | Jetson 의 Wi-Fi 안테나 두 개의 위치와 방향 | 차체 위쪽, 금속판·배터리·모터 배선에서 떨어져 있고 서로 직각 | 덮개 안이나 금속 뒤에 있으면 위치를 적고 사진을 남긴다 |
| A6 | 거리별 연결 | 휴대폰으로 `http://10.77.0.1/rover-control?link_log=1` 을 열고 차에서 5, 10, 20, 40 m 떨어져 각 1분, 열 안과 열 밖 | 상태 막대 배지가 '연결 좋음'·'연결 보통' | '연결 느림'·'아주 느림'이 되는 거리와 장소를 적는다 |
| A7 | 끊김 | A6 동안 배지가 '연결 끊김'이 되는지 | 끊김 없음 | 장소와 길이를 적는다(연결 기록에도 남는다) |
| A8 | 휴대폰 절전 | 배터리 최적화(절전)에서 브라우저를 뺀다. 절전 모드 끔 | 화면을 켜 둔 동안 연결이 이어진다 | 절전이 켜져 있으면 요청이 늦게 나간다 |
| A9 | 휴대폰의 '인터넷 없음' 처리 | `BTS_Jetson` 에 붙으면 나오는 '인터넷 없음' 알림에서 연결을 유지한다. 'Wi-Fi 끊김 시 모바일 데이터로 전환'(스마트 네트워크 전환, Wi-Fi 도우미 등)을 끈다 | 휴대폰이 AP 에 계속 붙어 있다 | 켜져 있으면 휴대폰이 스스로 AP 를 떠나 대시보드가 끊긴다 |
| A10 | 화면 꺼짐 | 화면 자동 꺼짐 시간을 늘린다 | 운전 중 화면이 켜져 있다 | 화면이 꺼지거나 탭이 숨으면 자율주행은 0.5 초 뒤 선다(의도된 동작). 수동 조종은 입력 만료(0.75 초)로 선다 |
| A11 | 노트북 | 전원 관리에서 무선 어댑터 절전을 끈다 | 연결 배지가 흔들리지 않는다 | |

## 3. 읽기 명령 (Jetson, 바꾸는 것 없음)

```sh
iw dev wlP1p1s0 info          # 채널(주파수), width, txpower
iw reg get                    # 나라 설정과 출력 한도
nmcli -f GENERAL.STATE,802-11-wireless.band,802-11-wireless.channel,802-11-wireless.powersave connection show ubts-network-mode-ap
systemctl status ubts-network-mode.service --no-pager
```

AP 를 켠 상태에서는 같은 무선 장치로 주변 AP 를 검색하지 못할 수 있다. 주변 채널은 휴대폰 앱으로 본다(A1).

## 4. 현장 연결 기록 남기기 (`?link_log=1`)

대시보드 화면은 0.5 초마다 아주 작은 요청(`/api/ping`)을 보내고, 운전 중에는 조종 요청의 왕복 시간도 함께 재서 연결 등급을 정한다.
주소 끝에 `?link_log=1` 을 붙여 열면 그 표본을 10 초마다 대시보드 서버로 보내 파일로 남긴다.

1. 휴대폰에서 `http://10.77.0.1/rover-control?link_log=1` 을 연다(다른 화면도 된다: `/sensors?link_log=1`).
   계속 켜 두려면 브라우저 개발자 도구에서 `localStorage.setItem("ubts-link-log", "1")`, 끄려면 `localStorage.removeItem("ubts-link-log")`.
2. 녹화를 켜 두면 표본마다 그때의 녹화 ID 가 붙는다. 거리·장소를 바꿀 때마다 시각을 적어 두면 나중에 나눌 수 있다.
3. 기록은 Jetson 의 대시보드 `runtime/link_log/YYYYMMDD.jsonl`(UTC 날짜, 하루 20 MB 까지)에 쌓인다. compose 기준으로 컨테이너의
   `/app/runtime` 은 레포 폴더의 `runtime/` 이다(설치된 컨테이너의 실제 마운트는 `docker inspect unist-orachard-dashboard` 로 확인).
4. 한 줄 = 표본 하나: `t`(휴대폰 시계 ms), `kind`(`ping`·`control`), `ok`, `rtt_ms`, `grade`(그때의 등급), `recording_id`, `profile`(전송 품질 선택).
   좌표, 주소, 기기 이름은 들어가지 않는다.
5. 파일을 PC 로 가져와 읽는다(표준 라이브러리만 쓴다):

```
python tools/slow_link/link_log_report.py <link_log 폴더 또는 파일> [--by-recording] [--json]
```

나오는 것: 표본 수와 시간, 핑 응답 비율, 왕복 시간 p50·p90·p95·p99·최대, **등급이 보는 값(최근 4 초의 가장 작은 왕복)의 분포**와
그 값이 지금 경계(25 / 90 / 220 ms)보다 작은 비율, 끊김(핑 두 번 연속 무응답) 횟수와 가장 긴 것, 등급별 시간 비율.

## 5. 등급 경계값 다시 맞추기

지금 경계와 계획은 시험대 프로필(가정값)에 맞춘 것이다([slow_link.md](slow_link.md) 11.4, 11.7). 현장 기록이 생기면 이 순서로 다시 맞춘다.

1. **장소별로 나눈다.** 4절의 보고를 녹화 ID 나 시각으로 나눠 '차 옆', '열 안 20 m', '열 끝' 같은 장소마다 '최근 4 초의 가장 작은 왕복' p50·p90 을 본다.
2. **경계를 정한다.** 등급은 카메라·LiDAR·IMU·GPS·상태 요청의 크기를 한꺼번에 정한다(`effectiveLinkProfile`). 어느 장소에서 어느 계획을 쓰고 싶은지 먼저 정하고,
   그 장소의 p90 이 그 등급 안에 들어오도록 `static/app/04-link-grade.js` 의 `LINK_GRADE.thresholdsMs` 를 고친다.
   경계 근처에서 흔들리지 않게 나빠질 때 25 %, 좋아질 때 15 % 여유(`degradeMargin`, `upgradeMargin`)는 그대로 둔다.
3. **시험대 프로필을 맞춘다.** `tools/slow_link/profiles.json` 의 왕복 지연과 멈춤(`stall_every_s`, `stall_ms`)을 장소별 값에 맞춘다.
   속도(`rate_kbps`)는 핑으로 알 수 없다. 현장에서 카메라 '자세히'(1080 원본)를 켜고 상태 막대의 카메라 fps 와 그림 크기(약 112 kB)로 어림하거나 따로 잰다.
   프로필을 바꾸면 받아들인 값(`tools/slow_link/accepted/baseline.json`)과 견줄 수 없으므로(잰 조건이 다름) 다시 재서 받아들인다.
4. **확인한다.**
   - `node tests/test_link_grade.js`(등급이 5 초 안에 따라가는지, 흔들리지 않는지, 끊김 판정. 경계를 바꾸면 시험의 표본 값도 같이 고친다), `python -B -m unittest tools.slow_link.tests.test_link_log_report`(보고 도구가 화면의 경계를 읽는지).
   - 시험대 네 프로필(`python -B tools/slow_link/run_bench.py --scenario drive --profile all ...`)과 `compare.py --targets`.
   - 화면 글자를 바꿨으면 글꼴 분할을 다시 만든다(`tools/fonts/split_ui_font.py`).
5. **설치.** 화면 파일만 바뀌므로 대시보드 설치 묶음 하나로 된다([../deploy/jetson_dashboard/README_KO.md](../deploy/jetson_dashboard/README_KO.md)).

## 6. 이 점검표가 다루지 않는 것

- AP 설정 바꾸기(채널, 출력, 안테나 옮기기)와 그 뒤의 확인: 사용자가 현장에서 하고 결과를 적는다.
- 휴대폰 기종별 절전 메뉴 이름: 기종마다 다르다.
- 무선 손실과 속도 변화의 시험대 흉내: 시험대는 멈춤으로만 흉내 낸다([slow_link.md](slow_link.md) 8절).
