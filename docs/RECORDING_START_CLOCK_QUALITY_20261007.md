# 녹화 시작 시각의 품질 표시

새 녹화는 `metadata.json`의 `clock_quality_snapshot`에 시작 순간의 시간 근거를 한 번 저장한다. GPS 수신이 이후 좋아지거나 끊겨도 이 snapshot을 다시 계산하거나 수정하지 않는다. 원본 recording ID, `started_at`, ROS header, sensor 수신 시각, 각 frame의 `t_s`, 영상 VFR 시간축은 변경하지 않는다.

표시 시각과 목록 정렬만 별도 `display_started_at`을 사용한다. 기존 감사로 생성된 유효한 `recording_clock.json` 보정은 새 snapshot보다 먼저 적용한다. 기존 기록에는 snapshot이나 sidecar를 새로 쓰지 않는다.

## 시간 근거

u-blox NAV-PVT의 UTC date/time validity는 위치 fix와 다른 필드다. 따라서 위치가 `no_fix`이거나 time-only fix여도 아래 시간 조건을 만족하면 UTC 근거로 쓸 수 있다. `fullyResolved` 하나만으로는 충분하지 않으므로 날짜·시간 validity와 정확도도 함께 확인한다. ([HPG 1.32 Interface Description, NAV-PVT](https://content.u-blox.com/sites/default/files/documents/u-blox-F9-HPG-1.32_InterfaceDescription_UBX-22008968.pdf), [ZED-F9P Integration Manual, Time validity](https://content.u-blox.com/sites/default/files/ZED-F9P_IntegrationManual_UBX-18010802.pdf))

- `schema=orchard.gps.v1`, `protocol=UBX_NAV_PVT`만 사용한다.
- `gps_time_valid_date`, `gps_time_valid_time`, `gps_time_fully_resolved`가 실제 boolean `true`여야 한다.
- receiver `tAcc`인 `gps_time_accuracy_s`는 유한 숫자이며 0~0.05초여야 한다.
- signed `gps_time_fraction_ns`는 boolean/float가 아닌 정수이고 -1e9~1e9 범위다. whole-second UTC calendar에 더한다.
- UTC 문자열은 명시적인 UTC timezone과 유효한 날짜를 가져야 한다. timezone 없는 값, 다른 timezone offset, calendar에 이미 붙은 fraction, 해석 불가능한 leap-second 값은 임의 보정하지 않는다.
- 마지막 NAV-PVT 수신의 monotonic age는 0~2초다. future/NaN/음수 monotonic 값은 거절한다. decoder의 host `received_at`도 finite하고 snapshot보다 미래가 아니며 2초 이내여야 한다.
- snapshot host 시각은 원본 `started_at`과 1초 이내여야 하며, 표시 보정 offset의 절댓값은 기존 sidecar 제한과 같은 24시간 이하다.

HPPOSLLH는 NAV-PVT 시간 필드를 복사하지만 자체 위치 packet 수신 시각이 새로 붙는다. 별도 NAV-PVT clock cache를 사용해 HPPOSLLH나 다른 문장으로 시간 age를 갱신하지 않는다. 같은 UTC epoch의 반복은 age를 갱신하지 않으며 이전 epoch 재수신은 cache를 무효화한다. UTC watermark를 유지하여 같은 오래된 frame을 두 번 보내도 다시 유효해지지 않는다. stale/no-data/unavailable 상태는 cache를 무효화한다.

## 표시와 한계

| API `clock_quality` | 대시보드 표시 | 의미 |
|---|---|---|
| `gnss_receiver_estimated` | GPS UTC 기준 | 검증된 receiver UTC와 host 수신 시각 차이로 시작 표시 시각을 추정했다. |
| `host_unverified` | 장치 시각 미검증 | 새 녹화 시작 시 검증 조건을 만족하는 receiver 시간 근거가 없었다. |
| `gnss_estimated` | GPS 시각 보정 | 기존 `recording_clock.json`의 유효한 감사 보정을 적용했다. |
| `host_clock` | 장치 시각 미검증 | snapshot 없는 과거 기록의 기존 API 값을 유지한다. |

`tAcc`는 receiver의 UTC 정확도 추정이며 USB·ROS 전달 지연을 측정하지 않는다. `gnss_receiver_estimated`는 host NTP 동기화나 시작 시각 50ms 정확도를 보증하지 않는다. evidence에 `transport_latency_verified=false`, `host_clock_sync_verified=false`를 명시하고 UI에도 수신 지연 미보정을 표시한다.

snapshot에는 UTC time/flags/accuracy/fraction, host·monotonic 수신 시각과 제한된 계산 근거만 저장한다. 위치·속도·위성 상태·driver payload·원본 GPS 객체를 복사하지 않는다. 별도의 기존 GPS 센서 기록은 원래 경로로 보존한다.

`display_clock()`는 metadata snapshot을 읽을 때도 원본 시작 시각 일치, 시간 검증 조건, offset과 표시 시각의 계산 일치를 재검사한다. 변경된 근거가 검증을 통과하지 않으면 `host_unverified`로 표시한다. 표시를 읽거나 목록을 정렬하는 작업은 기록 파일에 쓰지 않는다.

## 검증

- `tests/test_recording_clock_quality.py`: signed nano, tAcc 한계, strict flags, NaN/Infinity/future/미해결/잘못된 calendar, sidecar 우선순위, 수정된 snapshot 거절, 표시 정렬, 실제 NAV-PVT decoder의 time-only fix, synthetic recorder start/stop, source GPS `t_s`/frame 보존, old archive bytes 보존, HPPOS/duplicate/reordered epoch와 dropout을 검사한다.
- synthetic recorder는 생산 `RecordingState.start()/stop()/observe_gps()`를 임시 폴더에서 실행한다. `start_rosbag`은 mock이며 장치·ROS graph·실제 녹화 폴더에 접속하지 않는다. Windows에서는 ROS import 타입만 stub으로 제공하고, 별도 network-none ROS Iron 컨테이너에서도 기존 recorder/GPS 회귀와 함께 검증한다.
- `tests/test_recording_clock_quality.mjs`는 생산 UI 함수의 host/receiver/기존 sidecar 품질 표시를 확인한다. `node --check static/app.js`로 전체 문법도 확인한다.

이 패치는 로컬 검증 결과이며 Jetson 배포 및 실제 GPS 수신 상태 확인은 별도 단계다.
