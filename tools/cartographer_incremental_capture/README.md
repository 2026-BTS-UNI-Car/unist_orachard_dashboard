# Default-OFF incremental SDK capture sources

These six exact Node/bridge/header overlay files were compiled in a fresh full
cartographer_ros package, including every class-header consumer. The actual
loaded new DSO passed default-OFF, missing-provider rejection and setup-seal
checks. The original core static archive and all source inputs stayed unchanged.
See BUILD_QUALIFICATION.json for image, SDK, source and binary identities.
The new binary is retained locally and is not installed on Jetson or committed.

Use only the matching complete ROS Jazzy/Noble SDK/source tree. Copying a new
header/library into old cached consumers is invalid because class layouts and
constructor signatures changed. The old five-argument Node constructor remains,
and a separate DI constructor sets up capture before callbacks/timers. Rebuild
ALL package targets in fresh source/object/install directories; the full-package
qualification restored the reviewed ad8 transport ZLIB dependency. The image's
installed f0 DSO is dense-only; the qualified ad8 publisher is a different parent.
No source-to-apt-binary provenance beyond the observed header/core closure is
invented. These are overlay sources, not a self-contained ROS package installer.

Capture takes one SDK metadata batch and copies changed native probabilities and
known masks while the actual Node sensor mutex is held. SDK Grid pointers never
escape into a worker. Finite ownership is reserved before raw allocation; SHA
work, durable storage and body reads occur outside the sensor ingress mutex.
One admitted batch stays pending through exact archive acknowledgement, instead
of being replaced by every new scan. Numeric reservation order is retained.
Recoverable allocation pressure defers without changing native math, source
clock floors or integrity rejection rules. Stop releases callback publication
rights; the external SDK-free owner retains raw/wire leases until accounted close.

An explicitly enabled Bootstrap needs real digest and authenticated durable
receipt providers. Missing providers are rejected. Request/ACK binding includes
source context, nonce, native key/version/body ID, bit-exact geometry and raw SHA.
One actual compiled-library synthetic probe now supplies real OpenSSL SHA and
a durable receipt provider. It archived one exact 10-byte/2-cell body, rejected
forged/stale/bit-mutated ACKs, and checked Stop plus external-owner/wire drain.
The tested provider profile is only64 cells/320 bytes; this is not a full-size
native transport, actual Grid callback, ROS executor or sensor qualification.
A separate actual C++ binary-stream trial now archives16,391 cells and the
existing maximum4,194,304 cells/20,971,520 bytes through81,920-byte charged chunks.
Both exact receipts preserve nonce and raw geometry bits, reject forged/stale
ACKs, and drain source Stop with final RAW/wire/pending accounting zero.
The maximum whole synthetic profile took about5.25s; this is not live performance.
Standalone storage is proven separately from canonical shared-writer, actual
Grid/sensor callback, continuous backend and dashboard factory integration.
The copied metadata batch ID is NOT a genuine pose-graph revision; this preview
must retain its UNQUALIFIED_AD8_SDK_PREVIEW label and no physical authority.

Closed source checks:24 earlier lifecycle/ownership groups,8 targeted allocation
groups and44 static contracts; independent source and recipe checks passed.
Actual fresh full-package build:309.753s; configure/build/install/loaded ABI all
exit0, owned helper removed and exact absence verified. Prior failed allocator,
cleanup and configure attempts remain separate immutable checkpoints.

Still open: full-size enabled provider wiring, coherent source/clock admission,
native whole-record streaming, finite archive/read-lease GC, global robot path
and GPS measurement-clock association, ARM/live and physical tests. No wheel
encoder, PWM, camera or simulated truth is introduced as localization evidence.
Do not treat this compiled source interface as an installed or validated mapper.
