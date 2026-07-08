#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_server.orchard_mapping import build_orchard_map


def main() -> int:
    parser = argparse.ArgumentParser(description="Build orchard map analysis JSON for recorded dashboard archives.")
    parser.add_argument("recording_ids", nargs="+", help="Recording ids such as 20260708T053333Z_both")
    parser.add_argument("--archive-dir", default="/app/runtime/recordings")
    parser.add_argument("--output-dir", default="/app/runtime/orchard_analysis")
    args = parser.parse_args()

    archive_dir = Path(args.archive_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for recording_id in args.recording_ids:
        root = archive_dir / safe_id(recording_id)
        timeline_path = root / "timeline.json"
        if not timeline_path.exists():
            summaries.append({"id": recording_id, "ok": False, "error": "timeline_not_found", "path": str(timeline_path)})
            continue
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        orchard_map = build_orchard_map(timeline)
        out_path = output_dir / f"{safe_id(recording_id)}_orchard_map.json"
        out_path.write_text(json.dumps({"ok": True, "id": recording_id, "map": orchard_map}, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append(
            {
                "id": recording_id,
                "ok": True,
                "output": str(out_path),
                "tree_count": orchard_map.get("tree_count"),
                "path_points": len(orchard_map.get("path") or []),
                "camera_frames_analyzed": orchard_map.get("camera", {}).get("frames_analyzed"),
            }
        )
    print(json.dumps({"ok": all(item.get("ok") for item in summaries), "results": summaries}, ensure_ascii=False, indent=2))
    return 0 if all(item.get("ok") for item in summaries) else 1


def safe_id(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum() or ch in "_.-")


if __name__ == "__main__":
    raise SystemExit(main())
