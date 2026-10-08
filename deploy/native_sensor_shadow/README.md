# Native sensor shadow (ROS Iron, physical observer only)

This selfcontained package reads original LaserScan and Imu CDR from a newly spawned native observer, computes laser-sensor-plane diagnostics, and always returns HOLD with zero motion. It has no motor transport, control socket, mapping API, driver restart or autonomy-start path. Existing services do not import or start it automatically.

Default commands only print plans:

```bash
python3 -B physical_shadow_runner.py
python3 -B verify_source.py
```

Source verification and portable tests:

```bash
python3 -B verify_source.py --verify-source
python3 -B -m unittest -v test_source_package
```

The package-level `.gitattributes` preserves exact frozen bytes, including existing CRLF. Do not normalize them: manifests and vendored-module checks use byte hashes. The absolute paths in VENDORED_INPUTS are inert provenance; runtime imports resolve only inside this package. Private recordings and field coordinates are excluded.

## Operator image preparation

The original 19 build files are bound by SOURCE.json. Dockerfile.shadow remains unchanged. Dockerfile.adapter adds only ARG BASE_IMAGE / FROM ${BASE_IMAGE} so a new owned tag can resolve to the existing exact local SDK image. Verify SOURCE, the adapter and the base image association before building.

Supported immutable base: `sha256:fa63d0d9a0cc8b11ed9fe093314ad1cb522e051613ba0b5737de26790ec6a7b2`. Arbitrary registry/base fallback is unsupported. The operator can create a UUID-owned private tag pointing to that exact local image, then build with `--pull=false -f Dockerfile.adapter --build-arg BASE_IMAGE=<verified-owned-tag>`. The new image installs SciPy and compiles the reviewed C++ observer. Existing host packages, running services and role images remain untouched.

Installed paths:

- `/opt/ubts-shadow/package`: 13 runtime files, HANDOFF.json and DEPLOY_CLOSURE.json.
- `/opt/ubts-shadow/bin/actual_cdr_observer`: compiled observer.
- `/opt/ubts-shadow/physical_shadow_runner.py`: default-plan runner.

A build or dependency check does not prove fresh sensor delivery or successful fitting. The operator must bind the actual binary SHA and fresh publisher GIDs, verify private security/network/IPC/lifecycle configuration, and preserve actual roles, source files, maps and recordings. UID0 is explicit for observed DDS interoperability; it grants no device or control access. Use a read-only rootfs, cap-drop ALL, no-new-privileges, bounded CPU/memory, process-only log tmpfs and a new owned0700 evidence directory. The Dockerfile does not supply orchestration or protection guards.

## Explicit fresh observation

The reviewed launcher supplies `--observe-shadow`, `--package /opt/ubts-shadow/package`, `--package-manifest-sha256 714fa332f73983fe4bffe1606831663365e6c121fa049a18873e2b53479f5077`, `--observer-bin /opt/ubts-shadow/bin/actual_cdr_observer`, `--observer-binary-sha256 <verified-installed-binary-sha>`, `--scan-gid <fresh-gid32hex>`, `--imu-gid <fresh-gid32hex>` and `--evidence-dir <new-owned0700-directory>`.

The receiver and independent RCL_SYSTEM_TIME clock exist before native subprocess creation. Original sensor/callback timestamps remain unchanged. The [official Iron Clock API](https://raw.githubusercontent.com/ros2/rclpy/iron/rclpy/rclpy/clock.py) supports this explicit clock without constructing a Node or fabricating a /clock publisher. Native CDR, integer header stamps, per-message16-byte GIDs and callback CLOCK_MONOTONIC_RAW are preserved. Original rolling timing is validated but not deskewed. IMU covariance zeros remain unknown; REP145 and mounting do not become verified body heading.

Native capture is15 seconds; the outer processing budget is20 seconds. Concurrent bounded readers explicitly fail on a full16-row queue, oversized line,32MiB stdout/256KiB stderr or200diagnostic capacity. They never drop old rows to make delivery appear fresh. Only the exact owned observer process can be terminated. Partial capture and the causal error are retained. Unjoined readers or private evidence failure never qualify cleanup; OS process/fsync calls are not hard realtime guarantees.

## Verified scope

Local15 runner pure/mock tests and4 portable tests passed. The unchanged native observer separately delivered150 original3240-ray scans and300IMU messages in an actual15-second Jetson probe; all150 last-beam stamps were at or before their native callbacks. This does not qualify the new Python live factory, lossless delivery, trajectory/map accuracy, body velocity, braking or physical autonomy. Installed live results must be recorded separately. All physical outputs remain HOLD/0 even when diagnostics succeed.
