# Actual native sensor shadow — 2026-10-08

**현재 검증 상태 · 2026-10-08:** 실제 Jetson WEB에 signed gyro 검사 수정의 배포를 완료했다. 충분한 원본 IMU 회전 근거와 반대인 기하 fallback을 거부하는 두 파일 변경이며, ARM 44검사와 배포 검사를 통과했다. 새 WEB은 image `82aa0ea4…` / CID `07df9da9…`다. 이전 `06d722c9…` WEB은 stopped 상태로 보존했고 나머지 12개 서비스·native SO·470-scan 저장 지도와 epoch/backing bytes를 유지했다. 이후 curl HTTPS의 health·rover·live-map 3상태 조회도 통과했다: 조종 비활성·정지, 같은 470-scan/epoch, 지도 inactive/`needs_reset`, 자동 출력 false다. 이후 SSH 재접속은 banner timeout이어서 새 SSH inspect 성공으로 표현하지 않는다. 이번 archive 보존 검사는 metadata 범위이며 전체 bytes를 다시 해시한 결과가 아니다. AP는 OFF로 접속 검증은 보류 상태다.

실제 원본 LiDAR·IMU의 V4 commandless 진단은 15.016초 동안 141 scan(각 3,240 ray)·300 IMU에서 연속 상대 이동 진단 140개를 생성했다. 원본 CDR·16-byte callback GID·header·RAW/SYSTEM_TIME과 444행의 저장 바이트·종료·정리를 확인했다. V4 SOURCE `13be603a…`의 호스트 소스 갱신은 완료했지만 이 진단을 운영 WEB에서 상시 실행하거나 자동 출력을 활성화하지 않았다. 출력은 HOLD/0이며 장착·REP145·body 속도·제동·실제 지도 정확도는 미검증이다. 앞선 두 LIVE 실패는 이력으로 보존한다.

별도 BODY 수학 어댑터는 private 27검사, 같은 committed factory 상태에 잇는 기본 OFF wire는 9검사를 통과했다. 원본 delta/정수 간격의 SE(2) log와 명시 mount lever arm을 쓰며 미측정 설정은 HOLD/bodyNone이다. complete synthetic profile도 구간 constant-twist 진단일 뿐 물리 권한을 발행하지 않는다. BODY wire의 실제 설치·live 처리·반환 시각의 강한 freshness·body 정확도는 미검증이다.

SIM scalar/CDR trace의 C++ 3-topic 수신기를 격리 컴파일하고 private image `605fd4…`에서 **새 29검사 통과 + 이전 관련 99검사 재사용**을 확인했다. 128개를 이번에 모두 다시 실행한 것은 아니다. 최초 Jazzy constructor compile 실패와 V2 test harness 실패는 보존했다. ROS를 초기화하거나 센서 관측·130초 trial을 하지 않았으며 실행 중인 여섯 역할·1219-scan/full59 지도·112개 기록 파일/lock·source/world/백업을 유지하고 private CID를 정리했다. 실제 원본 수신을 연결하는 software gate이며 M12·M13 합격이 아니다. 최신 지속 부하 M12 p95 265.9ms/유효80.2%, 독립 pose M13 XY p95 0.770m/yaw0.167rad는 여전히 미합격이다. GPS·native IMU·2D LiDAR만 측위에 쓰고 wheel encoder·PWM·camera·Gazebo truth를 측위 입력으로 사용하지 않는다.

10월 3일 원본 기록의 정지 후보 4곳에서 JSON 332 scan·663 IMU와 PWM/GPS 행을 추출했고 원본 SHA를 유지했다. GPS 보고 속도와 raw IMU 회전/벡터 산포의 감소가 있지만 수신기 시각 차이·약 0.5초 명령 보고·미확인 적용 ACK·속도 정확도·장착 때문에 제동 시간/거리나 E-stop 성능을 인증할 수 없다. 뒤에 새 명령이 있는 창은 마지막 zero 관측까지 제한했다. PWM은 사건 구분에만 썼다.

- [x] 실제 WEB signed gyro 수정 두 파일의 ARM 검사·배포·기존 서비스/지도 보존.
- [x] 실제 원본 수신 → V4 명령 없는 15초/140개 상대 이동 진단과 소스 설치.
- [x] 원본 정지 후보 4개 JSON 창 추출·전후 GPS/IMU 진단. 제동 실측 자격은 별도 미완료.
- [x] SIM trace private 컴파일·새29검사와 관련99 재사용·원본/지도 보존. ROS live trial은 별도 미수행.
- [ ] M12 지속 지연·M13 지도 정확도, SIM trace live trial, 원본 수신 → body/production IPC/전체 mission 연결.
- [ ] 실제 장착·REP145·rolling scan·제동/E-stop·새 과수원 마지막 열/회전 공간과 물리 자동 출력 검증.

`usageLimited`는 Codex 목표 예산 상태이며 GitHub Actions 한도와 별개다. 아래 배포/검사 수치와 당시의 “현재/다음” 문장은 그 기록 시점의 이력이며 현재 판단은 이 상단을 기준으로 한다. 모든 TODO와 실차 자율주행을 완료 처리하지 않는다.

## V4 관측·패키지 설명 — 해당 관측 시점의 이력

현재 수정본 V4는 Jetson 실제 센서 통합 진단을 15.016초 동안 통과했습니다. 원본 LiDAR 141개(각 3240ray)·IMU 300개로 연속된 상대 이동 진단 140개를 생성했고, 모든 원본 수신 시각·CDR·publisher 출처와 정상 종료를 검증했습니다. 기존 13개 서비스·6개 보호 소스·470-scan 지도는 유지됐습니다. 센서 처리 검증이며 지도 정확도·body 속도·실차 자동 주행 검증은 아닙니다.

계산에 필요한 원본 IMU 보간 샘플과 최신 샘플만 묶고, 동일 경계의 중복 검사를 제거했습니다. 각 경계의 전체 원본 SHA/CDR 검사와 250ms/200ms 제한은 유지합니다. Factory 31개·runner 17개·portable package 4개 검사를 통과했습니다. SOURCE SHA는 `13be603ae2f35a5e17c5a8eef02a44a691ee4d3b2857a5ac0366bf082b05b2ba`입니다. 초기 설치 26개 파일과 V3의 9개 파일 교체 이력은 별도 체크포인트에 있으며, 검증된 V4의 7개 호스트 소스 파일은 raw 백업·CAS·SOURCE19 검증으로 갱신 완료했으며, 기존 보호 소스 6개와 운영 서비스는 변경하지 않았습니다.

이전 실패 두 건은 보존했습니다. 첫 실행은 불필요한 과거 IMU의 보관 경계 검사에서 중단됐고, 두 번째는 필요한 IMU의 200ms 수신 제한을 1.254ms 초과했습니다. 제한을 늘리거나 원본 시간을 변경해 통과시키지 않았습니다.

대시보드 위치 추정의 회전 방향 검사도 수정했습니다. 충분한 원본 gyro 구간이 있을 때 그 방향과 어긋나는 기하 fallback을 거부합니다. 기본 모듈 6개 검사에 이어 별도 ARM 44검사와 실제 WEB의 두 파일 배포를 완료했습니다(image82aa0ea4/CID07df9da9). 지도 정확도·지속 지연·실차 자동 구동 검증은 미완료입니다.

새 관측 전용 패키지는 [deploy/native_sensor_shadow](../deploy/native_sensor_shadow/README.md)에 있습니다. 기존 대시보드·센서 드라이버·모터 제어 서비스를 수정하거나 자동 실행하지 않습니다. 실제 자율주행 시작은 계속 차단되어 있으며 제안·적용 출력은 항상 HOLD/0입니다.

실제 Jetson에서 원본 CDR 관측자는15초 동안 LiDAR150개(각3240ray), IMU300개를 수신했습니다. 같은 native callback의 실제16-byte publisher GID, 원본 header와 CLOCK_MONOTONIC_RAW 수신시각을 보존했습니다. 모든150scan의 마지막 beam 시각이 callback 이후로 나오지는 않았습니다. 이는 관측자의 수신 검증이며 새 Python 처리기의 설치·실시간 처리 성공이나 주행 정확도 검증은 아닙니다.

소스 패키지는 독립적인13개 런타임 파일, 원본 observer 빌드 소스, runner와 Dockerfile을 포함합니다. 실제 ROS Iron SDK에는 NumPy가 있으나 SciPy가 없어, 검토된 immutable 이미지를 기반으로 새 파생 이미지에만 SciPy를 설치합니다. 기존 실행 컨테이너나 Jetson 호스트에는 설치하지 않습니다. 운영자는 실제 binary SHA와 현재 GID, 원래13role·센서·기록·470지도 보존 조건을 검증한 뒤 별도 관측 컨테이너에서 실행해야 합니다.

처리기는 원본 CDR과 causal IMU로 LiDAR 센서 평면의 상대 이동·회전을 진단합니다. 실제 IMU REP145, 센서 장착·기울기, body 좌표 속도, rolling scan deskew, 제동 특성은 아직 검증되지 않았습니다. 휠 엔코더·PWM·카메라·Gazebo truth를 위치추정에 사용하지 않습니다. GPS도 현재 fix가 없어 위치 권한을 대신하지 않습니다.

현재 로컬 검증은 factory31개·runner17개와 portable package4개의 pure/mock 테스트 통과이며, 별도 이미지에서 실제 fresh callback 처리·fit·최종 freshness fence의15초 성공도 확인했습니다. 기존 WEB에 통합 진단을 상시 실행하거나 자동 모터 출력을 활성화한 것은 아닙니다. 실패·불연속·지연을 시각이나 GID 변경으로 숨기지 않으며, 부분 원본과 원인을 남기고 HOLD합니다.

무결성 검증은 raw-byte SHA에 기반합니다. 패키지 내부 `.gitattributes`가 원래 CRLF를 포함한 파일을 보존하므로 복제 뒤 임의 LF 정규화를 하지 마세요. 기본 CLI는 계획만 출력합니다. 상세 설치·검증·명시적 관측 인자는 패키지 README에 있습니다.
