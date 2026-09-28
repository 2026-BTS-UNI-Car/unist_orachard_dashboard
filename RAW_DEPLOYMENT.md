# Raw dashboard deployment checkpoint

2026-09-28 KST: stream responsiveness update applied and verified on the real Jetson.

- Deployed application revision: `6e2a5ad56d67347adfc94f1aa1059ffc303f7bd7`.
- Repository: `2026-BTS-UNI-Car/unist_orachard_dashboard`, `main`.
- Application: `/home/unist/workspace/unist_orachard_dashboard`.
- Private rollback checkpoint: `/home/unist/workspace/ubts-dashboard-backups/20260928-165048-6e2a5ad56d67`.
- Replaced exactly six application services: dashboard, camera/LiDAR/IMU/GPS display bridges, and recorder.
- All seven other running containers kept their IDs, including the sensor drivers, ESP motor owner, occupancy renderer and Portainer.
- `.env` and camera settings hashes are unchanged. All 32 existing archives remain present.
- AI worker remains stopped with restart policy `no`; its container/model data remain available for rollback.
- ESP remains disarmed with left/right requests zero; recorder remains idle. No real arm, motion or recording request was sent.

All **13 deployment verification checks passed**, including fresh camera/LiDAR/IMU
samples and a fresh GPS bridge heartbeat. GPS is still `no_data`: a fabricated fix
is not used as a deployment condition. The real GNSS receiver is not connected yet.

The Jetson ARM ROS Iron image ran 120 tests: 119 passed, one explicitly opt-in
recording integration test skipped. Local ROS Jazzy ran the same suite successfully.
Node control/GPS browser tests passed. [GitHub smoke for the deployed revision](https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/actions/runs/36393840321)
also passed, including both image builds and the isolated four-sensor recording fixture.

A read-only browser check received camera, LiDAR and IMU on the actual dashboard,
visited five routes, and observed zero JavaScript errors and zero write requests.
The shortened driving captions are served by the live HTTP application. There are
no simulation/fusion panels in the real dashboard. Existing archives were not rewritten.

The real camera continues to publish approximately 15 fps at 1280×720: 45 source
frames over 3.00 seconds before the update and 46 over 3.001 seconds after the adaptive
follow-up. Camera exposure, rotation,
resolution and hardware settings were preserved. The scene is still visually dark;
bright-scene image quality has not been established. See [stream behavior and measured limits](docs/stream_latency.md)
for the separate synthetic transport test and passive browser observations.

The original Auto policy stayed at 160×90 / about 2 fps during a 78.54-second remote
observation. The deployed follow-up compares ACK latency with the current frame
period. In a fresh 77.56-second read-only session, Auto reached 640×360 / 6.53 fps
in the final 15-second window (115 ms last reported ACK). It also adapted downward
during the run; this is an observed improvement, not a fixed frame-rate guarantee.
There were zero JavaScript errors and zero write requests, and all 32 archives remained.

The latest follow-up enables `TCP_NODELAY` on both authenticated WebSocket proxy
connections, avoiding Nagle delays on JPEG packet tails and sensor acknowledgements.
Plain HTTP behavior, stream budgets and one-frame ACK flow remain unchanged.
Actual read-only connections through the deployed upstream connector upgraded all
four sensor WebSockets and confirmed `TCP_NODELAY=1`. Regression tests checked
both proxy sockets, missing-session rejection and binary JPEG decode/ACK exchange.
The Jetson synthetic loopback fixture ran about 29 fps with either option; it does
not establish an additional real-camera FPS gain from this change.

A fresh passive 78.86-second Auto session received 287 frames and finished at
640×360 / 5.60 fps over its final 15-second window, with 163 ms last ACK. LiDAR
displayed 450 points, IMU stayed live, and the original camera remained near
15 fps. The earlier adaptive-only session ended at 6.53 fps / 115 ms ACK;
these variable remote-link observations do not prove a further FPS gain.
The new session again had zero JavaScript errors and zero write requests.

Rollback uses the reviewed deployed script and its private checkpoint:

```bash
python3 /home/unist/workspace/ubts-dashboard-releases/6e2a5ad56d67347adfc94f1aa1059ffc303f7bd7/scripts/deploy_raw_dashboard.py rollback /home/unist/workspace/ubts-dashboard-backups/20260928-165048-6e2a5ad56d67
```

The backup contains source, private resolved configuration and immutable rollback
image IDs. It must stay private. The helper records the exact service set, preserves
older four-service checkpoint scope, and removes only newly introduced reviewed
release files when rolling back. Runtime data and unrelated files are preserved.
Rollback itself was not exercised on live services after this successful application.

The immediate previous application revision was `3d63f167f5f6b88b7355726a2790d9380f409b71`,
with its earlier checkpoint `/home/unist/workspace/ubts-dashboard-backups/20260928-160828-3d63f167f5f6`.

The previous AI-removal application revision was `35c67365249651995e71609ba4fa799abbb98fd4`,
with private checkpoint `/home/unist/workspace/ubts-dashboard-backups/20260928-022306-35c673652496`.
That deployment changed four services; the newer helper keeps that original scope
when restoring that checkpoint.

The rover repository retains its separate simulation provider, maps, autonomy and
Compose resources. This real deployment does not install those simulation features.

Commands to reproduce this release (the first two run inside the isolated
candidate ROS image with no hardware devices or network):

```bash
python3 -m unittest discover -s tests
python3 -m unittest discover -s tests -p test_ws_proxy_latency.py
python3 /home/unist/workspace/ubts-dashboard-releases/6e2a5ad56d67347adfc94f1aa1059ffc303f7bd7/scripts/deploy_raw_dashboard.py prepare /home/unist/workspace/ubts-dashboard-releases/6e2a5ad56d67347adfc94f1aa1059ffc303f7bd7
python3 /home/unist/workspace/ubts-dashboard-releases/6e2a5ad56d67347adfc94f1aa1059ffc303f7bd7/scripts/deploy_raw_dashboard.py apply /home/unist/workspace/ubts-dashboard-backups/20260928-165048-6e2a5ad56d67
```

Sanitized verification: [deployment, socket options and passive browser samples](docs/jetson_nodelay_validation.json).
