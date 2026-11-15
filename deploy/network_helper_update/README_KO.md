# Jetson 네트워크 도우미 교체 (Wi-Fi 관리, 2026-11-16)

대시보드 Network 쪽의 Wi-Fi 관리(주변 Wi-Fi 검색, 비밀번호로 연결, 자동 연결·우선순위, 바꿀 때 자동 되돌림)는 Jetson 호스트의 네트워크 도우미 `/usr/local/sbin/ubts-network-mode` 가 맡는다. 이 폴더의 `update_network_helper.py` 는 그 도우미 파일 하나를 새 판(`deploy/offline_ap/ubts_network_mode.py`)으로 바꾸고, 문제가 있으면 이전 판으로 되돌린다.

| 파일 | 하는 일 |
| --- | --- |
| `update_network_helper.py` | 점검(기본, sudo 없이), `--apply` 설치, `--rollback` 되돌리기 |
| `ubts_network_mode.py` | 새 도우미. 묶음(kit)에만 들어 있다. 레포에서는 `deploy/offline_ap/ubts_network_mode.py` 를 쓴다 |

## 1. 바뀌는 것과 그대로인 것

바뀌는 것:

- `/usr/local/sbin/ubts-network-mode` 한 파일. 이전 파일은 `/var/lib/ubts-network-mode/helper-backups/` 에 남는다(root 전용).
- `ubts-network-mode.service` 를 한 번 다시 시작한다. 다시 시작은 NetworkManager 연결을 건드리지 않는다. 컨트롤러가 다시 떠서 지금 Wi-Fi(또는 AP)를 그대로 이어 간다.

그대로인 것:

- systemd 유닛 파일, `/etc/ubts-network-mode.conf`, AP 비밀번호 파일, AP 프로필, 저장된 Wi-Fi 프로필.
- 대시보드 컨테이너(권한, 마운트, 환경변수). 대시보드는 지금처럼 `nsenter` 로 도우미를 부른다. 새 명령(`wifi-*`)은 요청을 표준 입력의 JSON 으로 받아 비밀번호가 명령행에 나오지 않는다.
- AP 전환과 "저장된 Wi-Fi 검색"(AP 에서 나와 저장된 Wi-Fi 를 60초 찾고 못 찾으면 AP 로 돌아옴)은 이전과 같다.

새 도우미가 더하는 명령(대시보드만 부른다): `wifi-scan`, `wifi-saved`, `wifi-connect`, `wifi-confirm`, `wifi-revert`, `wifi-profile`.

## 2. 순서 (도우미 먼저, 대시보드 다음)

새 도우미는 옛 대시보드와도 그대로 동작한다(옛 명령 `status`, `request-ap`, `request-wifi` 는 같다). 새 대시보드를 먼저 설치하면 Wi-Fi 관리 칸만 "도우미가 실패를 알렸습니다"로 보이고 AP 전환은 동작한다. 그래서 도우미를 먼저 바꾸고 대시보드를 바꾼다.

1. 도우미: 이 문서 3절 (sudo 필요).
2. 대시보드: `deploy/jetson_dashboard/README_KO.md` (sudo 필요 없음, docker 그룹).

## 3. 현장 순서

묶음 `ubts-network-helper-kit-<커밋>.tar.gz` 를 Jetson 에 복사한 뒤:

```sh
cd ~/ubts-releases
tar xzf ubts-network-helper-kit-<커밋>.tar.gz
cd ubts-network-helper-kit-<커밋>
sha256sum -c SHA256SUMS                      # 모두 OK

python3 update_network_helper.py             # 점검: 아무것도 바꾸지 않는다, sudo 없이
sudo python3 update_network_helper.py        # (선택) root 로 점검하면 상태 파일까지 본다
sudo python3 update_network_helper.py --apply
```

점검이 보는 것: 새 도우미의 구문과 새 명령, 줄 끝(LF), 설치된 도우미가 알려진 판인지(2026-11-16 에 읽은 sha256 `94f61dca…59651`), 서비스가 active 인지. 끝 줄이 `점검 통과` 면 설치할 수 있다. 종료 코드 0 = 설치 가능(또는 이미 설치됨), 2 = 지금은 안 됨(이유가 화면에 나온다).

`--apply` 의 순서: 점검 → 컨트롤러가 전환 중이 아닐 때까지 최대 90초 기다림(AP 시작, 저장된 Wi-Fi 검색, 시험 연결 중이면 기다린다) → 지금 도우미 보존 → 새 도우미 설치(0755 root) → 서비스 다시 시작 → 30초 안에 확인(서비스 active, 새 상태 파일, Wi-Fi 관리 항목, 방식이 그대로, `wifi-saved` 명령 응답). 확인이 하나라도 안 되면 보존본을 다시 넣고 다시 시작한다.

종료 코드: 0 설치됨, 1 실패했지만 이전 도우미가 다시 돎, 2 시작 전에 거절(바뀐 것 없음), 3 자동 되돌림도 실패(5절).

영수증: `/var/lib/ubts-network-mode/helper-backups/receipt-apply-<시각>.json`. 파일 해시, 서비스 상태, 방식 낱말(wifi/ap 등)만 적는다. SSID, 주소, 비밀번호는 적지 않는다.

## 4. 되돌리기

```sh
sudo python3 update_network_helper.py --rollback                     # 가장 새 보존본
sudo python3 update_network_helper.py --rollback --backup <보존본 파일>
```

보존본은 지우지 않는다. 되돌린 뒤 대시보드 Wi-Fi 관리 칸은 "도우미가 실패를 알렸습니다"로 보이고, AP 전환은 이전처럼 동작한다.

## 5. 되돌림도 실패했을 때 (종료 코드 3)

서비스가 멈춰 있어도 지금 Wi-Fi 연결은 NetworkManager 가 그대로 유지한다(도우미는 연결을 끊지 않는다). Jetson 에 SSH 나 화면으로 접속해:

```sh
systemctl status ubts-network-mode.service --no-pager
journalctl -u ubts-network-mode.service -n 50 --no-pager
ls -l /var/lib/ubts-network-mode/helper-backups/
sudo install -o root -g root -m 0755 <보존본> /usr/local/sbin/ubts-network-mode
sudo systemctl restart ubts-network-mode.service
```

## 6. 시험 연결과 자동 되돌림 (새 도우미의 동작)

- 대시보드에서 Wi-Fi 를 고르면 도우미가 먼저 프로필을 준비한다. 새 Wi-Fi 는 메모리에만 있는 프로필(`/run/NetworkManager/system-connections/ubts-wifi-trial-<uuid>.nmconnection`, root 0600, 자동 연결 꺼짐)로 만들고, 저장된 Wi-Fi 의 비밀번호를 바꿀 때는 원래 파일을 `/run/ubts-network-mode/backup/` 에 두고 고친다.
- 컨트롤러가 새 연결을 시작한다. 45초(`UBTS_WIFI_TRIAL_LINK_SECONDS`) 안에 주소와 기본 경로가 안 생기거나 연결이 실패하면 곧바로 되돌린다.
- 연결되면 대시보드에 남은 시간과 '이 연결 유지' 버튼이 나온다. 150초(`UBTS_WIFI_TRIAL_SECONDS`, 60~300) 안에 누르지 않으면 이전 연결(저장 Wi-Fi 프로필 또는 AP)로 되돌리고, 이번에 만든 프로필을 지우거나 고친 파일을 원래대로 돌린다. 이전 Wi-Fi 로도 못 붙으면 기존 안전장치(저장된 Wi-Fi 60초 검색 → AP)가 이어 받는다.
- 확인하면 고른 자동 연결·우선순위를 넣고 프로필을 디스크에 저장한다(NetworkManager 의 to-disk 갱신).
- 시험 중 컨트롤러가 다시 시작되면 바로 되돌린다. 시험 중 Jetson 이 재부팅되면 메모리 프로필은 사라지고, 비밀번호를 바꾸던 저장 프로필은 새 비밀번호·자동 연결 꺼짐으로 남는다(원래 파일은 `/run` 에 있어 재부팅 때 사라짐). 이때는 다른 저장 Wi-Fi 나 AP 로 접속해 다시 고친다.
- 비밀번호는 표준 입력 → 도우미 → root 전용 NetworkManager 파일로만 간다. 명령행, 로그, 대기 명령 파일, 상태 파일, 응답에는 들어가지 않는다.
- 이더넷이 연결되어 AP 가 유지되는 동안과, AP 시작·저장된 Wi-Fi 검색·다른 시험 연결 중에는 연결 요청을 받지 않는다. AP 방식에서는 무선랜이 하나라 주변 검색을 못 한다(최근 검색 결과나 저장된 Wi-Fi 로는 연결할 수 있다).

## 7. 확인한 것과 하지 않은 것 (2026-11-16)

- 확인: 가짜 nmcli 로 한 시험(`tests/test_network_wifi_helper.py`: 새 Wi-Fi 확인·시간 초과 되돌림·잘못된 비밀번호·주소 없음·직접 되돌림·저장 Wi-Fi 비밀번호 바꾸기와 원복·AP 로 되돌림·재시작 때 되돌림·거절 경우·비밀번호가 명령행과 파일에 없음), 이 도구의 시험(`tests/test_network_helper_update.py`), Windows 와 WSL(Ubuntu 22.04, Python 3.10).
- 하지 않음: 실제 Jetson 에 설치, 실제 NetworkManager 1.36.6 에서의 시험 연결. 특히 메모리 프로필이 확인 때 디스크로 옮겨지는지(to-disk 갱신)는 실제 장치에서 처음 확인한다. 옮겨지지 않으면 대시보드가 "재부팅하면 사라질 수 있습니다"라고 알린다.
