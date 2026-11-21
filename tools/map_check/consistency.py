#!/usr/bin/env python3
"""How well does a reconstructed path make repeated observations fall on each other? (no ground truth needed)

Input: results of run_localizer.py. For every fifth accepted scan the points (every 8th beam, up to 12 m) are placed with
the localizer's pose and compared with the points of the scans taken 4 to 12 s earlier, placed with their poses: the
distance to the nearest earlier point, cut at 0.3 m (points that were not visible earlier), is averaged. A second number
counts the 5 cm cells that all points of the recording occupy: for the same scans, fewer cells means a sharper map.
Use it to compare two versions of the localizer on the same recordings; the numbers of one version alone say little.

    python -B tools/map_check/consistency.py <result.npz> [<result.npz> ...]
"""
import json
import sys

import numpy as np
from scipy.spatial import cKDTree


def placed_points(z):
    keep = z['eligible']
    pose = z['pose'][keep]
    beams = z['beams'][keep].astype(np.float32)
    amin, inc = z['geometry']
    beams[beams > 12.] = np.nan
    angles = amin + inc * np.arange(beams.shape[1])
    c, s = np.cos(pose[:, 2])[:, None], np.sin(pose[:, 2])[:, None]
    lx, ly = beams * np.cos(angles)[None, :], beams * np.sin(angles)[None, :]
    return z['stamp'][keep], pose, pose[:, 0][:, None] + c * lx - s * ly, pose[:, 1][:, None] + s * lx + c * ly


def measure(path):
    z = np.load(path)
    if z['beams'].size == 0 or not z['eligible'].any():
        return None
    stamp, pose, wx, wy = placed_points(z)
    ok = np.isfinite(wx)
    total, near, count = 0., 0., 0
    for i in range(0, len(stamp), 5):
        lo, hi = np.searchsorted(stamp, [stamp[i] - 12., stamp[i] - 4.])
        if hi - lo < 10:
            continue
        idx = np.arange(lo, hi, 3)
        earlier = np.column_stack((wx[idx][ok[idx]], wy[idx][ok[idx]]))
        now = np.column_stack((wx[i][ok[i]], wy[i][ok[i]]))
        if len(earlier) < 1000 or len(now) < 50:
            continue
        d, _ = cKDTree(earlier).query(now, workers=1)
        total += float(np.minimum(d, .3).sum())
        near += float((d < .05).sum())
        count += len(d)
    cells = np.unique(np.floor(np.column_stack((wx[ok], wy[ok])) / .05).astype(np.int64), axis=0)
    step = np.hypot(*np.diff(pose[:, :2], axis=0).T) if len(pose) > 1 else np.zeros(1)
    return dict(result=str(path), accepted_scans=int(z['eligible'].sum()), scans=int(len(z['eligible'])),
                mean_nearest_cm=round(100 * total / max(count, 1), 2), share_within_5cm=round(near / max(count, 1), 3),
                cells_5cm=int(len(cells)), path_m=round(float(step.sum()), 1),
                turned_deg=round(float(np.degrees(np.abs(np.diff(np.unwrap(pose[:, 2]))).sum())), 0) if len(pose) > 1 else 0.)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for name in sys.argv[1:]:
        print(json.dumps(measure(name), ensure_ascii=False))
