"""`qew pr block N --by M` marks a pull request that waits on another: the label `blocked` (never an id) and the title suffix
` [blocked by: #M]`; unblocking, `pr sync` and the blocker's merge keep the two in step.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class PrBlockWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.waiting = self.w.item("Waiting work")
        self.assertEqual(self.w.open(self.waiting).code, 0)
        self.w.head = "fix-branch"
        self.fixing = self.w.item("Fix the blockage")
        self.assertEqual(self.w.open(self.fixing).code, 0)
        self.w.head = "third-branch"
        self.third = self.w.item("Another blocker")
        self.assertEqual(self.w.open(self.third).code, 0)

    def pull(self, number: int):
        return self.w.forge.pulls[number]

    def test_block_labels_and_suffixes_and_unblock_removes_one_blocker_at_a_time(self) -> None:
        """Prevents a blocked pull request that looks ready: the label shows it, the title names the blockers, and no label holds an id."""
        run = self.w.qew("pr", "block", "1", "--by", "2")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.pull(1).title, "Waiting work [blocked by: #2]")
        self.assertIn("blocked", self.pull(1).labels)
        self.assertEqual(self.w.qew("pr", "block", "1", "--by", "3", "--by", "2").code, 0)
        self.assertEqual(self.pull(1).title, "Waiting work [blocked by: #2, #3]")
        self.assertFalse([label for label in self.pull(1).labels if any(c.isdigit() for c in label)])
        self.assertEqual(self.w.qew("pr", "unblock", "1", "--by", "2").code, 0)
        self.assertEqual(self.pull(1).title, "Waiting work [blocked by: #3]")
        self.assertIn("blocked", self.pull(1).labels)
        last = self.w.qew("pr", "unblock", "1")
        self.assertEqual(last.code, 0, last)
        self.assertEqual(self.pull(1).title, "Waiting work")
        self.assertNotIn("blocked", self.pull(1).labels)

    def test_sync_keeps_the_suffix_when_the_item_is_renamed(self) -> None:
        """Prevents `pr sync` erasing the blocked-by note it must carry."""
        self.assertEqual(self.w.qew("pr", "block", "1", "--by", "2").code, 0)
        self.assertEqual(self.w.qew("set", self.waiting, "--title", "Renamed work").code, 0)
        self.assertEqual(self.w.qew("pr", "sync", "1").code, 0)
        self.assertEqual(self.pull(1).title, "Renamed work [blocked by: #2]")

    def test_merging_the_blocker_unblocks_every_pull_request_waiting_on_it_and_says_which(self) -> None:
        """Prevents a merged blocker leaving its dependants marked blocked forever."""
        self.assertEqual(self.w.qew("pr", "block", "1", "--by", "2").code, 0)
        self.assertEqual(self.w.qew("pr", "block", "3", "--by", "2").code, 0)
        self.assertEqual(self.w.qew("pr", "block", "3", "--by", "1").code, 0)
        sha = self.w.commit("fix")
        self.w.forge.edit_pull(2, head_sha=sha, draft=False)
        self.assertEqual(self.w.qew("evidence", self.fixing, "--change", sha).code, 0)
        merged = self.w.qew("pr", "merge", "2")
        self.assertEqual(merged.code, 0, merged)
        self.assertIn("unblocked pr #1", merged.out)
        self.assertIn("pr #3", merged.out)
        self.assertEqual(self.pull(1).title, "Waiting work")
        self.assertNotIn("blocked", self.pull(1).labels)
        self.assertEqual(self.pull(3).title, "Another blocker [blocked by: #1]")
        self.assertIn("blocked", self.pull(3).labels)

    def test_a_finished_dependant_is_unblocked_in_the_record_without_an_error(self) -> None:
        """Prevents the blocker's merge reporting a failure for a dependant that merged or closed meanwhile."""
        self.assertEqual(self.w.qew("pr", "block", "3", "--by", "2").code, 0)
        self.w.forge.edit_pull(3, state="closed")
        sha = self.w.commit("fix")
        self.w.forge.edit_pull(2, head_sha=sha, draft=False)
        self.assertEqual(self.w.qew("evidence", self.fixing, "--change", sha).code, 0)
        merged = self.w.qew("pr", "merge", "2")
        self.assertEqual(merged.code, 0, merged)
        self.assertNotIn("still lists", merged.out)
        record = next(m for m in self.w.ledger.load_mirrors(self.third) if m.get("kind") == "pr")
        self.assertEqual(record.get("blocked_by"), [])

    def test_refusals_write_nothing(self) -> None:
        """Prevents a pull request blocked by itself, by nobody, by a stranger's hand, or after it finished."""
        before = self.pull(1).title
        self.assertEqual(self.w.qew("pr", "block", "1", "--by", "1").code, 2)
        self.assertEqual(self.w.qew("pr", "block", "1").code, 2)
        self.assertEqual(self.w.qew("pr", "block", "1", "--by", "2", actor="stranger").code, 3)
        self.w.forge.edit_pull(1, state="closed")
        self.assertEqual(self.w.qew("pr", "block", "1", "--by", "2").code, 2)
        self.assertEqual(self.pull(1).title, before)
        self.assertNotIn("blocked", self.pull(1).labels)


if __name__ == "__main__":
    unittest.main()
