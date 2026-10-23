# 기록 재생 프로세스 분리 후보

현재는 **기본 OFF인 소스 후보**다. 실차·Jetson에는 설치하지 않았고 실제 child/HTTP 성능 시험도 하지 않았다. `DASHBOARD_ARCHIVE_PROCESS=1`을 명시한 새 서버에서만 사용한다. 다른 값과 미설정은 기존 경로다.

## 분리 경계

기록 목록, 기록 상세, timeline의 기존 세 가지 GET과 녹화 후 최선 노력 prewarm만 다룬다. timeline의 light/compact/polar 및 원본 profile은 기존 `recording_timeline`을 사용한다. 인증·세션·POST·수동 조종·STOP·실시간 센서·지도 생성·영상 Range/HLS/다운로드 경로는 이동하지 않는다.

메인 프로세스는 작고 제한된 명령을 stdin/stdout으로 보낸다. 별도 worker가 archive 전용 Provider, 파싱, 캐시, JSON 직렬화와 gzip을 담당한다. worker는 완성된 응답 파일의 고정 이름·길이·SHA·인코딩만 반환한다. 거대한 dict/pickle은 IPC로 보내지 않는다. 메인은 검증한 파일 descriptor를 64 KiB씩 전송한다. 메인의 SHA 검증과 metadata/stat 작업은 여전히 남으므로 실제 지연 개선은 별도 시험이 필요하다.

`TelemetryProvider.for_archive()`는 일반 생성자를 실행하지 않는다. fan/proc/recorder/live/rover 속성과 연결을 만들지 않고 기록 경로와 해당 메모리 캐시·잠금만 초기화한다. worker 환경에서는 모터·ROS·브리지·녹화기 endpoint 및 진단 변수를 상속하지 않는다. 이 구조는 OS 권한 sandbox가 아니다. 기존 제품 이미지와 UID의 코드 경계를 분리한 것이다.

## 제한과 수명

- worker는 한 개다. 응답 전송까지 포함한 입장권은 네 개이며 초과는 429다. 직렬 worker 대기·시작·처리·검증·응답 전송은 총 30초 절대기한을 공유한다. 시작은 최대 3초, 한 서버 수명에서 시작 시도는 세 번까지다.
- IPC 한 줄은 4096 bytes, 응답의 인코딩 전후는 각각 최대 128 MiB다. 응답 파일은 고유 ID이며 기존 파일을 덮어쓰지 않는다. worker stderr는 1 MiB에서 감시 거부한다.
- 새 기능의 전용 파생 캐시는 512 MiB/8192 entries 이내다. worker가 요청 전 사용량을 확인하고 모든 새 JPEG·segment·manifest 쓰기 전에 보수적으로 예약한다. 교체도 다시 청구하므로 여유가 있더라도 요청이 거부될 수 있다. 이때 원본·기존 캐시를 삭제하지 않는다. 응답 파일은 별도 제한이며 활성 네 개 입장권의 최악 임시 예산은 약 640 MiB이다.
- Linux worker는 AS 1 GiB, 파일당 약 129 MiB, 열린 FD 128을 제한한다. 부모 사망 SIGKILL을 설치한다. Windows는 부모 핸들 감시로 종료한다. Windows에는 Linux rlimit과 같은 메모리 보장을 주장하지 않으며 실제 진단은 Root 소유 kill-on-close Job과 예산을 추가로 요구한다.
- timeout·취소·worker crash·응답 epoch 불일치는 소유 Popen을 종료하고 요청을 실패 처리한다. 종료가 증명되지 않으면 Popen 핸들을 보존하고 새 시작을 거부한다. 서버 종료는 그 worker와 해당 응답만 정리한다. 종료 경로의 내부 최악 대기는 terminate 1초 + kill wait 1초 + reader 0.5초 + 진행 RPC 정리 1초다. 별도 실제 하네스는 이 경계를 정직하게 등록해야 한다.

## 기록과 캐시 보존

원본은 읽기만 한다. archive와 cache는 포함 관계를 거부하고 symlink/reparse 경로를 거부한다. 기록 ID는 고정된 단일 경로 요소다. 요청 전·worker 전후·메인 응답 파일 검증 후 source metadata fingerprint를 확인한다. 기록 목록은 최대 2048개다. 파일 내용 전체 해시는 이 경로에서 하지 않는다.

새 캐시는 기존 cache 아래 `archive-process-v1` namespace를 쓴다. 기존 캐시와 실패 증거를 지우지 않는다. timeline의 inode/ctime를 포함한 epoch가 바뀌면 다른 캐시 키를 쓰고 메모리 캐시를 무효화한다. 이미 준비된 LiDAR recovery는 기존 경로에서 읽기만 한다. worker의 캐시 순회와 쓰기 직전, 메인의 최종 JPEG 읽기에서 symlink·hardlink를 거부한다. 이 검사는 소유 디렉터리를 신뢰한다는 전제의 방어이며 악의적인 동일 UID의 동시 filesystem 교체를 완전히 sandbox하지 않는다.

## 확인 범위

작은 합성 시험 28개와 기존 archive-light 회귀 15개가 통과했다. 실제 Provider 일반 생성자·child·서버·장치·원본 기록을 시험에서 실행하지 않았다. 네 가지 timeline profile 및 gzip 결과를 작은 합성 기록과 기존 메서드 결과로 대조했다. 원래 POST/STOP·세션·영상/Range/다운로드 함수의 AST, 제어 API/core 및 기존 JS 파일은 고정 base와 동일함을 별도 검사했다.

아직 미검증: 실제 child 시작/종료와 Linux 제한, 실제 기록의 큰 timeline·JPEG 캐시 생성, 동시 요청과 중단, Windows 환경 호환, HTTP 응답 전송·브라우저 재생, 실제 STOP 지연이다. 실시간 매핑·실차 제동·자율주행 통과를 뜻하지 않는다. 기록 다운로드 tar 생성과 미디어 전송은 이 후보의 분리 대상이 아니다.
