"""Walks of `qew pr merge`: every unready pull request is refused and nothing is written.

Adversary: a draft, conflicted, red or unchecked pull request merged, a head that moved after the check
merged anyway, a merge reported that never landed, an override used as a master key, and half-done or
unproven work shipped through the forge.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.forge import ForgeAuth
from tests.pr_harness import PrWorld

BASE = dict(state="open", draft=False, mergeable=True, checks="passing", body="")


class MergeBase(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Work")
        self.assertEqual(self.w.open(self.item).code, 0)
        self.sha = self.w.commit("work")
        self.w.forge.edit_pull(1, head_sha=self.sha)
        self.assertEqual(self.w.qew("evidence", self.item, "--change", self.sha).code, 0)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)

    def merge(self, *extra: str, **kwargs):
        return self.w.qew("pr", "merge", "1", *extra, **kwargs)

    def nothing_merged(self) -> None:
        self.assertEqual(self.w.forge.merged, [])
        self.assertEqual(self.w.forge.count("merge_pr"), 0)


class RefusalWalk(MergeBase):
    def test_every_unready_pull_request_is_refused_with_its_own_message_and_nothing_is_written(self) -> None:
        """Prevents any unready merge, and checks the refusals can be told apart."""
        table = (
            ("draft", dict(draft=True), 2, "qew pr ready 1"),
            ("open checklist item", dict(body="- [ ] RED test <!-- pr-progress:red -->\n"), 2, "unticked"),
            ("conflict", dict(mergeable=False), 2, "conflicts"),
            ("unknown merge state", dict(mergeable=None), 2, "unknown, retry"),
            ("closed", dict(state="closed"), 2, "is closed"),
            ("merged", dict(state="merged"), 2, "is merged"),
            ("failing checks", dict(checks="failing"), 3, "are failing"),
            ("pending checks", dict(checks="pending"), 3, "are pending"),
        )
        for name, change, code, fragment in table:
            with self.subTest(name):
                self.w.forge.edit_pull(1, **{**BASE, **change})
                run = self.merge()
                self.assertEqual(run.code, code, run)
                self.assertIn(fragment, run.err)
                self.nothing_merged()

    def test_a_pull_request_with_no_checks_at_all_may_merge(self) -> None:
        """Prevents a deadlock on repositories without CI."""
        self.w.forge.edit_pull(1, checks="none")
        self.assertEqual(self.merge().code, 0)

    def test_the_golden_merge_pins_the_checked_head_and_points_at_the_next_step(self) -> None:
        run = self.merge()
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.w.forge.merged, [(1, self.sha)])
        self.assertIn(f"merged at {self.sha[:12]}", run.out)
        self.assertIn(f"{self.item}: evidence recorded; qew complete {self.item}", run.out)
        self.assertIn("qew land 1", run.out)

    def test_a_head_that_moved_after_the_check_is_refused_by_the_forge(self) -> None:
        """Prevents merging unreviewed commits."""
        self.w.forge.after_read.append(lambda forge: forge.edit_pull(1, head_sha="moved"))
        run = self.merge()
        self.assertEqual(run.code, 1)
        self.assertEqual(self.w.forge.merged, [])

    def test_a_check_state_the_client_does_not_know_is_indeterminate(self) -> None:
        """Prevents guessing readiness."""
        self.w.forge.edit_pull(1, checks="mystery")
        self.assertEqual(self.merge().code, 4)
        self.nothing_merged()

    def test_an_unconfigured_target_is_one_line_not_a_traceback(self) -> None:
        path = next((self.w.world.ledger / "mirrors" / self.item).glob("*-pr-1.json"))
        path.rename(path.with_name("forgejo-gone.example-o-r-pr-1.json"))
        run = self.merge()
        self.assertEqual(run.code, 2)
        self.assertIn("is not configured", run.err)
        self.assertNotIn("Traceback", run.err)


class VerifyWalk(MergeBase):
    def test_a_merge_the_forge_shows_late_is_still_a_success(self) -> None:
        """Prevents a false failure when the forge lags."""
        self.w.forge.merge_lag_reads = 1
        slept = []
        run = self.merge(sleep=slept.append)
        self.assertEqual(run.code, 0, run)
        self.assertEqual(slept, [1.0])

    def test_a_merge_that_never_lands_is_reported_not_celebrated(self) -> None:
        """Prevents telling the user work merged when it did not."""
        self.w.forge.merge_lag_reads = 99
        run = self.merge(sleep=lambda seconds: None)
        self.assertEqual(run.code, 3)
        self.assertIn("merge not visible", run.err)
        self.assertNotIn("merged at", run.out)

    def test_json_reports_the_merge_the_head_and_the_covered_items(self) -> None:
        """Prevents agents scraping text."""
        run = self.merge("--json")
        self.assertEqual(
            json.loads(run.out),
            {"merged": True, "number": 1, "head_sha": self.sha, "items": [self.item], "schema_version": 1},
        )


class AutoWalk(MergeBase):
    def setUp(self) -> None:
        super().setUp()
        self.w.forge.edit_pull(1, checks="pending")

    def test_auto_is_refused_where_the_base_branch_requires_no_checks(self) -> None:
        """Prevents an auto-merge that skips CI."""
        run = self.merge("--auto")
        self.assertEqual(run.code, 3)
        self.assertIn("would merge immediately", run.err)
        self.nothing_merged()

    def test_auto_enables_where_checks_are_required_and_names_the_follow_up(self) -> None:
        self.w.forge.required_checks["main"] = True
        run = self.merge("--auto")
        self.assertEqual(run.code, 0, run)
        self.assertTrue(self.w.forge.pulls[1].auto_merge)
        self.assertIn("auto-merge enabled", run.out)
        self.assertIn("qew land 1", run.out)
        self.nothing_merged()

    def test_auto_that_does_not_show_after_enabling_is_a_failure(self) -> None:
        self.w.forge.required_checks["main"] = True
        self.w.forge.auto_merge_ignored = True
        self.assertEqual(self.merge("--auto").code, 3)

    def test_unreadable_rules_are_indeterminate_and_never_assumed(self) -> None:
        """Prevents guessing."""
        self.w.forge.rules_error = ForgeAuth("HTTP 403")
        run = self.merge("--auto")
        self.assertEqual(run.code, 4)
        self.nothing_merged()

    def test_a_forge_without_auto_merge_says_so_in_one_line_and_changes_nothing(self) -> None:
        self.w.forge.required_checks["main"] = True
        self.w.forge.supports_auto_merge = False
        run = self.merge("--auto")
        self.assertEqual(run.code, 3)
        self.assertEqual(len(run.err.strip().splitlines()), 1)
        self.nothing_merged()

    def test_auto_never_waits_for_failing_checks_and_merges_green_ones_at_once(self) -> None:
        self.w.forge.required_checks["main"] = True
        self.w.forge.edit_pull(1, checks="failing")
        self.assertEqual(self.merge("--auto").code, 3)
        self.w.forge.edit_pull(1, checks="passing")
        self.assertEqual(self.merge("--auto").code, 0)
        self.assertEqual(len(self.w.forge.merged), 1)

    def test_auto_and_admin_contradict_before_any_call(self) -> None:
        calls = len(self.w.forge.calls)
        self.assertEqual(self.merge("--auto", "--admin", "--reason", "x").code, 2)
        self.assertEqual(len(self.w.forge.calls), calls)


class AdminWalk(MergeBase):
    def test_admin_without_a_reason_and_a_reason_without_admin_are_refused_before_any_call(self) -> None:
        """Prevents a silent bypass."""
        calls = len(self.w.forge.calls)
        self.assertEqual(self.merge("--admin").code, 2)
        self.assertEqual(self.merge("--reason", "because").code, 2)
        self.assertEqual(len(self.w.forge.calls), calls)

    def test_admin_skips_pending_and_failing_checks_prints_the_reason_and_leaves_an_audit_event(self) -> None:
        """Prevents a blocked emergency docs-only merge."""
        for checks in ("pending", "failing"):
            with self.subTest(checks=checks):
                self.w.forge.edit_pull(1, **{**BASE, "checks": checks})
                self.w.forge.merged.clear()
                run = self.merge("--admin", "--reason", "docs only fix")
                self.assertEqual(run.code, 0, run)
                self.assertIn("admin merge: docs only fix", run.out)
                self.assertEqual(self.w.forge.merge_calls[-1], (1, self.sha, True))
        audit = [e for e in self.w.events(self.item) if e.get("key") == "admin_merge"]
        self.assertEqual([e["new"] for e in audit], ["docs only fix", "docs only fix"])

    def test_admin_is_not_a_master_key(self) -> None:
        """Prevents admin bypassing the draft, checklist, conflict and ownership rules."""
        table = (
            ("draft", dict(draft=True), "draft"),
            ("open checklist", dict(body="- [ ] step\n"), "unticked"),
            ("conflict", dict(mergeable=False), "conflicts"),
        )
        for name, change, fragment in table:
            with self.subTest(name):
                self.w.forge.edit_pull(1, **{**BASE, **change})
                run = self.merge("--admin", "--reason", "emergency")
                self.assertEqual(run.code, 2, run)
                self.assertIn(fragment, run.err)
        self.w.forge.edit_pull(1, **BASE)
        stranger = self.merge("--admin", "--reason", "emergency", actor="stranger")
        self.assertEqual(stranger.code, 3)
        self.nothing_merged()

    def test_a_forge_without_a_bypass_refuses_one_line_and_merges_nothing(self) -> None:
        self.w.forge.bypass_supported = False
        run = self.merge("--admin", "--reason", "emergency")
        self.assertEqual(run.code, 3)
        self.assertEqual(self.w.forge.merged, [])


class ItemGateWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Work")
        self.assertEqual(self.w.open(self.item).code, 0)
        self.sha = self.w.commit("work")
        self.w.forge.edit_pull(1, head_sha=self.sha, draft=False)

    def test_an_open_child_task_blocks_the_merge_and_a_finished_one_lets_it_through(self) -> None:
        """Prevents merging half-done work."""
        self.assertEqual(self.w.qew("evidence", self.item, "--change", self.sha).code, 0)
        task = self.w.item("Step", kind="task", parent=self.item, claim=False)
        refused = self.w.qew("pr", "merge", "1")
        self.assertEqual(refused.code, 2)
        self.assertIn(task, refused.err)
        self.assertEqual(self.w.forge.merged, [])
        self.w.qew("start", task)
        self.w.qew("evidence", task, "--change", self.sha)
        self.assertEqual(self.w.qew("complete", task).code, 0)
        self.assertEqual(self.w.qew("pr", "merge", "1").code, 0)

    def test_a_merge_without_evidence_for_the_checked_head_is_refused(self) -> None:
        """Prevents shipping work nothing proves."""
        refused = self.w.qew("pr", "merge", "1")
        self.assertEqual(refused.code, 2)
        self.assertIn(f"qew evidence {self.item}", refused.err)
        later = self.w.commit("after the head")  # evidence for a commit the head does not contain
        self.w.qew("evidence", self.item, "--change", later)
        self.assertEqual(self.w.qew("pr", "merge", "1").code, 2)
        self.assertEqual(self.w.forge.merged, [])
        self.w.qew("evidence", self.item, "--change", self.sha)
        self.assertEqual(self.w.qew("pr", "merge", "1").code, 0)

    def test_a_head_missing_from_the_checkout_is_indeterminate_not_unproven(self) -> None:
        self.w.qew("evidence", self.item, "--change", self.sha)
        self.w.forge.edit_pull(1, head_sha="f" * 40)
        self.assertEqual(self.w.qew("pr", "merge", "1").code, 4)

    def test_a_foreign_claim_blocks_the_merge_until_the_item_is_taken_over(self) -> None:
        """Prevents merging another actor's claimed work by accident."""
        self.w.qew("evidence", self.item, "--change", self.sha)
        self.w.qew("release", self.item)
        self.assertEqual(self.w.qew("start", self.item, actor="other").code, 0)
        refused = self.w.qew("pr", "merge", "1")
        self.assertEqual(refused.code, 3)
        self.assertIn("other", refused.err)
        self.assertEqual(self.w.qew("release", self.item, actor="other").code, 0)
        self.assertEqual(self.w.qew("start", self.item).code, 0)
        self.assertEqual(self.w.qew("pr", "merge", "1").code, 0)


class PlanEpicMergeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.epic = self.w.item("Plan", kind="epic", draft=True)
        self.one = self.w.item("One", draft=True, parent=self.epic)
        self.two = self.w.item("Two", draft=True, parent=self.epic)
        self.assertEqual(self.w.open(self.epic).code, 0)

    def test_a_plan_pull_request_is_held_while_any_covered_item_is_a_draft(self) -> None:
        """Prevents merging a plan before approval."""
        for argv in (("ready", "1"), ("merge", "1")):
            run = self.w.qew("pr", *argv)
            self.assertEqual(run.code, 2, run)
            for item in (self.epic, self.one, self.two):
                self.assertIn(item, run.err)
        self.assertEqual(self.w.forge.count("ready_pr"), 0)

    def test_partial_approval_leaves_the_pull_request_held_naming_the_rest(self) -> None:
        """Prevents an early merge."""
        self.assertEqual(self.w.approve(self.epic, self.one).code, 0)
        run = self.w.qew("pr", "ready", "1")
        self.assertEqual(run.code, 2)
        self.assertIn(self.two, run.err)
        self.assertNotIn(self.one, run.err)

    def test_full_approval_releases_the_same_pull_request_with_no_forge_edit(self) -> None:
        """Prevents stuck pull requests."""
        self.assertEqual(self.w.approve(self.epic, tree=True).code, 0)
        edits = len(self.w.forge.calls)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)
        run = self.w.qew("pr", "merge", "1")
        self.assertEqual(run.code, 0, run)
        self.assertIn("unclaimed; nothing to complete yet", run.out)
        self.assertGreater(len(self.w.forge.calls), edits)

    def test_revert_of_a_covered_item_is_refused_once_the_pull_request_is_ready_and_allowed_while_it_is_a_draft(self) -> None:
        """Prevents a live pull request for work that went back to draft."""
        self.assertEqual(self.w.approve(self.epic, tree=True).code, 0)
        quote = "Alexander: 'please track this work in the Qew ledger now'"
        allowed = self.w.qew("draft", "revert", self.two, "--reason", "rethink", "--quote", quote)
        self.assertEqual(allowed.code, 0, allowed)
        self.assertEqual(self.w.approve(self.two).code, 0)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)
        refused = self.w.qew("draft", "revert", self.one, "--reason", "rethink", "--quote", quote)
        self.assertEqual(refused.code, 2)
        self.assertIn("pull request #1", refused.err)


if __name__ == "__main__":
    unittest.main()
