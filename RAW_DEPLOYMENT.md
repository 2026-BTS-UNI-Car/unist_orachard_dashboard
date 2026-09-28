# Raw dashboard deployment checkpoint

2026-09-28 KST: stream responsiveness update applied and verified on the real Jetson.

- Deployed application revision: `4098bfd028b264261fcff86afa207373b6b7727b`.
- Repository: `2026-BTS-UNI-Car/unist_orachard_dashboard`, `main`.
- Application: `/home/unist/workspace/unist_orachard_dashboard`.
- Private rollback checkpoint: `/home/unist/workspace/ubts-dashboard-backups/20260928-155435-4098bfd028b2`.
- Replaced exactly six application services: dashboard, camera/LiDAR/IMU/GPS display bridges, and recorder.
- All seven other running containers kept their IDs, including the sensor drivers, ESP motor owner, occupancy renderer and Portainer.
- `.env` and camera settings hashes are unchanged. All 32 existing archives remain present.
- AI worker remains stopped with restart policy `no`; its container/model data remain available for rollback.
- ESP remains disarmed with left/right requests zero; recorder remains idle. No real arm, motion or recording request was sent.

All **13 deployment verification checks passed**, including fresh camera/LiDAR/IMU
samples and a fresh GPS bridge heartbeat. GPS is still `no_data`: a fabricated fix
is not used as a deployment condition. The real GNSS receiver is not connected yet.

The Jetson ARM ROS Iron image ran 115 tests: 114 passed, one explicitly opt-in
recording integration test skipped. Local ROS Jazzy ran the same suite successfully.
Node control/GPS browser tests passed. [GitHub smoke for the deployed revision](https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/actions/runs/36388783570)
also passed, including both image builds and the isolated four-sensor recording fixture.

A read-only browser check received camera, LiDAR and IMU on the actual dashboard,
visited five routes, and observed zero JavaScript errors and zero write requests.
The shortened driving captions are served by the live HTTP application. There are
no simulation/fusion panels in the real dashboard. Existing archives were not rewritten.

The real camera continues to publish approximately 15 fps at 1280?720: 45 source
frames over 3.00 seconds both before and after the update. Camera exposure, rotation,
resolution and hardware settings were preserved. The scene is still visually dark;
bright-scene image quality has not been established. See [stream behavior and measured limits](docs/stream_latency.md)
for the separate synthetic transport test and passive browser observations.

Rollback uses the reviewed deployed script and its private checkpoint:

```bash
python3 /home/unist/workspace/ubts-dashboard-releases/4098bfd028b264261fcff86afa207373b6b7727b/scripts/deploy_raw_dashboard.py rollback /home/unist/workspace/ubts-dashboard-backups/20260928-155435-4098bfd028b2
```

The backup contains source, private resolved configuration and immutable rollback
image IDs. It must stay private. The helper records the exact service set, preserves
older four-service checkpoint scope, and removes only newly introduced reviewed
release files when rolling back. Runtime data and unrelated files are preserved.
Rollback itself was not exercised on live services after this successful application.

The previous AI-removal application revision was `35c67365249651995e71609ba4fa799abbb98fd4`,
with private checkpoint `/home/unist/workspace/ubts-dashboard-backups/20260928-022306-35c673652496`.
That deployment changed four services; the newer helper keeps that original scope
when restoring that checkpoint.

The rover repository retains its separate simulation provider, maps, autonomy and
Compose resources. This real deployment does not install those simulation features.
