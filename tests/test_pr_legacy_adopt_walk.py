"""`qew pr open ITEM --head H --adopt-legacy N --reason TEXT` takes over an unmarked pull request qew never recorded: the number and
head stay, the ledger records it on the item, and the reason is kept. A stranger's pull request is still refused without the flag.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.forge import PullRequestSpec
from tests.pr_harness import PrWorld


class LegacyAdoptWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Remainder")
        self.legacy = self.w.forge.open_pull_request(PullRequestSpec("old-branch", "main", "Old draft", "pre-qew body", True, "someone")).number

    def records(self) -> list[dict]:
        return [m for m in self.w.ledger.load_mirrors(self.item) if m.get("kind") == "pr"]

    def test_an_unmarked_pull_request_is_refused_without_the_flag_and_adopted_with_it(self) -> None:
        """Prevents the forge bypass: without the flag a stranger's pull request is protected; with it the existing number and head are kept."""
        plain = self.w.qew("pr", "open", self.item, "--head", "old-branch")
        self.assertEqual(plain.code, 2, plain)
        self.assertIn("--adopt-legacy", plain.err)
        self.assertEqual(self.records(), [])
        run = self.w.qew("pr", "open", self.item, "--head", "old-branch", "--adopt-legacy", str(self.legacy), "--reason", "owner assigned it to this item")
        self.assertEqual(run.code, 0, run)
        self.assertIn(f"pr #{self.legacy}", run.out)
        self.assertEqual([(r["number"], r["head"], r["state"]) for r in self.records()], [(self.legacy, "old-branch", "open")])
        self.assertEqual(len(self.w.forge.pulls), 1)
        self.assertIn(f"qew:{self.item}", self.w.forge.pulls[self.legacy].body)
        self.assertIn("owner assigned it to this item", json.dumps(self.w.events(self.item)))
        closed = self.w.qew("pr", "close", str(self.legacy))
        self.assertEqual(closed.code, 0, closed)

    def test_the_flag_needs_a_reason_and_the_pull_request_on_that_head(self) -> None:
        """Prevents a quiet or misdirected adoption: no reason, a number on another head, or a merged pull request is refused and nothing is recorded."""
        no_reason = self.w.qew("pr", "open", self.item, "--head", "old-branch", "--adopt-legacy", str(self.legacy))
        self.assertEqual(no_reason.code, 2, no_reason)
        other = self.w.forge.open_pull_request(PullRequestSpec("another", "main", "Other", "x", True, "someone")).number
        wrong = self.w.qew("pr", "open", self.item, "--head", "old-branch", "--adopt-legacy", str(other), "--reason", "x")
        self.assertEqual(wrong.code, 2, wrong)
        self.w.forge.pulls[self.legacy] = self.w.forge._with(self.legacy, state="merged", merge_sha="m" * 40)
        merged = self.w.qew("pr", "open", self.item, "--head", "old-branch", "--adopt-legacy", str(self.legacy), "--reason", "x")
        self.assertEqual(merged.code, 2, merged)
        self.assertEqual(self.records(), [])

    def test_a_pull_request_marked_for_another_item_is_not_taken(self) -> None:
        """Prevents stealing a qew pull request: the flag is for unmarked ones only."""
        other_item = self.w.item("Other")
        self.assertEqual(self.w.open(other_item).code, 0)
        number = next(m["number"] for m in self.w.ledger.load_mirrors(other_item) if m.get("kind") == "pr")
        head = next(m["head"] for m in self.w.ledger.load_mirrors(other_item) if m.get("kind") == "pr")
        run = self.w.qew("pr", "open", self.item, "--head", head, "--adopt-legacy", str(number), "--reason", "x")
        self.assertEqual(run.code, 2, run)


if __name__ == "__main__":
    unittest.main()
