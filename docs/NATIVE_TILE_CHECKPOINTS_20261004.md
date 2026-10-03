# 원래 해상도의 지도 보관

`TiledOccupancyMap.save_checkpoint(path)`는 overview PNG와 별도로 모든 64×64 log-odds 셀, 음수 좌표, 경로와 표시용 pose/provenance를 보관한다. immutable gzip blob을 먼저 설치하고 JSON manifest를 atomic 교체한다. 변경되지 않은 셀은 SHA로 검증한 blob을 재사용한다. 이전 세대 blob은 삭제하지 않는다.

`load_checkpoint`는 manifest/blob SHA, 압축·확장 크기, 타일 수, 좌표 중복, 셀 범위·수·bounds, config·pose·path를 검사한 뒤 완성된 map만 반환한다. pickle은 사용하지 않는다. 파일 단위 한도는 65,536타일·272MiB compressed·32M observed cells이며 path는12,000개 이하이다. caller가 mapper writer lock을 보유해야 한다.

실차 live 지도는 STOP/새 지도/정상 종료에 `runtime/live-map/last_native_map.json`과 blob을 저장한다. 서버를 다시 시작하면 저장된 지도를 표시하지만 센서 공백 사이의 이동을 알 수 없으므로 이어 그리기를 허용하지 않는다. 새 지도 버튼으로 새 위치 추정 구간을 시작한다. 이전 버전의 `last_map.json`은 PNG만 복구하며 native 셀 보관으로 표시하지 않는다. native 저장이 실패해도 STOP과 PNG 보관은 진행한다. PNG 저장 자체의 디스크 오류는 성공으로 표시하지 않는다.

SIM은 별도 persistent state volume의 `mapping/last_native_map.json`에 저장한다. 재시작 후 좌표 연결은 차단한다. SIM 녹화 종료/재생성은 각 기록에 `occupancy_tiles.json`과 blob을 보관한다. 이는 현재 wheel/IMU pose 기반 지도이며 엔코더 없는 측위 구현을 뜻하지 않는다.

raw 후보 재렌더 도구는 최종 submap마다 native checkpoint를 생성하고 다시 읽어 모든 배열·grid·pose·path를 비교한다. 후보 submap은 연결하지 않는다. 원본 scan/궤적과 시각별 overview는 별도로 유지한다. 현재 checkpoint에는 native 타일의 **최종 상태**가 저장된다. native 타일 LOD HTTP 조회, 과거 시각별 native 셀 snapshot, live/archive 공통 raw/GNSS mapper 및 위치 정확도 검증은 남아 있다.

기본 상태는 항상 표시용이다. 저장·복구 성공이 localization, sensor extrinsics, GNSS 품질 또는 주행 제어를 인증하지 않는다.
