"""Walks of `qew pr merge A B C`: pull requests whose heads form one union land through the merge gate once.

Adversary: a batch where one member is not ready merged anyway, heads that are not one union merged as if they were, a
follower the forge never marked merged recorded as merged, and the ledger left without a record of the followers.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import git
from tests.pr_harness import PrWorld


class BatchWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.items: list[str] = []
        self.shas: list[str] = []
        for n in (1, 2, 3):  # one linear history: each head contains the one before it
            item = self.w.item(f"Work {n}")
            self.w.head = f"branch-{n}"
            self.assertEqual(self.w.open(item).code, 0)
            sha = self.w.commit(f"work {n}")
            self.w.forge.edit_pull(n, head_sha=sha)
            self.assertEqual(self.w.qew("evidence", item, "--change", sha).code, 0)
            self.assertEqual(self.w.qew("pr", "ready", str(n)).code, 0)
            self.items.append(item)
            self.shas.append(sha)
        self.w.forge.merges_with = {3: [1, 2]}

    def merged_states(self) -> list[str]:
        return [self.w.forge.pulls[n].state for n in (1, 2, 3)]

    def test_a_union_of_three_merges_once_and_every_member_is_recorded(self) -> None:
        """Prevents the batch bypassing the ledger: the lead is merged once at its checked head, whatever order the numbers come
        in, and each follower gets its merged record and a hand-off line."""
        run = self.w.qew("pr", "merge", "2", "3", "1")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.w.forge.merged, [(3, self.shas[2])])
        self.assertEqual(self.merged_states(), ["merged"] * 3)
        for number, item in zip((1, 2, 3), self.items):
            self.assertIn(f"pr #{number}", run.out)
            self.assertIn(item, run.out)
            records = [r for r in self.w.ledger.load_mirrors(item) if r.get("kind") == "pr"]
            self.assertEqual([r.get("state") for r in records], ["merged"])

    def test_one_member_that_is_not_ready_stops_the_whole_batch(self) -> None:
        """Prevents a half-gated batch: a failing check on one member refuses the batch, names it, and merges nothing."""
        self.w.forge.edit_pull(1, checks="failing")
        run = self.w.qew("pr", "merge", "1", "2", "3")
        self.assertEqual(run.code, 3, run)
        self.assertIn("pr #1", run.err)
        self.assertEqual(self.w.forge.merged, [])

    def test_heads_that_are_not_one_union_are_refused_with_the_recipe(self) -> None:
        """Prevents merging two branches as a batch when neither holds the other: refused, nothing merged, and the error names
        the command that builds the union."""
        git(self.w.world.code, "checkout", "-q", "--detach", self.shas[0])
        side = self.w.commit("side branch")
        self.w.forge.edit_pull(2, head_sha=side)
        self.assertEqual(self.w.qew("evidence", self.items[1], "--change", side).code, 0)
        run = self.w.qew("pr", "merge", "2", "3")
        self.assertEqual(run.code, 2, run)
        self.assertIn("no head contains the others", run.err)
        self.assertIn("branch-2", run.err)
        self.assertIn("branch-3", run.err)  # the recipe names the heads to join
        self.assertEqual(self.w.forge.merged, [])

    def test_a_follower_the_forge_did_not_mark_merged_is_an_error_not_a_record(self) -> None:
        """Prevents recording a pull request as merged on the batch's say-so: the forge is read for every follower."""
        self.w.forge.merges_with = {3: [1]}
        run = self.w.qew("pr", "merge", "1", "2", "3", sleep=lambda seconds: None)
        self.assertEqual(run.code, 3, run)
        self.assertIn("pr #2", run.err)
        records = [r for r in self.w.ledger.load_mirrors(self.items[1]) if r.get("kind") == "pr"]
        self.assertNotEqual([r.get("state") for r in records], ["merged"])

    def test_the_post_merge_hook_sees_every_number_of_a_batch(self) -> None:
        """Prevents the merged-reminder missing the followers: every number after `qew pr merge` is read, a flag value is not."""
        from qew import hook_guard

        self.assertEqual(hook_guard.qew_pr_merges("qew pr merge 2 3 1 --target t --reason 9"), [2, 3, 1])
        self.assertEqual(hook_guard.qew_pr_merges("qew pr merge 7"), [7])


if __name__ == "__main__":
    unittest.main()
