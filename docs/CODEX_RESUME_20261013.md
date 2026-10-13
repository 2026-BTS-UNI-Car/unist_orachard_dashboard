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

## Latest source integration and reopened remote path

After the Claude final report, the clean actual main was `00dfc665fc74a3363cd8ade0d388b923796b480d` and SIM main was `dc3056bd42406365f91813e1e231ea5a900e8634`. The WAIT fix was applied narrowly to that newer C1b+G tree. Its five bridge/WAIT/tile suites passed 73 tests. The earlier branch is a historical candidate, not a deployment bundle for the newer image.

The native pose ledger now explicitly reports retention failures after its existing 16MiB/4,096-row cap: a bounded missing count, the first missing commit's unchanged clocks and scan SHA, and incomplete-prefix status. Mapping continues, retained entries are not evicted, and no disk writes or additional scan retention were introduced. This makes history loss observable; it does not implement complete long-run history persistence. The 34 portable history/GNSS/capacity tests pass, including two genuine factory/map commits and transport status. Three additional admission tests verify the actual service digest, changed-module rejection, and immutable-parent rejection.

Both native-map and GNSS manifests and service digest bindings were refreshed. The original pinned parent lineage and unqualified/no-authority flags remain unchanged. A source-admission check exposed and corrected an intermediate manifest lineage error before deployment; the loader was not weakened.

SSH later reconnected successfully. Read-only inventory confirmed native image `sha256:30c7fd7105f73ddf1ea0ea0d9d30a66215a8be28d6146dca1aacb28872671349`, dashboard `aa83a85d2969`, and the native owner stopped/unowned with no runtime authority or motor output. Software-only ARM validation and installation of the small guard/history-notice overlay are a separate next step; no successful installation is asserted by this source checkpoint.

The experimental predecessor-scan rescue was rejected as a continuity improvement: a 30-second authentic original-CDR OFF/ON/OFF comparison had identical 262 accepted fits and 52 actual publications; ten extra rescue attempts yielded zero rescues. It adds work without extending this map and stays private/default-OFF. A bounded bundle of original accepted-chain scans is being investigated next. Map continuity remains open.

## Verified small overlay installation

Source `b0f420111e41f6652a666741d4a062a566850d1c` was overlaid onto the exact running `30c7fd71` image, without recreating the dashboard or sensor/recorder/motor containers. ARM tests passed in seven groups: 19 + 7 + 12 + 6 + 29 + 3 + 34 = **110**, with no failures. Five overlaid files and their admitted map/GNSS manifest bytes were compared inside the image. The installer rechecked the same parent/container/config and an idle or stopped unowned service immediately before replacement.

Installed native image: `sha256:745321eeffc8ccb661fc2e37b2eeb23e27cf7f10cf9e08aab1a94f208491a487`. Installation finished at 15:25:47 KST; normal restart returned idle/unowned, runtime authority false, motor output false, restart count zero. Existing container retained as `ubts-native-sensor-observation-retained-guard-history-notice-20261013-20261013T152541`. The release result and rollback steps are in `~/ubts-releases/native-guard-history-notice-20261013/INSTALL_RESULT.json`; independent fetched evidence is `codex-resume-20261013/INSTALL_VERIFIED.json`.

The source manifests describe the unqualified source bundle, not live deployment state. Source admission, tested software installation, physical accuracy and continuous mapping are separate claims. No observation/recording/control was enabled by this installer. Complete long-run pose retention and a dedicated browser warning are still open.

The bounded post-gap bundle study also failed to improve recovery: 20 original accepted-chain scans spanning 1.90 seconds, 15 actually published prior target scans, 24 target/pooling variants, zero accepted bridges under unchanged C1b+G. This is a negative result for those configurations, not proof that all possible recovery is unobservable. The next diagnostic compares the first stalled gap at the independent ICP-reference pose and measures near/far returns, native overlap/information gates, IMU tilt, and acquisition timing.

## First stalled-gap diagnosis and next source work

For original scans 69/73, the independently computed ICP-reference pose passes the native correspondence, RMS and information checks, but forward full-return overlap is about 0.636 against the required 0.70. Starting the native solver at that reference still fails the supplemental overlap check. This is a diagnostic reference, not ground truth or a candidate input. No returns lie below 1m here, so masking close vehicle reflections does not fix this gap.

A 3–8m geometry subset finds a near-reference pose without an oracle, while a near subset finds a contradictory rotation. Blind range masking therefore cannot be deployed as a recovery fix. Measured motion within the original ~0.099s sweeps warrants a distortion investigation, but the previously audited angle-compensated driver rearranges/duplicates samples: per-return acquisition order and `time_increment` are not qualified. Hypothetical deskew trials remain private/default-OFF. Visibility, stable geometric support and independent IMU cross-checks are the next development work; the original numerical acceptance, freshness and ownership gates were not relaxed.

`tools/native_overlay_v2/` contains a source-only future installer improvement. Eight fake Docker/UDS tests pass; the older helper fails four corresponding regressions. It applies the Unix-socket timeout and restores the exact original restart policy/running state even if replacement fails before rename. The current successful release is unchanged. Actual disposable Linux/Docker rollback qualification remains open.

## Paired visibility retained as a source-only development option

The reviewed V2 proposal was integrated narrowly into the current adapter/session, preserving the healthy WAIT guard and bounded history notice. `paired_visibility_bridge_enabled` is typed and defaults to false, requires C1b+G and excludes the scan-to-map policy. The production service has no CLI enable switch. Source admission binds the exact ten modules, requires the manifest feature false and preserves the immutable original parent. All three relevant Docker recipes explicitly copy the new dependencies. This source has **not** been installed; native image `745321ee` and dashboard `aa83a85d` remain the last verified installations.

The changed supplemental overlap denominator distinguishes original-polar visibility from unmatched returns; it is not a claim that the old acceptance policy stayed identical. In coherent full-class 29.95-second consumed replay, actual transport publications rise from 52 to 218, with seven bridges and zero factory faults. Repeated original stamps, published poses and cell counts are identical. The existing raw alias proposal screen still lacks global uniqueness proof. The last gap still stalls, and accumulated trajectory difference from the consumed nontruth ICP proxy reaches 2.13m/3.32 degrees. Those limitations prevent enabling or deploying the geometry proposal.

Source regressions cover original-byte integrity, source admission, finite solver outputs, contradictory geometry, limited aliases, Stop/ownership, WAIT, incremental map parity and history/GNSS handling. The small original-CDR test pair and portable tests are in `tests/paired_visibility/`. The independent review found a V1 nonfinite-contract regression; immutable V2 fixes it, with V1 failures preserved. The Stop-after-encode replay proof uses the exact frozen private V2 prototype, not an ARM/browser run of this source integration.

The browser observation-map panel now warns when its pose reconstruction ledger exceeds capacity. It retains that warning through display Stop and connection loss, and clears it only on explicit New map or a reported complete ledger. A Node regression covers those state transitions. This warning is source-only; it does not implement complete long-run history persistence.

A separate consumed-data audit held the original current-scan sampling and every numerical kernel gate fixed while comparing the 480-anchor target with a target using all supported original endpoints. Both accepted all 262 previously accepted pairs. The moving-step ratio to the nontruth ICP proxy stayed approximately 0.82, while the denser target cost more work. Target density alone therefore is not a demonstrated drift fix and was not promoted. Evidence: `codex-resume-20261013/adjacent-target-density/SUMMARY.json` and its pre-run source/data/version freeze.
