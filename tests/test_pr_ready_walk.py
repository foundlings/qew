"""Walks of `qew pr ready` and of who may act on a pull request (`ready`, `check`, `merge`).

Adversary: a pull request reported ready when the forge did not change, a finished pull request touched,
a teammate's pull request acted on by accident, and guessed ownership when the claim history is corrupt.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class ReadyWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item()
        self.assertEqual(self.w.open(self.item).code, 0)

    def test_a_draft_becomes_ready_and_the_forge_is_read_back(self) -> None:
        """Prevents reporting ready when the forge did not change."""
        run = self.w.qew("pr", "ready", "1")
        self.assertEqual((run.code, run.out.strip()), (0, "pr #1 ready"))
        self.assertFalse(self.w.forge.pulls[1].draft)

    def test_ready_records_the_pull_request_as_no_longer_a_draft_in_the_ledger(self) -> None:
        """Prevents a ledger that still calls a ready pull request a draft (draft revert and the board read that record)."""
        def draft() -> object:
            return next(m for m in self.w.ledger.load_mirrors(self.item) if m["kind"] == "pr")["draft"]

        self.assertIs(draft(), True)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)
        self.assertIs(draft(), False)

    def test_json_reports_number_ready_and_changed(self) -> None:
        run = self.w.qew("pr", "ready", "1", "--json")
        self.assertEqual(json.loads(run.out), {"number": 1, "ready": True, "changed": True, "schema_version": 1})

    def test_an_already_ready_pull_request_is_reported_unchanged_and_writes_nothing(self) -> None:
        """Prevents redundant writes."""
        self.w.qew("pr", "ready", "1")
        again = self.w.qew("pr", "ready", "1")
        self.assertEqual(again.code, 0)
        self.assertIn("unchanged", again.out)
        self.assertEqual(self.w.forge.count("ready_pr"), 1)

    def test_a_merged_or_closed_pull_request_is_refused_and_never_written(self) -> None:
        """Prevents touching finished pull requests."""
        for state in ("merged", "closed"):
            with self.subTest(state=state):
                self.w.forge.edit_pull(1, state=state)
                self.assertEqual(self.w.qew("pr", "ready", "1").code, 2)
        self.assertEqual(self.w.forge.count("ready_pr"), 0)

    def test_a_pull_request_that_stays_a_draft_after_the_write_is_an_indeterminate_failure(self) -> None:
        """Prevents false success when the forge ignored the change."""
        self.w.forge.ready_ignored = True
        run = self.w.qew("pr", "ready", "1")
        self.assertEqual(run.code, 4)
        self.assertIn("still a draft", run.err)

    def test_a_non_positive_number_is_refused_before_any_ledger_or_forge_call(self) -> None:
        """Prevents odd forge URLs."""
        calls = len(self.w.forge.calls)
        for number in ("0", "-3"):
            with self.subTest(number=number):
                self.assertEqual(self.w.qew("pr", "ready", number).code, 2)
        self.assertEqual(self.w.qew("pr", "ready", "abc").code, 2)
        self.assertEqual(len(self.w.forge.calls), calls)

    def test_an_unrecorded_number_is_refused(self) -> None:
        run = self.w.qew("pr", "ready", "99")
        self.assertEqual(run.code, 2)
        self.assertIn("not recorded", run.err)

    def test_a_pull_request_over_a_draft_item_stays_held_and_names_the_approve_command(self) -> None:
        """Prevents readying a plan pull request early."""
        draft = self.w.item("Planned", draft=True)
        self.w.open(draft)
        run = self.w.qew("pr", "ready", "2")
        self.assertEqual(run.code, 2)
        self.assertIn("qew draft approve", run.err)
        self.assertIn(draft, run.err)
        self.assertEqual(self.w.forge.count("ready_pr"), 0)


class OwnershipWalk(unittest.TestCase):
    """other opens and claims; tester is the caller."""

    def setUp(self) -> None:
        self.w = PrWorld(actor="tester")
        self.addCleanup(self.w.close)
        self.item = self.w.item(by="other")
        self.assertEqual(self.w.open(self.item, actor="other").code, 0)

    def hand_over(self, item: str, to: str) -> None:
        self.assertEqual(self.w.qew("release", item, "--reason", "handover", actor="other").code, 0)
        self.assertEqual(self.w.qew("start", item, actor=to).code, 0)

    def test_the_holder_of_every_claim_acts_on_a_teammates_pull_request(self) -> None:
        """Prevents needing the original author to be online."""
        self.hand_over(self.item, "tester")
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)
        self.w.forge.edit_pull(1, body="- [ ] step <!-- pr-progress:a -->\n")
        self.assertEqual(self.w.qew("pr", "check", "1", "a").code, 0)

    def test_anyone_may_list_the_checklist_of_a_teammates_pull_request(self) -> None:
        self.w.forge.edit_pull(1, body="- [ ] step <!-- pr-progress:a -->\n")
        run = self.w.qew("pr", "check", "1", "--list")
        self.assertEqual((run.code, run.out.strip()), (0, "[ ] a step"))

    def test_the_opener_still_acts_after_giving_the_claim_up(self) -> None:
        self.assertEqual(self.w.qew("release", self.item, "--reason", "handover", actor="other").code, 0)
        self.assertEqual(self.w.qew("pr", "ready", "1", actor="other").code, 0)

    def refuse(self, *argv: str, merge: bool = False) -> None:
        run = self.w.qew("pr", *argv)
        self.assertEqual(run.code, 3, run)
        self.assertIn("other", run.err)
        self.assertIn("--take-over", run.err)
        self.assertEqual("--other-author" in run.err, merge)

    def test_nobody_but_the_opener_or_a_full_claim_holder_may_act(self) -> None:
        """Prevents acting on a teammate's pull request by accident."""
        self.refuse("ready", "1")  # other still holds the claim
        self.refuse("check", "1", "--all")
        self.refuse("merge", "1", merge=True)
        self.w.qew("release", self.item, "--reason", "handover", actor="other")  # now unclaimed
        self.refuse("ready", "1")
        self.hand_over_to_third = self.w.qew("start", self.item, actor="third")
        self.refuse("ready", "1")  # claimed by someone else
        self.assertEqual(self.w.forge.count("ready_pr"), 0)
        self.assertEqual(self.w.forge.body_writes, [])
        self.assertEqual(self.w.forge.merged, [])

    def test_holding_only_some_of_the_claims_is_not_enough(self) -> None:
        epic = self.w.item("Plan", kind="epic", by="other")
        first = self.w.item("One", parent=epic, by="other")
        second = self.w.item("Two", parent=epic, by="other")
        self.assertEqual(self.w.open(epic, actor="other").code, 0)
        pr = "2"
        # releasing an epic frees the descendants it holds; the new holder then takes them one claim at a time
        self.assertEqual(self.w.qew("release", epic, "--reason", "handover", actor="other").code, 0)
        self.assertEqual(self.w.qew("start", first, actor="tester").code, 0)
        self.assertEqual(self.w.qew("pr", "ready", pr).code, 3)
        self.assertEqual(self.w.qew("start", epic, actor="tester").code, 0)  # claims the epic and what is still free
        self.assertEqual(self.w.qew("pr", "ready", pr).code, 0)

    def test_a_pull_request_that_covers_no_item_is_refused(self) -> None:
        path = next((self.w.world.home("mirrors", self.item)).glob("*-pr-1.json"))
        record = json.loads(path.read_text())
        path.write_text(json.dumps({**record, "items": []}))
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 3)

    def test_ready_and_check_have_no_override_flag(self) -> None:
        """Prevents privilege creep: only merge may be forced past authorship."""
        self.assertEqual(self.w.qew("pr", "ready", "1", "--other-author", "--reason", "x").code, 2)
        self.assertEqual(self.w.qew("pr", "check", "1", "--all", "--other-author").code, 2)

    def test_other_author_merges_with_a_reason_and_prints_it(self) -> None:
        self.w.forge.edit_pull(1, draft=False)
        self.commit_and_evidence()
        run = self.w.qew("pr", "merge", "1", "--other-author", "--reason", "owner is on leave")
        self.assertEqual(run.code, 0, run)
        self.assertIn("merging other's pull request: owner is on leave", run.out)
        self.assertEqual(len(self.w.forge.merged), 1)

    def commit_and_evidence(self) -> None:
        sha = self.w.commit("work")
        self.w.forge.edit_pull(1, head_sha=sha)
        self.assertEqual(self.w.qew("evidence", self.item, "--change", sha, actor="other").code, 0)

    def test_other_author_needs_a_reason_and_still_obeys_every_readiness_rule(self) -> None:
        """Prevents using the override to skip readiness."""
        no_reason = self.w.qew("pr", "merge", "1", "--other-author")
        self.assertEqual(no_reason.code, 2)
        draft = self.w.qew("pr", "merge", "1", "--other-author", "--reason", "leave")
        self.assertEqual(draft.code, 2)
        self.assertIn("draft", draft.err)
        self.assertEqual(self.w.forge.merged, [])

    def test_corrupt_claim_history_fails_loudly_instead_of_guessing_an_owner(self) -> None:
        """Prevents wrong-owner merges."""
        self.w.ledger.append_event(self.item, "reopened")  # reopening claimed work: illegal from claimed (a second claim is a merged race, not corruption)
        run = self.w.qew("pr", "ready", "1")
        self.assertEqual(run.code, 4)
        self.assertEqual(self.w.forge.count("ready_pr"), 0)


if __name__ == "__main__":
    unittest.main()
