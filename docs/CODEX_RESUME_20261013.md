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

## Controlled V6: source tail delivered under actual HTTP load

Mapping/GPS/autonomy remain unfinished. Installed native745321ee unchanged; last read-only Jetson observation21:03KST idle/unowned/no authority/no motor. No original recording, physical calibration, firmware, arm or motor writes. V6 is a different-policy PRIVATE LOCAL consumed-development Cartographer preview, not native C1bG admission, ARM/live sensor qualification or physical accuracy.

One source/one host/no retries:897 original CDR/DDS rows exact, dense indices1..299; only startup0 absent.4550 packets processed with no queue/ingest/store rejects, peak203/1,691,786B under512/16MiB.348 actual HTTP GETs verified99 complete deliveries,98 known (68 active/30 retained). Final original scan299/sequence58/SHA d35aecf70d47b3a8e9e3826c7d0fb2cdf6e842d944e8061ecb8d76f4b3dbbca9. Source publication→commit first distinct sources p50.458/p90.849/max1.361s; separate host poll→known view p50.0721/p90.184/max.3494s. No cross-clock or repeated-EOF-as-new-latency claim. No Chrome rendering measurement in V6.

Stop retained exact snapshot; no late commits; all workers/listeners/relay/source processes closed and exact container absence proven. Preserve V3 negative552drops/tail260, unrun V4, V5 internal success/host timeout, and all draft failures. Workloads differ, so no strict V3→V6 speedup claim. Optional SIM palette fix published and actual stopped V3 fixture Chrome recolor observed; not installed on Jetson/dashboard.

Next software priorities: bounded persistent global submaps/tiles and viewport/chunked history for whole recordings (~39M cells cannot fit current whole-body limits); explicit GNSS quality/covariance/clock/frame/lever-arm factors (existing ROS bridge omits covariance); then whole10/03+10/10 and separate9/10 records, resource/Stop/multiple clients and physical accuracy. Current30s success does not close these. No stale Compose; saved-inspect Docker API and no-retry installs only. All frozen failures and NAS unowned edit remain preserved.

## Source-only pose history and unfinished persistent/GNSS work

V6 controlled30s source-tail/HTTP integrity verified; see prior checkpoint. Source-only SIM05ba353 adds DEFAULT-OFF tools/cartographer_pose_history (code520824bb),16 portable/no-skip +17private tests,299 exact original output rows,15 independent SQL-index fault probes. Reopen streams full raw/derived-index/clock audit;128-trajectories/8pins/64KiB replies. LOCAL tracking pertrajectory is not optimized-global map. This is not sensor callback/HTTP/global path/ARM integration or immediate physicalStop; no installed ledger replacement.

Persistent native viewport prototype preserves genuine final2.43M cells/158tiles but firstwrite47.13s is NOT realtime. V6 metadata-only50uniquebodies322MB/30s implies serious archivegrowth; tile-dedup and sustainedupstreamproducer capture/pagedcatalog integration unresolved. New performance-only revision separatesWSL/D-drive IO/validation/SQL phases; retain all negative evidence. GPS actual bridge drops covariance and ignoresantennaTF; core optimization code is reference-only, not assertedlinked identity. Historical receiver-time validity/association unqualified. NEWdefault-OFF translation-only weak-anchor pure candidate is being built, no core/SLAM run or GPSfusion qualified.

Lastsuccessful readonlyJetson21:03KST installed745321ee idle/unowned/noauthority/no motor. New23:15KST SSHreadfailedCloudflarebadhandshake; do not presentoldstatus as current. No motor/arm/firmware/mount/original changes. Fullfieldgeometry/longrunstorage/deltas/asyncowners/multiclient/Stop/ARM/live/physicaltests remainopen; keepautonomyOFF/HOLD. Source/history/frozenfailures andunownedNASedit preserved. No staleCompose,exactDockerAPI/no-retry install.

## Oct14 source primitives and remaining whole-field integration

SIM source-only defaultsOFF: posehistory05ba353 (16portable+17private+15independent), pureGNSS2ce4180 (35pure+6independent), persistentviewporta8180c1 V4r2 (12portablecontracts+23independent). Exactlocal299poseoutputs/native2.43Mcell finalbody preserved. V4r2 fixespointer/layout SQL,failedcandidatecap2 andbeginRAM/SQLgeneration coherence;Windowsfirstimport1.36s/pages<1ms/peak24MiB. No productionmapper/HTTP/globalpath/GPScore/ARM/live/physicalautonomy integration orfieldaccuracy claim. Same-OSlocal advisoryowner/accounting are notOSdiskquota/cross-OS/NAS/crashrecovery. OldfailedV1/V3/V4 andCASgrowth322MB/30s remain recorded.

NEWoptionalNumPyV5 private(defaultOFF/unchangedscalarfallback) is independently reviewed andgenuine-bytecosttestedbeforepromotion;745ownadversarialparityalone isnotallplatformbinary qualification. NEWdensecap/strictfieldEOF/actualDSObuild handlesfull5243scan bag beyondSIM4096/10000row+64MiB limits;publicSIM/nativebodycaps/timing/mathunchanged.15standalonecapcases passedbutpeerreproducedlateFinish truefooteraftertransientfalsefooterIO; preserveunexecutedbuild-v1 andnegative,candidate-v2/build-v2 mustfixbeforeactualcompile/replay. CoreGPSdropscovariance;oldmeasurementtime/fixedorientationunqualified;translationproposal cannot repairyaw/local SLAM.

LastsuccessfulJetsonnative745321ee10/13 21:03KST idle/unowned/noauthority/motor;23:15KST readonlySSHbadhandshake. Currentstatus/deploymentnotinferred. Motor/arm/firmware/mount/originalunchanged,autonomyOFF/HOLD. Next:incrementalproducer+versionedglobalcatalog/deltas/retention/chunkpath/nativeownerAPI/progress/Stop/multiclient,full10/03+10/10 then9/10,ARM/live/hardware. No staleCompose;exactDockerAPI/no-retry. PreserveunownedNASedit/frozenfailures/originals.

## Oct14 optional validation source and binary-lineage blocker

Optional NumPy validation is source-only SIM27c2a1e/defaultOFF. Root executed745 actual adversarial cases after fixing an initially inert test invocation; independent28 probes and exact2.43M-cell local benchmark passed. First Windows import.722s/pages<1ms/peak35.7MB are closed-storage observations, not live10Hz or whole-field/ARM qualification. Scalar fallback/geometry/probability bytes remain unchanged.

Cap-only V2 passed16 standalone plus2 recovered-IO probes, but actual build-v2 stopped before compilation because its assumed parent DSO was not installed. The exact helper was removed; no DSO/replay produced. Installed f0e8ae44 and cached36273fcf are under separate ELF/source/link review; the old567 qualified-overlay evidence must not be called installed-default proof. Separate strict fullfield EOF custody reader, derived-tile browser adapter and bounded producer/ACK/catalog prototype continue. GPS/core/global-path/whole-record/native-owner/live/physical integration remain open. Jetson current status is not inferred from the last successful21:03KST read;23:15SSHfailed.


# 2026-10-14 Codex 최신 체크포인트 — 전체 기록 연속 경로·실제 웹 좌표 렌더링 검증

UTC 2026-10-13T17:00:59.989970+00:00. **실제 대시보드 매핑 전체 해결·GPS 통합·실차 자율주행 완료 아님.** 실차 OFF/HOLD. 모터·조종·펌웨어·장착 보정·원본 변경 없음. 마지막 성공 Jetson 읽기는 10/13 21:03 KST native745321ee idle/unowned/authority false/motor false이고, 23:15 KST SSH는 Cloudflare bad handshake로 실패했다. 현재 보드 상태를 추정하지 않는다.

- [x] 전체 Oct3 `20261003T072530Z_both` 원본 입력: 17,671개 CDR 메시지/140,819,812B를 실제 EOF까지 읽고, LiDAR5,243/IMU10,395/GPS2,033의 원본 바이트·Header·bag시각·순서·개수·before/after SHA를 확인했다. String1,015개에는 Header를 만들어 넣지 않았다. 원본 GPS는 수집했지만 이 연구 매퍼의 core 입력으로 사용하지 않았다.
- [x] 별도 cap-only 실제 amd64 DSO `e6808077` 빌드: 정확한 부모 코드/객체/link/ELF 검사와 실제 로더·ABI·기본OFF/4096·명시5243·4MiB·실패IO 등10검사 통과. 원본 core/센서/clock/pose math/nativegridcaps 변경 없음. Jetson/ARM 배포 없음.
- [x] 전체 기록 매퍼 실행: 39.455초, sampled RSS539,436KiB, 정상 종료. 완전한 dense5235행은 원본scan8..5242 연속이며 중간·tail 누락0, 삽입node1771. 생성PB는 기존 전체 기록 PB와 바이트가 같다. 이 시간은 오프라인 처리 비용이며 실시간/느린 인터넷/물리 정확도 검증이 아니다.
- [x] 시작점 오해 원인 검증: 원본scan0..6은 첫IMU보다 먼저 기록됐다. 이전30초 fixture는 원본scan7..306이며 첫IMU는 같다. fixture0 누락은 원본scan7에 해당한다. **사전 예상scan1..5242를 요구한 원래 run은 실패 상태로 보존**하고, 기대값을 뒤에서 바꿔 원래 통과로 표시하지 않았다. 독립 감사가5235연속행·1771삽입·299동일pose·원본SHA를 재현했다.
- [x] 별도 postclosed 검사/지도 export: 새SLAM/센서재생 없이 이미 닫힌PB를 원래0.05m 해상도로 내보냈다. PGM39,411,987B와YAML은 기존 전체 지도 SHA와 정확히 같다. 기존30초299pose도 수치가 정확히 같다. 새 출력은 이미지 artifact이며 native확률 posterior/39Mcell transport 허용을 의미하지 않는다.
- [x] 원본 출력 cap 연구 source `tools/cartographer_dense_field`: 기본OFF/4096/4MiB 유지, 명시적fieldprofile만5243 등허용. Root WSL16검사/skip0. 공개 source이며 설치·원본·기존SIM CLI gate는 바꾸지 않는다. GitHub `e013378`.
- [x] 좌표 타일 client V2: Python9+Node27, 실제synthetic Worker1, 독립6Python/12Node그룹. 최대4미완료tile작업,128tile/2Mcell/4MiBrawcache, immutablecursor/snapshot/transformrevision/Stop/late-response/수동zoom유지. defaultOFF이며 originalpublisher manifest/certificate/globalpose를 위조하지 않는다.
- [x] Root 실제Chrome V2: 검증된 닫힌V6 지도에서54tile 렌더링/positive색상/동일source 재조회 후 수동확대 유지/Stop 후 지도 유지. 실제70GET(55bodyGET 전부200), 입력SHA 불변, listener/store 모두 종료. screenshot과JSON 보존. 원본30초 fixture의 retained display이며 현재실차/새pose/전체기록browser/slowinternet 검증은 아니다.
- [x] 실패도 보존: EOF-v1은 LiDAR전용ledger필드를 다른sensor에도 요구한 harness오류; v2는 Windows/WSL경로 차이로 container생성전 실패; v3 EOF 성공. 브라우저V1은 nativefetch receiver오류로 실패했고 V2에서 수정했다. 초기supplementary seam 및 독립cache-delay fixture오류도 수정전증거를 남겼다. 후보빌드-v2 부모DSO가정 실패와 기존V3/V5전송 실패도 유지한다.
- [ ] **최우선 실제 upstream 통합:** qualifiedad8 publisher는 여전히 전체catalog body를Node lock안에서 복사하고 cap오류를계속유지하며, RMWpublish뒤 receiverdurableACK없이wirecopy를 해제한다. 설치부모f0는 이publisher를 포함하지 않는다. 웹/저장만 고치면해결되지않는다. 정확한ad8조합을 pin한 새defaultOFF C++ incrementalcatalog/changedbody/ACK/DEFER/Stop기작과 비동기storage owner를 구현·독립검토 중이다. legacyprotocol/C1bG/C6OFF/nativebodycaps/센서clock/posemath는 유지한다.
- [ ] 한source writer/job, multi-client조회dedup, live우선순위/active-replay inhibition, boundedIO/queue/progress/Stop, receiverstore→derivedroute 실제 연결 및 wholefield sustained검증. published primitive들만으로 실제dashboard 매핑을 교체하지 않았다.
- [ ] 전체지도overview LOD/선택영역자동조절, finiteCAS/finishedmap/oldclientpins/보존정책/GC/powerfail, optimizedglobalrobotpath/원본sensorassociation/GPSclock·covariance·ENU기준/ARM/livebackend. 일반GPS5~10m를 정확한row위치/heading으로 쓰지 않는다.
- [ ] 전체10/03 도로·과수원/10/10현장 및 별도9/10차체·offset 조건 검증, 실제센서acquisition/mount확인, 수동저속feedback·제동·회전 시험,USBSSD백업/offlineGNSS시각/전압·전류/RTC 등현장 항목. consumeddevelopment를freshholdout으로 바꾸지 않는다.

재개: `CURRENT_CHECKPOINT.json`, `RESEARCH_PROGRESS_LATEST.json`, `cartographer-fullfield-dense-run-v1/HANDOFF.json`(원래expectation-negative), `cartographer-fullfield-postclosed-v1/HANDOFF.json`, `fullfield-dense-independent-review-v1/HANDOFF.json`, `viewport-browser-adapter-v2/HANDOFF.json`, `viewport-browser-independent-review-v2/HANDOFF.json`, `viewport-loopback-probe-v2/ROOT_CHROME_RESULT.json`. staleCompose 재생성 금지/exactDockerAPI/no-retry 설치 원칙. 원본·frozen실패·unownedNAS수정 보존.

---


# 2026-10-14 최신 체크포인트 — 저장 작업 공유·Stop·마감 시각 처리
UTC 2026-10-13T17:45:50.916988+00:00. **실차 매핑 교체·GPS 통합·자율주행 완료가 아니다.** 모터/조종/펌웨어/장착 보정/원본 변경 없음. 이번 단계는 로컬 source-only이며 Jetson 설치 없음. 최근 Jetson 읽기는 Cloudflare bad handshake로 실패했으므로 현재 보드 상태를 추정하지 않는다.

- [x] 비동기 저장/조회 owner V2: 단일 writer, source당 단일 job, 같은 job 중복 제거, 최대2source/4job/8MiBmetadata/20MiBbody, live 우선 phase scheduling, 상태·사유·메모리·진행 단계, 디스크 실패 시 명시 DEFER/retry, Stop/generation 후 기존 지도 유지. 실행 중 파일/SQL IO를 강제로 끊는 구현은 아니며 늦게 끝난 durable 결과는 저장돼도 화면에 공개되지 않는다.
- [x] 실제 발견한 V1 오류 보존 및 V2 수정: 마지막 SQL commit이 deadline 뒤에 반환되면 V1이 새 지도를 공개했다. V2는 최종 publication fence와 각 blocking return에서 deadline/owner를 다시 검사한다. 독립 검토 및 원본14+추가3=17 synthetic 검사 통과. 공개 패키지 검사17개/skip0/임시 리소스 종료 확인. 초기 공개 준비는 freeze JSON 필드 이름 오류로 파일 생성 전 실패했고, 증거를 남긴 뒤 수정했다.
- [x] 닫힌 V6 실제 native 데이터 연결 시험 **한 번**: 2,432,295cell/158tile/12,161,475B를 원본 row portions와 바이트 단위 비교. 원래 version108/18, bodyID54/55, source stamp, crop 및 SE3 유지. 두54tile 조회 lease가 저장 job1개를 공유했고 Stop 후 identity 유지, 모든 lease/thread/store 종료, 입력 SHA 불변. 관찰 job2.509초/샘플 resident8,729,175B/process peak34,529,280B. 로컬 비격리 비용이며 실시간 속도 향상·현장 정확도 주장이 아니다.
- [x] C++ incremental mechanism V1의 실제 source-contract 오류 두 개 보존: unknown -0.0 float bits 허용, 목록에서 사라진 trajectory clock high-water 손실. 별도 V2가 이를 수정하고 catalog sequence/native transform revision을 구분했다. V2 standalone13그룹 통과. 실제 SDK/Node/DSO 연결·인증 ACK는 아직 미검증이다.
- [ ] **가장 먼저:** exact ad8 실제 Node/SDK에 bounded changed-body copy/coherent catalog/보존 확인 ACK/DEFER/Stop 연결. Node/source lock 안에 disk/SQL/RMW를 넣지 않는다. legacy protocol/C1bG/C6OFF/native caps/source clock/pose math 유지. 아직 제안 seam이나 standalone model을 실제 producer 구현으로 표시하지 않는다.
- [ ] 실제 live/replay writer와 dashboard derived route 연결, 전체 현장 native 지도 transport/브라우저 지속 시험. 30초 닫힌 데이터 시험과 full-record pose/export 성공을 전체 경로 완료로 합치지 않는다.
- [ ] 전체 지도 overview LOD, manual zoom 보존, 유한 CAS/history 보존 정책/GC/재시작·전원 차단, optimized global robot path 및 GPS clock/covariance/ENU 기준. 일반 GPS5~10m는 과수열 위치나 heading의 정확한 기준으로 사용하지 않는다.
- [ ] Oct3 전 기록/Oct10 현장/별도 Sept10 차체·offset 검증, ARM/live/현장 수동 센서 feedback 시험, USBSSD 백업/offlineGNSS 시각/전류·전압/RTC 등 실제 장비 항목. 물리 자율주행 OFF/HOLD 유지.

재개: CURRENT_CHECKPOINT.json, RESEARCH_PROGRESS_LATEST.json, async-storage-viewport-owner-v2/HANDOFF.json, async-owner-independent-review-v2/HANDOFF.json, async-owner-closed-v6-probe-v2/HANDOFF.json, incremental-native-cpp-candidate-v2/HANDOFF.json 및 각 실패 증거. 실제 SDK adapter와 whole-field overview 작업은 계속 진행 중이다. stale Compose 재생성 금지, 설치는 exact Docker API/no-retry. unowned NAS 수정과 frozen 실패 보존.

---


# 2026-10-14 최신 체크포인트 — 실제 보드 재확인·SIM 선택 API
UTC 2026-10-13T18:20:31.982114+00:00. **전체 현장 센서→지도→대시보드 연결, GPS 통합, 실차 자율주행은 미완료.** 실차 OFF/HOLD. 모터/조종/펌웨어/장착 보정/원본 변경 없음. 이번 코드 변경은 SIM 선택 경로이며 보드 설치 없음.

- [x] Jetson 현재 읽기: 10/14 03:00 KST SSH 성공. native image745321ee 그대로, idle/unowned/runtimeauthority false/motorfalse. 03:02 대시보드 GET: LiDAR/camera/IMU live, GPS USBconnected/no_fix, roverdisarmed, recorderidle/activefalse/fresh, 디스크127,138,779,136B 여유. 과거 badhandshake 실패와 성공 상태를 각각 보존했다.
- [x] 현재 센서 카운터 관찰: 약3초 동안 IMU32.884Hz, camera bridge25.239fps(자체source_fps25.2). 카메라 모드는1080p30이나 실측 수신은25fps였다. LiDAR API에 scan_count가 없어 주파수를 만들지 않았다. 정지 상태의 짧은 bridge delivery 관찰이며 원본 취득/현장 녹화 프레임률 검증이 아니다.
- [x] SIM optional derived API: 이름 공간을 분리한 owner/service를 공개 소스에 연결. 기본OFF, provider가 명시적으로 소유 service를 주입해야 한다. GET status/open/page/body/release는 지도 job을 만들지 않고 입력 파일 경로를 받지 않는다. POST Stop은 기존 Origin/session 검사와 epoch/generation/serial 원자 비교로 오래된 화면이 새 세션을 멈추지 못하게 한다. 실제 생산자/factory/UI는 아직 연결되지 않았다.
- [x] V2 API 독립 오류 보존: malformedURL 예외, 비ASCII Content-Length 예외, 본문 읽기 전체 deadline 부재. V3는 ASCII1~4digit/singleLength/noTransferEncoding/4096B/절대3초 read1/엄격UTF8·중복key·비유한JSON을 검사한다. 기존17+신규9=26 검사 WSL 통과, 실제DashboardHandler+가짜provider의 loopbackHTTP 포함. 독립10경계검사 및 malformedURI 재검토 통과. 공개 패키지26/skip0/리소스종료도 확인했다. 소유한 공개 Python 파일 줄바꿈만 정리했고 AST 불변을 증명했다.
- [x] 닫힌 V6 저장 시험 독립검토:128output파일/SQLread-only/receipt2개/158tile/2,432,295cells/12,161,475B/기존시각·geometry·version108/18·bodyID54/55가 일치한다. job1개/client2개/Stop보존/정리 완료. 전체 필드 처리나 live 성능으로 확대해 해석하지 않는다.
- [ ] **최우선 실제 C++ 연결:** V1 actual Node/SDK source17standalone+22static 검사. 하지만 pendingbatch 덮어쓰기/종료 누락/nonce문자열 순서 문제가 남았다. V2는 보존 확인 전 batch 유지, rawcopy↔잠금밖SHA 분리, 실제DIbootstrap·상태조회·보존handle·종료를 작성/검토 중이다. 아직 ROS/SDK/DSO 소비자 빌드나 source certificate/실제receipt 공급자 검증은 없다. 기존constructor/CLI/defaultOFF/legacyprotocol/C1bG/C6OFF/native caps/sourceclocks/posemath 유지.
- [ ] 대시보드 optionalfactory/worker 종료 관리, 실제 producer→singlewriter→derivedview 연결, 전 현장 native 지도 transport와 브라우저 지속 시험. Source-only 성공을 실차 설치로 표시하지 않는다.
- [ ] whole-field display-only overviewLOD: 원본fullresolution CAS 유지, 최대positive/known·freecoverage 별도표시, max128tile/65536aggregatecells/8MiBcache, 조회는cache-only이고 준비worker1개를 공유. manualzoom/Stop/기존화면 유지와 boundeddeadline/DEFER를 구현 중이다.
- [ ] **추가 확인:** 카메라 raw취득/ROS발행/bridge수신/실제녹화 fps 구분 및30fps 미달 원인, 유한archive/historyGC·재시작·전원차단·oldleases, optimizedglobalpath/GPSmeasurementUTC·covariance·ENU, ARM/live/moving 검증. GPSno_fix를 유효위치로 바꾸지 않는다.
- [ ] Oct3 모든기록/Oct10현장/별도Sept10차체offset 평가, 현장센서feedback·30~60%수동제동/회전 시험, USBSSD/offlineGNSS시간/전류·전압/RTC 등 실제장비 항목. 기존development를 freshholdout으로 표시하지 않는다.

재개: CURRENT_CHECKPOINT.json, RESEARCH_PROGRESS_LATEST.json, DASHBOARD_READONLY_STATUS_20261014.json, SENSOR_CADENCE_READONLY_20261014.json, sim-derived-owner-route-v3/HANDOFF.json, sim-derived-route-independent-review-v3/HANDOFF.json, incremental-native-sdk-adapter-v1/HANDOFF.json 및 후속V2. 기본OFF선택코드/설치된7453를 구분한다. staleCompose 재생성 금지, exactDockerAPI/no-retry, frozen실패/unownedNAS 변경 보존.

---

## 2026-10-14 지도 표시·실제 SDK 연결 체크포인트 (2026-10-13T19:17:38.653017+00:00)

- [x] 공개 SIM 저장 관리자에 overview를 연결한 비공개 후보: 기존 수학·원본 CAS를 그대로 두고 namespaced owner/import만 연결했다. 실제 공개 클래스의 세대 변경·Stop·이전 화면 유지·계속 증가하는 snapshot 검사 11개 통과.
- [x] 실제 WSL DashboardHandler로 선택적 SIM overview HTTP 경로를 검사했다. 조회가 worker를 시작하지 않으며 기존 세션·Origin·Stop 요청 크기/시간 제한을 재사용한다. 4개 통과. 첫 HTTP 상태 조회의 빈 query 처리 실패와 독립 검토의 2239B 중첩 JSON 예외는 각각 보존했고 새 후보에서 수정했다. 전체 과수원/실차 검증으로 표시하지 않는다.
- [x] 닫힌 원본 V6의 2,432,295 셀/158 원본 타일을 13 표시 타일/38,240 aggregate 셀로 변환했다. scalar 소요 7.8228356초, 원본 import는 별도 2.5611482초. 원본 12,161,475B·version108/18·body54/55·시각/geometry는 불변이다. 처리 후 aggregate 본체는 보관하지 않아 사후 독립 본체 검증을 주장하지 않는다.
- [x] C++ 실제 전체 ROS 패키지 재빌드에 필요한 729파일/39,205,116B와 2527 dependency header pin을 읽기 전용으로 확보했다. 부모 ROS는 Jazzy/Noble이며 설치 부모 DSO f0는 dense-only, ad8 native publisher와 다르다. SDK source/header 전체 소비자를 새 object tree에서 빌드해야 한다.
- [x] C++ SDK V4의 제한된 할당 실패 재시도 5개/정적40개 검사 통과. 기존 실패한 allocator shim 로그를 보존했고 production은 V3와 동일하다. 추가 begin_capture/status/pump와 sequence-floor allocation 경계는 새 V5에서 검사 중이다.
- [x] 가속 V5의 검증된 package와 다른 같은 버전 NumPy 모듈을 수용하는 문제가 독립 검토에서 재현됐다. V5를 보존하고 V6에서 interpreter/package/spec/loader/module-object origin을 연결했다. V6 25개 검사 통과, 독립 검토와 단일 원본 데이터 비용 실험은 아직 남았다. 기본 가속 OFF, 원본 scalar/용량/시각/SE3 규칙은 유지한다.
- [ ] 즉시 다음: SDK V5 및 정확한 full-package build recipe 검토 후 한 번의 실제 SDK compile/DSO ABI OFF 검사를 실행한다. 아직 실제 ROS consumer compile은 하지 않았다.
- [ ] overview 후보 독립 검토 후 공개 SIM의 optional service/UI 연결, producer→single writer→viewport 실제 흐름·장시간 live/replay 처리·global 경로·GPS measurement UTC/covariance/ENU·ARM 검증을 완료한다.
- [ ] 유한 archive/history는 무한 보존이 아니며 소진될 수 있다. 현재 V6의 125 CAS 파일 21,901,830B는 모두 reachable이라 삭제 대상이 없다. 실제 read lease pin·원자적인 roots·receipt 보존·journaled GC가 필요하며 아직 구현하지 않았다. 원본/기록/미확인 작업을 지우지 않는다.

Jetson 신규 설치/조종/모터/펌웨어/장착 보정 없음. 최신 성공 관찰은 UTC18:00~18:14의 native7453 idle/unowned, recorder idle, rover disarmed, GPS connected/no_fix, IMU 약32.88Hz이다. camera 1080p30은 mode이고 bridge 약25.24fps는 preview 관찰이라 원본 녹화 fps로 간주하지 않는다. 이후 원본 카메라 확인 SSH timeout을 보존했으며 현재 접속/상태를 추정하지 않는다. 전체 목표는 미완료다.

재개: CURRENT_CHECKPOINT.json, RESEARCH_PROGRESS_LATEST.json, public-owner-overview-http-v4/HANDOFF.json, overview-closed-v6-probe-v1/HANDOFF.json, sdk-full-prebuild-closure-v1/HANDOFF.json, incremental-native-sdk-adapter-v5 및 sdk-full-rebuild-qualification-v1. frozen 실패·unowned NAS 변경 보존, stale Compose 재생성 금지, 설치는 exact Docker API/no-retry.

---


## 2026-10-14 실제 SDK 컴파일·브라우저·가속 검증 (2026-10-13T19:57:47.578060+00:00)

- [x] 공개 SIM overview2a4140b: Windows Python11+Node8, WSL Python15(실제HTTP4 포함) 통과, 독립 검토 통과. 기본OFF이며 GET은 지도 worker를 시작하지 않는다. 공개 ACTUAL 문서55be88f와 NAS 미러까지 확인했다.
- [x] **실제 ROS 전체 패키지 재빌드 성공:** SDK V5의 여섯 변경 파일을 fresh source/object/install 경로에서 모든 class-header 소비자와 함께 빌드했다. 총309.753s, configure/build/install/실제 로드 DSO OFF·누락 DI 거부·setup seal 검사가 모두 exit0이다. DSO799a1e079ff38e4fae64d855dc55c990c0c1885ec5d35e78fb3da1e62ac2022a(9,831,624B), 부모/core/source 불변, helper 제거·정확한 부재 확인. binary는 Git/Jetson에 설치하지 않았다.
- [x] 첫 실제 build V2는 configure9.111s의 FileNotFoundError로 실패했다. 최초 trace가 없어 원래 원인을 확정하지 않았다. 임시 파일 삭제에 안전한 용량 감시·전체 trace·정확한 소문자 Docker not-found 처리를 새 V3에서 검사했고 성공했다. 원래 실패·잘못된 absent=false·별도 부재 증거를 모두 보존했다.
- [x] **가속의 실제 닫힌 데이터 검증:** 같은2,432,295cells/원본2body를 표시용13tiles/38,240cells로 변환,1.4057048s(기존 별도 scalar7.8228356s 관찰). 원본 import는 별도2.4193246s. 원본/좌표/타일순서/SHA 일치, 실제aggregate13개611,840B를 저장하고 독립 readback했다. client2개26read/Stop/lease0/자원 종료. 엄격한A/B·live·ARM 성능으로 주장하지 않는다. NumPy qualification은 현재 Windows의 정확한 환경에만 적용되며 Linux/Jazzy는 별도 확인한다.
- [x] **실제 Chrome 표시 검증:** 닫힌V6 aggregate13개/38,240cells/positive445개가 표시됐다. 확대 후 새로읽기(완성2회)와 Stop 후 canvas PNG가 바이트 단위로 같았다. loopbackhost는 닫았고 이미 표시된 탭과 STOP_RETAINED.png를 보존했다. 첫 Worker 연결 실패는 별도로 보존했으며 테스트용 명시적start/API 연결을 새 후보에서 수정했다. 실시간센서/GPS/전체과수원 검증은 아니다.
- [x] GNSS UTC V4 source11개/독립 Stop delta 검토 통과. 실제feed100B frame/92B payload와 rawvalid/calendar/nano/tAcc/iTOW를 보존하고 independently checksum/decoded-field를 대조한다. 원본CDR의 시각 gate는 기본OFF이며 publication header를 measurement time으로 바꾸지 않는다. 옛7개 추출본3366행의 누락 품질을 복원하거나 유효하다고 간주하지 않는다.
- [ ] **현재 최우선 소프트웨어:** 실제 C++ Request/Ack↔기존 fsync/readback ReceiptStore 연결, enabled SDK-free 작은 body 검증, 원본 센서/Grid/coherent clock 연결 및 whole-record native stream, single-writer/대시보드 자동 factory/worker 수명 관리, source 품질·global robot path·GPS 시각 연관, finite history/read pin/GC.
- [ ] Windows 가속을 Linux/Jazzy·실제SIM/ARM에 무검증 적용하지 않는다. 정확한 Python/NumPy/native dependency closure를 읽기 전용 helper로 확인하는 새 recipe를 준비 중이다. Docker start는 source/profile 검토 후 한 번씩 실행한다.
- [ ] 이후에 실제 움직임·fresh holdout·30~60% 수동시험·GPS 실외fix·전류/전압·USBSSD·offlineGNSS시계·RTC·카메라 movingfps·Sept10 장착차이 검증. 실차 조종/모터/펌웨어/장착보정 및 자율주행 출력 없음.

전체 목표 미완료. Jetson 설치 native7453 유지, 마지막 현재상태 관찰은 UTC18:00~18:14이며 이후 접속/건강상태를 추정하지 않는다. 다음 재개: sdk-full-rebuild-qualification-v3/HANDOFF.json, overview-closed-v6-vector-probe-v2/HANDOFF.json, overview-closed-vector-independent-readback/HANDOFF.json, overview-browser-probe-v2/output/ROOT_CHROME_RESULT.json, gnss-receiver-utc-candidate-v4/HANDOFF.json 및 활성 provider/GC/Linux qualification 폴더. Frozen 실패·unowned NAS 변경·원본 보존; staleCompose 재생성 금지.

---

## 2026-10-14 GPS 원본 시각 보존·검증·연속 작업 체크포인트 (2026-10-13T20:29:37.821655+00:00)

- [x] ACTUAL 491abba: 실제 체크섬 검증된 NAV-PVT 입력 100B와 92B payload, calendar/validity/nano/tAcc/iTOW를 보존한다. ROS header는 발행 시각이라는 역할만 명시하며 센서 측정 시각으로 바꾸지 않는다. 공유 decoder/driver 3개는 SIM 343b36f에도 byte-identical로 반영했다. 원본 측정 clock 연결 및 GPS 매핑 factor는 아직 OFF다.
- [x] 공개 UTC admission 12개 synthetic 검사 통과, 별도 독립 검토 통과. 원래 GPS·serial·시계 검사 34개 통과, 녹화 검사 2개 통과/ROS 필요 4개 skip. 처음 환경 실패(NumPy/SciPy ABI, websockets, fixture 경로, OpenCV)는 보존했고, 별도 WSL venv에 프로젝트 범위의 의존성을 설치했다. 전역 패키지는 바꾸지 않았다. SIM 공유 변경은 관련 decoder 검사 3개 통과. ROS/직렬/보드 실제 동작을 검증했다고 주장하지 않는다.
- [x] 실제 Chrome overview 증거를 마감했다: 13 tiles/38,240 cells, 확대 후 refresh와 Stop에서 canvas 동일, 기능 API 30회 모두 200. favicon 404 1회는 남긴다. host와 lease 종료, Windows listener 목록에 해당 포트가 없음. 별도 socket 확인은 timeout 10035라 '연결 거부 확인'으로 주장하지 않는다. 이는 저장 fixture 표시 검증이며 실시간 대시보드 연결은 아니다.
- [ ] 실제 799a C++ SHA/영구 ReceiptStore 연결 테스트 V2: configure와 암호화 dependency 확인 성공, 테스트 CHECK 매크로가 glog와 충돌해 compile 실패. enabled 실행/receipt 저장은 아직 없으며 exact helper 제거·부재와 source 불변 확인. 실패를 보존한 새 V3 recipe에서 테스트 매크로만 변경해 독립/root 검토 후 한 번 실행한다. 기존 recipe는 재시도하지 않는다.
- [ ] GC V4: 실제 보관 파일 IO가 pin lock을 쥐어 Stop을 막는 문제를 수정, gated IO 중 open/Stop 검사 통과. 아직 private source이며 독립 검토·overview 전체 snapshot pin·실제 producer의 retirement authority 연결 필요. 원본·미확인 orphan·사용 중 파일은 정리하지 않는다.
- [ ] Linux/Jazzy NumPy 읽기 검사는 종료: d67 image, Python 3.12.3, NumPy 1.26.4, 1216 dependency hashes, helper 제거·부재. distribution metadata와 실제 SIM 실행 context는 미검증. Linux 전용 typed/default-OFF accelerator와 bounded helper를 준비하며 Windows 결과를 Linux/ARM에 자동 적용하지 않는다.
- [ ] 우선순위: 실제 native sensor→SDK producer→영구 ACK→단일 writer→공유 대시보드 runtime, 원본 전체 기록의 native map continuity, global robot path 및 GPS/IMU/LiDAR clock/covariance, bounded archive/pins, ARM·현장 검증. dense pose EOF 성공이나 fixture UI 성공으로 전체 통합 목표를 완료 처리하지 않는다.

Jetson 설치 native7453은 그대로다. 마지막 성공 상태 확인은 UTC18:00~18:14의 기록이며 현재 상태를 추정하지 않는다. 모터 출력·arm·펌웨어·장착 보정·원본 수정 없음. 상세 evidence: PUBLIC_GNSS_UTC_CHECKS.json, SIM_GNSS_SHARED_SOURCE.json, overview-browser-probe-v2/HANDOFF.json 및 진행 중 provider/GC/Linux 폴더. 모든 TODO 세 사본은 동일 UTF-8로 갱신한다.

---

## 2026-10-14 실제 C++ 저장 연결·Linux 검증·GC Stop 경계 (2026-10-13T20:50:39.998289+00:00)

- [x] 실제 DSO799a enabled DI 테스트 V3 성공: configure/compile/실행 exit0, 실제 OpenSSL SHA와 영구 ReceiptStore 연결. 10B/2cell 데이터 1개에서 원래 C++ nonce와 f64 signed-zero 비트·SHA를 보존하고 fake/stale/bit-mutated ACK를 거부한다. Stop 뒤 archive ACK는 허용하되 새 catalog는 발행하지 않으며 외부 owner·wire lease를 끝까지 유지하고 정상 종료한다. 68.397s, 정확한 helper 제거/부재, Python 자식 exit0, source 및 SDK97files 불변. root가 저장 DB·파일을 읽기 전용으로 다시 검증했다. V2 glog 매크로 충돌 실패 보존. 이 테스트의 범위는 최대64cells/320B이며 전체 native 지도·Grid·ROS executor·센서·ARM 검증이 아니다.
- [x] Linux NumPy overview V2 검증: d67 /usr/bin/python3 -I, Python3.12.3/NumPy1.26.4, 19개 finite synthetic 검사 모두 통과/skip0. 원래 scalar 및 vector 수식과 셀/좌표/SHA 결과 동일, typed defaultFalse, 의존성1216파일/초기105module 검증, 모듈·operator·interpreter 변경 거부, owner context/Stop/clock 처리 보존. thread/tmp/container 종료와 정확한 부재 확인. 실제SIM entrypoint/환경·distribution metadata 전체·Linux 실데이터 처리 속도는 미검증이라 운영 가속을 켜지 않는다.
- [x] GC V5 Stop 예약 경계 수정 및 독립 검토: 소유자 condition에서 승인한 '오래된·핀 없는 파생 데이터 정리'는 Stop 뒤에도 commit/unlink를 마무리할 수 있다. 이를 즉시 취소되는 작업이라고 주장하지 않는다. precheck→Stop return→journal commit 순서를 강제로 검증했고 현재 지도 pointer/raw 및 입력 SHA를 유지, 다음 정리 주기는 STOPPED, 모든 자원 종료. 기존 V4와 이전 실패·검사는 그대로 보존했다.
- [ ] GC는 아직 private/defaultOFF: 실제 공개 namespaced owner/view의 type guard를 보존한 overview snapshot/전체 page/실제 tile IO pin 연결, SDK의 obsolete receipt retirement 및 재전송 권한 연결이 필요하다. 쓰는 중인 읽기 pin을 단순 TTL로 해제하지 않는다. 원본·미확인 orphan·current/finished/inflight 데이터 보호와 원래 disk/RAM/native 수치 cap 유지.
- [ ] 최대4,194,304cells/20MiB native RAW와 16,384cells/80KiB wire cap을 그대로 사용하는 새로운 청크 저장 provider 작성 중. 청크 수신/쓰기 응답은 영구 body ACK가 아니다. 송신 RAW를 최종 전체 SHA/CAS fsync/readback/정확한 receipt까지 유지하고 일시 wire 복사만 청크 전송 후 해제한다. 청크마다 fsync하여 지연을 키우는 방식은 사용하지 않는다. 실제 소스 검토·bounded qualification·전체 SDK/Grid 연결 전까지 성능/운영 완료를 주장하지 않는다.
- [ ] 원본 GPS 감사: Oct3의 작은 원본 gps.jsonl 7개를 찾았고 archive manifest와 SHA 대조 중. jsonl 행 수와 rosbag /gps/data 메시지 수는 다르므로 동일하다고 간주하지 않는다. 최초 count 동일성 단정 실패는 보존하고, 원본 CDR ledger/hash와 timestamp를 따로 비교한다. 필드/packet을 복원하거나 GPS 측정 시각·Yaw·전역 지도 factor를 추정해서 켜지 않는다.
- [ ] 현재 최우선 미완료: 실제 센서→native SDK→단일 writer→공유 대시보드 factory/runtime 연결, 원본 전체 기록의 native map 지속성·global robot path 및 GPS clock/covariance, bounded history/pins/GC 통합, ARM/live/현장 검증. 물리 모터·arm·펌웨어·장착 보정 및 원본 변경 없음. Jetson native7453 유지; 설치된 기능과 source-only·private 검증은 계속 분리한다.

재개 evidence: sdk-enabled-storage-probe-v3/HANDOFF.json, ROOT_ENABLED_SDK_CLOSED_READBACK.json, linux-numpy-overview-candidate-v2/POSTCLOSED_HANDOFF.json, finite-archive-retention-prototype-v5/HANDOFF.json, gc-v5-independent-stop-review/HANDOFF.json. PUBLIC Git/NAS는 각 source head 확인 파일을 따른다. 전체 목표 미완료이며 software 잔여 항목을 현장 대기 항목으로 옮기지 않는다.

---

## 2026-10-14 20MiB 실제 저장·단일 writer·종료 경합·원본 보유 연결 (2026-10-13T21:37:57.533894+00:00)

- [x] 실제 C++799a/EVP binary-stream V2: 16,391cells/81,955B 및 2048×2048=4,194,304cells/20,971,520B를 기존80KiB wire 한도로 전송하고 영구 receipt 2개를 검증했다. Stop을 전송 전에 수행해도 이미 소유한 RAW는 유지하며 archive ACK 이후에만 해제한다. fake/version/nonce/f64 signed-zero 변조를 거부하고 최종 RAW/WIRE/PENDING0, 자식 exit0/helper 제거·부재, source 및 SDK97files 불변 확인. 최대 크기 전체 profile5.245s/cross0.798s/전체trial49.306s는 공유 host 합성 관찰이며 실시간·하드웨어·단독 finish throughput이 아니다. Root 읽기 전용 DB/CAS 재검증도 통과.
- [x] 첫 max trial V1은 configure/compile 성공 뒤 defaultOFF 시작127로 실패했다. ldd가 mounted799a 대신 이미지의 예전 `/ws/install` ABI를 선택했다. 실패를 보존하고 NEW V2에서 같은 정확한 LD 환경을 ldd/defaultOFF/두 profile에 적용하며 경로·SHA를 확인했다. 기존 DSO/core를 다시 빌드하거나 가짜 symlink로 대체하지 않았다.
- [x] 단일 writer 제안 V2: 실제 namespaced owner/Source/CommittedReadView가 만든 ReceiptStore를 빌려 같은 OS lock/DB를 공유, C++ 원래 nonce/f64/body identity와 display reference의 별도 관계를 검증, metadata-only BODY로 재복사/re-persist를 하지 않는다. 46개 finite Python 검사 모두 통과/skip0, 33source 불변, 독립 검토 완료. 실제 C++ endpoint/RAW-release 및 GC 병합은 아직 미구현이다.
- [x] exact-public-namespace retention/overview V5: 기존12개+V2교정1+V3직접생성1+V4동시cache-release1+V5worker-start1 =서로 다른16개 그룹을 확인했다(16개 전체 재실행이 아님). thread/cache/opening/pin을 실제 해제하기 전 closed로 보고하지 않으며 오류는 소유권을 유지하고 재시도할 수 있다. 수식/native math/RAM/disk cap과 엄격한 클래스 식별 유지. V3/V4의 추가 경합 증거는 별도 보존, V5 독립 follow-up 검토 중. 공개/Jetson에 아직 적용하지 않았다.
- [x] qualified-private-source NAS 백업: fullbody providerV2 20파일, actual probeV2 36파일, singlewriterV2 33파일, retentionV4 53파일을 frozen 해시로 복제했다. 원본GPS/CDR/body·raw inspect/env는 복사하지 않는다. V4에는 이후 발견된 worker-start 경합이 있으므로 다음 작업은 V5 이상의 확정된 source를 따른다.
- [ ] 가장 중요한 실제 연결: Python Source.stop 뒤 begin이 새 요청을 거부하는 규칙만으로는 C++가 Stop 전에 소유한 RAW를 아직 시작되지 않은 archive로 보낼 수 없다. 실제 bound C++ Runtime.stop barrier 뒤 current pending archive_requests≤8와 정확한 charged firstchunk로 만든 일회성 owned-FD 권한을 사용해야 한다. completed nonce/owns·resolve 결과나 Python bool/JSON callback은 권한이 아니다. 첫 lease는 그대로 첫 payload로 써서80KiB 한도를 넘기지 않는다. 최종 해제는 실제 Runtime.ack가 수행한다. 진짜 binder가 없으면 loader/production begin_preowned는 실패하도록 유지하고 synthetic model 권한을 승인으로 취급하지 않는다.
- [ ] 이 연결을 실제 singlewriter/metadata-only owner/overview pins/GC와 병합하고 source·Stop·generation·close·partial/native-ACK roots를 검증한다. 외부 CPP receipt를 무조건 영구 보존하면 disk cap에 걸리므로 실제 producer의 retired reference/re-admission authority 없이 안전한 정리를 완료 처리하지 않는다. helper120s/4096RPC의 사전 budget 예약·quiescent generation 교체도 운영 연결 필요.
- [ ] 실제 센서→SDK Grid→영구 writer→공유 대시보드 factory/runtime, 전체 원본 기록 native 지도/robot global path, GPS measurement-clock/covariance/고정 frame, ARM/live/현장 검증은 여전히 software/검증 잔여다. 원본필드·정확도·시계를 추정해서 채우지 않는다. 모터·arm·펌웨어·장착 보정·원본 변경 없음; Jetson 설치 native7453 유지, 자율주행 출력 OFF/HOLD.

재개: sdk-fullbody-storage-probe-v2/HANDOFF.json, ROOT_FULLSIZE_SDK_READBACK.json, single-writer-stream-owner-proposal-v2/POSTCLOSED_HANDOFF.json, single-writer-stream-independent-review-v2/HANDOFF.json, public-owner-retention-overview-integration-v5/HANDOFF.json, preowned-native-archive-integration-design-v1/HANDOFF.json. 최신 machine checkpoint의 active_work를 따른다. 전체 목표 미완료이며 단일 writer/genuine endpoint/retention integration을 우선 진행한다.

---
