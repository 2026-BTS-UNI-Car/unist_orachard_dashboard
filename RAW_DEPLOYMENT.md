# Raw dashboard deployment checkpoint

2026-09-28 KST: live Jetson deployment verified.

- Deployed application revision: `35c67365249651995e71609ba4fa799abbb98fd4`.
- Repository: `2026-BTS-UNI-Car/unist_orachard_dashboard`, `main`.
- Application: `/home/unist/workspace/unist_orachard_dashboard`.
- Private rollback checkpoint: `/home/unist/workspace/ubts-dashboard-backups/20260928-022306-35c673652496`.
- Replaced only dashboard, camera bridge, LiDAR bridge and recorder.
- AI worker stopped; restart policy `no`. Its existing container and model data are retained for recovery.
- Sensor drivers, ESP motor owner, GPS/IMU bridges, occupancy renderer, tunnel and other containers kept their IDs and remained running.
- `.env` and camera settings hashes unchanged; 32 existing archives retained; no real recording or motion command issued.

Validation: 101 tests on the Jetson ARM image (100 pass, 1 opt-in skip), isolated
four-sensor fixture test pass locally, GitHub smoke for deployed SHA successful.
Live browser received camera, LaserScan and IMU with zero JS errors and zero POST
requests. Live mode and ESP labels remained correct. No tree/fusion panels.
Two existing multi-sensor archives returned raw camera/LiDAR/IMU timeline segments
without analysis/overlay fields. Existing file contents were not rewritten.

Camera preview at inspection time was very dark. Direct ROS compressed frames were
also dark (mean about 1.24/255), so a bright scene was not validated; camera exposure,
rotation, resolution and hardware settings were preserved. GPS remains unconnected.

Rollback, if needed, uses the reviewed deployed script and the private checkpoint:

```bash
python3 /home/unist/workspace/ubts-dashboard-releases/35c67365249651995e71609ba4fa799abbb98fd4/scripts/deploy_raw_dashboard.py rollback /home/unist/workspace/ubts-dashboard-backups/20260928-022306-35c673652496
```

The backup contains source, resolved configuration and restorable image tags; it
must stay private. Dashboard image metadata was missing from the old Docker store;
its mounted root filesystem was exported/imported successfully without pausing the
running container. The restored image built and ran tests before application.
Rollback itself was not exercised on the live services after successful deployment.

The rover repository imported this deployed SHA via Git subtree and keeps its
simulation provider/Compose resources separate from this live application.
