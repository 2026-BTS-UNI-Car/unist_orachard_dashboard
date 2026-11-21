#!/usr/bin/env python3
"""How well does the shape of a reconstructed path agree with the GNSS track of the same recording?

GNSS samples of the recording (unique receiver epochs, the dashboard's own acceptance rules) become east/north metres
from the recording's first fix. The path of a run_localizer.py result is read at those times and laid over the GNSS
track by one rotation and one shift (no scale, no bending); the distances that remain are reported. A second number says
what the same steps would give if the heading were the IMU orientation alone. The GNSS accuracy of the day bounds what
this can show: with a single-point fix of several metres only bends larger than that are visible.
No latitude or longitude is printed or written; only distances in metres.

    python -B tools/map_check/gnss_fit.py <recording folder> <result.npz> [<result.npz> ...]
"""
import gzip
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dashboard_server.raw_mapping.gnss_constraints import UniqueGNSS  # noqa: E402


def gnss_track(recording):
    gnss = UniqueGNSS()
    try:
        with gzip.open(Path(recording) / 'mapping_source' / 'sensors.jsonl.gz', 'rt', encoding='utf-8') as stream:
            for line in stream:
                if '"/gps/data"' in line[:120]:
                    gnss.add(json.loads(json.loads(line)['message']['data']))
    except EOFError:
        pass
    factors = gnss.accepted
    return (np.array([f.stamp for f in factors]), np.array([f.xy for f in factors]).reshape((-1, 2)),
            np.array([f.reported_hacc_m for f in factors]))


def remaining(p, q):
    """Distances between p and q after the best rotation and shift of p."""
    pc, qc = p.mean(axis=0), q.mean(axis=0)
    u, _, vt = np.linalg.svd((p - pc).T @ (q - qc))
    r = vt.T @ u.T
    if np.linalg.det(r) < 0:
        vt[-1] *= -1
        r = vt.T @ u.T
    return np.linalg.norm((p - pc) @ r.T + qc - q, axis=1)


def heading_alone(pose, heading):
    """The same steps in the sensor frame, turned by the IMU heading instead of the map's heading."""
    yaw = np.unwrap(pose[:, 2])
    turn = np.where(np.isfinite(heading[1:]) & np.isfinite(heading[:-1]), np.diff(heading), np.diff(yaw))
    imu_yaw = yaw[0] + np.r_[0., np.cumsum(turn)]
    step = np.diff(pose[:, :2], axis=0)
    c, s = np.cos(-yaw[:-1]), np.sin(-yaw[:-1])
    body = np.column_stack((c * step[:, 0] - s * step[:, 1], s * step[:, 0] + c * step[:, 1]))
    c, s = np.cos(imu_yaw[:-1]), np.sin(imu_yaw[:-1])
    world = np.column_stack((c * body[:, 0] - s * body[:, 1], s * body[:, 0] + c * body[:, 1]))
    return np.vstack((pose[0, :2], pose[0, :2] + np.cumsum(world, axis=0))), float(np.degrees(yaw[-1] - imu_yaw[-1]))


def measure(track, path):
    stamps, xy, hacc = track
    z = np.load(path)
    keep = z['accepted']
    stamp, pose = z['stamp'][keep], z['pose'][keep]
    use = (stamps >= stamp[0]) & (stamps <= stamp[-1]) if len(stamp) else np.zeros(len(stamps), dtype=bool)
    if use.sum() < 20:
        return dict(result=str(path), reason='fewer than 20 GNSS epochs inside the run')
    at = lambda points: np.column_stack([np.interp(stamps[use], stamp, points[:, k]) for k in range(2)])  # noqa: E731
    d = remaining(at(pose[:, :2]), xy[use])
    out = dict(result=str(path), gnss_epochs=int(use.sum()), gnss_accuracy_median_m=round(float(np.median(hacc[use])), 1),
               track_span_m=round(float(np.linalg.norm(xy[use].max(axis=0) - xy[use].min(axis=0))), 0),
               rms_m=round(float(np.sqrt(np.mean(d ** 2))), 2), median_m=round(float(np.median(d)), 2), p95_m=round(float(np.percentile(d, 95)), 2))
    heading = z['heading'][keep] if 'heading' in z.files else np.full(len(stamp), np.nan)
    if np.isfinite(heading).mean() > .9:
        alone, end = heading_alone(pose, heading)
        d = remaining(at(alone), xy[use])
        out.update(imu_heading_alone_rms_m=round(float(np.sqrt(np.mean(d ** 2))), 2), map_minus_imu_heading_end_deg=round(end, 1))
    return out


if __name__ == '__main__':
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    track = gnss_track(sys.argv[1])
    for name in sys.argv[2:]:
        print(json.dumps(measure(track, name), ensure_ascii=False))
