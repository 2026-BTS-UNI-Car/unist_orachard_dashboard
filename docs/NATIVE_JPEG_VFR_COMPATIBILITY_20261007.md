# Native JPEG VFR input on FFmpeg 4.4 and PyAV 8

The Jetson Iron/Jammy image uses FFmpeg 4.4. Its concat parser rejects the
`option framerate 1000` directive. Removing that directive would silently
quantize recorded timestamps to the image demuxer's default 25 Hz, which is
not an acceptable sensor/video synchronization fallback.

`build_adaptive_recording.py` now defaults to `native_jpeg_vfr.py`, using the
public PyAV 8 remux APIs. `--vfr-input pyav` or `UBTS_MEDIA_VFR_INPUT=pyav`
selects this same path explicitly in newer FFmpeg validation images. Explicit
`concat` mode retains the old format for compatible external tooling; there is
no automatic fallback to coarse concat timestamps.

The helper verifies each generated JPEG's size and SHA256, demuxes that single
JPEG with `image2pipe` and a millisecond time base, copies its stream setup and
remuxes the encoded packet bytes into a generated Matroska intermediate.
`bytes(packet)` must equal the verified JPEG. No JPEG re-encoding, sensor
replay, device access or network access is involved. The original compressed
camera archive remains unchanged. Existing FFmpeg then performs the H.264
encode, source-sized native MP4, rotation and conditional HLS renditions.

Packet PTS and DTS come from original bag nanoseconds relative to the first
camera frame, rounded to the nearest millisecond. This bounds timestamp
rounding error to 0.5 ms. A collision at that resolution is rejected instead of
moving a source timestamp or dropping a frame. Older export manifests without
bag nanoseconds use their verified relative receipt times. Mixed/mismatched
clock fields are rejected.

PyAV 8 exposes `Packet.duration` and `is_keyframe` as read-only properties. The
JPEG demuxer supplies the keyframe flag and a one-millisecond stream time base.
FFmpeg 4.4 leaves the packet duration itself unknown (0), which is recorded
honestly. Exact adjacent PTS specify presentation intervals. The helper
verifies this contract rather than writing private FFmpeg structures.
The existing terminal-frame policy is preserved explicitly: repeat the last
JPEG at the terminal PTS after a median observed inter-frame interval (single
frame fallback: 1/15 s). The encoder uses the old concat demuxer's nominal
1000-Hz clock together with VFR mode, not a constant output cadence. Actual
frame PTS and counts are checked without interpolation, including completely
uniform 20-Hz input. This keeps the terminal presentation at 1 ms even when
source cadence is uniform; without that clock hint FFmpeg extends the repeat
by its guessed frame period. `ffprobe` checks final encoded duration. Consequently
the container includes one presentation-only repeated frame and can extend by
1 ms beyond the final hold endpoint. `source_frames` and source FPS count only
captured frames. The manifest records this policy and time quantum; capture
FPS is never forced to 30, and source dimensions are never upscaled.

FFmpeg 4.4 also has a direct HLS-encode duration accounting problem with this
VFR clock: a 13-frame clip spanning 0.8 s can receive `EXTINF:0.013` while its
actual fragment PTS still span 0.8 s. Each lower rendition is therefore encoded
once into a generated MP4, which materializes sample durations, then copied
without another encode into fMP4 HLS. The temporary rendition MP4 is deleted
after successful packaging; at most one such rendition is produced at a time.
B frames are disabled so encoded PTS and DTS remain equal and the HLS muxer
does not add a negative-DTS timeline offset. Source capture timestamps stay
unchanged apart from the documented millisecond rounding.

The complete, newly generated VOD playlist is validated before its target
duration is set to `max(1, ceil(max(EXTINF)))`. This handles FFmpeg's zero target
for clips under half a second. It changes no `EXTINF`, sample timestamp or
segment byte. Invalid durations, paths, incomplete playlists and malformed
targets fail without a playlist write; source archives are never normalized.

The intermediate and log are generated work files, retained with their attempt
on failure. Only verified MP4/HLS assets are installed as a generated sidecar.
Fresh-archive queue admission, CPU/thread limits, live/control priority,
explicit cancellation and original source-hash checks remain unchanged.

Validation uses real PyAV muxing and `ffprobe` packet/frame timestamps. The
standalone test checks JPEG payload SHA, monotonic PTS/DTS, dimensions, unknown
input packet durations, VFR transcoding, rotation, unchanged generated JPEG
source files and overwrite/tamper rejection. Uniform 20-Hz input checks the
terminal-frame clock; uniform approximately 30-Hz input checks multiple HLS
segment boundaries. The forced 1080 pipeline test writes a synthetic native
compressed ROS bag and checks final MP4 PTS alongside every source hash,
1080/720/480/360/240 HLS assets and archive timeline offset. For every HLS
variant, `ffprobe` reads actual init+fragment bytes: sample PTS/DTS, count,
segment endpoints, `EXTINF` and assembled duration must agree. Dependency skips
are not release passes; `UBTS_REQUIRE_MEDIA_PIPELINE=1` forbids them.

Primary API references:

- [PyAV 8 remux recipe](https://pyav.org/docs/8.0.1/cookbook/basics.html#remuxing)
- [PyAV 8 containers](https://pyav.org/docs/8.0.1/api/container.html#output-containers)
- [PyAV 8 time bases](https://pyav.org/docs/8.0.1/api/time.html)
- [PyAV 8 packet API](https://pyav.org/docs/8.0.1/api/packet.html)
- [PyAV 8.0.3 packet source: public getters and PTS/DTS setters](https://github.com/PyAV-Org/PyAV/blob/v8.0.3/av/packet.pyx)

Final ARM release-image verification and deployment are separate from local
AMD64 compatibility checks. This helper does not grant any motor authority.
