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

## Independent preview and raw publication

The first cadence-only deployment was subsequently measured at approximately
22.38 Hz preview-only and 20.5 Hz with native JPEG subscribed. Preview resizing,
JPEG encoding and raw BGR serialization/DDS publication still occupied the
acquisition callback; removing its extra wait alone did not qualify 30 FPS.

`camera_preview_worker.py` now performs that preview/raw work independently.
Capture rotates the owned frame once, marks it read-only, assigns its original
ROS stamp and hands it to a **one pending frame** slot. Replacing a pending
frame increments `overwritten`; it never creates a backlog. A pending raw
request is carried to the newest real frame, preserving its matching stamp
without inventing/interpolating sensor data. Native JPEG submission remains
independent, at its existing original resolution, quality 90 and queue limit 4.

Each reopen/settings change invalidates queued and in-flight generations.
Preview, raw and native publishers have separate publication gates so one
stream cannot block submission or another stream. The reset barrier waits
at most 0.5 seconds and reports a timeout if a previously started DDS call
does not finish. Shutdown invalidates work immediately and joins the preview
worker for at most three seconds; an uninterruptible codec/DDS call is reported
as `camera_preview_worker_shutdown_timeout`, rather than falsely called clean.
The native encoder counts generation cancellation separately from publication.

Driver status adds the worker's pending/busy state, overwritten/cancelled counts,
work time, queue wait and completed-frame age. `last_publish_work_ms` now means
the acquisition callback's rotation/handoff work, excluding background JPEG/DDS
work. Capture, preview and native rates must still be measured independently
after deployment. No new 30 FPS result is claimed by the device-free tests.

`tests.test_camera_cadence` and `tests.test_camera_preview_worker` cover actual
driver methods, a completely blocked preview callback with continued capture
and native submission, latest-only replacement, raw-request carry, generation
cancellation, bounded reset/shutdown failures, and readonly 1920×1080 native
JPEG encoding without changing quality or original pixels. The SIM repository's
USB-driver copy uses the same worker; Gazebo's camera source is separate and
is not changed by this USB-driver patch.
