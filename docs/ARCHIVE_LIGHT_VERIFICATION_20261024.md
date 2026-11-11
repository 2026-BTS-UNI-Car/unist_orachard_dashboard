# cold-cache 후보 검증 체크포인트

기준 `9db6671daacf81f06fa74b6145109d74820cff97`, 후보 branch `codex/archive-light-20261024`. 제품 파일은 telemetry.py와 telemetry_cache.py 두 개뿐이다. 수동 조종·STOP·lease 코드는 수정하지 않았다. 기존 J1·실제 설치·장치·원본 자료는 바꾸지 않았다.

## 실제 합성 검증

- r1: 신규11+기존warm1 PASS, 기존prepared fallback1 ERROR. 원래 r1.json/stdout 보존. 오류는 AST 격리 globals의 module context 누락으로 상대 import가 실패한 하네스 문제였다.
- 독립 정적 검토에서 transient BUSY/timeout/stale와 실제 preview 부재의 혼동, admission+Future 이중30초 경로를 발견했다. typed refusal을 full/light의 기존 ok:false 응답으로 끝내고 하나의 absolute wait budget을 공유하도록 고쳤다. 해당 관문2개를 추가했다.
- r2: 신규13+기존warm/prepared2 = **15/15 PASS**, 실패·오류·skip0. 실제 6.376초. 개인 HOLD를 O_EXCL로 잡고 종료 시 해제했으며 foreign SIM HOLD는 bytes 그대로 보존했다. 시험 전후 source SHA도 같다.

실제 production method AST를 그대로 추출한 작은 provider에서 실행했다. provider 초기화·native 장치 import·네트워크·브라우저·원본80MB timeline·원본 NPZ/기록을 사용하지 않았다. 준비된 fallback의 fixture만 작은 합성 gzip/임시 JSON으로 생성했다. 따라서 전체 앱 import/서비스 통합·실제80MB parse 횟수/지연/메모리·현장 STOP 반응·배포 검증은 아직 아니다.

## 검증된 경계

- 동시 같은 서명 요청은 실제 loader1회, owner 오류 시 waiter wakeup과 이후 재시도.
- 읽는 중 source 변경은 오래된 owner·waiter의 정상 반환을 차단한다.
- owner/LRU 및 waiter 상한, Future/slot cleanup, 같은 absolute wait budget.
- full LiDAR cache lock을 잡아둬도 light 응답이 완료되며 full build와 LiDAR/camera 목록 열람0.
- full RAM을 비운 뒤 작은 view를 재사용. 기존 완성 light cache도 큰 segment 재파싱 없이 사용.
- transient refusal은 준비된 LiDAR recovery를 실행하지 않는다. 실제 preview 부재 시 기존 prepared fallback은 동작한다.
- 작은 시간창·IMU compact profile·GPS/PWM sidecar·이전 샘플 carry-in·source 보존.

독립 정적 재검토에서 두 주요 지적은 닫혔고 새 중대 지적은 없었다. 다중 기록 LRU에서 light miss/full RAM hit로만 얻은 작은 projection을 별도 light LRU에 다시 넣지 않는 경로는 추가 parse를 만들 수 있는 성능 여지로 남긴다. 이 시점에서 범위를 더 넓혀 소스나 통과 결과를 바꾸지 않았다.

## 고정 소스

| 파일 | SHA256 |
| --- | --- |
| dashboard_server/telemetry.py | `7b90e74b525ab9c964fe11d7b6e6927bc5a772d73a4f2c94b45ce09e772a4bce` |
| dashboard_server/telemetry_cache.py | `26831e05e8504f1d01c37fca8a0faedcaef00f34106a09d0f263a2179a045cc6` |
| tests/test_archive_light_singleflight.py | `52ef24628fd73c6909000ec836e85c80e8359c42186409758a3042af374c5e51` |

실제 영수증은 `dashboard-uiperf-20261022/archive-light-20261024-tests/r2.json`과 r2.stdout이다. 원실패는 같은 폴더의 r1에 있다. 단회 실행기, 후보 원문 snapshot과 freeze도 그 폴더에서 보존한다. commit/push/설치는 아직 하지 않았다.

Root의 다음 실제 하네스/STOP 검토·성능 판정 전까지 개선 성공이나 배포 GO로 부르지 않는다. 기존 브라우저/gateway timeout과8초 판정은 바꾸지 않았다.
