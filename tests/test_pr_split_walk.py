"""A pull request that was split: dropping the root's box takes the root out of the covered set, so the slice merges alone.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class SplitPullRequestWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.epic = self.w.item("Whole epic", kind="epic", claim=False)
        self.slice = self.w.item("The slice", kind="task", parent=self.epic, claim=False)
        self.other = self.w.item("The rest", kind="task", parent=self.epic, claim=False)
        self.assertEqual(self.w.qew("start", self.epic).code, 0)
        self.assertEqual(self.w.open(self.epic).code, 0)
        self.sha = self.w.commit("slice")
        boxes = "".join(f"- [ ] {title} <!-- pr-progress:{item} -->\n" for item, title in
                        ((self.epic, "Whole epic"), (self.slice, "The slice"), (self.other, "The rest")))
        self.w.forge.edit_pull(1, head_sha=self.sha, draft=False, body=f"## Progress\n\n{boxes}")

    def test_dropping_the_root_and_its_other_children_lets_the_slice_merge_and_retitle(self) -> None:
        """Prevents a split pull request being stuck on `half-done work` of a root it no longer covers, with a title that
        still names the whole epic."""
        refused = self.w.qew("pr", "merge", "1", "--check")
        self.assertEqual(refused.code, 2, refused)
        for dropped in (self.epic, self.other):
            run = self.w.qew("pr", "check", "1", "--drop", dropped, "--reason", "split: the slice ships alone")
            self.assertEqual(run.code, 0, run)
        self.assertEqual(self.w.qew("pr", "sync", "1", "--items").code, 0)
        covered = self.w.qew("pr", "check", "1", "--list")
        self.assertIn(self.slice, covered.out)
        self.assertNotIn(self.epic, covered.out)
        self.assertEqual(self.w.qew("evidence", self.slice, "--change", self.sha).code, 0)
        retitled = self.w.qew("pr", "sync", "1")
        self.assertEqual(retitled.code, 0, retitled)
        self.assertIn("The slice", self.w.forge.pulls[1].title)
        merge = self.w.qew("pr", "merge", "1", "--check")
        self.assertEqual(merge.code, 0, merge)

    def test_dropping_the_only_covered_item_is_refused(self) -> None:
        """Prevents a pull request that covers nothing: close it instead."""
        lone = PrWorld()
        self.addCleanup(lone.close)
        item = lone.item("Only")
        self.assertEqual(lone.open(item).code, 0)
        lone.forge.edit_pull(1, body=f"## Progress\n\n- [ ] Only <!-- pr-progress:{item} -->\n")
        run = lone.qew("pr", "check", "1", "--drop", item, "--reason", "x")
        self.assertEqual(run.code, 2, run)
        self.assertIn("close", run.err)


if __name__ == "__main__":
    unittest.main()
