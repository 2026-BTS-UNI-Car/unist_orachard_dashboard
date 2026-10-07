#!/usr/bin/env python3
"""Build derived missing LiDAR previews offline, never replay sensor topics."""
import argparse,json
from dashboard_server.lidar_preview_recovery import build
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('archive');p.add_argument('--cache-root',required=True)
a=p.parse_args();v=build(a.archive,a.cache_root)
print(json.dumps({k:v[k] for k in ('schema','complete','frames','original_files_preserved','mapping_inputs_changed')}))
