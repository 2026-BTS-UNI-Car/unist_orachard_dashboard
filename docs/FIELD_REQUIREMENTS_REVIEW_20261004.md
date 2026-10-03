# 10월 3일 요구사항 재검토 · 2026-10-04

7개 기록의 원본 센서 전체 추출·해시·토픽 수 확인 및 도로5/과수원2 구분은 완료했다. 카메라는 장면 검토와 영상 재생에만 사용하고 위치 추정/OGM에는 쓰지 않는다. PWM296 안정 구간 분석도 완료했으나 엔코더·전압·검증된 차체 속도는 없다. 실제 모터 자율 구동은 보류한다.

## 추가 구현

- 실제 live map과 SIM의 지도 셀 저장을 타일로 분리했다. 원래 0.1m 셀은 유지하며 화면 PNG만 최대 1024픽셀로 축소한다. occupied 셀이 free/unknown에 묻히지 않도록 축소한다. RAM 셀 한도(기본1600만)에 도달한 경우는 `limited`로 명시하며 기존 셀은 계속 갱신한다. 이 변경은 위치 추정 자체의 정확도를 높였다는 뜻이 아니다.
- 기존 archive intermediate snapshot/과거시각 seek/zoom 유지 및 LiDAR180° 표시 배포는 유지한다. 원본 ROS 센서/TF/OGM 각도는 회전시키지 않는다.
- 720p 원본 compressed camera 전체 프레임 추출 및 실제 receipt 간격을 유지하는 VFR H.264/fMP4 HLS 변환 도구, 자동·수동 화질 선택, MP4 fallback을 구현했다. HLS.js1.7.3을 로컬에 포함하므로 AP 오프라인에서도 CDN을 요구하지 않는다. 원본 bag/metadata.json을 수정하지 않고 generated `adaptive_video` sidecar를 사용한다.
- 재생 프레임은 현재 시각 주변24초만 유지한다. 과거 seek 시 재수신한다. GPS 경로와 PWM 정보는 유지한다. HLS 사용 시 저해상도 preview JPEG를 중복 전송하지 않는다.
- SIM actuator response를 별도 모델로 분리했다. 약한 이동 선회, 높은 pivot deadband, 직진 편향과 stick-slip을 시험할 수 있다. 계수는 사용자의 실측 경험을 표현하는 가정이며 속도/제동 곡선의 fitted calibration이 아니다. 전달·기록 PWM과 Gazebo wheel target을 구분한다. 0출력은 정확히0이고 실제 하드웨어 모터는 접근하지 않는다.

## 전체 raw-rate 후보 holdout

| 기록 UTC | 장면 | 원본 스캔 | 기본 단절구간 | dense 보조 단절구간 | dense 승인 스캔 | p95 ms | 100ms 초과 |
|---|---|---:|---:|---:|---:|---:|---:|
| 072238 | road | 7799 | 3 | 3 | 7704 | 45.7 | 5 |
| 072329 | road | 913 | 1 | 1 | 906 | 33.0 | 0 |
| 072530 | road | 5243 | 3 | 3 | 5208 | 50.7 | 20 |
| 073551 | road | 8108 | 12 | 12 | 7877 | 65.9 | 119 |
| 080351 | orchard | 2459 | 61 | 48 | 1961 | 98.3 | 102 |
| 080909 | orchard | 2093 | 39 | 23 | 1816 | 93.8 | 96 |
| 081934 | road | 5432 | 9 | 8 | 5270 | 73.8 | 113 |

각 수치는 로컬PC의 offline 실행이다. 동시 FFmpeg/CPU 작업 조건이 달라 처리시간을 엄밀한 속도 개선 비교로 해석하면 안 된다. 승인률/단절 감소는 궤적 정확도의 근거가 아니다. 모든 후보는 `autonomy_eligible=false`이다.

최근6개 스캔의 반복 관측 지형을 주 기준으로 대체한 시험은 짧은 도로와 과수원 모두 악화돼 채택하지 않았다. 기존 정합이 실패할 때만 보조로 쓰는 후보는 과수원 단절48→44,23→22로 조금 개선했으나 p95 약179/155ms로 실시간 기본값에 채택하지 않았다. single-wall 퇴화·상충하는 정합·충격·unknown-gap 불연속 기준은 유지한다.

## 아직 완료가 아닌 것

- 실제 live/archive mapper는 기존 display-decimated LiDAR·IMU 추정이다. GPS와 raw10Hz scan을 사용하는 새 후보는 offline 검증 중이며 실차 기본 추정기로 배포되지 않았다. GPS hAcc3~10m/비RTK, IMU와 LiDAR의 장착 회전/rolling ray timing 미검증을 지도에서 숨기거나 임의로 연결하지 않는다.
- 긴 실제 기록 전체의 타일 재생 시험, Jetson 처리시간과 위치/방향 기준을 통한 기하 검증, 끊긴 submap의 확실한 재위치 추정, 새 과수원/가지/벽/마지막 열 검증이 남았다.
- 원본 카메라7개 모두의 adaptive package 생성·느린 망 검증과 실제/SIM 직접 배포는 별도 체크포인트의 관측 결과로 완료 여부를 기록한다. 첫 시험은92초/1280×720/1359원본프레임/약14.9fps이며 마지막 화면 유지1프레임만 변환본에 추가한다. 영상30fps 원본이라는 주장을 하지 않는다.
- 검증된 body pose·braking/slip 실측으로 actuator coefficient를 식별하는 과정과 엔코더 없는 SIM runtime adoption이 남았다. 원본 GPS 위치/카메라/raw bag은 GitHub에 올리지 않는다.


## 추가 관측 근거

7개 전체 raw 후보 궤적을 타일로 재렌더링했다. 약1262만 셀(서로 연결하지 않은98개 submap 합계)을 보존했고 모든 submap에서 spatial clipping은0이었다. 정합이 거부된 스캔은 삽입하지 않으며 격자 정확도/실차 측위 검증은 아니다. 파일의 시간은 원본 archive metadata.started_at으로 보정했다. bag 시작 기준과 섞어서 과거시각 seek를 표시하지 않는다.

실제 Chrome/실제92초 원본 변환 영상/150ms RTT 시험에서 2Mbps 첫 재생 약3.5초,512kbps 약11.3초를 관측했다. 두 조건에서 과거/미래 seek와 자동 화질이 동작했고2Mbps에서720p 수동 선택을 확인했다. 드롭0·JS 오류0·media HTTP 오류0이다. 512kbps에는 재생 중 버퍼 부족이 있어 무버퍼링 완료로 표시하지 않는다. 초기4초는 낮은 rendition으로 빠르게 시작하고 이후 bandwidth에 따라 올리며, 최고 원본720p는 선택 가능하다. 이 결과는 실제 Jetson/Cloudflare 회선 벤치마크와 구분한다.

처음 추출한 영상의 bag origin이 metadata.started_at보다0.701638초 늦음을 확인했다. VFR 간격은 보존하고 video offset을 명시했다. 원본1359프레임→1360변환프레임(마지막 hold1),1280×720,H.264High level4.1,91.212초를 ffprobe로 확인했다. 상세 public 증거는 `field_improvement_evidence_20261004.json`이다. 배포 증거는 배포 후 별도로 갱신한다.


## 직접 배포 체크포인트

실제 web 소스61fbd33/image744c4736 및 SIM 역할 소스05ba608를 직접 적용했다. 실차 센서/모터12컨테이너와 원본 metadata41개, SIM 원본 metadata10개·설정·GUI 이미지·mount를 보존했다. SIM GUI는 교체 simulator namespace로 재연결했다. 실제/가상 모터 조종과 자율주행은 비활성이다. 근거는 `field_rollout_deployment_20261004.json`이다.

후속 실시간 센서 동기화 대기는 구현/7회귀 검증 완료이며 별도 배포를 진행한다. 기록 재생과 실시간 카메라가 같은 회선을 사용하던 경합도 수정 중이다. fresh disarmed 상태에서 해당 브라우저의 preview만 일시 중지하고, 정지·실패·사용자 조종 활성화 시 복구한다. 센서 캡처/녹화 설정은 바꾸지 않는다.

안정 표면만으로 overlap 분모를 바꾸는 추가 가지 대응 후보는080351 전체2459스캔에서1961승인/48구간으로 개선이 없었다. 채택하지 않고 private 실험·해시·결과를 보존했다. 정합 기준을 느슨하게 해서 모르는 구간을 연결하지 않는다.


## 512kbps 재생 보완과 SIM 구동 검증

360p 영상 peak 약593kbps에 4초 센서 chunk 약90.6KB(gzip)가 더해져512kbps에서는 지속 버퍼가 부족했다. 원본720p/480p/360p를 보존하고240p/180kbps target variant를 추가했다. 별도 fixture Chrome150ms RTT에서 첫 재생2Mbps 약2.7초·512kbps 약7.1초, 이후10초 관측에서 각170프레임·드롭0·버퍼29.1/1.1초를 확인했다. 과거/미래 seek와720p 수동 선택도 유지했다. 512kbps에서는 실제240p라고 표시한다. 전체 긴 기록/실제 저속 Cloudflare 회선의 무버퍼링을 보증하지 않는다.

기존 native MP4/상위 variant bytes와 원본 metadata/bag는 보존한다. generated manifest SHA를 지정한 Linux atomic directory exchange로만 새 variant를 설치하고 이전 generated directory를 backup으로 남긴다. 중단한 구형 batch를 재개해도 SHA로 검증된 상위 패키지를 다시 덮지 않는다. codec은 native·4 HLS 스트림 각1360 packet(원본1359+last hold1),102asset SHA로 검증했다. 직접 SSH background에서7개를 계속 처리한다.

현재 SIM plant만 네 가지 수동 입력을 실행했다. 같은50/50 요청에서 약0.428/0.431m/s effective target의 작은 편향,50/75 요청에서0.492/0.651의 완만한 차이,35/-35 PWM 존재에도 pivot effective0,70/-70에서강한pivot target과 즉시STOP0을 확인했다. 이 수치는 가정 계수의 가상 actuator target이며 실차 속도/제동·encoderless 위치 정확도 검증이 아니다. 새 녹화와 자율주행은 시작하지 않았다. 근거는 `sim_field_stress_runtime_20261004.json`이다.


## 최종 추가 적용 관측 · 2026-10-04

최신 실제 web은d267fbb/imagea432830d, SIM web3be39fe/image64e6e26a다. 센서 대기와 기록 재생 중 live camera preview 경합 수정은 두 환경에 적용했고, 실제 Cloudflare에서 자동 중지/복구를 관측했다. actual 정지 상태6초에서9scans/2018cells 생성 후 STOPpaused 보존. 주행 지도 정확도 합격은 아니다.

7개 전체 영상 native와4화질 HLS 변환·설치 및 native+4HLS의 packet/codec/SHA 검증을 완료했다. native47,084원본 프레임을 유지하고 마지막 hold만 각1개 추가했다. 모든 기록의 Cloudflare playlist/native Range를 확인했다. 이후 RFC8216 실제 segment 길이/bytes로 master BANDWIDTH와AVERAGE-BANDWIDTH를 바로잡았고 영상 bytes·시각·원본은 재인코딩하지 않았다. sourcef2804c6 도구로 generated sidecar만 atomic 교체/backup 보존했다. 150ms RTT 동일 fixture 관측에서2Mbps auto360p/첫재생2.7초,512kbps240p/7.2초,10초 drop0·seek·720p 수동을 확인했다. 긴 실제 느린 회선의 무버퍼링은 미확인이다.

GPS 속도 오차/NED velocity/UTC 품질은 actual GPS serial owner0df5ccd/image20d54fad에 추가했다. 다른12역할·metadata41 보존, API field 전달/패킷 증가 확인. 실제 recorder image 격리 ROS 검증5개·제외0으로 JSONL 보존을 확인했다. 현재 실내 no-fix다. 과거 speed accuracy는 저장되어 있지 않아 복구하지 않는다. [GPS/PWM 진단](GPS_PWM_SPEED_AUDIT_20261004.md).

새 raw/GNSS mapper는 여전히offline/defaultoff이며 main SIM은legacywheel/IMU다. candidate submap 회복·beam timing/장착·독립 기하/Jetson latency 검증, native tile persistence, 새 녹화 자동 adaptive 큐, 실차 제동/운동 fitting과 새 과수원 자율주행을 완료 처리하지 않는다. 최신 근거는 `field_followup_deployment_20261004.json`이며 이전 단락의 배포/진행 정보는 당시 이력이다.
