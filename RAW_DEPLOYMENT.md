# Raw dashboard deployment checkpoint

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
