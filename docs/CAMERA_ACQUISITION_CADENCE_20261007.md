# Camera acquisition cadence — 2026-10-07

The USB driver previously checked `now - last_publish_at >= 1 / requested_fps`
before reading, then set `last_publish_at` after the blocking `cap.read()`.
An acquisition that already waited for the next sensor frame could therefore
be followed by another full frame interval before the next read. This is one
software explanation for a received rate below the selected device mode.

The driver now consumes an absolute **monotonic acquisition slot before the
read**. Elapsed slots are skipped once, rather than queued for catch-up. The
ROS header clock and wall-clock status timestamps remain separate. Camera
reopening creates a fresh cadence session; failed reads consume a slot but
never count as a successful frame. Raw-image publication is also paced with
monotonic time, so a wall-clock correction cannot stall or flood that topic.

No capture resolution, MJPG mode, camera mount rotation, JPEG quality,
subscriber QoS, or original-resolution recording path is changed. OpenCV
thread count is unchanged: a separate synthetic Jetson benchmark found that
forcing one thread slowed the tested encoding workload.

`camera_driver_status.json` adds `capture_cadence` with successful reads,
skipped slots, and an observed rate over at most the latest 120 read completions.
`last_read_ms` and `last_publish_work_ms` separate acquisition wait from
preview/raw publication work. These are driver processing measurements;
`actual.fps` still represents the V4L2-reported device mode. Neither number
proves sustained native recording or client rendering at 30 FPS.

Validation: `python -m unittest tests.test_camera_cadence -v` checks the
production driver methods without opening a device, running ROS, recording,
or changing hardware settings. Eight checks cover blocking acquisition,
missed slots, clock changes, failed reads, bounded observations, mode validity,
and preservation of 1920×1080 raw/native pixels with the separate 1280×720
JPEG preview and configured 180-degree rotation. A header-only sustained ROS
probe after a guarded camera-role deployment is still required to qualify
real capture, native JPEG delivery, and preview rates independently.

Before deployment, a read-only header probe observed 14.72 Hz preview-only
over 15 seconds. With the native-recording topic subscribed for another
15 seconds, preview and native JPEG delivery were both approximately 15.00 Hz;
the median source-stamp interval was 66.64 ms despite the selected 30 FPS mode.
The native JPEG was 1920×1080, the separate preview was 1280×720, and the native
encoder reported no queue drops. This probe parsed the first JPEG SOF header
only, without decoding every frame, opening the device, or starting a recording.
BEST_EFFORT/KEEP_LAST(1) subscriber loss remains possible. These baseline results
support the cadence diagnosis but do not qualify the un-deployed patch.
