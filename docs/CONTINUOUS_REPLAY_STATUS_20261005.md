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
short-prior exclusion and browser cursor-flow tests pass. The two user-selected
archives and five other recordings are being evaluated offline with unique
GNSS epochs. New algorithm deployment and absolute map accuracy remain pending
that evaluation and actual browser replay checks. Do not label this checkpoint
as a complete mapping accuracy validation or a real autonomous controller.

The currently deployed web commit is `5138fc1`, with lossless cell compression
and a bounded three-second initial seek window. The new cursor-driven mapper
is not deployed at this checkpoint. CI quota is exhausted; release tests run
directly on the Jetson ARM image. No motor commands are sent by these tests.

Private raw trajectories, coordinates, sensor files and numerical comparisons
are kept outside this repository in the field-data audit workspace.
