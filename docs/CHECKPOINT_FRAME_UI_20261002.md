# Live counter display cleanup — 2026-10-02 KST

The user requested removal of accumulating frame displays and only a verbal
assessment of autonomous driving feasibility. This release changes readout
text only. No autonomous logic, start permission or speed setting was changed.

- [x] Camera and rover camera cards hide the cumulative `frames` number.
- [x] Live map status hides the cumulative `scans` number, in hardware and SIM.
- [x] Internal frame sequence, decoder ACK, latest-frame delivery, FPS/freshness,
  map accumulation, raw recording and saved archive summaries remain intact.
- [x] Existing camera decode/ACK and map model/render suites pass. SIM's
  per-frame rendering and bounded readout regression also pass; JS syntax and
  diff checks pass.
- [x] Direct SSH Jetson web-only deployment, source `5bbf332fa39fa51de4f9e033827cb5339e411c2f`,
  image `sha256:1a3ad925629c36134217b0d9cfa0d9a3c5a4ff4f3c346cca58b620c9a0063522`. All 12 other containers and
  34 archive metadata files were preserved. Motors were
  disarmed and stationary; recorder idle. No drive or recorder start request.
- [x] SIM web-only deployment, source `205bb794ca0c2a4caace97de86b7562879212a8d`,
  image `sha256:d96907818b10aaf9d0d5792cbe3bd0b2bcecbb4738f123c273de1dd2a4082342`. Physics, Gazebo and RViz container IDs and
  10 archives were preserved. Autonomy inactive and speed
  preference remained 65%.
- [x] Headless Chrome read both served dashboards: no JS errors; live camera
  decoded successfully; cumulative camera/map counters absent from readouts.
  This check does not qualify camera frame rate or image quality.

Private frozen source manifests, deployment/rollback baselines and browser
evidence: `D:\Develop\UBTS\deploy-frame-ui-20261002`. The earlier live-map/speed
checkpoint is historical evidence and has not been rewritten.

Autonomous driving is technically possible without wheel encoders using 2D
LiDAR and raw IMU. The present hardware cannot simply enable it: encoderless
pose/body-speed feedback, physical PWM/braking characterization and unknown
orchard final-row/wall/branch/turn qualification remain incomplete. Main SIM
still uses legacy wheel-distance/IMU odometry; staged sensor-only candidates
are not an approved physical controller. Hardware autonomous START stays
disabled. Camera remains display/archive-only.
