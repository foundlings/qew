"""Walks of `qew land`: after a pull request merged, evidence of its head and completion of its items.

Adversary: completing work whose pull request never merged, completing someone else's claim, a completion
that skips the gate because it arrived through the pull request, and a forge-side merge nobody recorded.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class LandWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Work")
        self.assertEqual(self.w.open(self.item).code, 0)
        self.sha = self.w.commit("work")
        self.w.forge.edit_pull(1, head_sha=self.sha, draft=False)

    def state(self, item: str) -> str:
        return self.w.qew("show", item).out.split("state: ")[1].split()[0]

    def test_merge_then_land_completes_the_item(self) -> None:
        self.assertEqual(self.w.qew("evidence", self.item, "--change", self.sha).code, 0)
        self.assertEqual(self.w.qew("pr", "merge", "1").code, 0)
        run = self.w.qew("land", "1")
        self.assertEqual(run.code, 0, run)
        self.assertIn(f"{self.item} completed", run.out)
        self.assertEqual(self.state(self.item), "completed")

    def test_land_of_an_unmerged_pull_request_is_refused_and_writes_nothing(self) -> None:
        """Prevents completing work that never landed."""
        before = len(self.w.events(self.item))
        run = self.w.qew("land", "1")
        self.assertEqual(run.code, 2)
        self.assertIn("not merged", run.err)
        self.assertEqual(len(self.w.events(self.item)), before)

    def test_a_merge_made_outside_qew_still_lands_with_the_merge_head_as_evidence(self) -> None:
        """Prevents a forge-side merge leaving the item stuck without evidence."""
        self.w.forge.edit_pull(1, state="merged")
        run = self.w.qew("land", "1")
        self.assertEqual(run.code, 0, run)
        evidence = [e for e in self.w.events(self.item) if e["type"] == "evidence"]
        self.assertEqual([e["change_ids"] for e in evidence], [[self.sha]])
        self.assertEqual(self.state(self.item), "completed")

    def test_a_gate_failure_is_reported_per_item_and_fails_the_command(self) -> None:
        """Prevents a landing that hides an unfinished child."""
        task = self.w.item("Step", kind="task", parent=self.item, claim=False)
        self.w.forge.edit_pull(1, state="merged")
        run = self.w.qew("land", "1")
        self.assertEqual(run.code, 2)
        self.assertIn(f"{self.item}: not completed", run.out)
        self.assertIn(task, run.out)
        self.assertEqual(self.state(self.item), "claimed")

    def test_an_item_someone_else_holds_is_not_landed(self) -> None:
        """Prevents completing another actor's claim."""
        self.w.forge.edit_pull(1, state="merged")
        self.w.qew("release", self.item, "--reason", "handover")
        self.w.qew("start", self.item, actor="other")
        run = self.w.qew("land", "1")
        self.assertEqual(run.code, 0, run)
        self.assertIn(f"{self.item}: claimed by other; not landed", run.out)
        self.assertEqual(self.state(self.item), "claimed")

    def hand_to_other(self) -> None:
        self.w.forge.edit_pull(1, state="merged")
        self.w.qew("release", self.item, "--reason", "handover")
        self.w.qew("start", self.item, actor="other")

    def test_the_integrator_lands_an_item_another_actor_holds_and_the_reason_is_recorded(self) -> None:
        """Prevents merged work staying claimed because its merger is not the claimant."""
        self.hand_to_other()
        run = self.w.qew("land", "1", "--integrator", "--reason", "integrator merged it")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.state(self.item), "completed")
        taken = [e for e in self.w.events(self.item) if e["type"] == "completed"]
        self.assertIn("integrator merged it", str(taken[-1]))
        self.assertIn("other", str(taken[-1]))

    def test_the_integrator_flag_needs_a_reason_and_writes_nothing_without_one(self) -> None:
        """Prevents an unexplained takeover of other actors' claims."""
        self.hand_to_other()
        before = len(self.w.events(self.item))
        run = self.w.qew("land", "1", "--integrator")
        self.assertEqual(run.code, 2)
        self.assertIn("--reason", run.err)
        self.assertEqual(len(self.w.events(self.item)), before)

    def test_a_reason_without_the_integrator_flag_is_refused(self) -> None:
        """Prevents a reason silently ignored on a plain land."""
        run = self.w.qew("land", "1", "--reason", "why")
        self.assertEqual(run.code, 2)
        self.assertIn("--integrator", run.err)

    def test_the_integrator_still_cannot_land_an_unmerged_pull_request(self) -> None:
        """Prevents the flag completing work that never merged."""
        self.w.qew("release", self.item, "--reason", "handover")
        self.w.qew("start", self.item, actor="other")
        run = self.w.qew("land", "1", "--integrator", "--reason", "sweep")
        self.assertEqual(run.code, 2)
        self.assertIn("not merged", run.err)
        self.assertEqual(self.state(self.item), "claimed")

    def test_a_non_opener_landing_a_pull_request_whose_items_are_all_complete_is_not_an_error(self) -> None:
        """Prevents an after-merge sweep reading as a failure when there is nothing left to land."""
        self.assertEqual(self.w.qew("evidence", self.item, "--change", self.sha).code, 0)
        self.assertEqual(self.w.qew("pr", "merge", "1").code, 0)
        self.assertEqual(self.w.qew("land", "1").code, 0)
        run = self.w.qew("land", "1", "--integrator", "--reason", "sweep", actor="rudolph")
        self.assertEqual(run.code, 0, run)
        self.assertIn("nothing to land: all covered items complete", run.out)

    def test_an_integrator_who_neither_opened_nor_claimed_lands_the_held_item(self) -> None:
        """Prevents the PR-level owner rule refusing the merger before land's own per-item check runs."""
        self.w.forge.edit_pull(1, state="merged")
        self.w.qew("release", self.item, "--reason", "handover")
        self.w.qew("start", self.item, actor="claimer")
        run = self.w.qew("land", "1", "--integrator", "--reason", "rudolph merged", actor="rudolph")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.state(self.item), "completed")

    def test_a_non_opener_without_the_flag_still_cannot_complete_a_held_item(self) -> None:
        """Prevents dropping the owner rule from letting any actor complete another's claim."""
        self.w.forge.edit_pull(1, state="merged")
        run = self.w.qew("land", "1", actor="rudolph")
        self.assertEqual(run.code, 0, run)
        self.assertIn("claimed by", run.out)
        self.assertEqual(self.state(self.item), "claimed")

    def test_an_epic_pull_request_lands_its_children_before_the_epic(self) -> None:
        epic = self.w.item("Epic", kind="epic")
        child = self.w.item("Child", parent=epic)
        self.assertEqual(self.w.open(epic).code, 0)
        self.w.forge.edit_pull(2, head_sha=self.sha, state="merged")
        run = self.w.qew("land", "2")
        self.assertEqual(run.code, 0, run)
        self.assertEqual([self.state(child), self.state(epic)], ["completed", "completed"])
        self.assertLess(run.out.index(child), run.out.index(epic))

    def test_items_that_landed_stay_landed_when_a_later_item_cannot(self) -> None:
        """Prevents one unfinished item rolling back the completion of the work that did land."""
        epic = self.w.item("Epic", kind="epic")
        child = self.w.item("Child", parent=epic)
        self.w.item("Open task", kind="task", parent=epic, claim=False)
        self.assertEqual(self.w.open(epic).code, 0)
        self.w.forge.edit_pull(2, head_sha=self.sha, state="merged")
        run = self.w.qew("land", "2")
        self.assertEqual(run.code, 2)
        self.assertIn(f"{epic}: not completed", run.out)
        self.assertEqual(self.state(child), "completed")


if __name__ == "__main__":
    unittest.main()
