# Native camera and HLS preparation queue

The recorder admits a newly finalized `camera` or `both` archive after the
bag, preview timeline, GPS/PWM logs and native mapping writer have closed.
Finalization holds the recording slot: repeated Stop is idempotent and a new
Start cannot overwrite/open a recording during the previous bag flush.
Historical recordings are not automatically backfilled. The native sensor
bag and its metadata remain the source of truth; generated video is a sidecar.

## Scheduling and recovery

- At most eight admitted jobs, one child process group, one allowed CPU core
  with niceness 10. OpenCV, BLAS and FFmpeg thread limits are one in that child.
- Recording, armed/moving control and live mapping pause the entire process
  group. An active recorded mapper also takes precedence over media encoding.
  Missing/stale priority status pauses conversion. This is resource scheduling,
  never a command to the rover.
- Empty queues and queues containing only operator-paused/terminal jobs perform
  no priority HTTP polling. Status requests read existing job state.
- Pause/resume continue a healthy child. Cancel terminates its whole group.
  Explicit resume after failure/cancellation starts a new attempt directory.
  Partial attempts and old verified sidecars are retained. No recursive delete,
  overwrite of raw sensor data, or automatic replacement of an existing HLS
  package occurs.
- Queue state is stored per archive in `video_preparation.json`, schema
  `orchard.media_queue.v2`. Only marked unfinished jobs resume on recorder
  restart. Operator pauses and cancellations persist. The queue retains up to
  sixteen terminal jobs in RAM in addition to admitted work; on-disk markers
  remain. Capacity rejection is visible as `failed/media_queue_full`, requiring
  explicit resume when a slot becomes free.
- An exclusive flock prevents multiple queue owners. Parent-death handling
  terminates the encoder group after recorder failure. A service restart uses
  a fresh attempt rather than trusting an interrupted encode.

## Source and publication checks

The child hashes metadata, original preview timeline, GPS/PWM logs and every
native bag/mapping-source file, including SQLite DB3 as well as MCAP. It checks
those hashes after export, before install and after install. Source symlinks,
unfinished archives and archive path traversal are rejected. Existing media
without native source SHA proof is rejected for review.

The exporter prefers `/camera/recording/jpeg`, preserving original compressed
JPEG bytes and receipt timestamps. Source resolution and variable frame timing
are retained. The source-sized MP4 uses CRF 18 and is the top HLS representation;
720/480/360/240 representations are added only when the source is large enough.
No resolution/FPS upscaling or constant 30 FPS conversion is performed. A 1080
source therefore remains 1080; a 720 source cannot become native 1080.
Driver MJPEG passthrough frames are stored in sensor orientation and declare
`rotation=180` in the message format; the export records `rotation_deg` and
the build applies `hflip,vflip`. Older frames without that flag are upright.
In a passthrough session the decoded fallback re-encode (`reencoded_bgr_q90`)
keeps the same sensor orientation and rotation tag, so a payload switch inside
one recording is exported as one recording (`camera_payload=mixed`, per-kind
`camera_payload_frames`, `camera_payload_segments`); a real rotation or size
change is still rejected (`camera_mode_changed_inside_recording`).
`original_camera_codec_preserved` is true only when every frame is original
camera JPEG. Presentation timestamps use the message header (capture) stamps
when they increase by at least 1 ms and agree with the bag receipts within 1 s
(`frame_timing=recorded_camera_header_stamp_VFR`), else the receipts; receipts
that fall in the same millisecond are moved to the next millisecond and counted
(`pts_adjusted_frames`) instead of failing the whole recording. Frames are never
added or dropped, and receipts remain the timeline alignment clock.

## Control contract

Recorder loopback API:

- `GET /media-jobs`: immutable job status, counts, bounds and current stages.
- `POST /media-jobs/action` with `{ "recording_id": "id", "action": "pause" }`;
  allowed actions are `pause`, `resume`, `cancel`. There is no arbitrary
  command/path/encoder argument. No automatic legacy enqueue endpoint exists.

The dashboard forwards these through `/api/recordings/media-jobs` and
`/api/recordings/media-jobs/action`, using its normal origin/session checks.
Stages report verification/export/encode/install progress; they do not fabricate
an encoding completion percentage. Optional frame/dimension/duration fields
appear after the native export has produced verified counts.

Priority monitoring uses the local fresh `rover_bridge_status.json` and a
read-only `/api/live/mapping-jobs` response. `MEDIA_MAPPING_JOBS_URL` defaults to
`http://127.0.0.1:8080/api/live/mapping-jobs` for Jetson host networking. SIM
requires `http://dashboard:8080/api/live/mapping-jobs` for Compose networking.

## Validation and deployment boundary

Linux disposable-process tests cover bounded admission, exclusive ownership,
record-finalization order, whole-group pause/resume/cancel, persistent recovery,
idle HTTP inactivity, one-core affinity, source mutation and publication refusal.
The actual recorder/queue/adaptive suites passed 24 tests; the SIM suites passed
28 tests on 2026-10-07. These used synthetic archives and no physical sensor or
motor transport.

`tests.test_media_pipeline` additionally exercises a synthetic 1080
native ROS bag through the real child, FFmpeg, VFR MP4 and all HLS renditions.
It requires ROS bag support plus FFmpeg/ffprobe. Setting
`UBTS_REQUIRE_MEDIA_PIPELINE=1` makes missing dependencies fail rather than
skip in a release gate. A dependency skip is not a
release pass. Jetson's recorder was separately applied from `104adcc` after
ARM forced-1080 pipeline checks and a bounded readiness wait. Its 470-scan map,
73 protected files and twelve other roles were preserved. The recorder exposes
the queue API; applying the web alone cannot do that. No real recording was
started to validate this change. The follow-up portable VFR encoder has 45
forced-pipeline passes on each of FFmpeg 4.4/PyAV 8 and FFmpeg 6/PyAV 11.
The SIM clean-image release and its source/host clock alignment remain separate
runtime gates; synthetic wall-clock tests do not establish that alignment.
