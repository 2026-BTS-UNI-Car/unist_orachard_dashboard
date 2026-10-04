# Continuous recorded-sensor mapping checkpoint

The old mapper opened a new local frame after a one-second registration gap.
The browser replaced all displayed tiles on that frame transition. Keeping a
segment gallery did not prevent the main map from disappearing during replay.

The new archive mapper keeps one map frame, retains existing cells after a
failed observation, and attempts registration in that frame. A short motion
prior uses the receiver's recorded speed and native gravity-projected IMU gyro.
Its direction comes from previously observed scan motion, not GPS course or a
wheel command. The prior expires after 1.5 seconds without observed geometry;
its scan is not integrated as an occupied/free observation. Paths break across
such unobserved intervals. GNSS position factors are unique by receiver epoch
and validate motion after a sufficiently long independently observed baseline.

`새 지도 만들기` changes only the derived map pointer. Previous map epochs and
the prior pointer are retained for recovery. It pauses and rewinds playback.
Play requests processing only through the playback cursor; pause and backward
seek cannot request new future observations. Native cell events/checkpoints are
persisted while drawing. Restart reconstructs tracking from the already saved
raw prefix without discarding the displayed history. Source SHA and algorithm
fingerprints must match. Raw bags, camera frames, GPS, IMU and motor logs remain
unchanged. The existing API/session/origin checks protect both new actions.

Local lifecycle, future-cell isolation, source-integrity, shock recovery,
short-prior exclusion and browser cursor-flow tests pass. The two selected
archives and five other recordings completed offline evaluation with unique
GNSS epochs. All use one fixed frame. Low net rotation windows in 072530 show
median disagreement with native projected gyro falling from 2.084 to 0.971
degrees. Gyro is a consistency reference, not independent ground truth;
far-point thickness, mounting tilt and absolute accuracy need further validation.
This is not a validated real autonomous controller.

The first cursor-driven web release, `467e0a1`, was applied through SSH. Both
complete selected recordings passed CPU-only ARM validation: one frame, zero
intermediate resets, unchanged source hashes, pause and backward seek. An actual
080351 browser replay retained its cells and had no future frames.

The `608387c` candidate pipelines ordered localization in a spawned CPU process
with raster/storage work. At most two scans from the requested prefix are in
flight. Duplicate raster rays are eliminated without eliminating observed
cells. Serial and pipeline pose/path/tile payloads match exactly in regression
tests and a native 19-second real-source comparison. A roughly 20-second ARM
profile improved from 24.32 to 20.32 seconds. This is a processing measurement,
not a guarantee of end-to-end real-time performance on every scene/network.

The candidate also converts segmented maps on first Play, keeping the previous
derived pointer recoverable. New map remains available during causal processing.
Its 36 ARM tests pass; full pipeline validation includes 080351, 072530 and newly
requested 080909 before deployment. Follow the latest UBTS TODO checkpoint for
current deployed image and browser evidence. CI quota is exhausted; release
tests run directly on Jetson. No motor commands are sent by these tests.

Private raw trajectories, coordinates, sensor files and numerical comparisons
are kept outside this repository in the field-data audit workspace.
