# Browser-rendered occupancy grid

The source-rate live mapper and integrated archive mapper send metric coordinates
and occupancy states. They do not render or transmit PNGs for these views. The
browser creates cached Canvas tiles from numeric cells and draws visible tiles,
the rover pose and the fading red path. Camera video remains a separate stream.

`grid.encoding = tiles-cells-v1`, `tile_size = 64`. A tile `(x,y)` identifies a
64 by 64 block. For cell index `i`, its lower-left world corner is
`((64*x + i%64)*resolution, (64*y + floor(i/64))*resolution)`.
The first row is minimum world Y, including negative tile coordinates.

States are `0` unobserved, `1` free, `2` occupied, `3` observed but uncertain.
The native log-odds checkpoint retains all confidence values; this classification
is a compact display layer and has no localization or motor-control authority.

Each tile contains either numeric `rle2-v1` state/count pairs in `cells`, or
`packed2-v1` raw two-bit states encoded as base64. In a packed byte the first
cell occupies bits 0–1, the next bits 2–3, then 4–5 and 6–7. No image encoding,
image decoding or texture asset download is involved. HTTP gzip remains enabled.

The first request, a new epoch, or backward seek supplies a full tile set. Later
requests specify `tile_epoch` and `tile_since`; only changed blocks are sent,
as complete replacements, so dropped polls need no fragile patch chain. Unchanged
blocks remain in the client cache. Pose and path updates share the accepted map
revision. Short live gaps use path increments; older clients or a compacted path
receive its complete retained history. A GNSS raster rebuild changes tile epoch
but preserves view epoch and the user's manual metric zoom/pan.

Recorded map windows buffer cell events ahead of playback. They apply events only
up to the media clock, and reload a checkpoint when seeking backward or changing
isolated submaps. Earlier PNG archives and saved maps without native cells remain
read-only compatible; they are not advertised as recovered numeric cells. PNG
exports may still be produced for downloads, independently of the live renderer.
