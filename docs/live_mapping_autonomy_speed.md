# Live mapping and autonomous speed preference — 2026-10-02

The real dashboard now has a map below LiDAR. Start accumulates display bridge
returns without starting a recorder. Stop keeps the map and red trajectory;
manual zoom/pan survives growing bounds. Nearby automatic fit uses occupied
objects within 12 m; Full shows the retained extent. New map is an explicit reset.

The single background worker consumes latest-only loopback LiDAR and raw IMU
bridges; no serial port, motor socket, sensor restart or IMU direction correction
is introduced. Integration is limited to 2 Hz and 450 rays per scan. Grid bounds
are limited to 1024 cells per side, path history is bounded, and unchanged HTTP
versions omit the PNG. Camera delivery settings/protocol remain unchanged.

This is **unqualified display mapping**, not an autonomous localization permit.
No wheel encoder, GPS fix, camera AI or Gazebo truth supplies its position.
Translation-only ICP uses raw IMU heading differences. Source clock gaps, stale
or unavailable attitude, tilt changes and uncertain registration stop insertion.
Unknown extrinsics, rolling-ray timing, height changes and repetitive trees can
still bias this estimate; neither the raster nor its pose is exported for motion.
The displayed coordinate origin is the first LiDAR pose. Sensor height is about
0.60 m; no 3D terrain/branch clearance is inferred from this plane.

An autonomous cruise percentage can be saved through the hardware slider, but
hardware start remains disabled. The API returns 409 rather than forwarding any
start command. Hardware speed in m/s is uncalibrated. Simulation uses this ratio
for both configured cruise and turn speeds, owned speed changes do not extend the
watchdog, zero stops, and increasing it does not restart the controller.

Archives are generated from the original recorded LiDAR/IMU by the already
deployed occupancy renderer. On recorder stop, regeneration is requested in the
background. The resulting `dashboard_occupancy_grid.json` is stored beside raw
data; Recreate also works for older recordings. Raw inputs are preserved.
The archive panel displays the saved final map and renderer's complete fading
trajectory. It is labelled as the entire recording; it does not claim a new
timeline-synchronized robot position. Archive generation is single-flight.

Physical autonomous qualification remains incomplete: mounting and scan timing,
branch/tilt/height negatives, vehicle response and braking, unknown-orchard row
end/wall uncertainty, turn clearance and a complete encoderless mission.
