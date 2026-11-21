#!/usr/bin/env python3
"""Run the grid map's localizer over one recording, exactly as the recording player feeds it, and keep the result.

Reads <recording>/mapping_source/sensors.jsonl.gz (LaserScan, raw IMU, GNSS rows in host receipt order) and
<recording>/metadata.json. Writes one .npz with, per scan: recording time, sensor stamp, accepted or not, the kind of fit,
the pose (x, y, yaw of the LiDAR in the map frame), the fit residual, the processing time, the IMU heading read for the
sweep, and every 8th beam of the scan (for consistency.py). Nothing of the GNSS position is stored.

    python -B tools/map_check/run_localizer.py <recording folder> <out.npz>

Needs numpy and scipy (the dashboard's own requirements). The recording is only read.
"""
import gzip
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dashboard_server.playback_mapping import SourceCursorLocalizer  # noqa: E402
from dashboard_server.raw_mapping.raw_localization import source_stamp  # noqa: E402

BEAM_STEP = 8


def rows(path):
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                yield json.loads(line)
    except EOFError:
        return          # a recording cut off by a power loss: use what is there


def run(recording, out):
    recording = Path(recording)
    start = datetime.fromisoformat(json.loads((recording / 'metadata.json').read_bytes().decode('utf-8'))['started_at']).timestamp()
    localizer = SourceCursorLocalizer()
    engine = localizer.engine
    batch, columns = [], dict(t_s=[], stamp=[], accepted=[], eligible=[], kind=[], pose=[], rmse=[], cost_ms=[], heading=[], beams=[])
    geometry = (0., 0.)
    began = time.perf_counter()
    for row in rows(recording / 'mapping_source' / 'sensors.jsonl.gz'):
        batch.append(row)
        if row['topic'] != '/scan':
            continue
        tick = time.perf_counter()
        result, _, _ = localizer.consume(batch)
        columns['cost_ms'].append((time.perf_counter() - tick) * 1000)
        batch = []
        message = row['message']
        stamp = source_stamp(message)
        columns['t_s'].append(max(0., row['bag_time_ns'] / 1e9 - start))
        columns['stamp'].append(stamp)
        columns['accepted'].append(bool(result['accepted']))
        columns['eligible'].append(bool(result['accepted'] and result.get('map_eligible', True)))
        columns['kind'].append(str(result['reason']))
        columns['pose'].append(result['pose'] if result['accepted'] else engine.pose)
        columns['rmse'].append(float(result.get('fit_rmse_m') or 0.) if result['accepted'] else np.nan)
        # (code from before 2026-11-21 has no IMU heading: the column stays empty there)
        now = getattr(engine, '_scan_heading', None) if getattr(engine, '_scan_heading_for', None) == stamp else None
        columns['heading'].append(now[0] if now is not None else np.nan)
        values = np.array([v if v is not None else np.nan for v in message['ranges'][::BEAM_STEP]], dtype=np.float32)
        values[~np.isfinite(values) | (values < message['range_min']) | (values >= min(20., message['range_max']))] = np.nan
        columns['beams'].append(values.astype(np.float16))
        geometry = (float(message['angle_min']), float(message['angle_increment']) * BEAM_STEP)
    same = len({len(b) for b in columns['beams']}) == 1
    np.savez_compressed(out, geometry=np.asarray(geometry), pose=np.asarray(columns.pop('pose'), dtype=float).reshape((-1, 3)),
                        beams=np.asarray(columns.pop('beams')) if same else np.zeros((0, 0), dtype=np.float16),
                        **{name: np.asarray(values) for name, values in columns.items()})
    kinds = {}
    for value in columns['kind']:
        kinds[value] = kinds.get(value, 0) + 1
    cost = np.asarray(columns['cost_ms']) if columns['cost_ms'] else np.zeros(1)
    return dict(recording=recording.name, scans=len(columns['t_s']), accepted=int(np.sum(columns['accepted'])),
                heading_from_orientation=int(np.isfinite(columns['heading']).sum()), kinds=kinds,
                wall_s=round(time.perf_counter() - began, 1), median_ms=round(float(np.median(cost)), 1), p95_ms=round(float(np.percentile(cost, 95)), 1))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(json.dumps(run(sys.argv[1], sys.argv[2]), ensure_ascii=False))
