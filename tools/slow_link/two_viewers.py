#!/usr/bin/env python3
"""Does a slow second viewer slow the first one down? (NET-11 (6), docs/slow_link.md 14)

  python -B two_viewers.py --alone <"drive" run folder> --pair <"two_viewers" run folder> [--json <file>]

Both runs have the same profile for the first client. The first client's numbers of the pair run are compared with
the run where it was alone: a "higher is better" number may be at most 10 % lower, a "lower is better" one at most
10 % higher (plus a small absolute allowance for numbers near zero). The second (slow) client's numbers are shown for
reference. Exit value 0 = every compared number within the limit, 1 = one or more outside, 2 = a run cannot be used.
Standard library only.
"""
import argparse
import json
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))

import summarize  # noqa: E402

LIMIT = 0.10
# (key, Korean label, unit, better, absolute allowance)
COMPARED = [
    ("camera_fps", "카메라 fps", "fps", "higher", 0.2),
    ("camera_delay_p95", "카메라 지연 p95", "ms", "lower", 30),
    ("lidar_hz", "LiDAR 다시 그리기", "Hz", "higher", 0.2),
    ("lidar_delay_p95", "LiDAR 지연 p95", "ms", "lower", 30),
    ("command_rt_p50", "조종 명령 왕복 p50", "ms", "lower", 10),
    ("command_rt_p95", "조종 명령 왕복 p95", "ms", "lower", 15),
    ("stop_endpoint_p50", "STOP 버튼→제어 끝점 p50", "ms", "lower", 8),
]
PEER = [("camera_fps", "camera", "rate_hz"), ("camera_delay_p95", "camera", "p95"), ("lidar_hz", "lidar", "rate_hz"),
        ("lidar_delay_p95", "lidar", "p95")]


def read_summary(folder, scenario):
    path = Path(folder) / "SUMMARY.json"
    if not path.is_file():
        raise SystemExit(f"no SUMMARY.json in {folder}")
    summary = json.loads(path.read_text(encoding="utf-8"))
    run = summary.get("run") or {}
    if run.get("scenario") != scenario:
        raise SystemExit(f"{folder}: scenario {run.get('scenario')!r}, expected {scenario!r}")
    if run.get("failed") or summary.get("error"):
        raise SystemExit(f"{folder}: the run failed ({run.get('failed') or summary.get('error')})")
    return summary


def judge(better, alone, pair, allowance):
    if alone is None or pair is None:
        return "missing"
    if better == "higher":
        return "ok" if pair >= alone * (1 - LIMIT) - allowance else "slower"
    return "ok" if pair <= alone * (1 + LIMIT) + allowance else "slower"


def compare(alone_summary, pair_summary):
    alone_profile = (alone_summary.get("run") or {}).get("profile")
    pair_run = pair_summary.get("run") or {}
    if alone_profile != pair_run.get("profile"):
        raise SystemExit(f"profiles differ: alone {alone_profile}, pair {pair_run.get('profile')}")
    alone_row, pair_row = summarize.row_from_summary(alone_summary), summarize.row_from_summary(pair_summary)
    rows = []
    for key, label, unit, better, allowance in COMPARED:
        a, b = alone_row.get(key), pair_row.get(key)
        rows.append(dict(key=key, label_ko=label, unit=unit, better=better, alone=a, pair=b,
                         ratio=round(b / a, 3) if a and b is not None else None, verdict=judge(better, a, b, allowance)))
    clients = ((pair_summary.get("live") or {}).get("clients") or [])
    peer = clients[1] if len(clients) > 1 else {}
    render = peer.get("render_delay_ms") or {}
    peer_values = {key: (render.get(channel) or {}).get(field) for key, channel, field in PEER}
    return dict(profile=alone_profile, peer_profile=(pair_run.get("client_profiles") or [None, None])[1], rows=rows, peer=peer_values,
                host_paused=dict(alone=bool(alone_summary.get("host_pauses")), pair=bool(pair_summary.get("host_pauses"))),
                within_limit=all(row["verdict"] == "ok" for row in rows))


def render(result):
    lines = [f"첫 화면 프로필 {result['profile']} · 둘째 화면 {result['peer_profile']} · 허용 {int(LIMIT * 100)} % (+ 작은 절대값)"]
    for row in result["rows"]:
        lines.append(f"  {row['label_ko']}: 혼자 {row['alone']} {row['unit']} · 둘 {row['pair']} {row['unit']} · 비 {row['ratio']} · {row['verdict']}")
    lines.append("  둘째(느린) 화면: " + ", ".join(f"{key} {value}" for key, value in result["peer"].items()))
    if any(result["host_paused"].values()):
        lines.append(f"  PC 멈춤 표시: {result['host_paused']} (그 실행의 꼬리 값은 판정에 조심)")
    lines.append("판정: " + ("통과" if result["within_limit"] else "벗어남"))
    return "\n".join(lines)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--alone", required=True, help='run folder of scenario "drive" (one viewer)')
    parser.add_argument("--pair", required=True, help='run folder of scenario "two_viewers" with the same profile')
    parser.add_argument("--json", help="also write the result to this file")
    args = parser.parse_args(argv)
    try:
        result = compare(read_summary(args.alone, "drive"), read_summary(args.pair, "two_viewers"))
    except SystemExit as error:
        print(error, file=sys.stderr)
        return 2
    print(render(result))
    if args.json:
        Path(args.json).write_bytes((json.dumps(result, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return 0 if result["within_limit"] else 1


if __name__ == "__main__":
    sys.exit(main())
