# UBTS Jetson 오프라인 AP 번들

이 번들은 **별도의 USB Wi-Fi 어댑터만** Jetson의 AP로 사용합니다. 현재 기본
경로를 제공하는 내장/PCIe Wi-Fi는 후보로 선택하지 않으며, 다음 조건 중 하나라도
충족하지 않으면 활성화를 중단합니다.

- `/sys/class/net/<인터페이스>/device`의 실제 경로가 USB가 아님
- 해당 인터페이스 또는 같은 wiphy/같은 물리 USB 장치의 sibling netdev에 활성 연결,
  IPv4/IPv6 **어느 routing table에서든** 기본 경로, `iif`/`oif` policy rule이 있음
- 다른 NetworkManager 연결이 해당 인터페이스에서 활성 상태임
- 그 Wi-Fi PHY가 AP 모드를 광고하지 않음
- 로컬에 프록시 이미지, DNS 규칙, Portainer CA, 백엔드 서비스가 준비되지 않음
- AP 활성화 전후 전체 table의 기본 경로, `ip rule`, IPv4/IPv6 uplink
  `route get`의 interface/source/gateway/table 중 하나라도 달라짐

설치와 활성화는 분리되어 있습니다. `install.sh`는 파일과 **비활성 상태의**
systemd unit만 설치합니다. NetworkManager를 재시작/리로드하지 않고, 연결을 만들지
않고, 라우트를 바꾸지 않으며, timer도 켜지 않습니다.

## 구성

2026-10-04부터 대시보드는 `http://10.77.0.1`로도 접속한다. 다른 인터넷의
DNS나 공개 도메인의 HSTS와 무관하게 AP IP로 직접 요청하는 권장 경로다.
현재 보드의 단일 무선랜 자동 전환 구성은
[`jetson_offline_access.md`](../../docs/jetson_offline_access.md)를 따른다.

```text
phone --WPA2--> USB Wi-Fi AP (10.77.0.1/24)
                   |-- DNS bts.sarihub.com        -> 10.77.0.1
                   |-- DNS bts-docker.sarihub.com -> 10.77.0.1
                   |-- DNS jetson-ssh.sarihub.com -> 10.77.0.1 (TCP 22 direct)
                   `-- nginx 10.77.0.1:80
                         |-- bts.sarihub.com        -> http://127.0.0.1:8080
                         `-- bts-docker.sarihub.com -> https://127.0.0.1:9443
                                                       (CA 검증 유지)
```

DNS는 포트를 지정할 수 없으므로, 두 HTTP 호스트명을 AP 전용 주소의 HTTP 80으로
해석하고 host-network nginx가 `Host` 헤더로 백엔드를 구분합니다. nginx는
`10.77.0.1:80`에만 bind하므로 기존 uplink 주소에는 새 리스너를 열지 않습니다.
proxy와 dnsmasq에는 daemon 자체의 자동 restart 정책을 두지 않고 helper/timer가
검증 후에만 시작하므로 Docker/systemd 재시작만으로 리스너가 독립 부활하지 않습니다.
systemd unit도 NetworkManager나 Docker를 `Wants`/`Requires`로 끌어올리지 않으며,
기존 daemon이 사용할 수 없는 경우 해당 회차가 fail-closed로 끝납니다.

AP 주소는 NetworkManager의 `ipv4.method=manual`로만 부여합니다. 별도 dnsmasq가
**AP 인터페이스에만** DHCP/DNS를 제공하고 DHCP router option은 빈 값으로 보내므로
휴대폰에는 default gateway가 배포되지 않습니다. DNS upstream도 없으며 IP forwarding,
masquerade/NAT 또는 firewall rule을 만들지 않습니다. 따라서 이 AP는 인터넷 공유기가
아니라 Jetson의 두 로컬 HTTP 서비스와 SSH만 찾는 격리된 접속망입니다.

## 중요한 TLS 경계

클라이언트가 접속하는 주소는 다음 두 **HTTP URL**과 로컬 SSH 이름을 지원합니다.

- `http://bts.sarihub.com`
- `http://10.77.0.1` (권장 대시보드 주소, DNS 조회 없음)
- `http://bts-docker.sarihub.com`
- `ssh unist@jetson-ssh.sarihub.com` (DNS가 `10.77.0.1`을 반환하고 TCP 22로 직접 접속)

이 번들은 443 리스너, 자체 서명 클라이언트 인증서, 인증서 경고 자동 우회,
HTTPS에서 HTTP로의 강제 다운그레이드를 만들지 않습니다. 휴대폰과 Jetson 사이의
HTTP 트래픽은 WPA2 링크 보호에만 의존합니다. 브라우저에 해당 공개 도메인의 HSTS가
캐시되어 HTTP를 HTTPS로 자동 승격하면 이 구성은 의도적으로 실패합니다. 그 경우
HSTS가 없는 별도 브라우저 프로필을 쓰거나, 추후 휴대폰이 신뢰하는 로컬 CA를
명시적으로 배포해야 합니다. 인증서 경고를 무시하는 방식은 지원하지 않습니다.

반면 nginx에서 Portainer `127.0.0.1:9443`로 가는 구간은 항상 TLS 인증서를
검증합니다. `proxy_ssl_verify off`는 사용하지 않습니다.

## 사전 요구사항

- Jetson의 현재 인터넷 연결을 담당하지 않는 USB Wi-Fi 어댑터
- NetworkManager, `nmcli`, `iw`, `ip`, `ss`, `curl`, `dig`, `uuidgen`, `sha256sum`, `flock`
- `dnsmasq` (`/usr/sbin/dnsmasq`), `iptables-save`, `ip6tables-save`와 (설치되어 있다면) `nft`
- Docker Engine과 Docker Compose v2
- 현재 로컬에서 실행 중인 dashboard `127.0.0.1:8080`
- 현재 로컬에서 실행 중인 Portainer HTTPS `127.0.0.1:9443`
- USB 어댑터가 지원하는 AP 모드와 WPA2-PSK

작업 중인 인터넷이 있을 때 nginx 이미지를 먼저 내려받습니다.

```bash
sudo docker pull nginx:stable-alpine
sudo docker image inspect nginx:stable-alpine >/dev/null
```

Compose에는 `pull_policy: never`가 설정되어 있고 활성화 preflight도 로컬 image
존재 여부를 확인하므로, 오프라인 활성화 시 registry에 접속하거나 다른 image를
자동으로 받지 않습니다. 엄격한 배포에서는 검토한 digest를 별도 로컬 tag로 고정한
뒤 `UBTS_OFFLINE_PROXY_IMAGE`를 동일하게 지정할 수 있습니다. 다음 출력의 image ID와
가능한 경우 RepoDigest를 배포 기록과 대조하십시오.

```bash
sudo docker image inspect nginx:stable-alpine \
  --format 'id={{.Id}} digests={{json .RepoDigests}}'
```

`enable-auto` 실행 시 선택된 image reference는 root 전용 `ap.conf`에 함께 기록되어
timer가 기본 tag로 되돌아가지 않습니다. image reference는 비밀이 아니며 실제 image는
계속 로컬에 미리 존재해야 합니다.

```bash
sudo env UBTS_OFFLINE_PROXY_IMAGE=nginx:validated-arm64 \
  ubts-offline-ap enable-auto --interface wlx001122334455 \
  --password-file /etc/ubts-offline-ap/wpa2.pass
```

## 1. USB 인터페이스를 명시적으로 확인

USB 어댑터를 꽂은 뒤 이름과 실제 bus를 직접 확인합니다. 자동으로 첫 번째 Wi-Fi를
선택하지 마십시오.

```bash
nmcli device status
readlink -f /sys/class/net/wlx001122334455/device
ip -4 route show default
ip -6 route show default
iw dev wlx001122334455 info
```

실제 인터페이스 이름은 아래 모든 예의 `wlx001122334455`를 대체해야 합니다.

## 2. Portainer CA를 수동 검증하고 pinning

Portainer 인증서 체인의 CA(자체 서명 배포라면 검증한 서버 인증서 자체)를 신뢰할 수
있는 기존 배포 자료에서 가져옵니다. 네트워크에서 인증서를 자동으로 긁어온 직후
그 인증서를 자동 신뢰하는 TOFU 절차는 이 번들에 없습니다.

별도 신뢰 채널로 전달받은 SHA-256 fingerprint와 SAN을 먼저 비교합니다.

```bash
openssl x509 -in portainer-ca.pem -noout -sha256 -fingerprint -subject -issuer
openssl x509 -in portainer-ca.pem -noout -ext subjectAltName
sudo install -o root -g root -m 0644 portainer-ca.pem \
  /etc/ubts-offline-ap/portainer-ca.pem
```

`--portainer-tls-name` 값은 서버 인증서 SAN에 실제로 포함된 이름이어야 합니다.
예제의 `localhost`가 인증서와 다르면 올바른 SAN으로 바꾸십시오. 값이 맞지 않으면
preflight의 `curl --cacert`와 nginx 검증이 모두 실패하며 AP는 활성화되지 않습니다.

## 3. WPA2 비밀번호를 root 전용 파일로 입력

비밀번호는 저장소, 명령행 인자, `nmcli` 인자, systemd environment에 넣지 않습니다.
아래 명령은 터미널에서 echo 없이 읽어 root 전용 파일에 저장합니다.

```bash
sudo install -d -o root -g root -m 0700 /etc/ubts-offline-ap
sudo bash -c 'umask 077; IFS= read -r -s -p "WPA2 password: " secret; echo; printf "%s\n" "$secret" > /etc/ubts-offline-ap/wpa2.pass; unset secret'
sudo chown root:root /etc/ubts-offline-ap/wpa2.pass
sudo chmod 0600 /etc/ubts-offline-ap/wpa2.pass
```

허용 형식은 12-63자의 `A-Z a-z 0-9 . _ @ % + = , -`입니다. 활성화 시 helper는
비밀번호를 mode 0600 파일에서 읽어 `/run`의 짧게 존재하는 root 전용 임시
`passwd-file`을 통해 NetworkManager에 전달합니다. `set -x`를 사용하지 않으며
비밀번호 자체를 출력하거나 프로세스 인자에 넣지 않습니다. NetworkManager profile의
secret flag는 `not-saved`입니다.

## 4. 설치: 상태를 바꾸지 않는 단계

먼저 dry-run을 검토합니다.

```bash
cd deploy/offline_ap
./install.sh --dry-run
sudo ./install.sh
```

설치 후에도 AP와 timer는 꺼져 있습니다. 또한 현재 NetworkManager를 reload하지
않으므로 기존 인터넷 연결에는 영향이 없습니다. 설치기는 helper/timer/container,
`ubts-offline-ap-*` profile/keyfile 또는 이전 runtime baseline이 하나라도 있으면 기존
상태를 중지·삭제하지 않고 실패합니다. 설치와 활성화는 같은 operation lock을 사용해
timer와 수동 명령의 경합을 막습니다. 기존 설치의 갱신/제거는 별도의 검토된 유지보수
절차로 수행해야 하며 이 설치기가 자동 덮어쓰지 않습니다.

## 5. preflight와 dry-run

```bash
sudo ubts-offline-ap preflight --interface wlx001122334455

sudo ubts-offline-ap activate \
  --interface wlx001122334455 \
  --ssid UBTS-Jetson \
  --password-file /etc/ubts-offline-ap/wpa2.pass \
  --portainer-tls-name localhost \
  --dry-run
```

`--dry-run`도 USB bus, AP capability, default route, 파일 권한, 로컬 image와 백엔드
인증서를 실제로 검사하지만 `nmcli`, Docker Compose 또는 systemd 상태는 바꾸지
않습니다. 비밀번호는 dry-run 출력에도 나타나지 않습니다.

## 6. 1회 활성화

모든 preflight 결과를 확인한 뒤에만 실행합니다.

```bash
sudo ubts-offline-ap activate \
  --interface wlx001122334455 \
  --ssid UBTS-Jetson \
  --password-file /etc/ubts-offline-ap/wpa2.pass \
  --portainer-tls-name localhost
```

NetworkManager profile은 다음 값으로 강제됩니다.

- `802-11-wireless.mode=ap`
- `ipv4.method=manual`
- `ipv4.addresses=10.77.0.1/24`
- `ipv4.never-default=yes`
- `ipv6.method=disabled`
- `connection.autoconnect=no`

profile은 `/run/NetworkManager/system-connections`에 root mode 0600의 완전한
`.nmconnection` 임시 keyfile로 먼저 작성되고 같은 filesystem에서 hard-link no-clobber 방식으로 원자적으로 게시된 뒤
한 번의 `nmcli connection load`로 로드됩니다. 동일 이름 profile 또는 keyfile이 이미
있으면 이를 삭제하거나 수정하지 않고 실패합니다. 로드 직후 모든 보안·주소 property를
재검증하며 실패하면 이 transaction이 새로 만든 profile만 제거합니다.

활성화 직전/직후 다음 상태가 모두 동일해야 합니다.

- `ip -4/-6 route show table all default`
- `ip -4/-6 rule show`
- IPv4/IPv6 외부 probe에 대한 `ip route get`의 uplink interface, source, gateway, table
- global IPv4/IPv6 forwarding, `iptables-save`, `ip6tables-save`, 설치된 경우 stateless `nft` ruleset

AP 클라이언트 패킷이 기존 uplink로 전달되지 않도록 활성화 중에는 AP 인터페이스의
IPv4와 IPv6 forwarding sysctl을 모두 `0`으로 강제합니다. 두 값도 transaction baseline에 따로
저장되고 중지/실패 rollback 때 원래 값으로 복원됩니다. global forwarding이나 기존
uplink의 firewall/NAT 상태는 수정하지 않습니다.

`nmcli up`의 부분 실패, DNS 오류, proxy/양 backend health 오류를 포함한 어느 단계든
실패하면 `compose down → 전용 dnsmasq stop → AP down → 새 profile/keyfile 삭제` 순으로
rollback합니다. keyfile은 이 transaction의 UUID가 그대로인 경우에만 삭제합니다.
그 뒤 target active connection이 없고 AP forwarding, route/rule/uplink/firewall snapshot이
원래 상태로 돌아왔는지 검증합니다. 복원이 확인되지 않으면 일반 실패가 아니라
`rollback incomplete` fatal 상태로 끝나며 현장 복구 전 재시도하면 안 됩니다. 성공을
반환하기 전 AP DNS
`10.77.0.1:53`이 두 도메인을 모두 `10.77.0.1`로 답하고 dashboard와 Portainer proxy가
각각 정상인지 확인합니다.

불완전 rollback의 `/run/ubts-offline-ap` baseline은 진단 증거로 남으며 새 activation은
이를 덮어쓰지 않고 중단합니다. route/rule/firewall/uplink를 현장에서 baseline과 비교해
복구한 뒤에만 관리 profile의 안전한 정리 또는 명시적 재시도를 진행해야 합니다.

## 7. 휴대폰 DNS 설정과 접속 확인

휴대폰이 AP의 DHCP DNS 대신 통신사 DNS, Android Private DNS, 브라우저 DoH,
iCloud Private Relay, VPN/DNS profile을 강제하면 로컬 이름 override가 작동하지
않습니다.

- Android: 해당 Wi-Fi의 IP/DNS를 DHCP로 두고 **Private DNS를 자동 또는 사용 안 함**,
  브라우저의 Secure DNS/DoH를 끕니다. Strict Private DNS hostname을 사용하지 않습니다.
- iOS/iPadOS: 해당 Wi-Fi를 DHCP로 두고 iCloud Private Relay/`IP 주소 추적 제한`,
  VPN, 암호화 DNS profile 또는 브라우저 DoH를 이 테스트 동안 끕니다.
- 모바일 데이터가 실패한 Wi-Fi를 자동 우회하지 않도록 해당 기능도 잠시 끕니다.

그 뒤 정확히 다음 HTTP 주소를 엽니다.

```text
http://bts.sarihub.com
http://bts-docker.sarihub.com
```

대시보드의 권장 주소는 `http://10.77.0.1`입니다. IP 요청도 대시보드 healthcheck에
포함됩니다. `https://10.77.0.1`의 TLS 접속은 제공하지 않습니다.

## 8. USB 재연결/부팅 후 자동 재시도(명시적 opt-in)

`enable-auto`는 root-only 설정 파일에 인터페이스, SSID, 비밀번호 **파일 경로**,
Portainer TLS 이름과 검증한 proxy image reference만 기록하고 timer를 명시적으로
켭니다. 비밀번호 값은 기록하지 않습니다. 명령 후 timer가 AP를 활성화할 수 있으므로
활성화 권한을 부여하는 단계로 취급하십시오.

```bash
sudo ubts-offline-ap enable-auto \
  --interface wlx001122334455 \
  --ssid UBTS-Jetson \
  --password-file /etc/ubts-offline-ap/wpa2.pass \
  --portainer-tls-name localhost \
  --dry-run

sudo ubts-offline-ap enable-auto \
  --interface wlx001122334455 \
  --ssid UBTS-Jetson \
  --password-file /etc/ubts-offline-ap/wpa2.pass \
  --portainer-tls-name localhost
```

timer는 30초 간격으로 확인합니다. 지정 USB 인터페이스가 없으면 다른 인터페이스를
대체 후보로 고르지 않으며 proxy를 내리고 안전한 runtime AP profile을 정리한 뒤 기존
routing/uplink baseline 복원을 확인합니다. 출처를 증명할 수 없는 same-name profile/keyfile은
삭제하지 않고 실패합니다. 인터페이스가 존재하면 USB bus/AP/sibling, 전체 route/rule 및
firewall/forwarding, AP DNS, compose configuration fingerprint, container health와 dashboard 및
Portainer health를 매번 다시 검증합니다. 이 재검증 중 실패도 관리 profile을 내리는 검증된
rollback으로 이어집니다.

## 중지와 자동화 해제

```bash
sudo ubts-offline-ap disable-auto
sudo ubts-offline-ap deactivate --interface wlx001122334455
sudo ubts-offline-ap status
```

`disable-auto`는 향후 재시도만 끄며 현재 AP를 자동으로 내리지 않습니다.
`deactivate`는 runtime keyfile과 property가 모두 일치하는 관리 profile만 내리고 삭제한
뒤, 저장해 둔 활성화 전 routing/uplink baseline 복원을 검증합니다.

## 로컬 검증

```bash
./test_bundle.sh
```

이 검증은 Docker 유무와 무관하게 policy-route, sibling PHY/USB, 원자적 profile 생성과
transaction UUID 소유권, same-name/orphan 거부, `nmcli up` 부분 실패 rollback,
rollback-incomplete fatal, USB-absent timer, timer 시작 실패, installer 경합 차단,
manual/local-only DHCP-DNS, firewall/forwarding 복원 및 두 DNS 응답의 mock 회귀 테스트를
**필수 core test**로 실행합니다.
Docker가 없으면 이 core test를 건너뛰지 않고 통과시킨 뒤 Docker/nginx 통합 검사만
`OPTIONAL SKIP`으로 분리합니다. `shellcheck`가 설치되어 있으면 모든 shell script에
적용합니다. Docker nginx 검사는 `--network none`의 격리된
container loopback(`127.0.0.1`)에서만 구문 검사용 socket을 열기 때문에 실제 AP나 host
port를 열지 않습니다. 실제 활성화 때는 템플릿 변수가 `10.77.0.1`로 고정됩니다.
