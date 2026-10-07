# Missing recorded LiDAR/IMU previews

Some finished records contain an empty `timeline.json` while the native
`mapping_source/sensors.jsonl.gz` remains complete. Recovery is explicit and
offline; an HTTP playback request never scans the complete sensor archive.

```
python -m scripts.recover_recorded_lidar /archive/RECORD_ID --cache-root /cache
```

Use a read-only archive mount, a writable derived playback cache and a CPU limit.
Run only when live mapping, recording and vehicle control are idle. This command
does not publish ROS messages, start recording or change a map.

The builder verifies the complete source manifest and compressed source SHA,
then writes deterministic eight-second gzip chunks and atomically publishes a
finished manifest. Native bag receipt time is aligned to the original metadata
start, independently of any corrected display date. LiDAR display projection
matches the original bridge; at most 1,080 display rays are retained per scan.
Original sensor ranges and localization inputs remain unchanged. Native IMU
timestamps/vectors are retained without software mounting correction.

Playback reads only the requested chunks (up to 24 seconds plus one preceding
chunk), checks their hashes and rejects changed source metadata/stat or corrupted
chunks. The normal compact and object point formats remain available. A missing
or incompatible prepared cache returns `preview_recovery_required`; it does not
launch expensive work from a browser request.

Linux verification: eight recovery tests plus five existing telemetry/profile
tests passed. Tests cover original-file preservation, receipt-time alignment,
bounded seek, IMU synchronization, corrupt chunks, changed/active sources,
recovery of recorded PWM sidecar descriptors, compact point compatibility and the light endpoint without full raw decoding.
