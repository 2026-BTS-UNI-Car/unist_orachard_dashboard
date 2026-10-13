# Future deployment helper V2 — source only, not used by the active release

Published from the reviewed private candidate after the guard/history-notice overlay installed successfully. This helper was not substituted into that running or completed installer. The original eight mocked regression tests and additional real Linux Docker function checks are described below. Complete installer fault injection and restored native-service health remain unverified before a future install uses V2.

This folder contains only the two reviewed operational fixes for the native overlay installer. It does not change the currently running installer, the local release folder, any remote release, Docker containers, runtime source, physical outputs or original recordings.

Base helper SHA256: `51900677ed5bc877e2a1c59a4e37462d4c2b2e8e090cb3e4b4db76b16bb8b501`. The base source is `release-native-guard-notice-20261013/install_native_overlay.py`. Its bytes were checked again and remained unchanged after this work.

## Fix 1: effective Docker API timeout

The helper's overridden HTTP connect creates an AF_UNIX socket itself. HTTPConnection(timeout=60) alone did not apply to that replacement socket. V2 calls `self.sock.settimeout(self.timeout)` **before** connecting. API create/start/update/list calls therefore use the configured 60-second socket timeout. This is a socket-operation timeout, not a hard total installer deadline.

## Fix 2: complete rollback coverage and exact original state

Rollback tracking now begins before the first restart-policy mutation. Failures between update, stop and rename no longer skip restoration because retained_name is still unset.

The restore routine:

- Resolves the original by its immutable container ID and verifies its original image and full Config.
- Restores the original name when necessary.
- Restores the exact original RestartPolicy, including MaximumRetryCount, using Docker's update API. It does not replace it with unless-stopped.
- Restores the original running/stopped state and verifies ID, name, image, Config and full HostConfig afterward.
- Stops and renames only a proven replacement with this release label, original-ID binding and expected replacement image.
- Handles create succeeding while its reply is lost by finding and verifying the named replacement.
- Saves a `.inspect.private.json` evidence file and retains failed replacement containers; it never removes them.
- Refuses unknown occupants rather than deleting or stopping another operator's container. A failed name lookup is checked against the daemon list before treating the name as free.

The successful-install manual rollback metadata also retains the original policy and running state; standard on-failure retry counts are included in its command.

No automatic installer retry, lock removal or stale-socket deletion was added. The original per-release flock and one-attempt guard remain. This helper does not introduce a global lock across different release directories; ROOT must still own deployments exclusively.

## Tests

**V2: 8 tests passed, zero skips. V1: all four targeted regression tests failed as expected.** The same executable installer is evaluated with fake Docker CLI, HTTP API and UDS, never actual Docker or SSH.

Covered cases:

1. Restart update succeeds, original stop fails.
2. Original stop succeeds, rename fails.
3. Original rename succeeds, replacement create fails.
4. Docker API socket has timeout before connect.
5. Replacement start fails after creating/running it; replacement and inspect evidence survive.
6. Create completes but its response is lost; only the proven replacement is recovered.
7. Failure before mutation leaves the original untouched and does not invoke rollback.
8. An originally stopped container is not started by the restore routine.

The fixture deliberately uses `on-failure:7`, making a hardcoded unless-stopped restoration fail. It asserts the original Config and entire HostConfig remain identical. Windows-specific fcntl and AF_UNIX absence are stubbed only in tests. No production checks were disabled to pass the tests.

`TEST_RESULT.json`, `BASELINE_COMPARISON.json` and `logs/` hold the results. `fake-runs/` and `baseline-v1-readonly-copy/fake-runs/` retain the individual mocked install/rollback evidence and lock files; they contain no real credentials or devices.

## Additional real Linux Docker function checks

On October 13, eight additional checks passed against Linux amd64 Docker Desktop 29.5.3. The exact `restore_original` routine and Docker API executed against disposable actual containers. Checks covered restart-policy-only mutation, stopped-before-rename, renamed-before-create, started replacement, lost create reply, an originally stopped container, refusal to touch an unknown occupant, and an actual Unix-socket timeout (0.15075 seconds for a 0.15-second test timeout). The original ID/name/image/full Config/HostConfig and running state, including `on-failure:7`, were compared. Test resources were removed and the fourteen unrelated container IDs/configurations/states were unchanged.

Source SHA256: `cc0537ad7eab426741023d5aeb823585c65b4d2b526580e4b8427bae3aa11acc`. Sanitized evidence is `D:/Develop/UBTS/codex-resume-20261013/installer-docker-qualification/REAL_DOCKER_FUNCTION_RESULT.json` and its `HANDOFF.md`; the result SHA256 is `bdc6710fd9645d551846b7e531dc26e8c69b072b2678386d35efe7147a8887e6`. This was **function-level qualification**, not the complete installer: official Linux Docker CLI was unavailable, so inspect/run seams used real daemon REST operations. Failure boundaries were prepared as actual state transitions instead of injecting failures through every top-level CLI operation. No ARM, Jetson, build or native UDS health check was performed.

## Limits and next step

The real function checks do not qualify the entire installer or ARM/native-service restoration. V2 has not been deployed. Exact runtime restoration does not itself prove the restored native UDS has returned healthy; ROOT's normal post-operation service observation remains necessary. Restoration can fail if Docker is unreachable, the original is missing, or an unowned container occupies the name; those failures remain explicit and do not authorize touching unknown resources.

The active remote installer was not contacted because this task prohibited SSH. The local current release contained no fetched install-result artifact during review, so no independent remote phase/status claim is made here. ROOT owns that monitor.

ROOT should qualify the complete installer in a disposable Linux Docker environment with its real CLI and injected top-level failures, then verify restored service health before using it for another deployment. Do not overwrite the completed release script or assume these fixes apply to its earlier process.
