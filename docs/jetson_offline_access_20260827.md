# Jetson 오프라인 Wi-Fi 접속 설계와 실제 보드 판정

검증일: 2026-08-27 KST  
대상: NVIDIA Jetson Orin Nano Developer Kit Super (`L4T R36.5.0`)

## 결론

현재 보드의 내장 Wi-Fi를 AP로 전환하면 안 된다. 실제 보드에는 Wi-Fi 인터페이스가
`wlP1p1s0` 하나뿐이고 이 인터페이스가 현재 원격 접속과 Cloudflare Tunnel의 기본
경로를 제공한다. 무선 PHY는 AP 모드를 지원하지만 동시 managed+AP 인터페이스 조합을
지원하지 않는다. 따라서 원격 상태에서 이 장치를 AP로 바꾸면 현재 접속을 끊게 된다.

안전한 구현은 **별도의 AP 지원 USB Wi-Fi 어댑터**를 추가하고 그 장치만
`10.77.0.1/24`의 로컬 전용 AP로 쓰는 것이다. 저장소의 `deploy/offline_ap/` 번들은
USB 장치 여부와 uplink 비사용 여부를 확인하지 못하면 fail-closed로 중단한다.

## 실제 보드에서 확인한 기준 상태

| 항목 | 확인 결과 |
|---|---|
| Wi-Fi | `wlP1p1s0`, Realtek RTL8822CE PCIe |
| 현재 주소 | `192.168.219.173/24` |
| 기본 경로 | `default via 192.168.219.1 dev wlP1p1s0` |
| 동시 AP 조합 | 지원하지 않음 (`interface combinations are not supported`) |
| Dashboard | `127.0.0.1:8080`, 정상 |
| SSH | TCP 22, 정상 |
| Portainer | `127.0.0.1:9443`, 정상 |
| 관리 서비스 | NetworkManager, cloudflared, SSH 모두 active |
| 별도 USB Wi-Fi | 없음 |

## 보드 사전 설치 상태

2026-08-27에 다음 항목까지만 실제 보드에 적용했다.

- Docker Compose v2 `2.40.3`, `dig`, `nginx:stable-alpine` ARM64 이미지 준비
- nginx image ID `sha256:97d490c12ba55b4946b01546d1c3ed324e8d41ab1c9fcb2a616aa470620e5b46`
- `ubts-offline-ap` helper와 systemd unit 설치
- 필수 mock 안전 회귀 43개와 network-none nginx 설정 검증 통과
- timer `disabled`, dnsmasq/proxy `inactive`, AP NetworkManager profile 없음,
  `10.77.0.1` 주소 없음 확인
- 설치 전후 기본 경로와 NetworkManager·cloudflared·SSH 상태가 동일함을 확인

현재 Portainer 자동 생성 인증서는 SAN에 `localhost`가 있지만 subject와 issuer가 비어
있어 `curl --cacert` 검증이 실패한다. 이 상태에서 `proxy_ssl_verify off`로 우회하지
않는다. 별도 USB Wi-Fi와 함께 정상적인 subject/issuer 및 `localhost` SAN을 가진
Portainer 인증서/CA를 배포해 검증이 통과하기 전까지 AP 활성화는 차단된다.

## 오프라인 주소 계약

별도 USB Wi-Fi가 설치되면 휴대폰은 `UBTS-Jetson` WPA2 AP에서 DHCP/DNS를 받고,
로컬 dnsmasq는 아래 세 이름만 `10.77.0.1`로 반환한다.

```text
http://bts.sarihub.com            -> nginx :80 -> dashboard 127.0.0.1:8080
http://bts-docker.sarihub.com     -> nginx :80 -> Portainer 127.0.0.1:9443
ssh unist@jetson-ssh.sarihub.com  -> TCP 22 direct
```

DNS에는 포트 정보가 없으므로 Dashboard와 Portainer는 nginx가 HTTP `Host`로
구분한다. nginx는 AP 주소 `10.77.0.1:80`에만 bind한다. Portainer upstream은
HTTPS CA 검증을 유지하며 `proxy_ssl_verify off`를 허용하지 않는다.

이 AP는 인터넷 공유기가 아니다. DHCP default gateway를 배포하지 않고 DNS upstream,
IP forwarding, NAT/masquerade, 방화벽 규칙을 만들지 않는다. 따라서 정상 uplink와
라우팅 정책을 건드리지 않고 Jetson의 로컬 서비스만 제공한다.

## 활성화 안전장치

`ubts-offline-ap`는 다음 조건을 전부 만족해야만 활성화한다.

1. 선택 인터페이스의 sysfs 실제 장치 경로가 USB여야 한다.
2. 선택 장치, 같은 wiphy, 같은 USB 장치의 sibling 인터페이스에 활성 연결·기본
   경로·policy rule이 없어야 한다.
3. PHY가 AP와 WPA2를 지원해야 한다.
4. Dashboard, SSH, Portainer, dnsmasq, 고정된 nginx 이미지와 Portainer CA가 준비돼야 한다.
5. 활성화 전후 IPv4/IPv6 전체 routing table, policy rule, uplink route-get,
   forwarding과 방화벽 상태가 동일해야 한다.
6. DNS 세 이름, Dashboard와 Portainer 프록시를 모두 통과하지 못하면 즉시 rollback한다.

설치와 활성화는 분리한다. `install.sh`는 helper와 비활성 systemd unit만 배치하며
NetworkManager를 reload/restart하지 않고 profile·route·timer를 만들거나 켜지 않는다.

## 별도 USB 어댑터 장착 후 절차

인터페이스 이름을 자동 추측하지 말고 실제 USB 경로와 AP 지원 여부를 먼저 확인한다.

```bash
nmcli device status
readlink -f /sys/class/net/wlx001122334455/device
iw phy
ip -4 route show table all default
ip -6 route show table all default

sudo ubts-offline-ap preflight --interface wlx001122334455
sudo ubts-offline-ap activate \
  --interface wlx001122334455 \
  --ssid UBTS-Jetson \
  --password-file /etc/ubts-offline-ap/wpa2.pass \
  --portainer-tls-name localhost \
  --dry-run
```

dry-run 결과와 별도 복구 채널을 확인한 뒤에만 마지막 `--dry-run`을 제거한다. 자동
재활성화 timer는 별도의 명시적 `enable-auto` 작업 전까지 꺼진 상태를 유지한다.

## iPhone 검증 항목

- `UBTS-Jetson` SSID가 검색되고 WPA2로 연결되는가
- DHCP 주소가 `10.77.0.0/24`, DNS가 `10.77.0.1`인가
- 세 도메인이 모두 `10.77.0.1`로 해석되는가
- `http://bts.sarihub.com`과 `http://bts-docker.sarihub.com`이 올바른 서비스인가
- `ssh unist@jetson-ssh.sarihub.com`이 TCP 22로 직접 연결되는가
- 테스트 전후 기존 default route, Cloudflare Tunnel, 외부 SSH가 동일한가

공개 도메인의 HSTS가 휴대폰에 캐시돼 있으면 HTTP가 HTTPS로 강제 승격되어 로컬
접속이 실패할 수 있다. 인증서 경고 우회나 무검증 자체서명 인증서를 만들지 않는다.
필요하면 추후 휴대폰이 명시적으로 신뢰하는 로컬 CA와 별도 로컬 도메인을 설계한다.

## GPS 확인

Jetson Orin Nano Developer Kit에는 기본 GNSS/GPS 수신기가 없다. 실제 보드에서도
`gpsd`, GNSS ROS 토픽, GNSS 장치가 없었다. `/dev/pps0`는 `nvpps` ktimer이고,
`/dev/ttyUSB0`은 CP2102N USB-UART(LiDAR 사용 중)이므로 내장 GPS의 증거가 아니다.
지도 좌표가 필요하면 USB/UART 또는 M.2 방식의 외장 GNSS와 안테나를 추가해야 한다.

## 참고

- [NVIDIA Jetson Orin Nano Developer Kit hardware layout](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/hardware_layout.html)
- [NetworkManager settings reference](https://www.networkmanager.dev/docs/api/latest/nm-settings-nmcli.html)
- 상세 설치·rollback 계약: `deploy/offline_ap/README.md`
