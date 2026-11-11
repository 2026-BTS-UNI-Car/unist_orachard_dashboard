# WS v2: 서버 생성·종료 계약 보완

WS v1의 Root 실제 socket-pair 2개는 PASS였지만 Linux 인증 proxy/camera 시험 2개는 `server_close()`의 `autonomy_observe_lock` AttributeError로 전체 FAIL이었다. 원 freeze `ea71fae3647673ce66851938e8ed4e6a1401198a795903c992a857685984a9c2`와 실제 실패 자료는 보존한다. 로그 중 일부 데이터 전달이 진행됐다는 사실로 전체 시험을 PASS로 바꾸지 않는다.

이 결함은 설치 기준 `0b5065b`에도 있다. DashboardServer는 자체 생성자가 없었고 create_server만 **서버 생성·bind가 끝난 다음** 서버 객체에 lock/관찰 자원을 붙였다. 직접 생성한 DashboardServer는 종료 필드가 없었다. 또한 stdlib TCPServer 생성자는 bind/activate 실패 시 가상 server_close를 호출하므로, 같은 필드 누락이 원 bind 오류를 가리고 관찰 자원 정리에도 영향을 줄 수 있다. 단순한 시험 dummy lock 문제나 새 relay의 회귀로 분류하지 않는다.

v2는 새 worktree에서만 바꾼다. 생성자가 진짜 instance RLock과 선택적 owned supervisor/observer/native client를 base bind **이전**에 갖춘다. 직접 생성하면 관찰 자원이 None이고, handler class의 다른 서버 자원을 몰래 가져오지 않는다. create_server는 이미 만든 같은 자원·같은 lock을 생성자에 명시 전달한다. 기존 호출자 호환을 위해 생성 후 같은 객체 연결도 유지한다.

server_close 본문, 관찰 stop/join 정책, 미종료 supervisor 거부, 수동 조종·STOP handler와 native/client 구현은 그대로다. 자동 관찰 Start나 실제 모터 출력은 추가하지 않는다. 더 넓은 create_server 초기화 실패 롤백을 모두 해결했다고 주장하지 않는다.

순수 검증은 relay 12개와 lifecycle 8개다. lifecycle 시험은 실제 소스의 생성자·close·create_server를 compile해 가짜 base와 대조하고, 원 AutonomyObservation 클래스에 가짜 thread factory를 넣어 publication cancel → 원 join → resources_closed를 확인한다. 시험용 가짜 lock으로 실패를 숨기지 않는다. 첫 pure fixture의 SESSION_TTL_S 누락은 시험 scope에 상수를 주어 고쳤으며 제품 버그로 계산하지 않는다.

Root가 실제 Linux 환경에서 별도로 실행할 최소 후속은 다음과 같다.

1. 원 인증 proxy/camera 시험 2개를 새 source로 한 번 실행한다: `python -B -X utf8 -m unittest discover -s tests -p test_ws_proxy_latency.py -v`.
2. 자식 환경에만 `UBTS_TEST_DASHBOARD_LIFECYCLE=1`을 지정하고 `python -B -X utf8 -m unittest discover -s tests -p test_dashboard_server_lifecycle_runtime.py -v`. 실제 원 observation worker를 종료해 thread/owned/resources/socket 상태를 확인하고, 두 번째는 Root 소유 첫 서버와 같은 포트의 bind 실패에서 원 OSError·worker 정리·첫 서버 보존을 확인한다. 무입력·무계획·모터 인터페이스 없음이며 기본은 skip다.
3. 정상 인증/역ACK/생성 camera 전달과 종료가 모두 통과한 뒤에만 배포 검토한다. 실제 throughput·느린망·RPS·다중 client의 일반 성능 개선은 이 소스 수정만으로 확정하지 않는다.

작성자의 실제 socket/HTTP/관찰 thread/Docker/SSH/Jetson 실행은 0이며 Root만 실제 후속을 실행한다. 기존 main/423 입력·설치판·원 v1 source와 실패 자료는 바꾸지 않았다.
