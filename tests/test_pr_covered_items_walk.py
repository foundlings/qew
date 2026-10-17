"""A pull request's covered items follow the work: `pr check --add` extends them and `pr sync N --items` repairs a record that froze at open.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class CoveredItemsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.root = self.w.item("Root", kind="epic")
        self.assertEqual(self.w.open(self.root).code, 0)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)

    def covered(self) -> list[str]:
        (name,) = self.w.mirror_files(self.root)
        return json.loads((self.w.world.ledger / "mirrors" / self.root / name).read_text())["items"]

    def child(self) -> str:
        made = self.w.item("Child", parent=self.root, claim=False)
        return made

    def test_a_child_created_after_open_blocks_the_merge_until_it_is_added(self) -> None:
        """Prevents a PR that gained work after opening being unmergeable even with every box ticked, with no command to cover the child."""
        child = self.child()
        self.assertEqual(self.covered(), [self.root])
        refused = self.w.qew("pr", "merge", "1")
        self.assertIn("open children", refused.err)
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", child).code, 0)
        self.assertEqual(self.covered(), [self.root, child])
        self.assertNotIn("open children", self.w.qew("pr", "merge", "1").err)

    def test_sync_items_re_derives_the_covered_items_of_a_frozen_record(self) -> None:
        """Prevents an existing PR, whose record froze before its children existed, having no repair short of hand-editing the mirror."""
        child = self.child()
        run = self.w.qew("pr", "sync", "1", "--items")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.covered(), [self.root, child])
        again = self.w.qew("pr", "sync", "1", "--items")
        self.assertEqual(again.code, 0, again)
        self.assertEqual(self.covered(), [self.root, child])
        self.assertNotIn("open children", self.w.qew("pr", "merge", "1").err)


class MergeCheckWalk(unittest.TestCase):
    """`qew pr merge N --check`: every merge refusal, no merge, no write."""

    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.root = self.w.item("Root", kind="epic")
        self.assertEqual(self.w.open(self.root).code, 0)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)

    def ledger_files(self) -> dict[str, str]:
        base = self.w.world.ledger
        return {str(p.relative_to(base)): p.read_text() for p in sorted(base.rglob("*.json")) + sorted(base.rglob("*.md")) if ".jj" not in p.parts and ".git" not in p.parts}

    def test_an_uncovered_child_is_refused_by_check_with_the_merge_message_and_nothing_is_written(self) -> None:
        """Prevents a hook running a multi-minute gate for a merge qew itself would refuse in seconds, or --check differing from the merge."""
        self.w.item("Child", parent=self.root, claim=False)
        before, writes = self.ledger_files(), list(self.w.forge.body_writes)
        real = self.w.qew("pr", "merge", "1")
        self.assertEqual(real.code, 2)
        checked = self.w.qew("pr", "merge", "1", "--check")
        self.assertEqual((checked.code, checked.err), (real.code, real.err))
        listed = json.loads(self.w.qew("pr", "merge", "1", "--check", "--json").out)
        self.assertFalse(listed["mergeable"])
        self.assertIn("open children", listed["refusals"][0]["message"])
        self.assertEqual(self.w.forge.pulls[1].state, "open")
        self.assertEqual((self.ledger_files(), self.w.forge.body_writes), (before, writes))

    def test_a_clean_pull_request_passes_check_and_is_not_merged(self) -> None:
        """Prevents --check merging the pull request it was only asked to judge."""
        run = self.w.qew("pr", "merge", "1", "--check")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.w.forge.pulls[1].state, "open")
        self.assertTrue(json.loads(self.w.qew("pr", "merge", "1", "--check", "--json").out)["mergeable"])

    def test_a_passing_check_says_what_it_checked_and_not_that_the_merge_is_cleared(self) -> None:
        """Prevents a caller's gate log reading a qew preflight as the final verdict: the line names its scope (ledger and forge
        refusals only), says nothing was merged, and the exit code is unchanged."""
        run = self.w.qew("pr", "merge", "1", "--check")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(run.out.strip(), "pr #1: qew merge preflight passed (ledger and forge refusals only); nothing was merged")
        self.assertNotIn("every merge check", run.out)

    def test_check_lists_every_refusal_not_only_the_first(self) -> None:
        """Prevents a caller fixing one refusal at a time, one gate run each."""
        self.w.forge.edit_pull(1, body="## Progress\n\n- [ ] open box <!-- pr-progress:x -->\n")
        self.w.item("Child", parent=self.root, claim=False)
        listed = json.loads(self.w.qew("pr", "merge", "1", "--check", "--json").out)["refusals"]
        self.assertGreaterEqual(len(listed), 2)


if __name__ == "__main__":
    unittest.main()
