# Jetson 대시보드 설치 묶음

차에 달린 Jetson 의 대시보드 컨테이너(`unist-orachard-dashboard`)를 새 판으로 바꾸고, 문제가 있으면 이전 판으로 되돌리는 도구다. 명령은 Jetson 에서 직접 실행한다. 원격 접속이 되면 조정 쪽이 같은 명령을 실행할 수도 있다.

| 파일 | 하는 일 | 어디서 |
| --- | --- | --- |
| `install_dashboard.py` | 점검(기본), `--apply` 로 설치 | Jetson |
| `rollback_dashboard.py` | 점검(기본), `--apply` 로 되돌리기 | Jetson |
| `make_bundle.py` | 레포의 한 커밋에서 묶음 만들기 | PC |
| `MANIFEST.json`, `payload.tar`, `SHA256SUMS` | 묶음 내용(파일별 SHA-256)과 파일들 | `make_bundle.py` 가 만듦 |

Jetson 쪽 두 도구는 Python 3 표준 라이브러리만 쓴다(Ubuntu 22.04 의 Python 3.10 에서 동작 확인). 인터넷이 없어도 된다.

## 1. 하는 일과 하지 않는 일

하는 일:

- 대시보드 컨테이너 한 개만 바꾼다. `/app` 안의 `dashboard_server`, `static`, `config`, `docs`, `requirements.txt`, `scripts/__init__.py`, `scripts/recover_recorded_lidar.py` 가 대상이다(레포 `Dockerfile` 의 COPY 줄에서 정한다).
- 지금 돌고 있는 컨테이너를 그대로 이미지로 보존(docker commit)하고, 그 위에 바뀐 파일과 새 파일만 겹친다. 레포에서 지운 파일(예: 나눠지기 전의 `static/app.js`)은 새 판에서 지운다. 컨테이너 안에서 직접 고친 파일과 빌드 때 만든 `dashboard_server/_native_raster.so` 는 그대로 남는다.
- 새 컨테이너는 이전 컨테이너와 같은 설정(환경변수, 마운트, host 네트워크, 재시작 정책, 장치 권한, 헬스체크)으로 만든다. 환경변수 값은 화면과 영수증에 쓰지 않는다.
- 이전 컨테이너는 지우지 않는다. 멈추고 이름을 `unist-orachard-dashboard-kept-<시각>` 으로 바꾸고 재시작 정책을 `no` 로 둔다(재부팅해도 다시 켜지지 않는다).
- 어느 단계든 실패하면 이전 컨테이너를 자동으로 다시 켠다. 결과는 영수증 JSON 으로 남는다.

하지 않는 일:

- 모터 출력, 조종 활성화(arm), 펌웨어 쓰기, 장착 보정. 조종 관련 요청은 상태 읽기(GET)만 한다.
- `docker compose` 로 다시 만들기. Jetson 의 compose 파일은 10월 7일의 옛 이미지를 가리키므로 compose 로 만들면 옛 대시보드로 돌아간다. 이 도구는 compose 를 쓰지 않는다. 직접 `docker compose up` 도 하지 말 것.
- `docker pull`, `docker build`, `prune`, 컨테이너·이미지·볼륨 삭제, 전체 명령 재시도.
- ROS 브리지 컨테이너(카메라, LiDAR, IMU, GPS, 녹화기, ESP 브리지)와 native 관찰 서비스 교체. 4절 참고.

## 2. 준비

- 차는 멈춰 있고 조종 비활성 상태, 녹화하지 않는 중, 지도 작업(실시간 지도, 기록 재생 지도)이 멈춘 상태여야 한다. 설치기가 직접 확인하고, 아니면 아무것도 바꾸지 않고 멈춘다.
- Jetson 에서 Docker 를 쓸 수 있는 사용자. `docker ps` 가 되면 그대로, 안 되면 명령 앞에 `sudo` 를 붙인다.
- 디스크 여유: 보존 이미지와 새 이미지는 겹친 층만큼만 늘어난다(묶음 크기 이하, 지금 약 25 MB).
- 묶음 파일 `ubts-dashboard-kit-<커밋 12자>.tar.gz` (PC 에서 만든 것, 5절).

## 3. 현장 순서

### 3-1. 파일 복사와 풀기

USB 나 `scp` 로 Jetson 에 복사한 뒤:

```sh
mkdir -p ~/ubts-releases && cd ~/ubts-releases
tar xzf ubts-dashboard-kit-<id>.tar.gz
cd ubts-dashboard-kit-<id>
sha256sum -c SHA256SUMS        # 모두 OK 여야 한다
```

조정 쪽이 알려 준 `MANIFEST.json` 의 SHA-256 과 같은지도 본다(`sha256sum MANIFEST.json`).

### 3-2. 점검 (바꾸는 것 없음)

```sh
python3 install_dashboard.py
```

나오는 것: 대상 컨테이너와 이미지, 바뀌는 파일 / 새 파일 / 지울 파일 / 그대로 두는 옛 파일 목록, 쉬는 상태 확인 결과, 설치 가능 여부. 끝 줄이 `점검 통과` 면 설치할 수 있다. 종료 코드 0 = 설치 가능, 2 = 지금은 설치 불가(이유가 화면에 나온다).

### 3-3. 설치

```sh
python3 install_dashboard.py --apply
```

순서: 쉬는 상태 확인 → 지금 컨테이너 보존(docker commit, 잠깐 일시정지) → 네트워크 없는 준비 컨테이너에서 파일 겹치기 → 새 이미지에서 대시보드 모듈 import 확인(`app.py` 가 부르는 대시보드 모듈 전부) → 새 컨테이너 생성과 파일 SHA-256 대조 → 쉬는 상태 다시 확인 → 이전 컨테이너 정지·이름 변경 → 새 컨테이너 시작 → 준비 확인(`/api/health`, Docker 헬스체크, 모든 정적 파일이 묶음과 같은 바이트인지, 주요 API 5개 응답, 쉬는 상태). 대시보드가 끊기는 시간은 정지부터 준비 확인까지다. PC 연습에서는 약 8초였고 전체는 113초였다(8절). Jetson 은 더 걸릴 수 있다.

전체 시간 한도 480초, 실패하면 별도 150초 안에 되돌린다. 끝 줄이 `결과: installed` 면 성공. 종료 코드: 0 성공(또는 이미 같은 판), 1 실패했지만 이전 판이 그대로 돌고 있음(바꾸기 전에 멈췄거나 자동으로 되돌림), 2 시작 전에 거절, 3 되돌림도 실패(7절 끝).

### 3-4. 확인할 화면

브라우저에서 대시보드를 새로 연다(주소 `http://10.77.0.1` 또는 평소 주소). 정적 파일 주소에 내용 해시가 붙어 있어서 강력 새로고침은 필요 없다. 차는 움직이지 않는다.

1. 첫 화면이 뜨고 글꼴·배치가 새 판 모양이다.
2. 카메라 영상, LiDAR, IMU, GPS 카드가 갱신된다.
3. 조종 칸에 조종 비활성으로 보인다. 조종 활성화와 키 입력은 하지 않는다.
4. 녹화 목록과 지도 화면, 문서 화면이 열린다.
5. 휴대폰으로도 한 번 연다(느린 연결 확인).

이상하면 바로 3-5 로 되돌린다.

### 3-5. 되돌리기

```sh
python3 rollback_dashboard.py            # 점검
python3 rollback_dashboard.py --apply    # 되돌리기
```

가장 최근 설치를 되돌린다(`--receipt <영수증 폴더>` 로 고를 수 있다). 설치했던 컨테이너는 지우지 않고 `unist-orachard-dashboard-rolledback-<시각>` 으로 멈춰 둔다. 대시보드가 응답하지 않으면 녹화기와 조종 브리지 상태 파일로 쉬는 상태를 확인한다. 그것도 안 되면 거절한다. 차가 멈춰 있고 조종하는 사람이 없을 때만 `--skip-idle-check` 를 붙인다.

## 4. 무엇이 바뀌고 무엇이 안 바뀌는가

- 바뀌는 것: 대시보드 컨테이너의 `/app` 아래 파일(목록은 `MANIFEST.json` 의 `files`, 지울 것은 `remove`). 대시보드 서버 코드, 화면(HTML/JS/CSS/글꼴), 설정, 문서.
- 바뀌지 않는 것: ROS 브리지 이미지들(카메라·LiDAR·IMU·GPS 브리지와 드라이버, 녹화기, ESP 조종 브리지), native 관찰 서비스, compose 파일, Jetson 호스트 설정과 무선 AP 설정.
- `make_bundle.py` 는 기준판(마지막 설치 기록 `baseline-installed-20261025`) 이후 이 묶음이 설치하지 않는 변경을 `MANIFEST.json` 의 `baseline.not_installed_by_this_kit` 에 나눠 적고 화면에 알린다(`ros_bridge_images`, `native_sensor_stream`, `compose_files`). 이 목록이 비어 있지 않으면 그 변경은 이 묶음으로 현장에 가지 않는다. 브리지 쪽 변경은 별도 절차가 필요하다.
- `dashboard_server` 는 ROS 브리지 이미지에도 복사돼 있다(`Dockerfile.ros2`). 브리지 컨테이너는 자기 사본을 계속 쓴다.
- 다시 빌드해야 하는 변경은 이 방식으로 설치하지 않는다: `requirements.txt` 가 다르면 새 파이썬 패키지가 필요하고, `dashboard_server/native_raster.cc` 가 다르면 컴파일된 `_native_raster.so` 가 맞지 않는다. 설치기가 둘을 비교해 다르면 거절한다. 레포 `Dockerfile` 이 기준판과 다르면 `make_bundle.py` 가 묶음을 만들지 않는다.
- `Dockerfile.ros2` 점검(todo D-06): 이 묶음을 만든 커밋 기준으로 브리지 스크립트가 import 하지만 이미지에 복사하지 않는 파일이 `notes.dockerfile_ros2_imports.missing_imports` 에 적힌다. ROS 이미지를 레포에서 다시 빌드하면 그 브리지가 시작하지 못한다. 이 묶음은 ROS 이미지를 다시 빌드하지 않으므로 지금 설치에는 영향이 없다.

### 4-1. 느린 통신 작업(NET-03 ~ NET-11)과 화면 개편: 무엇이 이 묶음만으로 현장에 가나

기준판(`baseline-installed-20261025`) 이후의 변경을 다시 설치할 컨테이너로 나눴다(NET-12). 측정과 근거는 [`docs/slow_link.md`](../../docs/slow_link.md) 10~12절.

| 변경 | 어디 | 이 묶음(대시보드 컨테이너)만으로 |
| --- | --- | --- |
| 화면 6개, 조향 수동·자율·조정 화면, 실행기 중계(`/api/executor/*`) | `static/`, `dashboard_server/` | 된다. 조향 화면은 `DASHBOARD_VEHICLE=front_steer` 와 조향 브리지·실행기가 있어야 쓸모가 있다(아래) |
| 첫 화면 무게 줄이기, 정적 파일 해시 주소와 긴 캐시(NET-03, NET-04) | `static/`, `dashboard_server/static_assets.py` | 된다 |
| 연결 유지와 조종 우선(NET-05), 상태 요청 묶음·ETag(NET-06) | `dashboard_server/app.py`, `static/app/01-format-http.js` | 된다 |
| 카메라: 자동일 때 1080 원본 미리보기 안 씀, 화면 밖 패널 멈춤, 등급별 크기·화질(NET-08, NET-10) | `static/` | 된다. 카메라 브리지는 기준판 이후 바뀌지 않았다(브리지가 이미 받는 크기·화질만 고른다) |
| 연결 등급과 한 곳의 자동 조절, 현장 연결 기록(NET-10) | `static/app/04-link-grade.js`, `/api/ping`, `/api/link-log` | 된다 |
| 세션 연장, 다시 잇기 엇갈림, 끊김 뒤 처리(NET-11) | `dashboard_server/app.py`, `static/` | 된다 |
| 실시간 지도 경로를 늘어난 만큼만 보내기(NET-09 첫 부분) | `dashboard_server/`, `static/live-map*.js` | 된다 |
| LiDAR 이진 형식 polar-v2, 응답 창 2, 새 스캔 바로 보냄(NET-07) | `scripts/lidar_ws_bridge.py` → `ros2-lidar-bridge` | **안 된다.** 옛 브리지는 polar-v1(JSON)으로 답하고 새 화면은 그것을 그대로 그린다(LiDAR 갱신 수는 옛 수준) |
| IMU 표시 메시지 한 번에 하나(display-ack, NET-07) | `scripts/imu_ws_bridge.py` → `ros2-imu-bridge` | **안 된다.** 옛 브리지는 옛 흐름으로 보낸다(멈춤 뒤 밀린 IMU 가 남는다) |
| GPS 브리지 응답 기한 2 → 30 초(NET-11) | `scripts/gps_ws_bridge.py` → `ros2-gps-bridge` | **안 된다.** 옛 브리지는 2 초 넘는 멈춤에서 연결을 닫고, 화면이 다시 연다 |
| 조향 브리지 V3(새 서비스, compose 프로필 `steer`) | `scripts/steer_rover_bridge.py` 등 | **안 된다.** 조향 펌웨어 V3 와 함께 따로 설치한다(조향 차량이 될 때) |

- 브리지 쪽 변경은 ROS 이미지(`Dockerfile.ros2`)를 다시 만들어야 한다. 그 설치·되돌리기 절차는 아직 없다(이 묶음의 범위 밖). 만들 때도 compose 로 서비스를 한꺼번에 다시 만들지 않는다(1절).
- 그래서 이 묶음만 설치한 차에서는 화면 쪽 개선(첫 화면, 캐시, 조종 우선, 카메라 계획, 연결 등급)이 가고, LiDAR·IMU·GPS 스트림은 옛 형식으로 돈다. 섞여도 동작하게 만들었다(시험 `tests/test_lidar_polar_v2.py`·`.js` 의 polar-v1, 화면 코드의 옛 흐름 처리). 시험대의 수치(slow_link.md 12절)는 새 브리지까지 쓴 값이라, 브리지를 바꾸기 전의 LiDAR·IMU 수치는 그보다 나쁘다.
- 되돌리기: 대시보드는 3-5 절. 브리지를 바꾸지 않았으므로 브리지 쪽 되돌리기는 없다.
- 차의 무선 AP 설정은 이 묶음과 상관없다. 현장 점검표: [`docs/WIFI_AP_CHECKLIST_KO.md`](../../docs/WIFI_AP_CHECKLIST_KO.md).

## 5. 묶음 만들기 (PC)

레포 작업 트리에서:

```sh
python deploy/jetson_dashboard/make_bundle.py --commit <설치할 커밋> --out <레포 밖 폴더>
```

- 파일은 `git cat-file` 로 커밋에 저장된 바이트 그대로 읽는다(줄 끝 LF). 작업 트리의 수정은 들어가지 않는다.
- 기준판은 `--baseline` (기본 `baseline-installed-20261025`). 기준판 이후 레포가 지운 파일이 `remove` 가 된다. Jetson 에 기준판이 아닌 다른 판이 있다면 점검 화면의 '그대로 두는 옛 파일' 목록을 보고 판단한다.
- 같은 커밋이면 `payload.tar` 는 매번 같은 바이트다. 출력이 이미 있으면 덮어쓰지 않고 멈춘다.
- 설치 도구 세 파일은 `make_bundle.py` 옆의 것을 넣는다. 그 커밋에 있는 판과 같은지는 `MANIFEST.json` 의 `tools_vs_commit` 에 적힌다.

## 6. 영수증과 남는 것

- 영수증: `~/ubts-releases/dashboard-kit/<UTC 시각>_<묶음 id>/RECEIPT.json` (`sudo` 로 실행해도 실행한 사용자의 홈. `--state-dir` 로 바꿀 수 있다). 바뀐 파일의 전후 SHA-256, 이미지 ID(이전, 보존, 새), 컨테이너 ID와 이름, 단계별 시각, 확인 결과, 오류와 되돌림 결과. `LATEST_RECEIPT` 는 마지막 영수증 경로. 되돌리기 결과는 같은 폴더의 `ROLLBACK_<시각>.json`.
- Docker 에 남는 것(지우지 않음): 이미지 `ubts-dashboard-kit/snapshot:<시각>-<id>`(설치 전 상태), `ubts-dashboard-kit/release:<id>-<시각>`(새 판), 컨테이너 `...-kept-<시각>`(이전 판, 멈춤), `...-kit-stage-<시각>`, `...-kit-preflight-<시각>`(준비용, 종료 상태). 실패했다면 `...-failed-<시각>`. 공간이 필요하면 사람이 확인하고 지운다.

## 7. 문제 생겼을 때

| 화면의 말 | 뜻 | 할 일 |
| --- | --- | --- |
| `not_idle: rover armed=...` | 조종이 활성이거나 움직이는 중 | 조종 비활성, 차 정지 뒤 다시 |
| `not_idle: recorder state=...` | 녹화 중이거나 마무리 중, 또는 녹화기 상태를 못 읽음 | 녹화를 끝내고 기다린 뒤 다시. 녹화기 컨테이너가 꺼져 있다면 `--recorder-unavailable-ok` |
| `not_idle: mapping job active` | 실시간 지도나 기록 재생 지도가 도는 중 | 대시보드에서 지도 작업을 멈춘 뒤 다시 |
| `blocked: requirements_changed` / `native_raster_source_changed` | 이미지를 다시 빌드해야 하는 변경 | 이 묶음으로는 설치하지 않는다. 조정 쪽에 알림 |
| `bundle_payload_sha256`, `bundle_member_sha256` | 묶음 파일이 깨졌거나 바뀜 | 다시 복사, `sha256sum -c SHA256SUMS` |
| `docker_socket_permission` | Docker 권한 없음 | 명령 앞에 `sudo` |
| `target_missing`, `target_not_unique` | 대시보드 컨테이너를 못 찾음 | `docker ps` 로 이름 확인, `--container <이름>` |
| `preflight_import_failed` | 새 판이 이 이미지에서 import 되지 않음(패키지 부족 등). 이전 판은 건드리지 않음 | 영수증의 `preflight_log_tail` 을 조정 쪽에 보냄 |
| `ready_timeout`, `static_mismatch`, `api_probe` | 새 판이 제대로 뜨지 않음. 자동으로 되돌림 | 영수증을 조정 쪽에 보냄 |
| `another_kit_run_active` | 설치기나 되돌리기가 이미 도는 중 | 끝날 때까지 기다림 |

`결과: failed_rollback_failed` (종료 코드 3)는 새 판도 이전 판도 돌지 않을 수 있는 상태다:

```sh
docker ps -a --format '{{.Names}}\t{{.Status}}' | grep unist-orachard-dashboard
```

영수증 `RECEIPT.json` 의 `target.old_container_id` 가 이전 판이다. 그 이름이 `unist-orachard-dashboard` 가 아니고 그 이름을 쓰는 컨테이너도 없으면 `docker rename <이전 ID> unist-orachard-dashboard`, `docker update --restart unless-stopped <이전 ID>`, `docker start <이전 ID>` 로 직접 켤 수 있다(재시작 정책은 영수증의 `target.old_restart_policy`). compose 는 쓰지 않는다. 영수증 폴더를 통째로 조정 쪽에 보낸다.

## 8. 이 PC(x86)에서 연습한 것과 Jetson(ARM)의 차이

- 시험 1: `tests/test_jetson_dashboard_kit.py`. 가짜 Docker API 와 가짜 대시보드로 점검, 설치, 자동 되돌림(준비 실패, 정적 파일 불일치, 시간 한도), 쉬는 상태가 아닐 때 중단, 다시 빌드가 필요한 변경 거절, 묶음 변조 거절, compose·prune·pull·삭제·subprocess 미사용, 되돌리기를 본다. 장치·SSH·실제 컨테이너는 쓰지 않는다.
- 시험 2 (2026-11-13): Windows PC 의 Docker Desktop(x86_64, Engine 29)에서 실제 흐름을 한 번씩 연습했다. 시험 컨테이너는 이미 있던 x86 이미지(시뮬레이션 이미지, Python 3.12, numpy·scipy 있음) 위에 기준판(`baseline-installed-20261025`)의 `/app` 파일을 쓰기 층에 복사해 만들었다. Jetson 처럼 '옛 이미지 + 컨테이너 안 덮어쓰기' 상태다. 설정은 host 네트워크, `unless-stopped`, `--pid host`, 바인드 마운트, 장치 규칙, 헬스체크, 비공개 환경변수 하나, 쓰기 층에만 있는 파일 하나. 설치기는 Python 3.10(Ubuntu 22.04) 실행 컨테이너에서 docker.sock 으로 돌렸다.
  - 점검 2.4초: 349개 중 그대로 303, 바뀜 16, 새로 30, 지울 것 5, 그대로 두는 옛 파일 1.
  - 설치 113초(보존 commit 64초, 겹치기·commit 37초. 같은 때 다른 작업의 이미지 빌드가 돌고 있었다), 정지부터 준비 확인까지 약 8초. 새 컨테이너 파일 349개 SHA-256 일치, 지울 파일 5개 없음, 쓰기 층 파일 보존, 정적 파일 64개 HTTP 대조(해시 주소 27개 포함), API 5개, 이미지 층 31→32(보존)→33(새), Entrypoint·재시작 정책·네트워크·장치 규칙 유지, 영수증에 환경변수 값 없음.
  - 되돌리기 2.7초: 원래 컨테이너 ID 가 원래 이름으로 돌아오고 파일이 기준판과 같음.
  - 자동 되돌림(`--ready-timeout 1` 로 준비 실패를 일부러 냄): 실패 1초 뒤 원래 컨테이너가 다시 돎, 재시작 정책 복원, 실패한 컨테이너는 `-failed-` 로 멈춰 보존. 종료 코드 1.
  - 9월 29일 빌드 이미지(scipy 없음)로 한 점검은 `requirements_changed`, `native_raster_source_changed` 로 거절됐다. 그 이미지에 10/25 코드를 올리면 시작 때 scipy 가 없어 계속 재시작했다. 이 일로 import 확인을 `app.py` 가 부르는 모듈 전부로 넓혔다.
  - 연습한 시험 컨테이너와 이미지는 끝에 지웠다.
- 다른 점: Jetson 은 ARM64(aarch64)이고 이미지가 10월의 덮어쓰기 사슬(`d59da2a0…` 기록, python:3.11-slim 기반)이다. 묶음은 텍스트·글꼴 파일뿐이라 CPU 구조와 무관하지만, 컴파일된 `_native_raster.so` 는 Jetson 이미지의 것을 그대로 쓴다. PC 의 Docker Engine 판과 Jetson 의 판이 다를 수 있다(API 경로는 판 표시 없이 부른다). Jetson 은 느려서 보존·겹치기·시작 시간이 더 걸린다(한도 480초). Docker Desktop 의 host 네트워크는 가상 머신 안의 네트워크라 Jetson 의 host 네트워크와 같지 않다. PC 시험 컨테이너는 privileged 가 아니었고 조종 브리지 소켓이 없었다(조종 상태는 '브리지 없음'으로 확인됨). 실제 Jetson 대시보드 컨테이너의 설정은 inspect 결과를 그대로 복사하고, 만든 뒤 주요 칸(HostConfig 29개, Config 12개, 마운트, 네트워크)을 비교해 다르면 이전 판을 건드리지 않고 멈춘다.
- 쉬는 상태 확인은 `/api/live/mapping-jobs` 가 있어야 한다. 10/25 설치판에는 있다(10월 설치기도 이 경로를 썼다). 9월 판처럼 이 경로가 없으면 거절한다.
- Jetson 에서는 아직 한 번도 실행하지 않았다. 첫 실행은 점검만 하고 결과(바뀌는 파일 수, 그대로 두는 옛 파일 목록)를 조정 쪽과 확인한 뒤 설치한다.
