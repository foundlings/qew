"""`qew evidence ID --change X --repair` replaces the proof a completed item shows with changes that are on the target branch.

A completed item cannot take plain evidence, so wrong proof (a rewritten git hash) could only be fixed by reopening. Each test is a
scenario; its docstring names the failure it prevents.
"""

import json
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

    def test_open_item_can_retract_a_cited_change_with_an_auditable_reason(self) -> None:
        """Prevents an erroneous evidence id from becoming permanent while preserving its append-only history."""
        w = self.w
        item = w.item("Open work")
        change = w.commit("wrong proof")
        self.assertEqual(w.qew("evidence", item, "--change", change).code, 0)

        retracted = w.qew("evidence", item, "--retract", change, "--reason", "cited the wrong change")
        self.assertEqual(retracted.code, 0, retracted)
        events = w.events(item)
        self.assertTrue(any(event["type"] == "evidence" and change in event["change_ids"] for event in events))
        self.assertTrue(any(event["type"] == "evidence_retracted" and event["change_id"] == change
                            and event["reason"] == "cited the wrong change" for event in events))
        shown = self.w.qew("show", item, "--json")
        self.assertEqual(shown.code, 0, shown)
        document = json.loads(shown.out)
        self.assertEqual(document.get("evidence", []), [])
        self.assertEqual(document["evidence_retractions"][0]["change_id"], change)
        complete = w.qew("complete", item)
        self.assertEqual(complete.code, 2, complete)
        self.assertIn("no evidence recorded", complete.err)
        self.assertEqual(w.qew("evidence", item, "--change", change).code, 0)
        completed = w.qew("complete", item)
        self.assertEqual(completed.code, 0, completed)
        shown_again = json.loads(self.w.qew("show", item, "--json").out)
        self.assertEqual(shown_again["evidence_ids"], [change])
        self.assertEqual(shown_again["evidence_retractions"][0]["change_id"], change)

    def test_retraction_refuses_uncited_ids_and_completed_items(self) -> None:
        """Prevents retraction from erasing unrelated proof or rewriting the proof of completed work."""
        w = self.w
        item = w.item("Open work")
        uncited = w.commit("uncited")
        refused = w.qew("evidence", item, "--retract", uncited, "--reason", "not cited")
        self.assertEqual(refused.code, 2, refused)
        self.assertIn("not currently recorded", refused.err)
        self.assertFalse(any(event["type"] == "evidence_retracted" for event in w.events(item)))

        completed_change = w.commit("completed proof")
        self.assertEqual(w.qew("evidence", item, "--change", completed_change).code, 0)
        self.assertEqual(w.qew("complete", item).code, 0)
        completed_refusal = w.qew("evidence", item, "--retract", completed_change, "--reason", "too late")
        self.assertEqual(completed_refusal.code, 2, completed_refusal)
        self.assertIn("completed", completed_refusal.err)


if __name__ == "__main__":
    unittest.main()
