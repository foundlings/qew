"""A merge that the forge's remaining quota cannot finish is refused before anything is written, with the time the hour resets.

Adversary: a 15-minute project gate passing on a 36-pull-request union and the merge then dying on a 403 because the shared token's
core limit was already 0: the gate's quarter of an hour wasted and the merge left half-known.
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class PrBudgetWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.shas: list[str] = []
        for n in (1, 2, 3):
            item = self.w.item(f"Work {n}")
            self.w.head = f"branch-{n}"
            self.assertEqual(self.w.open(item).code, 0)
            sha = self.w.commit(f"work {n}")
            self.w.forge.edit_pull(n, head_sha=sha)
            self.assertEqual(self.w.qew("evidence", item, "--change", sha).code, 0)
            self.assertEqual(self.w.qew("pr", "ready", str(n)).code, 0)
            self.shas.append(sha)
        self.w.forge.merges_with = {3: [1, 2]}

    def test_a_batch_merge_is_refused_up_front_when_the_quota_cannot_finish_it(self) -> None:
        """Prevents the merge starting on a spent token: exit 4, the reset time named, nothing merged, nothing written."""
        self.w.forge.budget_left = (3, time.time() + 1800)
        run = self.w.qew("pr", "merge", "1", "2", "3")
        self.assertEqual(run.code, 4, run)
        self.assertIn("3 core calls left", run.err)
        self.assertIn("resets", run.err)
        self.assertEqual(self.w.forge.merged, [])

    def test_a_single_merge_is_refused_the_same_way(self) -> None:
        """Prevents the single merge being the way round the check."""
        self.w.forge.budget_left = (0, time.time() + 1800)
        run = self.w.qew("pr", "merge", "3")
        self.assertEqual(run.code, 4, run)
        self.assertEqual(self.w.forge.merged, [])

    def test_a_merge_with_enough_quota_goes_ahead(self) -> None:
        """Prevents the check refusing work the budget covers."""
        self.w.forge.budget_left = (4000, time.time() + 1800)
        self.assertEqual(self.w.qew("pr", "merge", "1", "2", "3").code, 0)
        self.assertEqual(self.w.forge.merged, [(3, self.shas[2])])

    def test_a_forge_without_a_figure_is_not_refused(self) -> None:
        """Prevents a backend that reports no quota being blocked."""
        self.w.forge.budget_left = None
        self.assertEqual(self.w.qew("pr", "merge", "1", "2", "3").code, 0)


if __name__ == "__main__":
    unittest.main()
