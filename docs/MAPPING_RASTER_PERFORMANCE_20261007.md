# Exact display raster acceleration

The live/archive map's ray union and signed tile updates have an optional native
kernel. The geometric model, pose estimator, ray rounding, occupied precedence,
free-endpoint margin, signed log odds, saturation and memory-limit admission
remain the same. The browser still receives tile coordinates/cells and renders
them; this does not reintroduce server map images.

`native_raster.cc` has no global state, allocation or sensor/control inputs.
The bounded raster uses x-major bitmap indices. Tile updates validate all
lookups before writing, apply one increment to each unique observed cell and
keep the existing fallback for duplicate/unsupported coordinates or a reached
cell budget. `ctypes.CDLL` calls release Python's GIL during the kernels.

The Docker build compiles for its target architecture. Missing/disabled native
code falls back to the NumPy reference; `UBTS_NATIVE_RASTER=0` forces that path.
`raster_backend` in map status reports the loaded paths. The equivalence tests
cover randomized 3,240-ray scans, negative coordinates, endpoint precedence,
margins, conservative free evidence, saturation and ordered admission at the
cell cap. Required Linux tests assert the kernel is actually present.

This is a speed change, not independent map-accuracy or autonomous-drive
qualification. Physical S2 scans are rolling; per-ray timestamps, scan order,
IMU-to-laser rotation and vehicle-motion deskew still need verification.
No native navigation observer or automatic motor output is installed here.
