# 원본 1080 JPEG 실시간 표시

2026-10-25 실제 Jetson에 적용했다. 지원 클라이언트는 새로고침하면 V2 전송창 3과 원본 JPEG 표시를 요청한다. 기존 서버 기본값과 구형 클라이언트는 V1·기존 preview 경로를 유지한다. `?camera_preview=legacy`는 원본 표시를 끄고 기존 preview를 요청한다. `?camera_ack_window=1`은 V1을 요청한다.

## 원인과 변경

카메라는 이미 1920×1080 MJPEG로 캡처하고 있었지만 bridge가 구독하던 `/camera/image_jpeg`는 회전·축소·재압축한 1280×720 preview였다. 카메라 설정을 올리는 것으로 해결할 문제가 아니었다. 원본 publisher `/camera/recording/jpeg`는 구독자가 있을 때만 카메라 원본 JPEG를 발행한다.

명시적인 `quality_mode=original&resolution=1920x1080&transport=binary-v1` 요청이 있을 때만 원본 토픽을 구독한다. bridge는 JPEG SOF와 format의 크기·방향·출처 선언을 검사하고, 원본 payload를 디코딩하거나 재압축하지 않고 전달한다. 수치 JPEG 품질은 추정하지 않는다. 원본 수신이 정상이고 기존 preview를 원하는 접속자가 없으면 bridge의 raw·preview DDS 구독도 해제한다. 원본이 오래되거나 기존 클라이언트가 접속하면 기존 구독을 복구한다. 카메라 드라이버 자체의 preview 작업은 변경하지 않았다.

원본 픽셀은 센서 방향이고 `rotation=180`을 선언한다. 화면은 디코딩 성공 후 CSS `rotate(180deg)`를 한 번 대입한다. 회전된 기존 preview에는 빈 transform을 대입한다. 회전을 누적하거나 전송 JPEG를 다시 인코딩하지 않는다. source 토픽·크기·방향·출처가 바뀌면 V2 연결을 닫고 새 연결에서 시작한다.

원본 요청자가 모두 닫으면 원본 DDS 구독과 최신 payload를 해제한다. 기존 packet 5MiB, 미응답 합계 8MiB, 창 최대 3, 절대 ACK 마감 8초, 최신 프레임 우선, WAN 상한 4,000,000B/s, FramePacer와 ACK 정책을 유지했다. 대역폭이 부족하면 원본 화질을 낮추지 않고 전송 간격을 늘린다.

## 실제 검증 범위

1. 원본 토픽을 5초 동안 읽어 94개를 확인했다. 모두 SOF 1920×1080, `original_camera_mjpeg`, 방향 180이었다. JPEG 파일은 저장하지 않고 SOF·format·첫/끝 SHA만 남겼다.
2. 정확한 현재 컨테이너의 writable layer를 snapshot한 뒤 카메라 파일만 겹쳐 설치했다. 실제 파생 이미지의 모듈 import와 계약 검증, runtime spec·source 보존, 정상 시작·idle 점검이 통과했다. 두 역할 설치 시간은 37.65초였다. 기존 정확한 컨테이너는 정지 상태로 보존했다.
3. Jetson localhost의 기존 인증 프록시를 통해 **30.0086초 동안 895개를 실제 디코딩했다(29.8248fps)**. 전부 **1920×1080**이었고 ROS callback 입력 SHA와 WebSocket JPEG SHA가 **895개 모두 같았다**. source 보고는 29.4–30.8fps였다. 매초 29–31개였고 JPEG 크기는 평균 112,011B, 최대 119,768B였다. bridge 인코딩 시간은 0이었다.
4. Cookie 없는 요청의 403, 기존 V1 연결, V2의 정확한 sequence ACK, 안정된 source/session, 정상 close가 통과했다. 요청한 명령은 카메라 frame ACK뿐이다. 조종 활성화·모터·녹화 시작은 0이었다.
5. 드라이버·카메라 설정·원본 녹화 경로·backend·STOP·인증·SHADOW·FramePacer와 기존 정책은 그대로였다. 각 source hash와 컨테이너 identity를 전후 대조했다. 화면 회전은 실제 JS 함수의 성공·실패·timeout·source 교체·연속 프레임 단위 검증으로 확인했다.
6. 검증 후 현대 클라이언트용 원본 표시 bootstrap만 원자적으로 켰다. 실제 HTTP `/` 응답과 파일 hash가 일치했고 서비스 재시작은 없었다. 이미 켜져 있던 V2 창 3도 그대로 유지했다.

이는 **localhost 경로의 실제 카메라 JPEG 디코딩 30초 검증**이다. 외부 WAN 브라우저에서 1080/30이 항상 유지된다는 의미가 아니다. 약 112KB JPEG의 30fps에는 payload만 약 3.36MB/s가 필요하다. 느린 회선에서는 같은 원본 화질을 유지하면서 FPS가 낮아진다. H.264 등 영상 전용 전송은 별도 작업이다. 이 검증에서 녹화물을 새로 만들거나 자율주행을 시험하지 않았다.

## 운영과 복구

이 저장소는 camera overlay의 소스다. 현재 장치의 전체 SHADOW backend를 포함하는 완전한 배포본으로 취급하거나 compose로 다시 만들면 안 된다. 현재 정확한 컨테이너의 writable layer를 snapshot해 기존 파일과 runtime을 보존해야 한다. 현대 클라이언트용 두 bootstrap flag도 현재 writable layer에 있다.

화면에서 임시로 기존 preview를 쓰려면 `?camera_preview=legacy`로 접속한다. 원본 표시 bootstrap만 되돌리는 helper는 기본 OFF이며, 명시 실행 시 현재 index hash와 역할 identity를 대조한 다음 백업 index를 원자 복원한다. 이 복원은 아직 실행하지 않았다. 전체 버전 복구용 이전 컨테이너와 원래 inspect도 설치 디렉터리에 남겼다. 원본 기록이나 볼륨을 지우지 않는다.

안전한 로컬 증거 위치:

- `CAMERA1080_ORIGINAL_METADATA_20261024T204057Z/RESULT.json`
- `CAMERA_ORIGINAL1080_INSTALL_READBACK_20261024T205117Z.json`
- `CAMERA_ORIGINAL1080_PRODUCTION_PROBE_20261024T205127Z/ROOT_RESULT.json`
- `CAMERA_ORIGINAL1080_BOOTSTRAP_ENABLE_20261025_R1/ROOT_RESULT.json`

위 경로는 `D:/Develop/UBTS/codex-continue-20261023/` 아래다. 원본 센서 파일·이미지·GPS·쿠키·토큰은 이 소스나 문서에 포함하지 않았다.
