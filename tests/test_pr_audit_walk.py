"""`qew check` and `qew repair-landings` audit pull requests on a schema 9 ledger: nothing merged is left unlanded or unlinked unseen.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import pr_record
from tests.pr_harness import PrWorld


class AuditWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)
        self.count = 0

    def pr(self, item: str, *flags: str) -> int:
        self.count += 1
        self.w.head = f"branch-{self.count}"
        self.assertEqual(self.w.open(item, *flags).code, 0)
        self.w.forge.edit_pull(self.count, head_sha=self.w.commit(f"work {self.count}"), draft=False)
        return self.count

    def mark_merged_in_the_record(self, number: int) -> None:
        """What a merge seen late leaves: the record says merged, nothing landed it."""
        record = pr_record.find(self.w.ledger, number, None)
        pr_record.update_mirror(self.w.ledger, record, state="merged")

    def test_a_fully_landed_merge_is_clean(self) -> None:
        """Prevents the audit crying wolf about a merge that did everything it declared."""
        item = self.w.item("Work")
        number = self.pr(item, "--closes", item)
        self.assertEqual(self.w.qew("pr", "merge", str(number)).code, 0)
        run = self.w.qew("check")
        self.assertEqual(run.code, 0, run)
        self.assertNotIn("land_pending", run.out + run.err)

    def test_a_pending_item_is_named_with_its_pull_request_and_does_not_fail_the_check(self) -> None:
        """Prevents an item a union left behind staying open silently, or an acknowledged flag failing every gate."""
        epic = self.w.item("Epic", kind="epic")
        self.w.item("Child", parent=epic, claim=False)
        number = self.pr(epic, "--closes", epic)
        self.w.qew("pr", "merge", str(number))
        run = self.w.qew("check")
        self.assertEqual(run.code, 0, run)
        shown = run.out + run.err
        self.assertIn("land_pending", shown)
        self.assertIn(f"pr #{number}", shown)
        self.assertIn(epic, shown)

    def test_a_merged_pull_request_whose_closes_item_is_open_with_no_record_is_a_problem(self) -> None:
        """Prevents a merge that was never landed (seen late, or landed by nobody) leaving an item open without anyone knowing why."""
        item = self.w.item("Work")
        number = self.pr(item, "--closes", item)
        self.mark_merged_in_the_record(number)
        run = self.w.qew("check")
        self.assertNotEqual(run.code, 0, run)
        shown = run.out + run.err
        self.assertIn(f"pr #{number}", shown)
        self.assertIn(f"qew land {number}", shown)

    def test_a_merged_pull_request_with_no_declaration_is_a_problem_naming_migrate(self) -> None:
        """Prevents an unlinked merged pull request hiding among the migrated ones."""
        item = self.w.item("Work")
        number = self.pr(item, "--closes", item)
        self.w.qew("pr", "unlink", str(number), item)
        self.mark_merged_in_the_record(number)
        run = self.w.qew("check")
        self.assertNotEqual(run.code, 0, run)
        self.assertIn("pr migrate", run.out + run.err)

    def test_the_audit_never_stops_a_sync(self) -> None:
        """Prevents bookkeeping findings (a merged pull request nobody landed or declared) refusing every sync of the ledger."""
        item = self.w.item("Work")
        number = self.pr(item, "--closes", item)
        self.w.qew("pr", "unlink", str(number), item)
        self.mark_merged_in_the_record(number)
        self.assertNotEqual(self.w.qew("check").code, 0)
        run = self.w.qew("sync", "--dry-run")
        self.assertNotIn("problem(s)", run.err, run)

    def test_repair_landings_lists_and_then_finishes_pending_items(self) -> None:
        """Prevents a pending item having no command that clears it for every pull request at once."""
        epic = self.w.item("Epic", kind="epic")
        child = self.w.item("Child", kind="issue", parent=epic, claim=False)
        number = self.pr(epic, "--closes", epic)
        self.w.qew("pr", "merge", str(number))
        self.w.forge.edit_pull(number, state="merged")
        listed = self.w.qew("repair-landings", "--dry-run")
        self.assertEqual(listed.code, 0, listed)
        self.assertIn(f"pr #{number}", listed.out)
        self.assertIn(epic, listed.out)
        self.w.qew("start", child)
        self.w.qew("evidence", child, "--change", self.w.commit("child"))
        self.assertEqual(self.w.qew("complete", child).code, 0)
        repaired = self.w.qew("repair-landings")
        self.assertEqual(repaired.code, 0, repaired)
        self.assertIn("state: completed", self.w.qew("show", epic).out)
        self.assertIn("nothing", self.w.qew("repair-landings", "--dry-run").out)


    def test_open_pull_requests_closing_one_item_are_not_noted_once_the_item_is_finished(self) -> None:
        """Prevents `qew check` nagging to merge or re-declare pull requests that already merged outside qew, whose item is done."""
        item = self.w.item("Shared work")
        first = self.pr(item, "--closes", item)
        second = self.pr(item, "--closes", item)
        before = self.w.qew("check")
        self.assertIn(f"#{first} #{second} all close {item}", before.out + before.err)
        closed = self.w.qew("close", item, "--reason", "not_planned")
        self.assertEqual(closed.code, 0, closed)
        after = self.w.qew("check")
        self.assertEqual(after.code, 0, after)
        self.assertNotIn("all close", after.out + after.err)

if __name__ == "__main__":
    unittest.main()
