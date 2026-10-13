# Codex takeover checkpoint — 2026-10-13

This checkpoint supplements `NATIVE_MAP_CONTINUITY_20261013.md`. The previous development study and its outputs are preserved; a candidate bridge has not been promoted to the physical rover.

## Verified source fix

`CommittedNativeMapAdapter.invalidate_gap()` previously preserved an in-flight registration for `native_original_pair_wait` even if its factory had faulted, requested Stop, or been detached. The sibling `native_fit_not_committed` label already required a healthy factory. Both labels now require the same healthy factory and an unset Stop intent. Healthy registration work remains eligible for preservation. Explicit map Stop, a factory fault, and the later raster/publication phases retain cancellation.

The original implementation fails three new regression methods. The candidate passes 66 tests covering bridge ownership, the optional scan-to-map path, incremental tiles, and original reanchor WAIT behavior. Run on Windows Python 3.13 with `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`:

```text
python -B -m unittest test_native_map_bridge test_native_map_scan_to_map test_native_map_incremental test_v21_reanchor_waits
Ran 66 tests in 100.724s — OK
```

All eight runtime hashes declared in `NATIVE_MAP_SOURCE.json` were checked after the patch. This fixes a cancellation guard; it does not establish geometric continuity, physical accuracy, an ARM deadline, or live installation. No motor output, arming, firmware change, or mount calibration was performed.

## Development study reconciliation

The prior C7 DEV3 producer completed all four real-data evaluations and its synthetic evaluation with exit status zero at 12:32:30 KST. Its scripts, logs, and results were copied into a separate snapshot with before/copy/after SHA256 verification. The old aggregate summary was stale; counts below were read from each latest `per_pair.jsonl`, rather than summed from overlapping report strata.

| Development set | Candidate | Reference agreements | Reference disagreements above the study threshold | Rejected |
| --- | --- | ---: | ---: | ---: |
| October 3, 1,782 pairs | C1b | 1,246 | 17 | 507 |
| October 3, 1,782 pairs | C1bG | 1,158 | 2 | 611 |
| October 3, 1,782 pairs | C6 | 1,212 | 5 | 551 |
| October 3, 1,782 pairs | C6b | 1,141 | 0 | 630 |
| October 10, 924 pairs | C1b | 679 | 0 | 238 |
| October 10, 924 pairs | C1bG | 666 | 0 | 251 |
| October 10, 924 pairs | C6 | 742 | 1 | 173 |
| October 10, 924 pairs | C6b | 733 | 1 | 182 |

The reference is an offline LiDAR ICP chain, not ground truth. Some disagreements do not agree with the second proxy either; none should be described as a measured physical positioning error without that qualification. Gray outcomes are omitted from this table. October 3 is now development data and cannot be reused as a fresh holdout.

On the old synthetic layouts C1bG accepts 404/3,339 pairs and C6b accepts 295/3,339, with no false accepts in the synthetic study. Both reject every pair in several useful orchard scenes, including the curved scene. Reduced disagreement alone therefore does not solve continuous mapping.

## Work that remains open

- Measure retained-map recovery through the actual classes and committed transport publication, including the longest stall. A prepared snapshot is not proof that it was published or seen by a browser.
- Preserve ownership and cancellation while investigating the fixed retained-keyframe recovery deadlock. Do not fabricate the missing transform by simply retaining a rejected adjacent chain.
- Harden the evaluation protocol before scoring fresh data: freeze harness, comparison rules, dependencies, data manifests, effective parameters, and verdict; refuse overwrite/concurrent evaluation; propagate failures and validate result coverage.
- Prepare September 10 inputs separately. These older records have a different IMU mounting and covariance entries of -1. They support an explicitly labeled offline geometry study, not unchanged strict live-factory qualification.
- Do not enable the default-OFF C6 scan-to-map bridge or physical autonomous motor output on the strength of these development results.
- Run source tests and actual ARM/deadline/Stop checks before a software-only deployment. Do not recreate the dashboard from the stale Compose image pin; consult the saved-inspect recreation procedure.

## Resumption locations

- New private checkpoint: `D:\Develop\UBTS\codex-resume-20261013\CURRENT_CHECKPOINT.json`.
- Preserved study: `study-snapshot-v3\SNAPSHOT.json` and `LATEST_DEVELOPMENT_COUNTS.json` under that checkpoint directory.
- Narrow patch evidence: `wait-guard-candidate\HANDOFF.json`, original source copies, minimal diff, baseline failure logs, and candidate pass logs.
- Source integration: isolated branch `codex/resume-wait-guard-20261013`, based on `6a3987d116a58de6237623b9bf94164b1b08cb25`.

The first SSH reconnect failed during the SSH banner exchange. A separate dashboard request returned Cloudflare HTTP 530 at 12:39 KST. These observations establish that the remote path was unavailable then, not that the board was powered off. No live installation is claimed.
