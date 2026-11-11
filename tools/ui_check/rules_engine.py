"""규칙 판정과 기준선 비교(순수 함수).

- 고정 규칙: 어떤 수치가 정해진 조건을 지키는지 본다(예: 가로 넘침은 0).
- 기준선 비교: 같은 쪽(자료 묶음 · 경로 · 화면 크기)의 수치가 기준선보다 나빠졌는지 본다.
규칙마다 mode가 "fail"이면 어기면 실패이고, "report"이면 알리기만 한다.
"""
from __future__ import annotations

from fnmatch import fnmatchcase

RULES_SCHEMA = "ubts.ui_check.rules"
RULES_SCHEMA_VERSION = 1
MODES = ("fail", "report")
OPERATORS = ("<=", "<", "==", ">=", ">", "is_true", "is_false")


class RulesError(ValueError):
    """규칙 파일이나 실행 결과 파일의 형식이 맞지 않을 때."""


def validate_rules(rules: dict) -> None:
    if rules.get("schema") != RULES_SCHEMA or rules.get("schema_version") != RULES_SCHEMA_VERSION:
        raise RulesError(f"rules schema must be {RULES_SCHEMA} v{RULES_SCHEMA_VERSION}")
    seen = set()
    for rule in rules.get("fixed_rules", []):
        for field in ("id", "metric", "op", "mode"):
            if field not in rule:
                raise RulesError(f"fixed rule is missing '{field}': {rule}")
        if rule["id"] in seen:
            raise RulesError(f"duplicate rule id: {rule['id']}")
        seen.add(rule["id"])
        if rule["op"] not in OPERATORS:
            raise RulesError(f"unknown operator in rule {rule['id']}: {rule['op']}")
        if rule["mode"] not in MODES:
            raise RulesError(f"unknown mode in rule {rule['id']}: {rule['mode']}")
        if rule["op"] not in ("is_true", "is_false") and "value" not in rule:
            raise RulesError(f"rule {rule['id']} needs 'value'")
    regression = rules.get("regression", {})
    for name, spec in regression.get("metrics", {}).items():
        if spec.get("better") not in ("lower", "higher", "true"):
            raise RulesError(f"regression metric {name}: 'better' must be lower, higher or true")
        if spec.get("mode") not in MODES:
            raise RulesError(f"regression metric {name}: unknown mode {spec.get('mode')}")
    for field in ("missing_page_mode", "new_page_mode", "load_error_mode"):
        if regression.get(field, "fail") not in MODES:
            raise RulesError(f"regression.{field} must be fail or report")


def _matches(value: str, patterns) -> bool:
    if not patterns:
        return True
    return any(fnmatchcase(value, pattern) for pattern in patterns)


def page_in_scope(page: dict, rule: dict) -> bool:
    """규칙의 routes / viewports / datasets 조건에 이 쪽이 들어가는지. 비어 있으면 모두 해당."""
    return (
        _matches(page["route"], rule.get("routes"))
        and _matches(page["viewport"]["name"], rule.get("viewports"))
        and _matches(page["dataset"], rule.get("datasets"))
    )


def condition_holds(value, op: str, expected=None):
    """조건을 지키면 True, 어기면 False, 값이 없어 판단할 수 없으면 None."""
    if op == "is_true":
        return bool(value)
    if op == "is_false":
        return not bool(value)
    if value is None:
        return None
    if op == "<=":
        return value <= expected
    if op == "<":
        return value < expected
    if op == "==":
        return value == expected
    if op == ">=":
        return value >= expected
    if op == ">":
        return value > expected
    raise RulesError(f"unknown operator: {op}")


def _violation(kind: str, mode: str, page: dict | None, **fields) -> dict:
    row = {"kind": kind, "mode": mode}
    if page is not None:
        row.update(page_key=page["key"], dataset=page["dataset"], route=page["route"], viewport=page["viewport"]["name"])
    row.update(fields)
    return row


def evaluate_fixed_rules(pages: list, rules: dict) -> list:
    violations = []
    for rule in rules.get("fixed_rules", []):
        for page in pages:
            metrics = page.get("metrics")
            if not metrics or not page_in_scope(page, rule):
                continue
            value = metrics.get(rule["metric"])
            holds = condition_holds(value, rule["op"], rule.get("value"))
            if holds is None:
                holds = rule.get("on_missing", "skip") != "violation"
            if not holds:
                violations.append(
                    _violation(
                        "rule",
                        rule["mode"],
                        page,
                        rule_id=rule["id"],
                        title_ko=rule.get("title_ko", rule["id"]),
                        metric=rule["metric"],
                        value=value,
                        op=rule["op"],
                        expected=rule.get("value"),
                    )
                )
    return violations


def load_error_violations(pages: list, rules: dict) -> list:
    """열지 못했거나 재지 못한 쪽. 기준선이 없어도 항상 본다."""
    mode = rules.get("regression", {}).get("load_error_mode", "fail")
    return [
        _violation("load_error", mode, page, title_ko="쪽을 열거나 재지 못함", value=str(page.get("error"))[:200])
        for page in pages
        if page.get("error") or not page.get("metrics")
    ]


def worse_than_baseline(spec: dict, baseline_value, value) -> bool:
    """기준선보다 나빠졌는지. 한쪽 값이 없으면 비교하지 않는다(True가 좋은 값인 경우는 예외)."""
    better = spec["better"]
    if better == "true":
        return bool(baseline_value) and not bool(value)
    if baseline_value is None or value is None:
        return False
    allowance = max(float(spec.get("tolerance", 0)), abs(float(baseline_value)) * float(spec.get("tolerance_ratio", 0)))
    if better == "lower":
        return value > baseline_value + allowance
    return value < baseline_value - allowance


def compare_with_baseline(pages: list, baseline_pages: list, rules: dict) -> list:
    regression = rules.get("regression", {})
    specs = regression.get("metrics", {})
    current = {page["key"]: page for page in pages}
    previous = {page["key"]: page for page in baseline_pages}
    violations = []
    for key, old in previous.items():
        if key not in current:
            violations.append(_violation("missing_page", regression.get("missing_page_mode", "fail"), old, title_ko="기준선에 있던 쪽이 이번 실행에 없음"))
    for key, page in current.items():
        if page.get("error") or not page.get("metrics"):
            continue  # load_error_violations가 따로 센다.
        old = previous.get(key)
        if old is None:
            violations.append(_violation("new_page", regression.get("new_page_mode", "report"), page, title_ko="기준선에 없는 새 쪽"))
            continue
        if not old.get("metrics"):
            continue
        for name, spec in specs.items():
            value, base = page["metrics"].get(name), old["metrics"].get(name)
            if worse_than_baseline(spec, base, value):
                violations.append(
                    _violation(
                        "regression",
                        spec["mode"],
                        page,
                        rule_id=f"baseline:{name}",
                        title_ko=spec.get("title_ko", name),
                        metric=name,
                        value=value,
                        baseline=base,
                    )
                )
    return violations


def apply_strict(violations: list) -> list:
    """--strict: 알리기만 하던 항목도 실패로 센다."""
    return [{**row, "mode": "fail"} for row in violations]


def failing(violations: list) -> list:
    return [row for row in violations if row["mode"] == "fail"]


def describe(row: dict) -> str:
    """위반 한 건을 한 줄로 적는다."""
    where = " ".join(str(row[name]) for name in ("dataset", "route", "viewport") if name in row)
    mark = "실패" if row["mode"] == "fail" else "참고"
    title = row.get("title_ko", "")
    if row["kind"] == "rule":
        expected = "" if row["op"] in ("is_true", "is_false") else f" {row['expected']}"
        return f"[{mark}] {where} · 규칙 {row['rule_id']}({title}): 값 {row['value']} · 조건 {row['op']}{expected}"
    if row["kind"] == "regression":
        return f"[{mark}] {where} · 기준선보다 나빠짐 {row['metric']}({title}): {row['baseline']} → {row['value']}"
    if row["kind"] == "load_error":
        return f"[{mark}] {where} · {title}: {row.get('value', '')}"
    return f"[{mark}] {where} · {title}"
