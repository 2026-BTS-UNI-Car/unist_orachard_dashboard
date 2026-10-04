# IMU / map delivery checkpoint — 2026-10-04

This checkpoint supersedes the earlier numeric-cell deployment for delivery latency. It does not qualify absolute localization accuracy or enable real autonomous motor control.

## Confirmed causes and changes

- Archive map look-ahead was aborted on normal media-clock updates, including catch-up after buffering. Only explicit seeks, backward jumps and paused cursor changes now invalidate an incompatible request.
- A 12-second map window repeated complete occupancy tiles at every scan. Negotiated `codec=xor2-v1` windows now carry lossless two-bit cell changes. Each window starts with an independent complete seed; older clients retain full tile responses. Original histories and log odds remain unchanged.
- IMU/GPS/PWM delivery shared the large LiDAR download. The `dashboard-telemetry-v1` timeline profile serves small derived telemetry caches first, with independent prefetch, cancellation and bounded IMU history. Obsolete LiDAR fetches are canceled on recording changes or distant seeks.
- Live IMU waited for one ACK round trip per sample. Negotiated `flow=latest` sends compact latest native samples at 8 Hz in auto mode (2/5/10/20 Hz for tiny/slow/balanced/detail). A bounded send buffer and send timeout prevent unlimited application queuing. Mapping and ROS consumers retain full raw quaternion/covariance messages; legacy ACK clients still work.
- A visible browser window was observed delivering animation callbacks only every ~1 second. Archive media-clock updates now race animation frames with an 80 ms timer; the winning callback cancels the other. Camera decoding remains native and independent.
- One October 3 recording had a zero-byte preview timeline. Its IMU playback cache was recovered from the hash-verified raw sensor extract on the original bag receipt clock. The empty source file, GPS, PWM, metadata and bags were preserved. The first recovery took 23.46 seconds; its installed warm cache served a 24-second telemetry window in 0.27 seconds. Its missing historical LiDAR preview timeline still needs separate recovery; the integrated map uses the complete raw scan history.

## Observed validation

All seven October 3 recordings were compared at 24–36 seconds. Every cell at every accepted scan, pose, timestamp, path and submap reset matched the original map window. No scans were omitted. Gzip map payload reductions ranged from 57.3% to 78.5%. In the orchard example the 12-second window fell from 1,318,078 to 283,707 bytes. Its 24-second IMU/GPS/PWM response was 29,012 bytes; the prior mixed sensor response was 601,643 bytes.

ARM candidate checks cover the mapper, cell changes, telemetry endpoint, raw cache recovery and HTTP/control gates. Eight native IMU state/processing tests passed against the installed ARM image with read-only fixture mounts. A real localhost WebSocket test delivered seven distinct IMU samples without ACKs in 0.756 seconds. Simulation candidate checks passed 44 tests, preserving the other three GUI/sensor containers, ten recording metadata files, frozen map and autonomy settings.

Browser validation used the actual Cloudflare dashboard with temporary 512 kbps / 250 ms HTTP network emulation. The complete 210.12-second orchard recording produced 1,549 map commits, averaging 7.57 Hz. Media-clock minus displayed-map age was at most 0.20 seconds and 0.092 seconds at the 95th percentile. Live IMU display averaged 7.92 Hz. Two of 1,982 sampled playback observations showed no IMU, both at the recording tail (209.88 and 210.06 seconds); the last original IMU sample was at 209.61 seconds. Missing source data is not replaced with invented motion. A backward seek to 20 seconds displayed the 19.9-second map and retained the user's manual zoom. These measurements do not establish centimeter map accuracy, moving-rover safety, or performance on every network. Network emulation is HTTP-scoped and does not establish that WebSocket traffic had identical throttling.

## Installed scope and remaining work

Only the actual web dashboard and IMU bridge changed; the other eleven running roles and device/configuration owners were preserved. Follow-up releases changed the web role alone. No motor commands or real ROS bag replay were sent. The stored live map retained its native cells and frozen state.

The local simulation web role received the shared telemetry cache and clock changes. Its existing pose source remains unchanged; this deployment does not finish encoderless simulation localization. Real autonomy remains deferred pending localization and braking validation. Legacy 1080p media preparation, complete broken-preview recovery, calibration/lever-arm measurement, longer adverse-network tests and field map accuracy validation remain in `UBTS_TODO.md` / root `todo.md`.
