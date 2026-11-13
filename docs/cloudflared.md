# cloudflared 설정

Cloudflare Tunnel을 쓰면 Jetson Orin Nano Super가 공인 IP를 갖지 않아도 외부 HTTPS 주소로 대시보드에 접속할 수 있습니다.

## 임시 테스트

대시보드 서버를 먼저 실행합니다.

> **주의(2026-11-13):** 차(Jetson)에서는 대시보드 컨테이너를 `docker compose`로 다시 만들지 않는다. Jetson의 compose 파일은 옛 이미지를 가리켜서
> 옛 대시보드로 돌아간다. 대시보드는 [설치 묶음](../deploy/jetson_dashboard/README_KO.md)으로 바꾼다. 아래 명령은 그 전의 기록이다.

```bash
cd /home/unist/workspace/unist_orachard_dashboard
docker compose up -d --build
# docker compose 플러그인이 없으면: docker-compose up -d --build
```

다른 터미널에서 임시 터널을 엽니다.

```bash
cloudflared tunnel --url http://127.0.0.1:8080
```

출력되는 `https://...trycloudflare.com` 주소로 접속합니다. 임시 주소는 재실행할 때 바뀝니다.

## named tunnel

```bash
cloudflared tunnel login
cloudflared tunnel create unist-orachard-dashboard
cloudflared tunnel route dns unist-orachard-dashboard bts.sarihub.com
mkdir -p /home/unist/.cloudflared
cp cloudflared/config.example.yml /home/unist/.cloudflared/config.yml
```

`/home/unist/.cloudflared/config.yml`의 `hostname`과 `credentials-file`을 실제 값으로 바꿉니다. 현재 공개 예정 주소는 `bts.sarihub.com`입니다.

LiDAR 실시간 지도는 브라우저에서 같은 origin의 `wss://bts.sarihub.com/ws/lidar`로 접속합니다. 권장 ingress는 `/ws/lidar`만 LiDAR bridge(`127.0.0.1:8765`)로 보내고, 나머지는 dashboard(`127.0.0.1:8080`)로 보내는 구조입니다.

```yaml
ingress:
  - hostname: bts.sarihub.com
    path: /ws/lidar
    service: http://127.0.0.1:8765
  - hostname: bts.sarihub.com
    service: http://127.0.0.1:8080
  - service: http_status:404
```

이 path route가 아직 적용되지 않았더라도 dashboard server가 `/ws/lidar`를 받아 내부 `ws://127.0.0.1:8765/ws/lidar` bridge로 프록시합니다. bridge가 꺼져 있으면 화면에는 연결 실패 대신 `bridge unavailable` 상태가 표시됩니다.

## systemd 등록

```bash
sudo cp deploy/systemd/cloudflared-unist-orachard-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared-unist-orachard-dashboard.service
sudo systemctl status cloudflared-unist-orachard-dashboard.service
```

## 보안 권장값

- Cloudflare Access로 연구팀 Google/GitHub 계정만 접속 허용
- `DASHBOARD_HOST=127.0.0.1`로 서버를 로컬에만 바인딩하고 외부 접속은 tunnel만 통과
- 쓰기 API를 공개하지 않기
- 터널 credentials 파일은 Git에 커밋하지 않기
- 실험 영상이나 원본 센서 데이터가 추가될 경우 별도 인증 레이어 적용
