# Recording worker candidate: validation checkpoint

Recording timeline parsing and encoding share the dashboard process with control requests. This candidate moves four archive operations into one bounded worker process; the main server checks and streams encoded response files. The feature flag `DASHBOARD_ARCHIVE_PROCESS` remains OFF by default.

This branch is an experimental checkpoint, not a release or deployment approval. The installed Jetson dashboard remains unchanged by this branch.

## Evidence as of 2026-10-24 KST

- Frozen product source: `7ca3990a02802427814567702a582ef139bb33410539411a9c090b4c6de62f6c`; seven source/test/document files copied byte-for-byte into this separate worktree.
- Synthetic tests: 28 passed. Existing archive regressions: 15 passed. These checks do not establish actual throughput or vehicle safety.
- Protected POST/STOP/authentication handler AST, control API/core, and existing JS were unchanged in the source checks.
- Actual fixed baseline measurement completed with three authenticated zero FakeMotor stops, parse overlap, input preservation, and owned-process cleanup. Under-load HTTP STOP was about 1452 ms in this single local diagnostic.
- Actual isolated-worker measurement established a distinct child PID, parent ownership, worker parsing/building, no archive parsing in the main process, light telemetry HTTP 200, three zero FakeMotor stops, and cleanup. Under-load HTTP STOP was about 186 ms, but the LiDAR response missed the existing 10-second header deadline. **The candidate's overall result is FAILED.** This partial observation is not a passing performance comparison.
- The worker's parsing/building took about 2.101/5.747 seconds. The later JSON/encoding/compression/hash interval is not yet attributed. No deadline or resource cap was extended to pass the test.
- The first measurement-controller attempt also failed; its raw evidence is preserved. A separate controller repair passed the baseline. The original exact failure location is unknown.

## Remaining work

Measure the remaining response stages, improve the bounded encoding path if supported by those measurements, and repeat the fixed functional/cleanup and latency checks on a separately frozen candidate. Linux/ARM resource validation, large-record tests, camera/video/Range regressions, and a reversible Jetson installation remain pending. Do not merge or enable this feature as a validated deployment based on this checkpoint.

No physical motor output, control enable, firmware change, mount calibration, or raw recording modification was performed. Raw sensor payloads, original GNSS coordinates, private settings, and authentication material are excluded from this branch.
