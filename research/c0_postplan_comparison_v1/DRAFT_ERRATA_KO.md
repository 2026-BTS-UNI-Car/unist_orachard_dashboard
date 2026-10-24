# 동결 전 초안 결함과 수정

첫 다섯 파일 초안은 map-disabled `stream_rows.StreamObserverRows`만 고려했다. 실제 map-enabled runner는 상속하지 않는 별도 `stream_rows_map.StreamObserverRows`를 생성한다. 독립 검토가 이 누락을 발견했으며, 수정 전 source를 설치했다면 새 op가 AttributeError를 일으킬 가능성이 있었다. 실제 실행·설치하지 않았으므로 서비스 장애가 발생했다는 뜻은 아니다.

최종 후보는 여섯 번째 MapRows에 같은 scalar getter를 추가하고, NATIVE_MAP_SOURCE의 해당 파일 SHA와 서비스 literal을 새 manifest에 결합한다. BUILD_PROVENANCE는 fresh derived image에서 stream_rows SHA 한 필드만 갱신한다. 두 실제 클래스의 full getter AST는 원본 그대로다. map-enabled 실제 클래스→실제 service helper dispatch, 양쪽 getter의 TTL/feature/Stop/None 및 no-range-copy 회귀를 추가했다.

첫 generated 시험은 15개 중 실패1/오류2였다. app 함수 이름을 main으로 가정한 AST 시험, Windows cp949 기본 읽기, Windows socket.AF_UNIX 부재를 fake transport 시험에서 처리하지 않은 시험 하네스 문제였다. 해당 시험만 실제 create_server, 명시 UTF-8, fake AF_UNIX로 고쳤으며 원 제품 안전 조건을 완화하지 않았다. 이어진 16개 시험은 전부 통과했다. 이 문서는 최초 초안 문제를 숨기지 않기 위한 기록이다.
