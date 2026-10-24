# 센서 WebSocket의 느린 클라이언트 처리

기준은 `0b5065b`(UI `9db6671` 포함)다. 설치판 전체를 최적화 완료로 판단하지 않는다. 기존 같은 fixture 측정은 LAN/느린 링크 결과가 혼재했고 입력 시작 위상·호스트 교란도 있었으므로 이번 변경의 효과로 재사용하지 않는다.

문제는 `DashboardHandler._relay_websocket`의 blocking `sendall()`이다. 대상 소켓의 송신 버퍼가 가득 차면 세션 만료를 확인하는 루프로 돌아오지 못하며 같은 연결의 반대 방향 ACK도 처리하지 못한다. 업스트림 소켓은 handshake 뒤 timeout=None이다. 이 현상을 실제 기준선 메서드에 가짜 소켓을 넣어 재현했다. 실제 회선에서 관찰한 지연의 단일 원인으로 확정한 것은 아니다.

새 `dashboard_server/ws_relay.py`는 인증을 마친 센서 relay에서만 사용한다. 양방향 pending buffer를 각각 65,536바이트로 제한하고, nonblocking partial send와 read/write readiness를 함께 처리한다. 버퍼가 차면 그 방향 입력만 멈추고 역방향 ACK는 계속 처리한다. 각 방향의 쓰기 진행이 8초간 없거나 원 세션 기한에 도달하면 relay를 끝내 소유 handler가 원래 경로로 정리한다. 마지막 소켓 timeout을 복구한다. 임의 바이트나 프레임을 버리지 않으며 JPEG 해상도·화질·캡처·원 ROS 데이터·녹화·센서 bridge는 바꾸지 않는다.

원 proxy가 30초 idle일 때 생성하던 LiDAR timeout JSON 주입은 제거했다. 빈 TCP buffer는 WebSocket frame 경계라는 증거가 아니므로 늦은 JPEG/scan 조각 사이에 주입하면 원 payload가 손상된다. 같은 이유로 세션 만료 때도 opaque relay가 close frame을 임의 주입하지 않고 소유 handler가 TCP를 닫는다. source bridge의 원 status와 기존 클라이언트 센서 나이 표시는 유지한다. split frame → 30초 idle → 나머지 frame이 정확히 같은 bytes가 되는 순수 회귀를 추가했다. Root 검토 지적을 반영한 수정이며, 서버의 generic idle 안내가 더는 생성되지 않는 의미 차이는 이 범위로 한정한다.

원 `DashboardHandler`의 나머지 46개 메서드 AST가 동일하다. 수동 조종·lease·STOP·HTTP 인증과 UI handler도 동일하다. 새 파일과 app.py를 함께 포함해야 하며 app.py만 설치하면 import에 실패한다.

검증 범위:

- 순수 12개 시험: blocked viewer에서 역방향 ACK, 부분 전송의 바이트 순서, handshake tail, spurious readiness, 세션 만료, 방향별 stall, 설정 복구, EOF drain, 독립 queue. 실제 네트워크 속도 측정이 아니다.
- 소스 대조: 실제 설치 app.py SHA `7ae72f613f96b140f0ff7e632e501a5c166a7b07ec11e6bc493316bee9b0fe50`와 기준선 원문이 같다. Root 읽기 전용 receipt `JETSON_PREDEPLOY_READONLY_20261024T044616Z.json`에 근거한다.
- 작성자의 실제 TCP·HTTP·브라우저·ARM·Jetson 실행은 0이다. Root의 v1 socket-pair 2개는 PASS지만 인증 proxy/camera 2개는 기준선에도 있던 서버 종료 필드 누락으로 전체 FAIL이었다. 이 v2의 lifecycle 수정과 실제 후속 자격은 별도 문서를 따른다. 기존 upstream 연결 실패 안내의 `_ws_send_json` 경로는 이번 변경 범위가 아니다. 네트워크가 8초 동안 전혀 쓰기를 진행시키지 못하는 viewer는 끊길 수 있다.

Root가 독점 측정 슬롯에서 실행할 최소 순서:

1. 순수 회귀: `python -B -X utf8 -m unittest discover -s tests -p test_ws_relay_backpressure.py -v`
2. 생성 byte/ACK만 쓰는 실제 socket-pair 시험 2개(최대 약 6초, 외부 접속·모터 없음): PowerShell에서 `UBTS_TEST_SENSOR_RELAY_LOOPBACK=1`을 해당 자식 환경에만 설정하고 `python -B -X utf8 -m unittest discover -s tests -p test_ws_relay_loopback.py -v`. 기본은 skip이며 작성자는 실행하지 않았다.
3. 기존 인증 proxy·binary decode-ACK 회귀: 같은 product 소스에서 `python -B -X utf8 -m unittest discover -s tests -p test_ws_proxy_latency.py -v`. cv2/numpy/websockets와 Linux fcntl이 있는 기존 환경을 사용한다. 환경 실패와 제품 실패를 구분한다. 이 시험은 실제 조종 API를 호출하지 않는다.

후속 같은 입력 A/B는 기준선 0b5065b와 이 후보만 비교한다. 64KiB 방향별 상한·원 세션 기한·바이트 순서·인증을 필수로 유지하고, 느린 viewer와 정상 viewer를 동시에 연결해 정상 viewer ACK/프레임 전달과 종료 후 relay/registry 부재를 확인한다. 입력 source count/시작 위상과 호스트 잡음이 다르면 속도 개선 판정은 보류한다. 다른 UI 성능 후보 20개 차이나 실차 물리 제동의 효과로 확대하지 않는다.
