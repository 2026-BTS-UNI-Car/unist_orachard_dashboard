# 2026-10-10 센서 관찰 처리 개선

**센서 관찰 서비스의 최적화를 Jetson에 설치했다. 지속 입력과 실제 자율주행은 아직 미완료다.** 모터 명령과 장착·제동 보정값 변경은 수행하지 않았다. 바퀴 전원이 꺼져 있다는 사용자 설명은 물리 주행 검증을 대신하지 않는다.

## 변경 및 검증

원본 센서 봉투의 검증 해시를 bytes의 `repr` 대신 타입·길이를 포함하는 버전별 SHA 입력으로 계산한다. 기존 경계마다 다시 계산하며 원본 CDR SHA, 전체 decode/equality, ownership·publisher GID·시계·Stop 검사는 유지한다. 이전 스캔과 정확히 같은 endpoint 배열은 최대 두 개의 불변 surface 결과를 재사용한다. 해시가 같아도 전체 bytes를 비교하며 float32와 비원시 endian은 기존 계산으로 돌아간다. 캐시는 검증한 패키지의 생성자 옵션으로만 켠다.

| 확인 | 범위와 결과 |
| --- | --- |
| 원본 기록 표면 비교 | 7기록의 첫 120스캔, 833쌍의 배열·법선·KD 조회 동일 |
| 전체 정합 비교 | 56쌍의 결과 동일, 원래 승인 54·거부 2 유지 |
| 통합 회귀 | 45개 PASS, 시계·변조·Stop·수신 후 commit 경계 포함 |
| ROOT 스트림·로더 회귀 | 69개 PASS |
| ROOT 공개 surface 회귀 | 독립 프로세스의 합성 검사 6개 PASS |
| 오프라인 digest 비교 | 같은 프로세스의 20 ABBA 블록, pair 중앙값 69.9→52.6ms; ARM/live 개선율로 일반화하지 않음 |
| Jetson 이미지 검사 | ARM64, 원본 C++ binary 재사용, source/binary/package SHA 및 기본 비실행 확인 |
| 운영 보존 | 기존 13개 서비스·지도 470스캔/queue0·설정 유지, 이전 관찰 서비스 보존 |

source 유효시간 250ms, RAW receipt 유효시간 200ms, 초기 IMU horizon 500ms는 유지했다. IMU driver의 20Hz 요청과 센서 설정은 바꾸지 않았다. 테스트 suite의 중복을 확인하지 않고 고유 테스트 합계를 만들지 않는다.

설치 이미지: `sha256:27f3e9d0cdd4ae71f4d5c07557d143c698dd4807cba216f86bec2e40d79c2b5d`.
패키지 HANDOFF SHA: `4d65ac4cc51172e83b8242bc074c9d6f222bc672cf552691c1bc89b70a15d1c5`.

## 실제 시험에서 남은 문제

두 번의 별도 시험에서 각각 60회 상태를 조회했다. 첫 시험은 IMU 도착 전 초기 LiDAR 3개 중 시각 검사가 실패해 정합 결과가 0개였다. 같은 버전의 재시험은 정합 결과 15개 뒤 `native_original_ingress_rejected:stale_or_future_native_source`로 실패했다. 마지막 정상 결과의 numeric fit은 17.18ms, prepare total은 23.63ms, commit total은 6.95ms였다. 이 마지막 정상 결과의 timing을 실패한 쌍의 timing으로 해석하지 않는다.

두 시험 모두 적용 좌우 출력 0, authority false, exit 0·kill 없음·queue0·reader/pipe 정리·owner 해제를 확인했다. 실패 후 읽어 버린 입력을 검증 완료로 승격하지 않는다. **60회 조회가 60초 지속 센서 처리 성공이라는 뜻은 아니다. native protocol qualification은 false다.** 이전 버전의 실패도 비공개 체크포인트에 보존한다.

대시보드의 **주행 제어 → 현장 시험 준비 → 관찰 시작/정지**로 확인한다. 입력 fault 후에는 관찰 정지와 owner 정리를 확인해야 한다. 관찰 서비스는 모터 장치·GPIO·모터 소켓 없이 설치하며 기본 대기 상태와 재시작 시 비구독 상태를 유지한다. 기존 `/api/autonomy/start`의 검증 대기는 유지한다.

## 다음 검증

- [ ] 실패한 초기 입력과 후속 ingress의 실제 sampled 시각 차이를 보존해 Header age·callback age·FIFO 대기를 구분한다. TTL을 완화하거나 시각을 다시 붙이지 않는다.
- [ ] 정상 종료까지 지속 입력과 처리 지연을 검증한다. IMU 50Hz 요청은 아직 적용·검증하지 않았다.
- [ ] GPS 융합·전역 지도 정확도·encoderless 차체 피드백과 실제 장착·회전·제동을 검증한다.
- [ ] SIM backend의 지도 적분 비용과 새 환경·다중 접속·실제 화면 지연을 검증한다. 이번 설치는 SIM runtime을 변경하지 않았다.

기존 전체 센서 audit에는 10월 3일 7기록의 LiDAR 32,047스캔과 IMU 63,515샘플이 있다. 기록된 GPS hacc는 3.0169–9.592m이며 RTK 정확도 입증이 아니다. PWM은 명령 이력으로 다루고 위치·바퀴 속도의 측정값으로 사용하지 않는다. 위 833쌍/56쌍 시험을 전체 녹화의 지도 정확도 검증으로 확대하지 않는다.

## 선택적 빌드

기존에 검증한 ARM 관측 이미지가 있는 Jetson에서만 다음 경로를 사용한다. C++를 재컴파일하는 빌드가 아니며 운영 Compose의 자동 실행 설정은 바꾸지 않는다.

```sh
docker build --pull=false --network=none \
  -f deploy/native_sensor_stream/Dockerfile.optimized \
  --build-arg PHYSICAL_PACKAGE_SHA=4d65ac4cc51172e83b8242bc074c9d6f222bc672cf552691c1bc89b70a15d1c5 \
  -t ubts-native-observation-reviewed .
python -B -m unittest discover -s deploy/native_sensor_stream -p 'test_*.py'
python -B tests/test_optimized_surface_cache.py
```

GitHub Actions를 사용하지 않고 직접 SSH로 빌드·설치했다. 구체적인 CID·실패 시도·실행 로그·Git 상태는 UBTS 비공개 체크포인트에 보존한다.
