# IMU 수신 비율 경계 수정

2026-10-08 20:20 KST 기준, Jetson 운영 WEB에 배포를 완료했다. 새 ARM 이미지 `f3d9e2a5…`에서 기본 모듈 경로의 29개 검사와 네이티브 지도 API 검증을 통과한 뒤 WEB만 교체했다. 배포 후 SSH와 8개 HTTP API 응답을 다시 확인했다. 나머지 12개 서비스의 컨테이너·프로세스, 네이티브 라이브러리, 기존 470-scan 지도의 epoch·signed cell·backing 파일은 유지됐다.

기존 지도는 `needs_reset` 상태로 보존했다. 지도 시작·초기화·녹화·실제 모터 명령은 실행하지 않았으며 자동 출력은 비활성이다. 이전 WEB과 소스 백업을 롤백용으로 남겼지만 롤백 실행을 재현한 것은 아니다. 대용량 기록물 보존 검사는 metadata 범위이며 전체 바이트를 다시 해시한 결과가 아니다.

GPS·IMU·2D LiDAR 매핑에서 충분한 IMU 데이터가 있는데도 정확히 80%인 구간의 gyro prior가 꺼지는 오류를 수정했다. 원본 ROS header의 정수 nanosecond와 실제 적분에 포함된 유효 구간만 누적해 `5 * covered >= 4 * interval`로 판정한다. float 적분값, 정합 수식, sigma, innovation 한계와 TTL은 유지한다.

기준점·현재 scan 모두 원본 정수 시각을 보관한다. 알 수 없는 header, 중복·역순, 200ms 초과 공백으로 수신 비율을 부풀리지 않는다. 실제 입력 경로에 일부 메타데이터가 없을 때 float 판정으로 우회하지 않는다. 과거 메타데이터 없는 직접 helper 호출만 기존 동작을 유지한다.

Jetson의 운영 이미지 `82aa0ea4…`를 기반으로 네트워크·센서·기록물 접근 없이 새 소스 두 파일만 읽기 전용으로 덮어 씌운 ARM 컨테이너에서 29개 검사를 통과했다. 검사에는 정확한 80%, 1ns 미달, 원본 시각, 공백, 회전 방향, 실시간/기록 동일 prefix, 지도 유지·중지와 새 지도 이후 이전 작업 차단이 포함된다. 기존 운영 컨테이너와 앱 소스는 이 검사에서 변경하지 않았다.

SIM 쪽은 Linux 44개 검사를 통과했고 같은 플랫폼에서 원본/수정 적분 상태의 bitwise 동일성을 확인했다. 공개 회귀 검사에 들어 있는 Windows 기준 수치와 Linux SciPy 계산은 일부 1 ULP 차이가 있어, rate·integral의 비교에만 1e-14 허용치를 사용한다. 원본 정수 시각 및 float 시각·수신 구간 상태 비교는 그대로 유지한다.

이 수정은 지도 정확도나 실차 자율주행의 합격을 뜻하지 않는다. 같은 native 수집의 58구간에서 기존 결과를 모두 재현했고 이미 prior가 켜진 37건은 유지됐다. 수정 대상 21건 중 16건의 개별 yaw 오차가 개선됐고 5건은 최대 0.0239° 악화됐다. 주요 인접 스캔 오정합, IMU/scan 적분 끝 시각, 실측 장착·rolling scan·BODY 속도·제동·E-stop·전체 과수원 미션 검증은 남아 있다. 실제 자동 모터 출력은 비활성이다.

로컬 배포 증거: `actual-integer-coverage-web-release-candidate-20261008/runs/d22922daa4c44744b4c1cffca00356b1/apply/qualification-outer-verified.json`, SHA `35eeaa5337c192ffaa2f7f4480e17c96a19120abf03103997ea4a5e66e2706f2`. 독립 검토는 `actual-integer-WEB-postapply-independent-review-20261008`에 보존했다. 이후 실시간 처리 지연·지도 정확도와 물리 자율주행 검증은 별도로 계속한다.
