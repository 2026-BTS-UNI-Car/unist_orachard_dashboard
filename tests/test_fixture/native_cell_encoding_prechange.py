"""Frozen original encode_cells from actual tile_transport SHA3fcda3e7."""
import base64
import json
import numpy as np


def encode_cells(states):
    """Row-major states: 0 unknown, 1 free, 2 occupied, 3 observed uncertain.

    Run lengths are numeric state/count pairs. High entropy tiles use two-bit
    states instead; their base64 is raw cell data, with no image/file codec.
    """
    values = np.asarray(states, dtype=np.uint8).ravel()
    if len(values) != 4096 or np.any(values > 3):
        raise ValueError('invalid_tile_states')
    edges = np.r_[0, np.flatnonzero(values[1:] != values[:-1])+1, len(values)]
    runs = np.column_stack((values[edges[:-1]], np.diff(edges))).ravel().tolist()
    groups = values.reshape(-1, 4)
    packed = (groups[:, 0] | (groups[:, 1] << 2) | (groups[:, 2] << 4) | (groups[:, 3] << 6))
    data = base64.b64encode(packed.tobytes()).decode('ascii')
    if len(json.dumps(runs, separators=(',', ':'))) <= len(data)+2:
        return dict(encoding='rle2-v1', cells=runs)
    return dict(encoding='packed2-v1', cells=data)
