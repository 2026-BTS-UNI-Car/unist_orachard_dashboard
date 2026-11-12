# 카메라 전송창 V2 적용과 검증

후속 변경(2026-10-25): 원본 1080 JPEG 표시도 실제 설치·검증했다. 기존 아래 720 결과는 V2 전송창만 적용했던 중간 검증 기록이다. 최신 결과는 [원본 1080 JPEG 표시](CAMERA_ORIGINAL1080_PREVIEW.md)를 본다. localhost 인증 프록시에서 895개/30.0086초, 29.82fps·1920×1080·모든 입력/전송 SHA 일치를 확인했고 현대 클라이언트 기본값으로 켰다. WAN 30fps 보장은 아니다.

2026-10-25 실제 Jetson의 카메라 브리지와 대시보드에 적용했다. 기준은 `edfac41` 검증 UI 브랜치이며 설치 전 카메라 서버 세 파일이 실제 장치와 byte 단위로 같음을 확인했다. 화면 JS는 장치의 최신 원본을 기준으로 카메라 구간만 수정해 다른 UI 변경을 보존했다. 조종·STOP·인증·카메라 캡처·원본 녹화 코드는 바꾸지 않았다.

이 브랜치는 카메라 파일 overlay의 출처다. 현재 보드의 별도 SHADOW 통합 backend 전체를 담는 배포본이 아니므로 저장소 전체를 덮어쓰거나 compose로 배포하지 않는다. 정확한 현재 컨테이너의 writable layer를 snapshot한 뒤 이 카메라 세 파일만 덧붙였다. 설치 전후 기존 `app.py`, SHADOW 모듈, FramePacer와 runtime spec을 그대로 보존했으며 이전 정확 컨테이너도 정지 상태로 남겼다.

기존 브라우저와 옵션 없는 연결은 V1 창 1을 유지한다. 지원하는 새 브라우저만 `?camera_ack_window=2` 또는 `?camera_ack_window=3`으로 V2를 명시 요청한다. 전역 `window.UBTS_CAMERA_ACK_WINDOW`도 같은 명시 옵션을 제공한다. 서버는 `flow=decode-ack-v2&ack_window=2|3`을 함께 받은 경우에만 V2를 쓴다. 잘못된 값은 기존 V1로 처리한다.

서버는 서로 다른 최대 3개 delivery sequence를 관리하고, ACK는 자기 연결·source epoch·sequence·source frame에 해당하는 credit만 반환한다. 중복·오래된 ACK·미래 ACK·다른 source/frame·형식 오류는 구분하며 다른 frame의 credit을 풀지 않는다. 오래된 미응답 frame의 8초 마감은 새 ACK로 연장되지 않는다. 송신과 pacer 대기도 이 마감에 중단되고 종료 시 모든 task와 credit을 해제한다. source 변경은 연결을 닫아 이전 TCP frame이 남은 상태에서 credit을 재발급하지 않는다.

미응답 packet은 최대 3개·합계 8MiB, 한 packet은 최대 5MiB다. 브라우저는 해독 중 한 장과 최신 대기 한 장만 가진다. 교체된 대기는 `superseded`로 ACK하며 해독 성공으로 세지 않는다. 해독 실패·timeout에서는 마지막 정상 이미지를 유지한다. 기존 byte ceiling과 화질·크기 적응 규칙, FramePacer, V1 budget 계산은 유지한다. SIM 전용 12MB/s·원본 JPEG 통과·MSAA·렌더링 옵션은 이 포트에 넣지 않았다.

SIM 소스의 같은 프로토콜은 실제 Linux 인증 프록시와 TCP 지연 시험에서 통과했다. 생성한 동일 1920×1080 JPEG(Q70, 429,113바이트)를 실제 디코딩한 결과이며 실카메라 시험은 아니다.

| 추가 왕복 지연 | 창 1 | 창 2 | 창 3 |
| --- | ---: | ---: | ---: |
| 100ms | 7.33fps | 14.67fps | 21.67fps |
| 200ms | 4.00fps | 7.67fps | 11.67fps |

이 실제 대시보드 포트는 fake clock 11개 검사, 실제 async 송신기와 메모리 WS 검사, 실제 JS 함수와 가짜 이미지 로더 검사, 기존 V1 해독 검사를 통과했다. async 검사에서 미응답 3개, 만료 시 pacer/send 취소, 종료 후 credit·byte·client 0을 확인했다. 이 포트 자체의 실제 네트워크·장치 성능 검증은 아직 하지 않았다.

실제 장치에서 30초의 인증된 카메라 수신·해독·종료를 확인하고, 원래 backend·STOP·인증 경로의 source 보존을 확인한 후 현대 브라우저용 bootstrap의 `window.UBTS_CAMERA_ACK_WINDOW=3`을 명시 활성화했다. 서버 기본값과 구형 캐시 클라이언트는 창 1을 유지한다. 새 페이지를 열면 지원 클라이언트가 창 3을 요청하며, `?camera_ack_window=1`로 V1을 명시할 수 있다. backend `app.py`는 변경하지 않았다.

## 실제 Jetson 결과

- 카메라 두 역할 설치는 36.11초에 정상 완료했다. 파생 이미지의 실제 모듈 import, 기본 V1·명시 V2·4MB/s ceiling·credit 해제 검사를 통과했다. 컨테이너 설정과 보존할 소스의 전후 대조도 통과했다.
- 기존 인증 프록시를 통한 실제 카메라 30.0007초 시험에서 654장을 해독했다. 표시 크기는 **1280×720**, 관측값은 **21.80fps**이며 마지막 source 보고값은 21.3fps였다. 매초 20~24장이었다. 원본 녹화는 시작하지 않았다.
- Cookie 없는 연결의 403 거부, V1 연결 호환, V2 frame의 정확한 ACK, 안정된 source session, 자기 연결 종료가 통과했다. 조종 활성화·모터 출력·STOP 구동 명령은 보내지 않았다. 시험 전후 차량은 disarmed·정지, 녹화기는 idle이었다.
- 지원 브라우저용 창 3 flag는 `app.js` 앞 bootstrap 한 줄만 원자적으로 추가했다. 이전 index를 백업하고 파일·디렉터리를 동기화했으며 실제 HTTP 응답과 새 파일 SHA가 같음을 확인했다. 컨테이너나 서비스 재시작은 없었다. 이 flag는 현재 컨테이너의 writable layer에 있으므로 후속 배포에서도 현재 컨테이너 snapshot을 보존해야 한다.

이 결과는 720 표시 경로 검증이다. **1080 표시·30fps·원본 JPEG 그대로 전송은 아직 검증하지 않았다.** 기존 detail preview 상한 720과 WAN byte ceiling은 이 작업에서 유지했다. 1080 원본 녹화 경로는 바꾸지 않았다.

표시 해상도·원본 녹화·WAN byte ceiling을 바꾸지 않았으므로 이 변경만으로 1080p/30fps를 보장할 수 없다. 위 JPEG의 30fps에는 payload만 12.87MB/s(102.99Mbps)가 필요하다. H.264 등의 후속 전송 방식은 별도 과제다.
