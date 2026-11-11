"""규칙 판정과 기준선 비교 시험."""
import json
import sys
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import rules_engine  # noqa: E402


def page(route="/rover-control", viewport="390x844", dataset="stress", **metrics):
    base = {
        "horizontal_overflow_px": 0,
        "overlaps": 0,
        "small_targets": 0,
        "stop_in_first_viewport": True,
        "stop_top_px": 100,
        "page_height_px": 1000,
        "console_errors": 0,
    }
    base.update(metrics)
    return {"key": f"{dataset}|{route}|{viewport}", "dataset": dataset, "route": route, "viewport": {"name": viewport}, "metrics": base}


RULES = {
    "schema": rules_engine.RULES_SCHEMA,
    "schema_version": rules_engine.RULES_SCHEMA_VERSION,
    "fixed_rules": [
        {"id": "no-horizontal-overflow", "title_ko": "가로 넘침 없음", "metric": "horizontal_overflow_px", "op": "<=", "value": 0, "mode": "fail"},
        {"id": "touch-44", "title_ko": "누름 영역", "metric": "small_targets", "op": "<=", "value": 0, "mode": "report"},
        {"id": "stop-first", "title_ko": "STOP 첫 화면", "metric": "stop_in_first_viewport", "op": "is_true", "routes": ["/rover-control", "/drive*"], "viewports": ["390x*"], "mode": "fail"},
    ],
    "regression": {
        "missing_page_mode": "fail",
        "new_page_mode": "report",
        "metrics": {
            "overlaps": {"better": "lower", "tolerance": 0, "mode": "fail"},
            "page_height_px": {"better": "lower", "tolerance_ratio": 0.1, "mode": "report"},
            "stop_in_first_viewport": {"better": "true", "mode": "fail"},
            "stop_top_px": {"better": "lower", "tolerance": 4, "mode": "report"},
        },
    },
}


class ConditionTest(unittest.TestCase):
    def test_numeric_operators(self):
        self.assertTrue(rules_engine.condition_holds(0, "<=", 0))
        self.assertFalse(rules_engine.condition_holds(21, "<=", 0))
        self.assertTrue(rules_engine.condition_holds(3, "<", 4))
        self.assertTrue(rules_engine.condition_holds(4, ">=", 4))
        self.assertFalse(rules_engine.condition_holds(4, ">", 4))
        self.assertTrue(rules_engine.condition_holds(4, "==", 4))

    def test_boolean_operators(self):
        self.assertTrue(rules_engine.condition_holds(True, "is_true"))
        self.assertFalse(rules_engine.condition_holds(None, "is_true"))
        self.assertTrue(rules_engine.condition_holds(False, "is_false"))

    def test_missing_numeric_value_is_undecided(self):
        self.assertIsNone(rules_engine.condition_holds(None, "<=", 0))

    def test_unknown_operator(self):
        with self.assertRaises(rules_engine.RulesError):
            rules_engine.condition_holds(1, "~", 1)


class ScopeTest(unittest.TestCase):
    def test_empty_scope_matches_everything(self):
        self.assertTrue(rules_engine.page_in_scope(page(), {}))

    def test_route_viewport_dataset_patterns(self):
        rule = {"routes": ["/rover-control", "/drive*"], "viewports": ["390x*"], "datasets": ["stress"]}
        self.assertTrue(rules_engine.page_in_scope(page(), rule))
        self.assertTrue(rules_engine.page_in_scope(page(route="/drive/manual"), rule))
        self.assertFalse(rules_engine.page_in_scope(page(route="/system"), rule))
        self.assertFalse(rules_engine.page_in_scope(page(viewport="1440x900"), rule))
        self.assertFalse(rules_engine.page_in_scope(page(dataset="empty"), rule))


class FixedRuleTest(unittest.TestCase):
    def test_clean_page_has_no_violation(self):
        self.assertEqual(rules_engine.evaluate_fixed_rules([page()], RULES), [])

    def test_fail_and_report_modes(self):
        rows = rules_engine.evaluate_fixed_rules([page(horizontal_overflow_px=21, small_targets=46)], RULES)
        self.assertEqual({row["rule_id"]: row["mode"] for row in rows}, {"no-horizontal-overflow": "fail", "touch-44": "report"})
        self.assertEqual(len(rules_engine.failing(rows)), 1)
        self.assertEqual(len(rules_engine.failing(rules_engine.apply_strict(rows))), 2)

    def test_scoped_rule_only_applies_to_listed_pages(self):
        pages = [page(stop_in_first_viewport=False), page(route="/system", stop_in_first_viewport=False), page(viewport="1440x900", stop_in_first_viewport=False)]
        rows = rules_engine.evaluate_fixed_rules(pages, RULES)
        self.assertEqual([row["page_key"] for row in rows], ["stress|/rover-control|390x844"])

    def test_missing_value_is_skipped_unless_rule_says_otherwise(self):
        rules = {**RULES, "fixed_rules": [{"id": "stop-top", "metric": "stop_top_px", "op": "<=", "value": 800, "mode": "fail"}]}
        self.assertEqual(rules_engine.evaluate_fixed_rules([page(stop_top_px=None)], rules), [])
        rules["fixed_rules"][0]["on_missing"] = "violation"
        self.assertEqual(len(rules_engine.evaluate_fixed_rules([page(stop_top_px=None)], rules)), 1)

    def test_describe_names_page_and_rule(self):
        row = rules_engine.evaluate_fixed_rules([page(horizontal_overflow_px=21)], RULES)[0]
        line = rules_engine.describe(row)
        for part in ("실패", "stress", "/rover-control", "390x844", "no-horizontal-overflow", "21"):
            self.assertIn(part, line)


class BaselineTest(unittest.TestCase):
    def test_same_run_passes(self):
        self.assertEqual(rules_engine.compare_with_baseline([page()], [page()], RULES), [])

    def test_lower_is_better(self):
        spec = {"better": "lower", "tolerance": 1}
        self.assertFalse(rules_engine.worse_than_baseline(spec, 10, 11))
        self.assertTrue(rules_engine.worse_than_baseline(spec, 10, 12))
        self.assertFalse(rules_engine.worse_than_baseline(spec, 10, 3))

    def test_relative_tolerance(self):
        spec = {"better": "lower", "tolerance_ratio": 0.1}
        self.assertFalse(rules_engine.worse_than_baseline(spec, 1000, 1100))
        self.assertTrue(rules_engine.worse_than_baseline(spec, 1000, 1101))

    def test_higher_and_true_are_better(self):
        self.assertTrue(rules_engine.worse_than_baseline({"better": "higher"}, 5, 4))
        self.assertTrue(rules_engine.worse_than_baseline({"better": "true"}, True, False))
        self.assertFalse(rules_engine.worse_than_baseline({"better": "true"}, False, False))
        self.assertFalse(rules_engine.worse_than_baseline({"better": "true"}, False, True))

    def test_missing_numbers_are_not_compared(self):
        self.assertFalse(rules_engine.worse_than_baseline({"better": "lower"}, None, 5))
        self.assertFalse(rules_engine.worse_than_baseline({"better": "lower"}, 5, None))

    def test_regression_is_reported_with_both_values(self):
        rows = rules_engine.compare_with_baseline([page(overlaps=2, page_height_px=1500, stop_in_first_viewport=False)], [page()], RULES)
        by_metric = {row["metric"]: row for row in rows}
        self.assertEqual(by_metric["overlaps"]["mode"], "fail")
        self.assertEqual((by_metric["overlaps"]["baseline"], by_metric["overlaps"]["value"]), (0, 2))
        self.assertEqual(by_metric["page_height_px"]["mode"], "report")
        self.assertEqual(by_metric["stop_in_first_viewport"]["mode"], "fail")
        self.assertIn("기준선보다 나빠짐", rules_engine.describe(by_metric["overlaps"]))

    def test_improvement_is_not_a_violation(self):
        self.assertEqual(rules_engine.compare_with_baseline([page()], [page(overlaps=3, page_height_px=5000, stop_in_first_viewport=False)], RULES), [])

    def test_missing_and_new_pages(self):
        rows = rules_engine.compare_with_baseline([page(route="/drive")], [page()], RULES)
        self.assertEqual({row["kind"]: row["mode"] for row in rows}, {"missing_page": "fail", "new_page": "report"})

    def test_load_error_is_a_failure(self):
        broken = {"key": "stress|/system|390x844", "dataset": "stress", "route": "/system", "viewport": {"name": "390x844"}, "metrics": None, "error": "TimeoutError"}
        rows = rules_engine.load_error_violations([page(), broken], RULES)
        self.assertEqual([(row["kind"], row["mode"], row["route"]) for row in rows], [("load_error", "fail", "/system")])
        self.assertEqual(rules_engine.compare_with_baseline([broken], [dict(broken, metrics={"overlaps": 0}, error=None)], RULES), [])


class RulesFileTest(unittest.TestCase):
    def test_shipped_rules_file_is_valid(self):
        rules = json.loads((TOOL_DIR / "rules.json").read_text(encoding="utf-8"))
        rules_engine.validate_rules(rules)
        self.assertTrue(any(rule["metric"] == "horizontal_overflow_px" for rule in rules["fixed_rules"]))
        self.assertTrue(any(rule["metric"] == "overlaps" for rule in rules["fixed_rules"]))
        self.assertTrue(any(rule["metric"] == "stop_in_first_viewport" and rule.get("routes") for rule in rules["fixed_rules"]))

    def test_fixed_layout_rules_are_enforced(self):
        """1차 버그 수정으로 고친 것은 fail이어야 한다(되돌아가면 compare.py가 실패한다)."""
        rules = json.loads((TOOL_DIR / "rules.json").read_text(encoding="utf-8"))
        modes = {rule["id"]: rule["mode"] for rule in rules["fixed_rules"]}
        for rule_id in ("no-horizontal-overflow", "no-content-overflow", "no-clipped-text", "no-clipped-text-844x390",
                        "no-broken-image", "canvas-matches-box", "canvas-follows-resize", "no-overlap", "no-console-error"):
            self.assertEqual(modes.get(rule_id), "fail", rule_id)

    def test_accepted_record_passes_and_the_record_before_the_overhaul_does_not(self):
        rules = json.loads((TOOL_DIR / "rules.json").read_text(encoding="utf-8"))

        def failing_rule_ids(name):
            run = json.loads((TOOL_DIR / name / "ui_check.json").read_text(encoding="utf-8"))
            self.assertEqual(len(run["pages"]), 90, name)
            self.assertFalse(Path(run["static_dir"]).is_absolute(), f"{name}: static_dir must not be a local absolute path")
            rows = rules_engine.evaluate_fixed_rules(run["pages"], rules) + rules_engine.load_error_violations(run["pages"], rules)
            return sorted({row.get("rule_id", row["kind"]) for row in rows if row["mode"] == "fail"})

        self.assertEqual(failing_rule_ids("accepted"), [])
        self.assertEqual(failing_rule_ids("baseline"), [
            "canvas-follows-resize", "canvas-matches-box", "no-broken-image", "no-clipped-text",
            "no-clipped-text-844x390", "no-content-overflow", "no-horizontal-overflow"])

    def test_validation_rejects_bad_rules(self):
        for broken in (
            {"schema": "other", "schema_version": 1},
            {**RULES, "fixed_rules": [{"id": "x", "metric": "m", "op": "~", "value": 0, "mode": "fail"}]},
            {**RULES, "fixed_rules": [{"id": "x", "metric": "m", "op": "<=", "value": 0, "mode": "maybe"}]},
            {**RULES, "fixed_rules": [{"id": "x", "metric": "m", "op": "<=", "mode": "fail"}]},
            {**RULES, "fixed_rules": [RULES["fixed_rules"][0], RULES["fixed_rules"][0]]},
            {**RULES, "regression": {"metrics": {"m": {"better": "sideways", "mode": "fail"}}}},
        ):
            with self.assertRaises(rules_engine.RulesError):
                rules_engine.validate_rules(broken)


if __name__ == "__main__":
    unittest.main()
