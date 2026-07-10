#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_server.orchard_mapping import MAP_ALGORITHM_NAME, build_orchard_map


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Precompute versioned orchard maps so archive playback never blocks on scan matching.")
    parser.add_argument("--archive-dir", default=os.environ.get("RECORDING_ARCHIVE_DIR", str(ROOT / "runtime" / "recordings")))
    parser.add_argument("--analysis-dir", default="")
    parser.add_argument("--recording-id", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    archive_dir = Path(args.archive_dir)
    analysis_dir = Path(args.analysis_dir) if args.analysis_dir else archive_dir.parent / "orchard_analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    recordings = [archive_dir / value for value in args.recording_id] if args.recording_id else sorted(path for path in archive_dir.iterdir() if path.is_dir())
    results = []
    for recording in recordings:
        timeline_path = recording / "timeline.json"
        cache_path = analysis_dir / f"{recording.name}_orchard_map.json"
        started = time.time()
        if not timeline_path.exists():
            results.append({"id": recording.name, "ok": False, "error": "timeline_not_found"})
            continue
        if not args.force and current_cache(cache_path, timeline_path):
            payload = load_json(cache_path)
            results.append(summary(recording.name, payload.get("map") or {}, time.time() - started, cached=True))
            continue
        timeline = load_json(timeline_path)
        if not timeline:
            results.append({"id": recording.name, "ok": False, "error": "invalid_timeline"})
            continue
        orchard_map = build_orchard_map(timeline)
        payload = {"ok": True, "id": recording.name, "map": orchard_map}
        write_json_atomic(cache_path, payload)
        results.append(summary(recording.name, orchard_map, time.time() - started, cached=False))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    output = {"ok": all(item.get("ok") for item in results), "algorithm": MAP_ALGORITHM_NAME, "recordings": results}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["ok"] else 1


def current_cache(cache_path: Path, timeline_path: Path) -> bool:
    if not cache_path.exists() or cache_path.stat().st_mtime < timeline_path.stat().st_mtime:
        return False
    payload = load_json(cache_path)
    return bool(payload.get("ok") and payload.get("map", {}).get("algorithm") == MAP_ALGORITHM_NAME)


def summary(recording_id: str, orchard_map: dict[str, Any], elapsed_s: float, cached: bool) -> dict[str, Any]:
    return {
        "id": recording_id,
        "ok": bool(orchard_map.get("ok")),
        "cached": cached,
        "elapsed_s": round(elapsed_s, 2),
        "path_points": len(orchard_map.get("path") or []),
        "tree_count": int(orchard_map.get("tree_count") or 0),
        "layer_counts": {key: len(value.get("trees") or []) for key, value in (orchard_map.get("maps") or {}).items()},
        "odometry": orchard_map.get("odometry") or {},
        "lidar_ood": orchard_map.get("lidar_ood") or {},
    }


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
