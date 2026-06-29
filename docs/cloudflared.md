# cloudflared 설정

Cloudflare Tunnel을 쓰면 Jetson Orin Nano Super가 공인 IP를 갖지 않아도 외부 HTTPS 주소로 대시보드에 접속할 수 있습니다.

## 임시 테스트

대시보드 서버를 먼저 실행합니다.

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
