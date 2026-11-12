"""The target table: targets.json against the accepted values and the target check of the compare command."""
import contextlib
import copy
import io
import sys
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import compare  # noqa: E402


def run_main(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = compare.main([str(arg) for arg in args])
    return code, out.getvalue(), err.getvalue()


class TargetsTest(unittest.TestCase):
    def setUp(self):
        self.document = compare.load_targets(compare.TARGETS_FILE)
        self.targets = self.document["targets"]
        self.accepted = compare.load_accepted(compare.ACCEPTED_FILE)

    def test_every_target_is_well_formed(self):
        self.assertEqual(len({target["id"] for target in self.targets}), len(self.targets))
        groups = self.document["groups"]
        self.assertEqual(list(groups), ["가", "나", "다", "라", "마", "바"])       # the six draft groups
        self.assertEqual({target["id"].split("-")[0] for target in self.targets}, set(groups))
        for target in self.targets:
            self.assertIn(target["scenario"], compare.METRICS[target["metric"]]["scenarios"], target["id"])
            self.assertTrue(target["tasks"], target["id"])
            for task in target["tasks"]:
                self.assertRegex(task, r"^NET-(0[3-9]|1[0-2])$", target["id"])
            self.assertTrue(target["note_ko"].strip(), target["id"])
            self.assertNotIn("|", target["what_ko"] + target["note_ko"], target["id"])
            self.assertNotIn("\n", target["what_ko"] + target["note_ko"], target["id"])

    def test_today_is_the_accepted_value(self):
        checked = 0
        for target in self.targets:
            cell = self.accepted["cells"].get(f"{target['scenario']}/{target['profile']}")
            if cell is None or cell["commit"] != self.document["baseline"]["commit"]:
                continue                                           # the accepted values moved on after an intended change
            self.assertEqual(target["today"], cell["metrics"].get(target["metric"]), target["id"])
            checked += 1
        if self.accepted["commits"] == [self.document["baseline"]["short"]]:
            self.assertEqual(checked, len(self.targets))

    def test_a_kept_target_is_met_today_and_most_others_are_not(self):
        for target in self.targets:
            met = compare.target_met(target, target["today"])
            if target["kind"] == "keep":
                self.assertTrue(met, target["id"])
        self.assertGreater(sum(1 for target in self.targets if not compare.target_met(target, target["today"])), len(self.targets) // 2)

    def test_the_target_check(self):
        measured = {name: dict(cell["metrics"]) for name, cell in self.accepted["cells"].items()}
        today = {f"{t['scenario']}/{t['profile']}": {} for t in self.targets}
        for target in self.targets:
            today[f"{target['scenario']}/{target['profile']}"][target["metric"]] = target["today"]
        code, lines, rows = compare.check_targets(self.document, today)
        self.assertEqual((code, len(rows)), (0, len(self.targets)))
        self.assertEqual(compare.check_targets(self.document, today, strict=True)[0], 1)   # targets still to be reached
        kept = next(target for target in self.targets if target["kind"] == "keep")
        cell = f"{kept['scenario']}/{kept['profile']}"
        broken = copy.deepcopy(today)
        broken[cell][kept["metric"]] = kept["target"] * (3 if kept["op"] in ("<=", "<") else 0.3)
        code, lines, rows = compare.check_targets(self.document, broken)
        self.assertEqual(code, 1)
        self.assertEqual([row["id"] for row in rows if row["broken"]], [kept["id"]])
        self.assertIn("지킬 값이 깨졌다", "\n".join(lines))
        del broken[cell][kept["metric"]]                           # a kept target without a number is not a pass
        self.assertEqual(compare.check_targets(self.document, broken)[0], 1)
        only = {cell: today[cell]}
        self.assertEqual({row["cell"] for row in compare.check_targets(self.document, only)[2]}, {cell})
        if self.accepted["commits"] == [self.document["baseline"]["short"]]:
            self.assertEqual(compare.check_targets(self.document, measured)[0], 0)

    def test_the_command_prints_the_check_and_the_table(self):
        code, out, err = run_main("--targets")
        self.assertEqual(err, "")
        self.assertIn(code, (0, 1))
        self.assertIn(self.targets[0]["id"], out)
        code, out, err = run_main("--targets-table")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.rstrip("\n"), compare.targets_table(self.document))


if __name__ == "__main__":
    unittest.main()
