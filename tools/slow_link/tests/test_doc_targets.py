"""docs/slow_link.md: its target table is the one made from targets.json, and it names no local path."""
import re
import sys
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import compare  # noqa: E402

DOC = TOOL_DIR.parents[1] / "docs" / "slow_link.md"


class TargetTableInTheDocumentTest(unittest.TestCase):
    def setUp(self):
        self.document = compare.load_targets(compare.TARGETS_FILE)
        self.targets = self.document["targets"]

    def test_the_table_in_the_document_is_the_one_made_from_the_file(self):
        text = DOC.read_bytes().decode("utf-8")
        self.assertNotIn("\r", text)
        begin, end = "<!-- targets-table:begin -->", "<!-- targets-table:end -->"
        self.assertEqual((text.count(begin), text.count(end)), (1, 1))
        table = text.split(begin)[1].split(end)[0].strip("\n")
        self.assertEqual(table, compare.targets_table(self.document))
        self.assertEqual(len(table.splitlines()), len(self.targets) + 2)

    def test_the_document_holds_no_local_path(self):
        text = DOC.read_bytes().decode("utf-8")
        self.assertIsNone(re.search(r"[A-Za-z]:[\\/]", text))
        self.assertIsNone(re.search(r"(?i)users[\\/]|/home/|/mnt/", text))


if __name__ == "__main__":
    unittest.main()
