"""실행 결과를 고정 규칙과 기준선에 견주어 통과/실패를 정한다.

    python compare.py --run <이번 결과 JSON> --baseline <기준선 JSON>

끝내는 값: 0 = 통과, 1 = 실패 항목 있음, 2 = 파일이나 인자가 잘못됨.
- 고정 규칙(rules.json의 fixed_rules): 수치가 정해진 조건을 지키는지.
- 기준선 비교(rules.json의 regression): 같은 쪽의 수치가 기준선보다 나빠졌는지.
mode가 "report"인 항목은 알리기만 하고 실패로 세지 않는다. --strict를 주면 그것도 실패로 센다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))

import rules_engine  # noqa: E402
import summarize  # noqa: E402


def load_rules(path: Path) -> dict:
    rules = json.loads(path.read_text(encoding="utf-8"))
    rules_engine.validate_rules(rules)
    return rules


def group_counts(rows: list) -> list:
    """같은 규칙끼리 묶어 '규칙 · 건수' 줄을 만든다."""
    counts: dict = {}
    for row in rows:
        key = row.get("rule_id") or row["kind"]
        entry = counts.setdefault(key, {"title": row.get("title_ko", ""), "count": 0, "pages": set()})
        entry["count"] += 1
        entry["pages"].add(row.get("page_key", ""))
    return [f"{key}({entry['title']}): {entry['count']}건, {len(entry['pages'])}쪽" for key, entry in sorted(counts.items())]


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="화면 점검 결과를 규칙과 기준선에 견준다")
    parser.add_argument("--run", required=True, type=Path, help="이번 실행 결과 JSON")
    parser.add_argument("--baseline", type=Path, help="기준선 JSON. 주지 않으면 고정 규칙만 본다")
    parser.add_argument("--rules", type=Path, default=TOOL_DIR / "rules.json", help="규칙 파일")
    parser.add_argument("--strict", action="store_true", help="알리기만 하는(report) 항목도 실패로 센다")
    parser.add_argument("--verbose", action="store_true", help="알리기만 하는 항목도 한 건씩 보여 준다")
    parser.add_argument("--max-lines", type=int, default=60, help="한 건씩 보여 줄 최대 줄 수")
    parser.add_argument("--json", type=Path, help="판정 결과를 이 JSON 파일에도 쓴다")
    args = parser.parse_args(argv)

    try:
        run = summarize.load_run(args.run)
        baseline = summarize.load_run(args.baseline) if args.baseline else None
        rules = load_rules(args.rules)
    except (OSError, ValueError) as error:
        print(f"읽지 못했습니다: {error}", file=sys.stderr)
        return 2

    violations = rules_engine.load_error_violations(run["pages"], rules)
    violations += rules_engine.evaluate_fixed_rules(run["pages"], rules)
    if baseline is not None:
        violations += rules_engine.compare_with_baseline(run["pages"], baseline["pages"], rules)
    if args.strict:
        violations = rules_engine.apply_strict(violations)
    failures = rules_engine.failing(violations)
    reports = [row for row in violations if row["mode"] != "fail"]

    print(f"이번 결과: {args.run} ({len(run['pages'])}쪽)")
    if baseline is not None:
        print(f"기준선: {args.baseline} ({len(baseline['pages'])}쪽)")
    print(f"규칙: {args.rules}" + (" · --strict" if args.strict else ""))
    if failures:
        print(f"실패 {len(failures)}건:")
        for line in group_counts(failures):
            print(f"  = {line}")
        for row in failures[: args.max_lines]:
            print("  " + rules_engine.describe(row))
        if len(failures) > args.max_lines:
            print(f"  … 외 {len(failures) - args.max_lines}건")
    if reports:
        print(f"참고(실패로 세지 않음) {len(reports)}건:")
        for line in group_counts(reports):
            print(f"  = {line}")
        if args.verbose:
            for row in reports[: args.max_lines]:
                print("  " + rules_engine.describe(row))
            if len(reports) > args.max_lines:
                print(f"  … 외 {len(reports) - args.max_lines}건")
    print("판정: " + ("실패" if failures else "통과"))

    if args.json:
        result = {
            "schema": "ubts.ui_check.compare",
            "schema_version": 1,
            "run": str(args.run),
            "baseline": str(args.baseline) if args.baseline else None,
            "rules": str(args.rules),
            "strict": args.strict,
            "passed": not failures,
            "failures": failures,
            "reports": reports,
        }
        args.json.write_bytes((json.dumps(result, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
