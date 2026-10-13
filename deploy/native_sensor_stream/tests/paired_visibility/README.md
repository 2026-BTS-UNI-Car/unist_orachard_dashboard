# Paired visibility development regressions

These tests exercise a **default-OFF, unqualified** map bridge. It changes the supplemental overlap denominator to account for visibility in the two original scans. The original kernel correspondence, residual and information checks remain. This is a different acceptance policy, so passing these tests does not qualify it for deployment or vehicle motion.

`inputs/K.cdr` and `inputs/H.cdr` are complete original LaserScan messages from `20261003T072530Z_both`, scans 69/73. They are consumed development fixtures, not a fresh holdout. Their acquisition timestamps and every range remain unchanged. SHA256:

- K: `2c6703890cec20deddc8cbafc85c1710a9f7328e1c32f8a2b29e1118dad1c9a6`
- H: `b94a889f302b87431f3b70f6ae33479e3e3b4a4672fa5def07179ab831990ee8`

From the repository root, with NumPy/SciPy installed:

```text
python -B -m unittest discover -s deploy/native_sensor_stream/tests/paired_visibility -v
```

Set `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1` for comparable timing. The tests cover original-byte integrity, native timestamps, typed/default-OFF flags, numerical finiteness, contradictory geometry, lenient/degenerate competitors, limited synthetic aliases and cooperative Stop/deadline handling.

The production service has no CLI switch to enable this proposal. The source loader requires its manifest feature to remain false. Full-class consumed replay produced 218 actual transport publications versus 52 with the proposal OFF, but retained a late unmatched gap and a trajectory difference up to 2.13m/3.32 degrees versus a nontruth ICP proxy. The inherited raw-score/top-peak alias proposal generator does not establish global uniqueness. ARM, HTTP/browser, sustained live timing and physical accuracy are not qualified for this proposal.

Development evidence and narrow integration parent bytes are preserved in `D:/Develop/UBTS/codex-resume-20261013/paired-visibility-candidate-v2/`. The successfully installed Jetson image `745321ee` contains only the earlier WAIT cancellation guard and pose-history notice backend; it does not contain this proposal or its new UI warning.
