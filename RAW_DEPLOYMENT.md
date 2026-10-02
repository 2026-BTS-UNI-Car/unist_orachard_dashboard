# Raw dashboard deployment checkpoint

## Latest live mapping and speed preference: 2026-10-02 KST

Source `7165518` is directly SSH-deployed in web image `6909dee0`. It adds the
below-LiDAR live map, stopped-map retention, occupied-object automatic framing,
persistent manual zoom and red trajectory, plus archived grid regeneration from
the original LiDAR/IMU records. The archive order is media, grid, IMU, GPS, PWM.
The autonomous speed preference is saved, while physical autonomous START stays
disabled pending encoderless localization/braking qualification.

The ARM isolated suite passed7 tests; native browser checks had no JavaScript
errors and generated a saved11-scan archive grid. Real live mapping accumulated9
scans in6 seconds, then stopped and retained the grid.12 other container
identities,32 original archive metadata inventories and protected settings were
preserved. No physical drive or new recording was performed. Existing camera,
manual control, raw IMU, LiDAR, PWM and recurring recording UI suites pass.

[Deployment proof](docs/jetson_live_map_deployment_20261002.json),
[browser proof](docs/jetson_live_map_browser_20261002.json),
[implementation and limits](docs/live_mapping_autonomy_speed.md).
The recovery helper and exact prior web image/source backup are retained at
`/home/unist/workspace/ubts-dashboard-releases/live-map-20261002/`.
Its `remote_release.py rollback` restores only the saved web image and selected
source files; sensor/motor services and raw archives are outside its mutations.
The final backup is the prior live-mapping web release, not the September image.

## Latest verified recorder polling: 2026-10-01 KST

Source `037607f0743364e04b4a99d05709756c7253eb31` runs in web image
`664ee011`. The recorder UI now polls repeatedly while the control page is open,
using a monotonic attempt interval and the existing timeout, single-flight and
reply-order guards. This fixes the display becoming unknown after the initial
freshness window despite a healthy recorder. Both recorder views recover when
requests resume; the timer only issues status GET requests.

Six actual and eight SIM JavaScript suites passed. The exact committed Git bytes
also passed the frozen UI suite, with 15 deployment-helper tests and four native
ARM recorder API tests. A separate post-deployment observation kept fresh idle
status for at least 20 seconds. Exact served source, runtime/configuration,
default image tag and all preservation checks passed: 15 other containers,
settings/Compose inputs and all 32 archive metadata inventories were unchanged.
[Polling deployment evidence](docs/jetson_recorder_ui_polling_deployment.json).

Recovery checkpoint: `20261001-130315-recorder-poll-037607f07433`, release
`recorder-poll-037607f-003`, pinned helper `deploy_recorder_ui_polling_003.py rollback`
with the checkpoint path and
`--digest edfa6ef1d5d6dc786383338d9a1c3fa19ae0aa0122368cab4f6fa88131f33b7a`.
The guarded rollback restores only its web image/source/default tag; the previous
image `a993984b` has a backup tag. No rollback, motor command or recording was
performed. This checkpoint verifies served source and API behavior; live browser
rendering and physical recording are separate checks.

## Prior recorder display freshness: 2026-10-01 KST

Source `aef37013607a306a2d7b5f0588c1fba5d1eff09e` now runs in web image
`a993984b`. This changes only `static/app.js`: the Data Recorder detail row
shares the primary recorder freshness gate, and the existing one-second local
UI timer expires both displays when network responses stop. Five actual and
seven SIM Node suites passed; the native ARM candidate passed four recorder
API freshness tests. Exact served bytes and all ten post-apply checks passed.
All 15 other containers, including GPS, settings/Compose inputs and 32 archive
metadata inventories were preserved. The recorder remains idle with fresh API status.
[Web-only deployment evidence](docs/jetson_recorder_ui_freshness_deployment.json).

Recovery checkpoint: `20261001-022818-recorder-ui-aef37013607a`, release
`recorder-ui-aef3701-001`, pinned helper `deploy_recorder_ui_001.py rollback`
with the checkpoint path and
`--digest cc6483c752e9d8f5b7c71ab2e41c1400b694b7c015f3fe91c53c6e93f0ec724e`.
Its owned rollback restores only the prior web image/source/default tag; all
idle/ownership/config guards stay enabled. The default web tag selects the new
image, and the previous target `7f5ea0aa` has a backup tag. No rollback was needed.
This checks served code/API state, not a new browser-render or physical recording
test. Browser timer scheduling remains subject to event-loop/background throttling.

## Prior GPS and recorder API deployment: 2026-10-01 KST

Direct SSH deployment of source `9e5ffd056e486be149c488ac1a939216ee405f3c`
updated only GPS driver, GPS bridge and web: images `9738ed34`, `b79baa57`
and `7f5ea0aa`. 39 scoped native ARM tests passed before application; the separate
source validation has 44 Linux tests. Exact served assets, fresh recorder API
status, advancing original NAV-SAT/NAV-SIG epochs and all 11 post-apply checks
passed. The GPS observation at that deployment was `no_fix`, 0 satellites used and fresh
empty satellite/signal lists. Other 13 containers, settings/Compose inputs and 32
archive metadata inventories were preserved. Recorder, motors and receiver
configuration were not changed. [Full deployment evidence](docs/jetson_gps_reception_recording_deployment.json).

Recovery uses checkpoint `20261001-015431-gps-reception-9e5ffd056e48` and its
captured per-role Compose chains, source backups and `rollback-*.json` files.
The release `gps-reception-9e5ffd0-002` retains the pinned
`deploy_gps_reception_002.py rollback` command; it requires the checkpoint path
and `--digest f2d1c91bac0f16789e06a113c41daa919819c8d89f6539ecbd965d9998ae17d7`.
Its ownership/idle guards must remain intact. The three default image tags now
select the new images, with original tag targets backed up; older explicit
overrides still select their saved images. No rollback was required. This
checkpoint verifies API/served bytes, not a new browser or physical recording test.

## Historical sensor state: 2026-09-30 KST

This historical section was superseded by the latest deployment above.
Direct SSH deployment completed the USB GPS mapping, native IMU relay/recording,
camera preview policy and the remaining native-IMU recording label. Current web
image is `96142732`, camera bridge `e0d95231`, IMU bridge `e5b89181`, recorder
`25401caa` and GPS driver `38e9d1f1` (full digests in the linked evidence).

- [Native IMU deployment](docs/jetson_imu_raw_removal_deployment.json)
- [Camera preview deployment](docs/jetson_camera_preview_deployment.json)
- [Recording label follow-up](docs/jetson_imu_recording_label_deployment.json)
- [Four-sensor observation and ownership limitations](docs/jetson_four_sensor_observation.json)
- [Initial GPS RF/satellite diagnosis](docs/jetson_gps_reception_diagnosis.json)
- [GPS antenna-supervisor follow-up](docs/jetson_gps_antenna_diagnosis.json)
- [Subsequent Windows USB reception](docs/windows_gps_reception_diagnosis.json)
- [Later Windows indoor placement comparison](docs/windows_gps_placement_comparison.json)

GPS USB reception worked on Jetson; diagnosis002 received 7,059 bytes without rejected UBX
frames. Its four satellite lists were empty; one signal list contained a BeiDou
search entry with C/N0=0, not an acquired signal or position fix. Five individually
queried RAM antenna-control/supervision values were false. This does not prove
physical antenna power OFF; MON-RF status/power remained DONTKNOW. The user reports
phone maps working with Wi-Fi and cellular disabled. Indoor placement alone is
not established as the cause. Board USB5V support does not verify the antenna
bias feed. Receiver settings were unchanged; the same GPS service was restored
with seven checks passing, 15 other containers unchanged and 32 archive metadata
inventories preserved.

The receiver was subsequently moved to Windows COM3 for two bounded observations
of 60.055s and 120.016s: 2,745 valid packets in total, zero rejected. The final
report had GPS8/GPS27/GLONASS18 at C/N0 30/29/27dBHz and quality7, but all 45
independent NAV-PVT samples still reported no fix and zero used satellites.
Reported signal entries are not the used-satellite count. The user confirmed
keeping the same antenna, moving both the GPS board and external antenna toward
a window and changing C-to-A to C-to-C;
host, placement, cable and elapsed acquisition time are confounded. Both reads
closed the port and made no receiver configuration writes. A placement-controlled
host/cable comparison, outdoor position fix and RTK validation remain pending.
The latest observation moved both the board and antenna back indoors, with the
same PC and C-to-C cable. It received 922 valid packets and zero rejected over
60.048s, but all 14 signal reports had zero locked signals. Only GPS14 appeared
with positive C/N0 (12 then 7dBHz), quality3/detected but unusable. All 15 NAV-PVT
samples had no fix and zero used satellites. This supports a placement-related
reception difference, not sole causation or a Jetson/cable defect. Different
satellites' C/N0 values are not a same-satellite attenuation measurement. The
port was closed; no receiver settings or firmware changed.

These are observation checkpoints, not a new Jetson deployment. Camera source
cadence was about15Hz despite30FPS capture settings; sustained30FPS/WAN performance
is not established. Actual motion, new recordings and physical calibration were
not performed; the32 existing archives were preserved.

## Historical checkpoints

2026-09-30 KST source checkpoint: the IMU availability/covariance and quaternion
corrections are validated but **not yet deployed**. All 154 unique local Iron
tests passed (152 in the main run, two opt-in integrations separately), with
63 focused actual/SIM Jazzy checks. [Validation details](docs/imu_contract_validation.json).
The real driver still publishes leading `-1` covariance, confirmed read-only.
Its subscribers are the preview bridge and recorder; no motion consumer was
observed. A scoped driver/recorder release, backup and ARM validation are being
prepared. The deployed application remains the motor-recording revision below.

2026-09-29 KST: motor pin/PWM recording deployed to the real Jetson.

- Application revision: `8fe7cd6ef37aa1d5fef8bc4b4f582d7e21b80c33`.
- Recreated exactly **`dashboard` and `ros2-data-recorder`**, copying six changed
  runtime files onto their respective previous immutable images.
- Web image: `sha256:2cd094c2450f355315fef0a697bbe2bf66c7b51d185af28abcddf7ec69fd7b64`.
- Recorder image: `sha256:6f73aba3b303ee68f3532d4a2a20a366518ce415770237b4f1f8b2225f51f345`.
- Private checkpoint:
  `/home/unist/workspace/ubts-dashboard-backups/20260929-210544-motor-8fe7cd6ef37a`.
- All **36 deployment checks passed**. The other 14 containers retained their
  IDs, images, start times, running state and restart policies. All 32 archive
  names/counts, settings and existing Compose inputs are preserved.
- The recorder passively receives fresh motor status with the new schema,
  zero recorded frames and no pipeline error. The rover is disarmed and the
  recorder idle. No actual new recording, arm or motion request was sent.
- Only the default **web** image tag was updated after verification. The
  recorder/shared ROS base tags used by bridges and drivers were not retagged.

New recordings include motor status and requested/applied topics, a full
`motor.jsonl` sidecar and a pin table aligned with archive playback. MCU-reported
PWM is distinguished from requested PWM and simulated output; it is not wheel
speed feedback. The actual vehicle has no wheel encoders. See
[recorded motor values and limitations](docs/motor_pwm_recording.md) and the
[sanitized deployment evidence](docs/jetson_motor_recording_validation.json).

The immutable ARM ROS Iron recorder candidate passed **24 tests in 9.243 s**,
including synthetic ROS subscription/rosbag/sidecar integration in a private,
network-isolated domain. The local isolated Iron full suite passed **144 tests
in 36.028 s**, with no skips. Node archive replay checks and
[GitHub CI 36564999759](https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/actions/runs/36564999759)
passed. Host, container and served static hashes match. The current browser
visit failed with `ERR_PROXY_CONNECTION_FAILED`; a successful live visual
inspection of this release is **not** claimed. No hardware recording or drive
test is covered by these results.

Two preparation attempts stopped before application: the legacy Docker builder
rejected `COPY --chmod`, then the capability-dropped test container could not
traverse the private release directory. The successful preparation used
`COPY --chown` plus explicit `RUN chmod`, and ran only the isolated candidate
tests as the release owner with writable tmpfs. Production users/configuration
were unchanged. No rollback was needed for the successful application.

### Recovery for the motor-recording release

Keep this checkpoint and its resolved configuration private. First confirm the
rover is disarmed and the recorder idle. Restore every manifest entry from
`host-source/` using the owner/mode in `baseline.json`; for entries whose
`old_sha256` is null, remove only that newly added file. The private release
helper's `replace_source()` and application failure branch implement this
procedure:
`/home/unist/workspace/ubts-dashboard-releases/8fe7cd6ef37aa1d5fef8bc4b4f582d7e21b80c33/deploy_motor_template.py`.
It has no standalone `rollback` subcommand. After restoring host sources, use
the saved **complete Compose chain** plus the rollback override:

```bash
python3 - <<'PY'
import json, subprocess
from pathlib import Path
app = Path('/home/unist/workspace/unist_orachard_dashboard')
backup = Path('/home/unist/workspace/ubts-dashboard-backups/20260929-210544-motor-8fe7cd6ef37a')
baseline = json.loads((backup / 'baseline.json').read_text())
command = ['docker', 'compose', '-p', 'unist_orachard_dashboard', '--project-directory', str(app)]
for path in baseline['config_files']:
    command.extend(['-f', path])
command.extend(['-f', str(backup / 'rollback-images.json'), 'up', '--no-deps', '--no-build',
                '--force-recreate', '-d', 'dashboard', 'ros2-data-recorder'])
subprocess.run(command, check=True)
subprocess.run(['docker', 'tag', baseline['default_web_image'], 'unist-orachard-dashboard:latest'], check=True)
PY
```

Then run the helper's rollback verification contract against the saved baseline:
old source/container/HTTP hashes, both old images, unchanged configuration,
all 14 other containers, settings, 32 archives and disarmed/idle state. Do not
retag a shared ROS image or use the older six-service rollback below. This
manual post-success recovery procedure has not been exercised.

## Previous chartless-page checkpoint

2026-09-29 KST: chart preparation is skipped on pages without history plots.

- Frontend revision: `8f37f43664c2a6450ca76d1f552d5bddfba222ed`.
- Replaced **only `dashboard`**, using the existing web image with one changed
  `static/app.js`. No sensor protocol, recorder, controller or driver changed.
- Image: `sha256:8e5a2ca68f75e83d6381e6dea43da921c6c9c91e0b67c20bff811dfb42468bc7`.
- Host/container/HTTP JavaScript SHA256:
  `81107e57c0c7e046a3f26e50d85dfda2d78975506711081b768dd0f3a9c1a180`.
- Private rollback checkpoint:
  `/home/unist/workspace/ubts-dashboard-backups/20260929-042306-static-8f37f43664c2`.
- All **16 checks passed** on the first attempt; no rollback was needed.
  All 15 other containers (12 running) and 32 archive names/counts are unchanged.
  Settings and Compose inputs are preserved; the rover remains disarmed and the
  recorder idle. No motion or recording command was sent.

The live sensor, research and documentation pages return before preparing chart
colors and history arrays on every animation frame. Explicit LiDAR redraws still
work. Existing history plots, separate CPU charts, route changes, zoom, resize,
theme and the 3200-point scan-before-ACK contract passed regression checks.

The local SIM browser gate observed zero history-only CSS/normalization calls
over 50.1416 seconds, then verified five nonuniform system chart canvases and a
return to the rover page. These checks establish the removed work and preserved
UI behavior; they do not establish a camera FPS improvement. A read-only Jetson
browser reload confirmed the executed early exit, live mode, 32 archives, short
guide, disarmed controls, both requested PWM values at 0%, and the initial LiDAR
counter. ROS/camera/IMU still showed connecting at the instant of inspection;
this limited check does not verify camera FPS or a nonblack scene.
See the [sanitized chartless-page deployment report](docs/jetson_chartless_render_validation.json).

Rollback remains web-only: restore this checkpoint's exact `host-app.js` while
preserving ownership/mode, then apply its `rollback-image.json` as the final
Compose override with `up --no-deps --no-build --force-recreate -d dashboard`.
This restores the preceding `3bca7845…` web image without restarting bridges,
drivers or the recorder. Raw inspect/Compose evidence stays in the private backup.

## Previous LiDAR redraw checkpoint

2026-09-29 KST: common LiDAR redraw optimization deployed to the real Jetson.

- Frontend revision: `f163ac12e182c1af32c68e7c5dac1ed893072516`.
- Replaced **only `dashboard`**. The image inherits the existing web runtime and
  copies only `static/app.js`; sensor bridges, drivers, recorder and ESP owner
  kept their container IDs, image IDs, start times and restart policies.
- Image: `sha256:3bca7845a7792c3dabff52febaaa18bfc2329b39da2bb516e35277cf00d05f5a`.
- Private rollback checkpoint:
  `/home/unist/workspace/ubts-dashboard-backups/20260929-034114-static-f163ac12e182`.
- All 16 deployment checks passed. Host/container/HTTP JavaScript hashes match,
  settings and Compose inputs are unchanged, 32 archives remain, and the rover
  and recorder remain disarmed/idle. No motion or recording command was sent.

System-history animation no longer redraws an unchanged live LiDAR scan on every
browser frame. New scans still render all 3200 requested points before ACK;
resize, theme, route and range changes retain explicit redraws. Both live LiDAR
routes use this behavior. Recorded playback, camera frame flow and control code
are unchanged. The same fix is present in the isolated simulation worktree.

The first attempt safely restored the old web image/file when an overly strict
comparison rejected reordered environment and bind lists. Read-only comparison
then confirmed identical unique environment assignments and identical unique,
nonoverlapping mount destinations. The successful retry canonicalized only those
order-insensitive lists; their values and all other runtime settings still match.
The initial attempt and rollback evidence remain in the private checkpoint.

Actual browser reload confirmed the new redraw state field, ROS-ready state,
32 archives, short control guide, live LiDAR updates, no AI labels, and zero
requested PWM/disarmed controls. The camera image remained black; the WAN Auto
profile showed 160x90 at about 2 browser FPS versus 15 source FPS. This verifies the
UI update, not a bright camera scene or a camera-rate improvement.

In a separate parked local simulation observation, canvas clears fell from 1516
over 20.0075 s to 121 over 20.0015 s. Camera DOM loads were 600 before and 544 after;
no camera FPS gain is claimed. See the [sanitized deployment and validation](docs/jetson_lidar_redraw_validation.json).

Rollback for this static-only checkpoint must also target only `dashboard`:
restore the checkpoint's `host-app.js` to the existing host `static/app.js`
while preserving its ownership/mode, then use the saved `rollback-image.json`
as the final Compose override with `up --no-deps --no-build --force-recreate -d dashboard`.
The checkpoint contains the exact old image ID, source and resolved Compose
configuration. Do not use the six-service helper below for this narrow rollback.

## Previous full application checkpoint

2026-09-28 KST: sensor execution, preview pacing and receive-FPS labels applied
and verified on the real Jetson.

- Deployed application revision: `f4e052dd5a50ca31e9ce4f6f6f88db8fc2289fea`.
- Repository: `2026-BTS-UNI-Car/unist_orachard_dashboard`, `main`.
- Application: `/home/unist/workspace/unist_orachard_dashboard`.
- Private rollback checkpoint: `/home/unist/workspace/ubts-dashboard-backups/20260928-172315-f4e052dd5a50`.
- Replaced exactly six application services: dashboard, camera/LiDAR/IMU/GPS display bridges, and recorder.
- All seven other running containers kept their IDs, including sensor drivers, the ESP motor owner, occupancy renderer and Portainer.
- Both settings hashes (`.env` and camera settings) are unchanged; all 32 archives remain present.
- AI remains stopped with restart policy `no`; its container and model data remain available for rollback.
- ESP is disarmed, both wheel requests are zero, and the recorder is idle. No real arm, motion or recording request was sent.

Camera, LiDAR and IMU now keep one ROS executor for each callback loop instead of
registering/removing the node on every message. Equivalent output dimensions and
quality share one JPEG cache entry. Camera pacing uses absolute deadlines to avoid
accumulating scheduler delays while retaining byte-rate limits and one outstanding
ACK. A missed interval is discarded instead of producing a catch-up burst. The
main FPS readout reports browser reception; the ROS source rate remains separate.
The existing real-camera 15 FPS cap, hardware configuration and raw recorder source
are unchanged. Simulation mapping, autonomy and recording adaptations are not installed here.

All **13 deployment verification checks passed**, including fresh camera/LiDAR/IMU
samples and a GPS bridge heartbeat. The real GNSS receiver is still disconnected;
GPS remains `no_data` rather than receiving a fabricated fix.

The Jetson ARM ROS Iron candidate ran 126 tests: 125 passed and one opt-in recording
integration test skipped (19.759 seconds). The local isolated ROS Jazzy run passed
the same suite (21.171 seconds). Node control/GPS checks, JavaScript syntax and
Compose validation passed. [GitHub smoke for this application revision](https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/actions/runs/36397001306)
also passed, including image builds and the isolated four-sensor recording fixture.

A passive browser visit verified all five routes, camera/LiDAR/IMU data, the new
receive-FPS label, no AI/simulation panels, zero JavaScript errors and zero write
requests. Separate read-only upstream connections confirmed `TCP_NODELAY=1` for
all four sensor WebSockets. The board still publishes approximately 15 FPS at
1280×720. Its camera scene remains dark; bright-scene quality is unverified.

Two independent 30-second Auto-profile observations used a private SSH forward:

| Observation | Before | After |
| --- | ---: | ---: |
| Browser observation duration | 32.880 s | 32.512 s |
| Total received frames | 123 | 125 |
| Final 15-second window | 5.60 FPS | 6.20 FPS |
| Final image size | 640×360 | 640×360 |
| Last reported ACK | 156.34 ms | 134.77 ms |
| Camera bridge CPU, percent of one core | 16.43% | 12.30% |
| LiDAR bridge CPU, percent of one core | 19.51% | 21.27% |
| IMU bridge CPU, percent of one core | 6.14% | 5.94% |

CPU observations lasted approximately 35 seconds and included client startup.
The remote link, adaptive profiles and startup windows differ. These observations
confirm operation; they do not establish a controlled FPS or CPU improvement.
Both sessions had zero JavaScript errors/write requests and preserved all archives.
See [stream policy and earlier measurements](docs/stream_latency.md) and
[sanitized current verification](docs/jetson_executor_pacing_validation.json).

Rollback uses this exact application helper and private checkpoint:

```bash
python3 /home/unist/workspace/ubts-dashboard-releases/f4e052dd5a50ca31e9ce4f6f6f88db8fc2289fea/scripts/deploy_raw_dashboard.py rollback /home/unist/workspace/ubts-dashboard-backups/20260928-172315-f4e052dd5a50
```

The backup contains source, private resolved configuration and immutable rollback
image IDs. Keep it private. The helper preserves older four-service checkpoint
scope, settings and runtime data. Rollback was available automatically on apply
failure; it was not exercised after this successful deployment.

Previous checkpoints remain available:

| Change | Application revision | Private backup suffix |
| --- | --- | --- |
| TCP_NODELAY | `6e2a5ad56d67347adfc94f1aa1059ffc303f7bd7` | `20260928-165048-6e2a5ad56d67` |
| Adaptive ACK budget | `3d63f167f5f6b88b7355726a2790d9380f409b71` | `20260928-160828-3d63f167f5f6` |
| AI removal, four services | `35c67365249651995e71609ba4fa799abbb98fd4` | `20260928-022306-35c673652496` |

Commands used for local validation and deployment (ROS tests run in an isolated
candidate image without hardware devices or network):

```bash
python3 -m unittest discover -s tests
node tests/test_rover_browser.js
node tests/test_gps_map.mjs
node --check static/app.js

docker compose config --quiet
python3 /home/unist/workspace/ubts-dashboard-releases/f4e052dd5a50ca31e9ce4f6f6f88db8fc2289fea/scripts/deploy_raw_dashboard.py prepare /home/unist/workspace/ubts-dashboard-releases/f4e052dd5a50ca31e9ce4f6f6f88db8fc2289fea
python3 /home/unist/workspace/ubts-dashboard-releases/f4e052dd5a50ca31e9ce4f6f6f88db8fc2289fea/scripts/deploy_raw_dashboard.py apply /home/unist/workspace/ubts-dashboard-backups/20260928-172315-f4e052dd5a50
```

## 2026-09-30 IMU 배포 준비 중단 체크포인트

공분산·quaternion 수정 소스 `a615eb2`는 **실차에 적용하지 않았다**. ARM 후보 driver7개·recorder20개 검사는 통과했지만 적용 전3초 ROS probe에서 드라이버 이름이 아직 확인되지 않고 표본1개만 수신되어 준비가 중단됐다. 최초 원인 미기록 실패도 별도로 보존했다. 기존 소스·두 서비스·설정·32개 기록·다른14개 컨테이너는 후속 읽기 검사에서 보존을 확인했다.

검사 도구는 최대8초 탐색 후 한 번의3초 측정 구간을 사용하도록 보강했고 Windows/Linux37개씩 통과했다. 센서 지연·자세·공분산 기준은 유지한다. 재시도는 SSH `websocket: bad handshake`로 원격 실행 전에 실패했으므로 새 도구 staging과 준비/배포가 남았다. 원격 연결이 끊긴 뒤의 현재 장치 상태를 재확인한 것은 아니다. [세부 체크포인트](docs/imu_prepare_hold.json)

재개 시 원래 SSH 경로로 접속하여 최신 상태/원본 hash를 다시 검사하고 새 체크포인트에서 준비한다. 실제 적용은 IMU driver와 recorder만 대상으로 하며 recorder의 메모리상 보정 기준이 초기화될 수 있다. 모터·조종·실차 기록·강제 보정은 수행하지 않는다.

## 2026-09-30 IMU 규약 수정 적용 확인

위 준비 중단은 과거 이력이다. `a615eb2387f866e806ba508695da03bc6b88daa8`의 세 파일을 실제 IMU driver/recorder에 적용했고, 재시작 없이 수행한 최종 검증의25개 항목을 모두 통과했다. ARM 후보 driver7개·recorder20개, 검증 도구 Windows/Linux43개씩 통과했다. 기존32개 기록·설정·Compose·다른14개 컨테이너를 보존했다. 초기 적용 검사와 rollback guard는 Docker의 설정 직렬화 차이 때문에 거절됐으며, 실제 mount/환경/장치/이미지는 확인한 뒤 좁은 비교 수정으로 재검증했다. 최초 실패 자료는 지우지 않았다. [검증 결과](docs/jetson_imu_contract_deployment.json)

새 측정의 covariance0은 불확실도 미확정이며 센서 장착/좌표계 보정을 입증하지 않는다. 기존90도 소프트웨어 yaw 회전·시작 자세/gyro 보정은 이번 규약 수정과 별개다. 사용자가 이후 제거를 요청해 원본 relay 방식으로 전환 작업 중이다. USB GPS 연결도 별도 작업이다. GitHub Actions 한도 소진에 따라 이후 로컬/ARM 검사 근거를 사용한다.
