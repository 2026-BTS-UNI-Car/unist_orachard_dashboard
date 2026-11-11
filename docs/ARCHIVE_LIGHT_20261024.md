# 기록 재생 cold-cache 최소 후보

기준 commit은 `9db6671daacf81f06fa74b6145109d74820cff97`이다. 별도 worktree/branch에서만 수정한다. J1 소스, 기존 scratch와 staged 변경, 실제 장치·서비스·기록은 바꾸지 않는다. 아직 실제 벤치·렌더·브라우저·장치·원본 배열 실행은 없다.

## 변경 범위

- 같은 경로·mtime_ns·파일크기 서명을 읽는 동시 요청은 현재 HTTP 요청 한 개의 Future를 공유한다. 새 threadpool이나 background worker는 없다.
- parse owner 상한은 기존 timeline cache count(기본2)다. Future 표는 finally에서 정리하며 동시에 상한을 넘지 않는다. 요청 waiter는 최대4×cache count이며 admission→Future가 하나의 absolute deadline(최대30초)을 공유한다. BUSY·timeout·source 변경은 typed refusal로 분리하고 기존 `ok:false,error` 응답으로 끝낸다. 실제 preview 부재와 혼동해 준비된 LiDAR recovery를 읽지 않는다. 새 HTTP 프로토콜이나 성공 응답은 만들지 않으며 브라우저·gateway·기존8초 판정은 바꾸지 않는다.
- shared cache lock 안에서는 큰 파일 I/O·JSON parse·Future/semaphore 대기를 하지 않는다. owner가 실패하면 waiter도 깨우고 다음 요청이 다시 시도할 수 있다. source가 읽는 중 바뀌면 이전 서명 결과는 게시·반환하지 않는다. 이 경로의 자동 재시도는0이다.
- 이미 parse한 timeline에서 IMU/GPS/모터·작은 metadata만 선택한 별도 LRU view를 보존한다. 크기는 기존 cache count 이하이며 LiDAR/camera frame 참조를 보존하지 않는다. 전체 timeline 복사나 새 디스크 cache 포맷은 없다.
- light 요청은 이미 완성된 작은 telemetry diskcache가 있으면 재사용한다. 없으면 source의 작은 view를 직접 slice한다. LiDAR segment를 파싱해서 telemetry cache를 만들거나 전체 LiDAR cache build/전역 lock을 기다리지 않는다. full-cache 생성과 full API는 기존 경로다.
- IMU/GPS/모터 시간창·중복 제거·sidecar·compact profile과 수동 조종/STOP/lease 경로를 유지한다. 기존 full RAM cache를 비울 때도 bounded light view는 남겨 반복 parse를 막는다.

서명은 기존과 동일한 mtime_ns+size다. 같은 크기와 mtime를 강제로 유지한 변조까지 검증하는 원본 SHA/불변성 증명으로 부르지 않는다. owner 파일 읽기의 디스크 hang을 이 수정이 중단한다고 주장하지 않는다. waiter 수명은 유한하지만 owner는 기존 동기 파일 I/O이고 원본 custody는 별도다. 기존 full LiDAR cache의 전역 lock 및 일반 기록 API 구조도 바꾸지 않는다.

## 사전 고정 집중 검증 계획

HOLD 중에는 source와 시험만 작성한다. Root 슬롯 배정 후 작은 synthetic timeline만 써서 다음 관문을 확인한다. 실제 80MB 자료와 원본 NPZ는 읽지 않는다.

1. 같은 source를 동시에 읽는 두 요청의 실제 loader 호출1회, 동일 결과, Future 정리.
2. owner 예외 및 owner TimeoutError 전파, waiter wakeup, slot 반환과 이후 재시도 가능.
3. 읽는 중 서명 변경: 이전 owner와 waiter는 빈 결과, 새 서명 결과만 현재 cache에 남음.
4. 서로 다른 기록의 owner 제한·LRU 제한, waiter 상한 초과 실패 폐쇄와 owner 결과 무오염.
5. full LiDAR cache 작업을 의도적으로 막아둔 동안 light 요청 완료. build 함수 호출0·LiDAR/camera 목록 열람0.
6. full RAM drop 뒤 light 요청이 같은 작은 view를 쓰며 loader 재호출0.
7. 기존 완성 light diskcache 재사용, 큰 LiDAR segment 재파싱0.
8. tiny window 응답의 IMU/GPS/모터·profile·빈 LiDAR/camera 계약과 source 보존.
9. BUSY·timeout·stale가 실제 preview 없음과 섞이지 않아 full/light 모두 recovery 호출0.
10. admission에서20초를 사용한 가상 시계 경로는 Future에 남은10초만 전달. 새30초를 시작하지 않음.

주요 시험은 실제 method AST를 그대로 추출해 소형 provider에서 실행한다. Windows에서 native device import나 provider 생성이 하드웨어/서비스에 접근하는 것을 피하기 위한 격리이며 알고리즘을 새로 구현한 대체물이 아니다. 결과는 source SHA와 함께 저장하고 실제 성능/배포 성공으로 승격하지 않는다. 이후 실제 하네스·센서 계약·STOP 검토와 성능 판정은 Root가 별도 수행한다.

첫 r1 결과는 신규11검사+기존warm1검사 PASS, 기존prepared fallback1검사 ERROR였다. 격리 AST globals의 `__name__`·`__package__`가 없어 상대 import가 실패한 하네스 오류이며 원래 r1.json/stdout은 보존했다. 이후 실제 Python module context를 격리 globals에 추가했다. 또한 독립 정적 검토의 transient refusal/recovery 혼동 및 최대60초 대기 가능성을 위 두 제품 경계와 두 검사로 보완했다. 보완 후 재검증은 Root 슬롯에서 별도로 수행한다.
