"""Cross-repository references (owner/repo#123) in an item's text are listed by qew show as untrusted text, never followed.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class CrossReferencesWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_references_in_the_body_and_comments_are_listed_once_in_order(self) -> None:
        """Prevents a link to other work staying buried in prose: show lists each reference once, body first."""
        item = self.world.create("--kind", "issue", "--title", "Linked", "--body", "Blocked on acme/widgets#12 and see acme/widgets#12 again, also foundlings/qew#7.", "--quote", QUOTE)
        self.world.run("comment", item, "--body", "Upstream: other-org/lib.core#301 fixed it; not a ref: a/b#c or #5")
        shown = self.world.run("show", item).stdout
        self.assertIn("references: acme/widgets#12 foundlings/qew#7 other-org/lib.core#301", shown)
        data = json.loads(self.world.run("show", item, "--json").stdout)
        self.assertEqual(data["references"], ["acme/widgets#12", "foundlings/qew#7", "other-org/lib.core#301"])

    def test_a_reference_is_text_only_never_an_edge_or_a_path(self) -> None:
        """Prevents forge text turning into structure: a reference adds no need, no parent and no file access."""
        item = self.world.create("--kind", "issue", "--title", "Hostile", "--body", "../../etc/passwd#1 and x/y#1; rm -rf /", "--quote", QUOTE)
        self.assertEqual(self.world.run("check").returncode, 0)
        data = json.loads(self.world.run("show", item, "--json").stdout)
        self.assertEqual(data.get("references"), ["x/y#1"])
        self.assertNotIn("blocked_by", data)

    def test_an_item_without_references_prints_no_line(self) -> None:
        """Prevents noise on every item."""
        item = self.world.create("--kind", "issue", "--title", "Plain", "--body", "nothing here", "--quote", QUOTE)
        self.assertNotIn("references:", self.world.run("show", item).stdout)


if __name__ == "__main__":
    unittest.main()
