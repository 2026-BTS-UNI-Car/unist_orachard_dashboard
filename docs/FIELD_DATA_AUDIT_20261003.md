# October 3 field recording audit and mapping checkpoint

Sensor streams were read in full from all seven finalized MCAP archives. Selected topic counts match rosbag metadata and full bag SHA256 hashes were recorded. Originals remain unchanged. Camera scene review uses 10-second samples, not a claim that every video frame was visually reviewed.

Original size: 32,226,406,747 bytes. Sum of recording durations: 3214.45s. Raw scans: 32,047; raw IMU: 63,515; GPS publications: 6,207; compressed camera frames: 47,084.

| Archive | Scene evidence | Scan / raw IMU count | GPS hAcc median / max (m) | GNSS UTC minus host (approx s) |
|---|---|---:|---:|---:|
| `20261003T072238Z_both` | night road/paved plaza | 7799 / 15451 | 5.552 / 9.427 | 7880 |
| `20261003T072329Z_both` | day road/paved plaza | 913 / 1801 | 7.611 / 8.503 | 0 |
| `20261003T072530Z_both` | day pavement/road/traffic | 5243 / 10395 | 4.028 / 6.907 | 0 |
| `20261003T073551Z_both` | night road/pavement | 8108 / 16101 | 4.652 / 9.592 | 7880 |
| `20261003T080351Z_both` | orchard/grass/branches/people | 2459 / 4860 | 3.877 / 5.282 | 0 |
| `20261003T080909Z_both` | orchard/grass/slope/branches | 2093 / 4142 | 4.504 / 4.922 | 0 |
| `20261003T081934Z_both` | day road/sidewalk/traffic | 5432 / 10765 | 5.035 / 6.556 | 0 |

## Findings that change the implementation

- Two night-road archives have a stable approximately 7,880-second host/GNSS offset. Their filename/metadata time overlaps daytime records although they are different drives. Never merge records as duplicates by filename time alone. Keep archive-local sensor timing and preserve GNSS UTC separately. Receiver UTC validity metadata were not recorded, so the observed offset is not a hardware clock calibration.
- All observed GPS fixes are autonomous, with reported horizontal accuracy about 3–10m. There are two publications per GNSS epoch in many bags. Count unique epochs and avoid treating repeated packets as independent high-precision constraints. GPS positions cannot be copied directly into a 10cm grid pose.
- Raw scans contain 3,240 beams and run near 10Hz; raw IMU runs near 20Hz. The archived display timeline is about 2Hz LiDAR and decimated IMU. The existing live display mapper also integrates at 2Hz. Source-rate sensor estimation must be separated from low-bandwidth display delivery.
- In the fully reviewed 92s road record, LiDAR receipt-minus-source time has median about 0.100s and maximum about 0.809s, while source scan cadence remains near 0.1s. Receipt pairing alone can be wrong. The external archive renderer already has source-stamp alignment and adaptive rotational deskew; it still consumes the decimated timeline and does not fuse GPS.
- IMU sensor axes are not physically calibrated to the LiDAR/body axes. LiDAR/IMU X/Y are approximately colocated according to the user; Z and roll/pitch/yaw are not thereby equal. Existing arbitrary display orientation corrections must stay removed. Tilt compensation must not apply raw IMU roll/pitch as laser tilt without measured extrinsics.
- The orchard recordings show grass/low branches, slope, people and repeated trees. There is no basis for assuming a clean horizontal trunk-only scan, static scene or known tree count. Shock/tilt/degenerate intervals need explicit insertion guards and recovery, not permanent silent mapping failure.
- Equal MCU-reported PWM does not imply straight body motion. For example, equal-PWM samples in the final road recording have median absolute sensor gyro-Z about 2.69deg/s. Pivot response and acceleration vary substantially. These are sensor-frame observations with command transitions included, not fitted actuator gains or measured wheel speeds.
- PWM sidecars are 2Hz cached MCU status; reported sample age can be unknown. Requests and reported PWM differ during transitions. A PWM percentage must not be integrated as wheel odometry. Raw requested/applied/status topics were retained for further timing analysis.
- Raw compressed camera samples are 1280x720, while the dashboard preview timeline stores 640px JPEGs. The bag also contains sparse 1920x1080 raw images. Native high-quality playback must use the actual compressed stream rate/timestamps and preserve the original bag; a preview cannot be labeled as continuous original 1080p video.

## Changes prepared in this checkpoint

- Display-only LiDAR half turn for live and archive views, including SIM rays/ticks. Raw angles, exports and mapping coordinates are unchanged.
- Hardware archive map generation now writes bounded intermediate snapshots and an atomic time index, reports calculation/drawing progress, and selects only a snapshot at or before the requested playback time. Interrupted generations remain partial; source changes invalidate the index; old final-only maps require regeneration for historical playback.
- Hardware map playback follows the camera/sensor playhead; backward seek cancels a future decoder and preserves manual zoom/pan within the same map epoch. Regeneration preview is explicitly separate from playback.
- These map changes are display/history plumbing for the existing unqualified LiDAR/IMU renderer. GPS fusion, full-rate scan estimation, physical tilt calibration, loop closure, actuator fitting and real autonomous driving are not complete.

## Next work in priority order

1. Reproduce current live-map registration/tilt stops against each full source stream. Benchmark raw 10Hz scan and 20Hz IMU pairing with measured frame timing, relative mount uncertainty and rotational/translation deskew feasibility.
2. Build a shared offline/live localization path: robust LiDAR+gyro local odometry and covariance-aware, unique-epoch GNSS constraints. Separate local odom from map corrections and rebuild retained submaps when global correction changes. Reject ambiguous matches, branches/ground slices and shocks; do not fabricate free space from invalid returns.
3. Compare road and orchard holdouts. Sparse/shaky orchard returns must not become an unjustified last-row or clear-wall conclusion. Document no-encoder uncertainty before any autonomy output.
4. Fit command response only on stable valid sensor intervals, separately for forward/reverse, differential turns, pivots and surface. Investigate alignment bias, breakaway friction, vibration and braking. Do not fit transition/fault samples as steady gain.
5. Generate quality-preserving, time-indexed native MP4/adaptive renditions from compressed camera bags; measure first-frame, seek and buffering under a throttled link before replacing existing playback transport.

## Resume artifacts

- Private local inventory/schema/review/hash evidence: `D:\Develop\UBTS\orchard-realdata-audit\20261003-field`.
- Private full sensor extractions, original sidecar copies, hashes and quality tables remain on Jetson under `runtime/field_audit/20261003/extracted`. `.partial` folders never count as complete.
- Reproducible read-only extraction/quality tools: `tools/field_data/`. Run inside the ROS recorder environment for extraction; quality analysis requires NumPy. Camera/GPS paths/bags are not committed to GitHub.
- Deployment and real-data history playback validation are tracked separately in the checkpoint/TODO; unit-test success alone is not deployment evidence.
