"""Read the field link log of the dashboard (runtime/link_log/*.jsonl) and show what the link grade saw.

The page writes the log only when it is opened with ?link_log=1 (static/app/04-link-grade.js, NET-10 (5)).
Each row: t (ms, the phone's clock), kind (ping | control), ok, rtt_ms, grade, recording_id, profile.
This report is the input for re-tuning the grade boundaries (LINK_GRADE.thresholdsMs) and the bench profiles
(profiles.json): see docs/WIFI_AP_CHECKLIST_KO.md. Standard library only; it reads the files and writes nothing.

usage: python link_log_report.py <file or folder> [...] [--by-recording] [--json]
"""
import argparse
import json
import sys
from pathlib import Path

# The page's own values (static/app/04-link-grade.js). A test keeps them equal.
THRESHOLDS_MS = {"good": 25, "normal": 90, "slow": 220}
WINDOW_MS = 4000
GRADES = ("good", "normal", "slow", "very_slow", "offline", "unknown")


def read_rows(paths):
    rows = []
    for path in paths:
        path = Path(path)
        files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
        for file in files:
            for line in file.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("t"), (int, float)):
                    rows.append(row)
    rows.sort(key=lambda r: r["t"])
    return rows


def pct(values, q):
    """Same as analyze.pct (nearest rank on len - 1)."""
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, max(0, int(round(q / 100 * (len(values) - 1)))))], 1)


def window_minima(rows, window_ms=WINDOW_MS, step_ms=1000):
    """What the grade looks at: the smallest answered round trip of the last window_ms, once a second."""
    answered = [(r["t"], r["rtt_ms"]) for r in rows if r.get("ok") and isinstance(r.get("rtt_ms"), (int, float))]
    if not answered:
        return []
    out, start, t = [], 0, answered[0][0] + window_ms
    end = answered[-1][0]
    while t <= end + step_ms:
        while start < len(answered) and answered[start][0] < t - window_ms:
            start += 1
        inside = [rtt for when, rtt in answered[start:] if when <= t]
        if inside:
            out.append(min(inside))
        t += step_ms
    return out


def offline_stretches(rows):
    """Stretches (s) of pings without an answer, two or more in a row (the page calls that offline)."""
    out, began, missed = [], None, 0
    for row in rows:
        if row.get("kind") != "ping":
            continue
        if row.get("ok"):
            if began is not None and missed >= 2:
                out.append(round((row["t"] - began) / 1000, 1))
            began, missed = None, 0
        else:
            if began is None:
                began = row["t"]
            missed += 1
    return out


def grade_share(rows):
    """Share of time in each grade, weighting each sample by the time until the next one (gaps over 5 s are not counted)."""
    spent = {}
    for a, b in zip(rows, rows[1:]):
        gap = b["t"] - a["t"]
        if 0 < gap <= 5000:
            spent[a.get("grade") or "unknown"] = spent.get(a.get("grade") or "unknown", 0) + gap
    total = sum(spent.values())
    return {grade: round(100 * spent[grade] / total, 1) for grade in GRADES if grade in spent} if total else {}


def report(rows):
    pings = [r for r in rows if r.get("kind") == "ping"]
    answered = [r["rtt_ms"] for r in rows if r.get("ok") and isinstance(r.get("rtt_ms"), (int, float))]
    minima = window_minima(rows)
    below = {name: round(100 * sum(1 for m in minima if m < limit) / len(minima), 1) if minima else None
             for name, limit in THRESHOLDS_MS.items()}
    stretches = offline_stretches(rows)
    return dict(
        samples=len(rows), minutes=round((rows[-1]["t"] - rows[0]["t"]) / 60000, 1) if rows else 0,
        pings=len(pings), ping_answered_pct=round(100 * sum(1 for r in pings if r.get("ok")) / len(pings), 1) if pings else None,
        rtt_ms={p: pct(answered, p) for p in (50, 90, 95, 99)} | {"max": max(answered) if answered else None},
        window_min_ms={p: pct(minima, p) for p in (10, 50, 90, 95)},
        window_min_below_pct=below,
        offline=dict(count=len(stretches), longest_s=max(stretches) if stretches else None),
        grade_time_pct=grade_share(rows),
        profiles=sorted({r.get("profile") for r in rows if r.get("profile")}),
    )


def show(name, result):
    print(f"== {name}: {result['samples']} samples, {result['minutes']} min")
    print(f"   pings answered: {result['ping_answered_pct']} % of {result['pings']}")
    print("   round trip ms: " + ", ".join(f"p{p} {v}" if p != "max" else f"max {v}" for p, v in result["rtt_ms"].items()))
    print("   smallest of 4 s (what the grade uses): " + ", ".join(f"p{p} {v}" for p, v in result["window_min_ms"].items()))
    print("   share of 4 s minima under the boundaries: "
          + ", ".join(f"{name} < {THRESHOLDS_MS[name]} ms: {v} %" for name, v in result["window_min_below_pct"].items()))
    print(f"   offline stretches: {result['offline']['count']}, longest {result['offline']['longest_s']} s")
    print("   time in each grade %: " + json.dumps(result["grade_time_pct"], ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report of the dashboard's field link log")
    parser.add_argument("paths", nargs="+", help="link_log JSONL files or the link_log folder")
    parser.add_argument("--by-recording", action="store_true", help="one report per recording_id (rows without one are 'none')")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)
    rows = read_rows(args.paths)
    if not rows:
        print("no link log rows", file=sys.stderr)
        return 2
    groups = {"all": rows}
    if args.by_recording:
        groups = {}
        for row in rows:
            groups.setdefault(row.get("recording_id") or "none", []).append(row)
    results = {name: report(group) for name, group in groups.items()}
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        for name, result in results.items():
            show(name, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
