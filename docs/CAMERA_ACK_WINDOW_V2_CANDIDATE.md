# 카메라 전송창 V2 설치 후보

이 브랜치는 실제 대시보드용 소스 후보이며 보드에 설치하지 않았다. 기준은 `edfac41` 검증 UI 브랜치다. 2026-10-25 현재 장치의 카메라 서버 세 파일이 그 기준과 byte 단위로 같음을 확인했다. 화면 JS는 현재 장치의 최신 원본을 기준으로 카메라 구간만 수정해 다른 UI 변경을 보존했다. 조종·STOP·인증·카메라 캡처·원본 녹화 코드는 바꾸지 않는다.

기존 브라우저와 옵션 없는 연결은 V1 창 1을 유지한다. 지원하는 새 브라우저만 `?camera_ack_window=2` 또는 `?camera_ack_window=3`으로 V2를 명시 요청한다. 전역 `window.UBTS_CAMERA_ACK_WINDOW`도 같은 명시 옵션을 제공한다. 서버는 `flow=decode-ack-v2&ack_window=2|3`을 함께 받은 경우에만 V2를 쓴다. 잘못된 값은 기존 V1로 처리한다.

서버는 서로 다른 최대 3개 delivery sequence를 관리하고, ACK는 자기 연결·source epoch·sequence·source frame에 해당하는 credit만 반환한다. 중복·오래된 ACK·미래 ACK·다른 source/frame·형식 오류는 구분하며 다른 frame의 credit을 풀지 않는다. 오래된 미응답 frame의 8초 마감은 새 ACK로 연장되지 않는다. 송신과 pacer 대기도 이 마감에 중단되고 종료 시 모든 task와 credit을 해제한다. source 변경은 연결을 닫아 이전 TCP frame이 남은 상태에서 credit을 재발급하지 않는다.

미응답 packet은 최대 3개·합계 8MiB, 한 packet은 최대 5MiB다. 브라우저는 해독 중 한 장과 최신 대기 한 장만 가진다. 교체된 대기는 `superseded`로 ACK하며 해독 성공으로 세지 않는다. 해독 실패·timeout에서는 마지막 정상 이미지를 유지한다. 기존 byte ceiling과 화질·크기 적응 규칙, FramePacer, V1 budget 계산은 유지한다. SIM 전용 12MB/s·원본 JPEG 통과·MSAA·렌더링 옵션은 이 포트에 넣지 않았다.

SIM 소스의 같은 프로토콜은 실제 Linux 인증 프록시와 TCP 지연 시험에서 통과했다. 생성한 동일 1920×1080 JPEG(Q70, 429,113바이트)를 실제 디코딩한 결과이며 실카메라 시험은 아니다.

| 추가 왕복 지연 | 창 1 | 창 2 | 창 3 |
| --- | ---: | ---: | ---: |
| 100ms | 7.33fps | 14.67fps | 21.67fps |
| 200ms | 4.00fps | 7.67fps | 11.67fps |

이 실제 대시보드 포트는 fake clock 11개 검사, 실제 async 송신기와 메모리 WS 검사, 실제 JS 함수와 가짜 이미지 로더 검사, 기존 V1 해독 검사를 통과했다. async 검사에서 미응답 3개, 만료 시 pacer/send 취소, 종료 후 credit·byte·client 0을 확인했다. 이 포트 자체의 실제 네트워크·장치 성능 검증은 아직 하지 않았다.

현대 브라우저에 기본 창 3을 제공하려면 배포 시 별도 명시 feature flag로 bootstrap의 `window.UBTS_CAMERA_ACK_WINDOW=3`을 설정할 수 있다. 서버 기본값과 구형 클라이언트는 창 1로 둔다. 현재는 활성화하지 않는다. 실제 장치에서 최소 30초의 인증된 카메라 수신, 원래 화면 품질, 조종·STOP 경로, 해독과 종료를 확인한 후에만 이 옵션을 켠다. backend `app.py`를 다른 설치 작업과 동시에 변경하지 않는다.

표시 해상도·원본 녹화·WAN byte ceiling을 바꾸지 않았으므로 이 변경만으로 1080p/30fps를 보장할 수 없다. 위 JPEG의 30fps에는 payload만 12.87MB/s(102.99Mbps)가 필요하다. H.264 등의 후속 전송 방식은 별도 과제다.
