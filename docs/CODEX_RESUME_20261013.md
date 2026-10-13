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

Independent review reproduced an older first-reply race in `NativeAcceptedMapClient`: a healthy reply after display Stop could emit a stale waiting state and clear that warning. A revision/cancellation fence now precedes the first reply's notifications, and an obsolete returned page pin is released. Neutral warning text makes no claim that a stopped/offline producer is still accumulating; an explicit capacity reason without a positive count gets a generic notice. The real-client/presenter regression and eight independent Stop/Start, page-release and reason-only probes pass. Original failure evidence and the separate follow-up remain preserved; this browser change is also source-only.

A separate consumed-data audit held the original current-scan sampling and every numerical kernel gate fixed while comparing the 480-anchor target with a target using all supported original endpoints. Both accepted all 262 previously accepted pairs. The moving-step ratio to the nontruth ICP proxy stayed approximately 0.82, while the denser target cost more work. Target density alone therefore is not a demonstrated drift fix and was not promoted. Evidence: `codex-resume-20261013/adjacent-target-density/SUMMARY.json` and its pre-run source/data/version freeze.

A separately frozen bidirectional audit of the same 262 original pairs also does not resolve drift: forward moving-step ratio is 0.819, inverse reverse 0.847 and their diagnostic symmetric mean 0.835. Both directions keep the kernel gates, but the mean is not a separately qualified kernel pose. The cost roughly doubles. This study remains diagnostic only, with no factory or native-map-owner change (`adjacent-symmetry-audit/SUMMARY.json`). Retained original anchors are being studied next, with their older-anchor provenance kept separate from the currently admitted latest-map-keyframe bridge type.

## Further consumed-data evidence; no geometry deployment

Retaining an original anchor improves early proxy agreement but reduces recovery: 57/262 geometry proposals and a 22.779-second untracked source interval. It is not a replacement for the current proposal. The existing dashboard dense-point fallback also fails to remove the discrepancy: 244/262 pairs accepted; on 204 shared moving pairs, projected displacement ratio is 0.809 for the native plane model and 0.791 for the point model. Its coverage/residual policy differs from the native model. Both studies remain private, with their negative results preserved.

Metadata was checked against all 300 original CDR scans: corresponding headers, bag times, frame, 3,240 beams and projection angles match exactly. Reference-NPZ indices are fixture indices plus seven; the previously named first-gap 69/73 is NPZ scope, or fixture 62/66. Exact original headers/SHA identify the pair. Thirteen range-max representation differences are excluded by both actual fit policies. Sweep timing varies in the originals, but the reference does not use it for deskew. Native `.05–20m` and reference `.35–25m` effective ranges, spatial weighting, keyframes and solver policy differ. No physically measured 20% travel error is established by proxy comparison alone.

The first useful sampling result is a separately frozen original-endpoint spatial sampler. It retains the first actual endpoint per 5cm cell, then at most 480 original-order representatives, and computes original eight-neighbor normals from the unchanged complete original dense cloud. It changes sample selection, not original ranges, clock values or the numerical kernel gates; it is consumed development chosen after diagnosis, not a fresh holdout. Target-only sampling contributes little; current sampling accounts for most of the gain. On 211 shared accepted moving pairs, projected agreement to the nontruth reference improves from 0.812 to 0.911 and signed median step difference from -16mm to -7mm. Both-sided sampling accepts 251/262, introducing eleven rejections; worst position difference remains about 20cm and worst yaw becomes slightly worse. Five endpoint/provenance tests and sixteen limited synthetic controls/aliases pass. This is not actual map publication, continuity or physical qualification.

The spatial sampler remains private/default OFF. A coherent full-class replay needs a new correctly fingerprinted factory/package revision and explicit unadmitted-development harness; the existing pinned source loader must reject a modified package. Do not monkeypatch a function after admission while claiming the original package SHA, weaken production admission or forge an older-anchor bridge binding. Preserve the current installed `745321ee` image and all original recordings while evaluating the next candidate.

## Actual Docker function checks and separate long-run limits

The future V2 helper passed eight additional function-level checks against disposable actual Linux amd64 Docker containers. Exact original container ID/name/image/Config/full HostConfig/running state and `on-failure:7` were restored; an unknown occupant was untouched and the socket timeout took effect. Test resources were removed and the fourteen unrelated containers were unchanged. This used real daemon REST inspect/run seams because the official Linux CLI was unavailable. It does not qualify the full installer, top-level failure injection, ARM or restored native UDS health. The active release was not changed. Evidence: private `installer-docker-qualification/REAL_DOCKER_FUNCTION_RESULT.json`, with details in `tools/native_overlay_v2/README.md`.

Read-only reconciliation of the existing cand8 aggregate outputs found zero `capacity_latched_skips` across all six recordings, with retained cell counts 32,938–148,913, below 250,000. Their early persistent freezes are supported by WAIT and unmatched bridge/backoff evidence, rather than the cell limit. Exact per-run events/status were not available in that local copy, so this is not an event-by-event exclusion claim.

There are nevertheless separate long-run limitations: 250,000 retained historical cells with a terminal capacity latch until explicit New map; 512 complete-generation display tiles and matching browser limits; two concurrent 120-second page pins; and a 16MiB/4,096-row pose prefix. The last limitation reports history loss without stopping map commits. A future long-run design needs bounded active local tiles plus persistent global tiles, versioned viewport/delta paging and chunked hash-bound pose retention, rather than merely increasing every cap. This design is not implemented or qualified. The limit audit and original input/source hashes are preserved privately in `native-limit-audit/LIMIT_AUDIT.json`.

A fresh read-only SSH status at 19:24 KST confirmed installed native `745321ee` idle/unowned, authority false and motor output false. No observation, recording, control or deployment was started by that check.

## Spatial factory full-class result: rejected

The coherent spatial-factory study was frozen before counted replay and used an explicitly unadmitted private package/profile. The unchanged production loader rejects it; the typed sampler defaults OFF and rejects configuration mismatch/tamper. A separate review verified all 897 original rows and the v3 source closure. Construction-time v1/v2 path-binding and missing build-provenance dependencies were preserved, then corrected in a separate v3 harness. Explicit evaluation bindings disclose synthetic callback receipts/GIDs and virtual source clock; no build/image qualification is fabricated. Observer wrappers instrument the real transport publication fence, not the registration geometry, and the numerical reference was loaded only after all three runs finished.

This candidate is **negative and must not be promoted**. Factory target/current spatial sampling with an unchanged uniform retained-keyframe bridge produced 78 actual pointer publications versus 221 for the uniform baseline. Factory fits were 251 versus 262, with eleven new rejection WAITs. The candidate's last publication was at source offset 9.2928s and its terminal stall 20.6597s, compared with 26.1767s/3.7758s for the baseline. Zero session faults do not remedy either stall. On only the 78 common published stamps the position difference to the consumed nontruth proxy improves, but worst yaw becomes worse; comparing whole-run accuracy distributions would favor the candidate's much shorter coverage. Lower CPU/latency after the map freezes is not an optimization result.

Stop-after-encode prepared three generations, published two and fetched no version above two; the owned worker joined and publication was inhibited. All three replay sessions closed with no owned worker remaining, and sources/inputs were rehashed. This is not HTTP/browser, ARM or physical qualification. Evidence: private `spatial-factory-publication-study-v3/HANDOFF.json` (SHA256 `8ebf1250c25413c34a2eba143b380725297184cd6368152292c2c47ab629c4ff`) and separate `spatial-factory-independent-review/V3_FOLLOWUP_HANDOFF.json`.

The next separately frozen development study will distinguish factory sampling from retained-keyframe bridge sampling in a factorial comparison. It may change explicit sample selection, but must keep numerical kernel, visibility, competitors, ownership, Stop and original clocks intact. Any changed map code requires a new explicitly unadmitted closure; a recomputed production-format manifest is not evidence that production rejected it. No geometry candidate is installed or enabled.

## Follow-up diagnosis: no sampler rescue; recovery scheduling defect

The completed sampling factorial is also negative: uniform factory/uniform bridge publishes 221, uniform factory/spatial bridge 202, spatial factory/uniform bridge 78 and spatial/spatial 78. Matching the bridge sampler does not recover the spatial factory stall. New arms, Stop and source/output hashes are preserved in private `spatial-bridge-consistency-study/HANDOFF.json`. A carried-over factory-only scope label in the initial freeze was incorrect; counted execution had begun, so the immutable freeze was preserved and a separately hashed `SCOPE_ADDENDUM.json` discloses the independent factory/bridge options. An independent review verifies the loader rejection, exact changed source scope and sixteen limited consumed synthetic contrasts, without a promotion recommendation.

An exact-source trace of only the eleven newly rejected original pairs identifies `final_insufficient_overlap` across all 27 original hypotheses. At fixture93→94 uniform sampling gives 235/480 correspondences and spatial gives 180/480, below the **factory** overlap floor0.4 (192 required). This differs from the supplemental bridge coverage policy. Current supported normals remain 430, so deficient normal construction is not the failure. Spatial selection shifts current returns below5m from149 to58 and beyond10m from256 to348; 190 of195 distance-gate failures are beyond10m. Distance/line/normal masks overlap and must not be summed. The information and accepted-RMS gates occur later and are not reached; no claim they would pass is made. The unchanged original kernel, exact source clocks/CDR and pre-fit freeze are in private `spatial-rejection-gate-diagnosis-v2/`.

A read-only recovery audit finds all251 accepted spatial seals captured and processed, with no replacement, head supersession or cancellation. The real rejected93→94 transform remains unknown, and the next owned head95 was attempted but its bridge failed. There is also a concrete scheduling flaw: `_run` consumes a new `_head` before `_bridge_allowed` applies keyframe-only backoff; a skip discards that recovery opportunity without geometry. Reconstructing source policy against existing events/counters identifies thirteen such untested new heads. Individual attempted headers/gates were not directly logged, so that reconstruction is explicitly distinguished from source-proven behavior. Evidence: private `recovery-path-source-audit-v2/HANDOFF.json`.

The next private candidate changes scheduling only: one bounded opportunity for each genuine new owned chain head, while repeated/current retries remain throttled. It must preserve original source/provenance, every geometry gate, bounded storage/work and Stop. This does not promise that skipped geometry is matchable or that complete recovery follows. No sampling proposal is promoted and no guessed missing transform is accepted.


## Scheduling V4 result and existing Cartographer comparison

Mapping continuity is unfinished software work. Installed native remains745321ee (last readonly20:10KST idle/unowned/no authority/no motor output). Paired visibility and browser followups remain default-OFF/source-only, not installed. Do not infer current runtime from this observation.

Spatial sampling and factory/bridge consistency were negative. Owned-head scheduling V4 has now completed:stress78/78 shared poses/cells identical,13 bypass credits but no recovery; uniform218/217 gives no benefit. Nineteen owner regressions and unchanged-ingress duplicate checks pass; Stop fenced and all five workers joined. Do not promote/deploy this private UNADMITTED candidate. Preserve separate41.234848ms timeline-origin erratum and all original failed reports.

Next path reuses EXISTING Cartographer code/images, a DIFFERENT acceptance policy in a separate UNQUALIFIED display backend. Existing Oct3 fullfield run has1771 sparse optimized nodes (dense OFF), not per-scan live evidence. New original30 comparison consumed300 scans/597IMU unchanged CDR:299 finite dense outputs, startup0 missing, no tailgap,108 insertions,1.716s offline. Unknown physical extrinsic/per-return timing still prevent accuracy/autonomy claims. Do not fabricate native factory seals/certificates or count callbacks as map cell updates.

Active private work:isolated paced1x genuine Node::Run/native probability lifecycle using exact repaired node/DSO. Original CDR/header/dt remain untouched; use_pose_extrapolator=false is explicit presentation config for old source dates. Verify actual loaded object, receipt/delivery latency, Pause/Stop/EOF fences, bounds/cleanup with independent review before a separate DEFAULT-OFF local preview. No Jetson changes or production route replacement in this experiment.

Use root todo.md, docs/CODEX_RESUME_20261013.md and CURRENT_CHECKPOINT.json. No motor output/arming/firmware/mount calibration/original mutation. Stale Compose recreation prohibited; exact saved-inspect Docker API and no-retry installs only. Keep unowned NAS edits. Long-run250k-cell/512-tile/2-pin/16MiB/4096-row limits, persisted chunk history, overall geometry/live/ARM/browser qualification and physical trials remain open. Do not report that only field work remains.

## Counted online Cartographer V3: negative delivery result

Mapping remains unfinished. Installed native745321ee unchanged; readonly21:03KST idle/unowned/no authority/motor. No motor/arm/firmware/mount/original writes. Separate Cartographer UNQUALIFIED preview now executed genuineNodeRun:897 unchanged originalDDSrows and299 dense outputs covering tail299. Different policy, amd64 only, no C1b+G/native admission or physical qualification.

OnlineV3 is NEGATIVE for real-time map delivery:552 queue.Full rejections against512packets/16MiB, peak4,776,692B, finaldeliveredfixture260,45 knowncommits. Source-publication→commit median703ms/p95 2.41s/max5.16s. Do not call successful SLAM callbacks a successful delivery path. Stop,no-late-snapshot,worker/HTTP/relay cleanup andownedcontainerremoval passed; actualChrome map/retention observed, not pixel/live-latency qualification. V2 noexec pre-Nodefailure andfirsthostcancellation are preserved; V3 private tmpfs exec is explicitly bounded/nosuid/nodev, other source unchanged.

Active private followups:profileexactcapturedwire codec/store/log.flush stagecost; newboundedtransportcandidate after independentfreeze; last-COMMITTED-only localpalette repaint onoffline backend with stale/Stop/singleflight/hash guards. No candidate deployment. Queue256/8MiB would shrink the actual512/16MiB queue and is not applied. Unknown historical S2 source/SDK/params and physical acquisition/extrinsic remain explicit; do not erase dt or guesscalibration. Existing long-run tile/history and field/ARM/autonomy blockers remain open.

Continue root todo.md, CURRENT_CHECKPOINT.json and docs/CODEX_RESUME_20261013.md. Preserve failed studies,originalrecords,NASunownededits. Never staleCompose recreate; exactsavedinspect Docker API/no-retry releases only. Software work remains; do not report only hardware/field remains.

## V5 internal result and separate host readiness failure

Mapping/autonomy unfinished. Native installed745321ee unchanged; readonly21:03KST idle/unowned/no authority/motor. No physical/original/firmware/mount/arm writes. V3NEGATIVE552drops/finalfixture260 remains preserved. V5 internal pipeline passes:897 originalDDSrows,299dense through299,4774packets/no queue-ingest-store rejects,peak152/1,353,672B under512/16MiB. First delivery52distinctticks p50.509/p95.987/max1.139s; repeatedEOFage separate. All Stop/process/HTTP/container cleanup passed.

Root60s hostREADYwait failed beforelateproducerREADY; no hostACK andHTTPrequests0. Preserve PRE_READY_TIMEOUT. Never claim controlledV3→V5 browser speedup; NEWV6 couples source+hostwith exactCID/token/status proof beforepacing andboundedactual500ms GET/SHAobserver. No autosource retry,original mapper/config/times/caps/Stop unchanged. Separate different-policyCartographer UNQUALIFIED display, no nativeC1b+G orARM/physicaladmission.

OptionalSIMpalette fix publishedd59bd23/defaultOFF; ROOTportable96Node/zero skips/backend/parity/inventory pass. ActualChromeclosednegativeV3fixture260 server-disconnect recolor andstale retention observed; screenshot/JSONsaved,notpixelhash/live/ARMqualification. Existingdashboard/Jetson not changed. Longrunpersistenttiles/history, geometry/GNSSanchor, slowmultipleclients andphysicaltests remainopen. RootTODO/CURRENT/publicresume doc retainfailedstudies,NASunownededit and strict no-staleCompose/no-retry install rules.
