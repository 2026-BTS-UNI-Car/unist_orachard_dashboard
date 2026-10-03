# Field mapping candidate and PWM observations — 2026-10-04

All seven original sensor streams were replayed offline with a frozen candidate (`3e0ad9f`).
The source SHA/counts were verified. No ROS replay, motor command, encoder,
PWM odometry, camera localization or simulator truth was used. The deployed
dashboard still uses the existing display mapper; this candidate is not installed.

## Raw mapping benchmark

| Archive suffix (20261003 UTC label) | Scene | Accepted/all scans | Separate segments | Segments with conditional GNSS alignment | All calls p95 (ms) | Calls >100ms | Limited rasters |
|---|---|---:|---:|---:|---:|---:|---:|
| `072238` | road | 7701/7799 | 3 | 3 | 46.29 | 2 | 2 |
| `072329` | road | 906/913 | 1 | 1 | 24.60 | 0 | 0 |
| `072530` | road | 5205/5243 | 3 | 3 | 38.83 | 2 | 0 |
| `073551` | road | 7875/8108 | 12 | 6 | 52.81 | 23 | 0 |
| `080351` | orchard | 1801/2459 | 61 | 0 | 63.53 | 10 | 0 |
| `080909` | orchard | 1663/2093 | 39 | 0 | 56.85 | 3 | 0 |
| `081934` | road | 5251/5432 | 9 | 3 | 47.51 | 7 | 1 |

Accepted registration measures algorithm continuity, not trajectory accuracy.
Times include rejected calls and were measured on the local PC, not on Jetson.
Segment gaps are unknown motion and remain separate. GNSS uses independent
receiver epochs and reported horizontal accuracy, with accuracy/unknown antenna
offset floors. Conditional planar alignment and soft position factors are diagnostic;
heading calibration, calibrated covariance and optimized yaw are not supplied.
No orchard segment met the GNSS alignment acceptance conditions.

The current bounded raster clipped three long-road segment maps. Raising a PNG
limit does not solve coordinate growth or global corrections. Retained local submaps,
tiled rendering and map rebuild after pose correction remain required.

An optional dense-point fallback (`4d4b6e1`) on orchard080351 accepted
1961/2459 scans in 48 segments, versus1801/2459 in61segments.
All-call p95 rose to 98.28ms and
102 calls exceeded100ms. It remains off by default and
has not completed all road/other-orchard holdouts or geometric accuracy validation.
Point-to-line support diagnostics identify insufficient stable overlap as a main
failure mode. Branches, grass, changing scan plane and dynamic objects are possible
contributors, not proven individually from this counter alone.

Additional offline checks of the same frozen fallback finished on the92second road
recording072329 and the second orchard080909. The road stayed906/913 accepted
in1segment (p95≈33.02ms;0 calls>100ms). The second orchard improved from
1663/2093 accepted in39segments to1816/2093 in23segments, but p95≈93.82ms
and96 calls>100ms. Continuity improved without proving accuracy. Four remaining
road holdouts, the physical mounting/deskew assumptions and geometric validation
still prevent runtime adoption. Concurrent local-PC timing is not a controlled
Jetson performance comparison.

## Requested/MCU-reported PWM versus raw IMU

The analyzer requires connected/fresh bridge status, exact requested/reported pin
agreement, unchanged output for at least2seconds and inter-report gaps<=0.85seconds.
It excludes the first1second of each stable interval. D8/D5 and D3/D0 report integer
0..255 logic PWM; the report is not a measurement of voltage or wheel speed.
The MCU report's own age is not verified by these archives. All three input files
(metadata, motor log and complete sensor stream) are SHA-checked independently.

Each cell below is **interval count / median of absolute interval median sensor-Z
gyro rate (deg/s)**. Sensor axes are not calibrated to the body/LiDAR, so these values
are not certified body yaw rates or actuator gains.

| Archive suffix | Equal forward | Different forward PWM | Counter-rotation |
|---|---:|---:|---:|
| `072238` | 58 / 0.87 | 0 / - | 0 / - |
| `072329` | 7 / 0.78 | 3 / 2.69 | 0 / - |
| `072530` | 26 / 0.56 | 28 / 1.29 | 0 / - |
| `073551` | 69 / 1.01 | 0 / - | 0 / - |
| `080351` | 12 / 1.06 | 0 / - | 3 / 25.40 |
| `080909` | 18 / 0.84 | 4 / 3.58 | 0 / - |
| `081934` | 48 / 1.01 | 0 / - | 0 / - |

Equal forward output has nonzero sensor-Z rotation in every recording's group
median. Straight-line perfect-wheel simulation is therefore an unsupported default.
The three counter-rotation intervals occur in only one orchard recording. They
cannot identify a surface-independent breakaway threshold, braking distance,
wheel radius/track, left/right motor gains or the full PWM response curve.
Forward and reverse behavior, turning slip and surface effects must stay distinct.

For orchard080351, the median interval p95 acceleration-norm deviation is
4.70m/s² for equal forward and3.14m/s² for counter-rotation; the20Hz IMU cannot
resolve high-frequency chassis vibration. This does not establish that pivoting
always vibrates more or less than straight driving. The user's reported strong
pivot vibration remains a physical constraint, with unmeasured high-frequency content.

## Resume work

1. Reduce orchard discontinuities with retained local geometry and explicit recovery;
   verify dynamic/branch/ground-plane rejection and unknown relative mount tilt.
2. Validate rolling-scan timestamps/deskew; never rotate the LiDAR plane using
   uncalibrated raw IMU orientation as a replacement for measured extrinsics.
3. Build retained submaps/tiled occupancy with uncertain gaps kept separate and
   rebuild retained scans after global position correction. Validate against repeated
   static geometry, unique GNSS epochs and independent road/orchard holdouts.
4. Evaluate PWM response only where localization and source-time alignment are
   independently supported. Complete stopping/braking validation before real autonomy.
5. Implement and measure native/adaptive video playback under slow-link throttling;
   use the actual compressed-camera rate rather than mislabeled preview1080p.

Reproduction tools are in `tools/field_data/` in orchard-rover-clone. Use private
source directories and outputs; do not commit original camera/GPS paths or bags.
Machine-readable aggregate evidence: `field_raw_benchmark_20261004.json`.
