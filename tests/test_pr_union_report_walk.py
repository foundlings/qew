"""A union names what it completed and what it left behind, in the ledger and on the lead's description; a closed member says so.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.forge import ForgeUnavailable
from tests.pr_harness import PrWorld

CLOSING = re.compile(r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[: ]+\S*#\d+", re.I)


class UnionReportWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)
        self.count = 0

    def pr(self, item: str, *flags: str) -> int:
        self.count += 1
        self.w.head = f"branch-{self.count}"
        self.assertEqual(self.w.open(item, *flags).code, 0)
        self.w.forge.edit_pull(self.count, head_sha=self.w.commit(f"work {self.count}"), draft=False)
        return self.count

    def union(self) -> tuple[int, int, str, str, str]:
        first, second, left = self.w.item("First"), self.w.item("Second"), self.w.item("Epic", kind="epic")
        self.w.item("Child", parent=left, claim=False)
        one = self.pr(first, "--closes", first)
        two = self.pr(second, "--closes", second, left)  # the epic has an open child: it stays behind
        self.w.forge.merges_with[two] = [one]
        return one, two, first, second, left

    def test_the_lead_description_lists_per_member_what_was_completed_and_what_was_left_open(self) -> None:
        """Prevents a merged union whose description says nothing of which member completed which item, or what stayed open."""
        one, two, first, second, left = self.union()
        merged = self.w.qew("pr", "merge", str(one), str(two))
        self.assertEqual(merged.code, 0, merged)
        body = self.w.forge.pulls[two].body  # the lead is the member whose head holds the others
        self.assertIn("### Landed", body)
        self.assertRegex(body, rf"pr #{one}: completed `{first}`")
        self.assertRegex(body, rf"pr #{two}: completed `{second}`")
        self.assertRegex(body, rf"pr #{two}: left open `{left}`")
        self.assertIsNone(CLOSING.search(body), body)

    def test_a_forge_that_refuses_the_description_never_fails_the_landed_merge(self) -> None:
        """Prevents a description write after the merge turning an attested, landed merge into an error."""
        one, two, first, *_ = self.union()
        self.w.forge.raise_on["update_pr"] = ForgeUnavailable("down")
        merged = self.w.qew("pr", "merge", str(one), str(two))
        self.assertEqual(merged.code, 0, merged)
        self.assertIn("state: completed", self.w.qew("show", first).out)
        self.assertIn("description", merged.err)

    def test_closing_a_member_unmerged_leaves_a_note_on_its_items_and_changes_no_state(self) -> None:
        """Prevents an item that only a closed member declared looking as if its pull request is still coming."""
        item = self.w.item("Only here")
        other = self.w.item("Also")
        number = self.pr(item, "--closes", item, "--remains", other)
        self.assertEqual(self.w.qew("pr", "close", str(number)).code, 0)
        for target in (item, other):
            notes = [e["text"] for e in self.w.events(target) if e["type"] == "commented"]
            self.assertTrue(any(f"pull request #{number} was closed unmerged" in n for n in notes), notes)
        self.assertIn("state: claimed", self.w.qew("show", item).out)
        merge = self.w.qew("pr", "merge", str(number), "--check", "--ledger-only")
        self.assertNotEqual(merge.code, 0)  # a closed pull request is not mergeable
        shown = json.loads(self.w.qew("pr", "links", str(number), "--json").out)
        self.assertEqual(shown["closes"], [item])  # the declaration is history; it is just not landed


if __name__ == "__main__":
    unittest.main()
