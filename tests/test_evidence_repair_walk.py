"""`qew evidence ID --change X --repair` replaces the proof a completed item shows with changes that are on the target branch.

A completed item cannot take plain evidence, so wrong proof (a rewritten git hash) could only be fixed by reopening. Each test is a
scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import git
from tests.pr_harness import PrWorld


class RepairWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Done")
        self.old = self.w.commit("first")
        self.assertEqual(self.w.qew("evidence", self.item, "--change", self.old).code, 0)
        self.assertEqual(self.w.qew("complete", self.item).code, 0)

    def shown(self) -> str:
        return self.w.qew("show", self.item).out

    def test_plain_evidence_on_a_completed_item_is_still_refused_and_says_repair(self) -> None:
        """Prevents the refusal being a dead end: it names the way to fix wrong proof."""
        run = self.w.qew("evidence", self.item, "--change", self.w.commit("second"))
        self.assertEqual(run.code, 2, run)
        self.assertIn("--repair", run.err)

    def test_repair_records_the_new_proof_in_the_header_and_as_an_event(self) -> None:
        """Prevents bad proof staying on a completed item for good."""
        new = self.w.commit("the right change")
        run = self.w.qew("evidence", self.item, "--change", new, "--repair")
        self.assertEqual(run.code, 0, run)
        self.assertIn(new[:12], self.shown())
        recorded = [e for e in self.w.events(self.item) if e["type"] == "evidence" and e.get("repair")]
        self.assertEqual([e["change_ids"] for e in recorded], [[new]])
        self.assertEqual(self.w.qew("check").code, 0)

    def test_repair_refuses_a_change_that_is_not_on_the_target(self) -> None:
        """Prevents repairing bad proof with proof that is not on main either."""
        git(self.w.world.code, "checkout", "-q", "-b", "side")
        stray = self.w.commit("side only")
        git(self.w.world.code, "checkout", "-q", "main")
        run = self.w.qew("evidence", self.item, "--change", stray, "--repair")
        self.assertEqual(run.code, 2, run)
        self.assertIn("not an ancestor", run.err)

    def test_repair_needs_a_named_change_and_work_that_is_claimed_approved_or_completed(self) -> None:
        """Prevents --repair guessing the current change, or being used on a draft or closed item."""
        self.assertEqual(self.w.qew("evidence", self.item, "--repair").code, 2)
        draft = self.w.item("Draft work", draft=True)
        run = self.w.qew("evidence", draft, "--change", self.w.commit("x"), "--repair")
        self.assertEqual(run.code, 2, run)
        self.assertIn("cannot repair the evidence", run.err)


if __name__ == "__main__":
    unittest.main()
