# Raw dashboard deployment checkpoint

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
