"""`qew land N --offline` lands a merged pull request from the ledger and the local checkout alone: no forge, no token, no network.

Merged means the pull request's head is an ancestor of the target branch here. Each test is a scenario; its docstring names the
failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import git
from tests.pr_harness import PrWorld
from tests.test_pr_preflight_walk import NoForge


class OfflineLandWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)
        self.item = self.w.item("Work")
        self.assertEqual(self.w.open(self.item, "--closes", self.item).code, 0)

    def land(self, head: str, *extra: str):
        return self.w.qew("land", "1", "--offline", "--head", head, *extra, forge=NoForge())  # type: ignore[arg-type]

    def test_a_head_on_the_target_lands_with_no_forge(self) -> None:
        """Prevents offline or forge-less work being unable to record that its pull request merged."""
        sha = self.w.commit("work")
        run = self.land(sha)
        self.assertEqual(run.code, 0, run)
        self.assertIn("state: completed", self.w.qew("show", self.item).out)
        self.assertEqual([e["change_ids"] for e in self.w.events(self.item) if e["type"] == "evidence"], [[sha]])

    def test_a_head_not_on_the_target_is_not_merged_and_nothing_is_written(self) -> None:
        """Prevents an unmerged branch being landed because the forge could not be asked."""
        git(self.w.world.code, "checkout", "-q", "-b", "side")
        stray = self.w.commit("side only")
        git(self.w.world.code, "checkout", "-q", "main")
        before = len(self.w.events(self.item))
        run = self.land(stray)
        self.assertEqual(run.code, 2, run)
        self.assertIn("not an ancestor", run.err)
        self.assertEqual(len(self.w.events(self.item)), before)

    def test_the_head_is_read_from_the_checkout_when_not_named(self) -> None:
        """Prevents needing the forge just to learn the head: the recorded branch is looked up locally, and the refusal says how to name it."""
        run = self.w.qew("land", "1", "--offline", forge=NoForge())  # type: ignore[arg-type]
        self.assertEqual(run.code, 4, run)
        self.assertIn("--head", run.err)

    def test_it_is_idempotent(self) -> None:
        """Prevents a second offline land completing twice or duplicating notes."""
        sha = self.w.commit("work")
        self.assertEqual(self.land(sha).code, 0)
        again = self.land(sha)
        self.assertEqual(again.code, 0, again)
        self.assertEqual(len([e for e in self.w.events(self.item) if e["type"] == "completed"]), 1)


if __name__ == "__main__":
    unittest.main()
