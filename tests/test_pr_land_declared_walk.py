"""Merging through qew on a schema 9 ledger lands the declaration: evidence, completions, comments, and a record of what stayed open.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import git
from tests.pr_harness import PrWorld


class LandDeclaredWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)
        self.count = 0

    def pr(self, item: str, *flags: str) -> tuple[int, str]:
        """Open the next pull request with its own commit on main as its head; returns (number, head sha)."""
        self.count += 1
        self.w.head = f"branch-{self.count}"
        run = self.w.open(item, *flags)
        self.assertEqual(run.code, 0, run)
        sha = self.w.commit(f"work {self.count}")
        self.w.forge.edit_pull(self.count, head_sha=sha, draft=False)
        return self.count, sha

    def state(self, item: str) -> str:
        return self.w.qew("show", item).out.split("state: ")[1].split()[0]

    def evidence(self, item: str) -> list[str]:
        return [c for e in self.w.events(item) if e["type"] == "evidence" for c in e["change_ids"]]

    def pr_events(self) -> list[dict]:
        base = self.w.world.ledger / "prs"
        return [json.loads(p.read_text()) for p in sorted(base.rglob("*.json"))] if base.is_dir() else []

    def test_merge_completes_what_it_closes_with_its_own_evidence_and_notes_the_rest(self) -> None:
        """Prevents a merged PR leaving its closes item open, without evidence, or completing what it only touched."""
        done, partial, related = self.w.item("Done"), self.w.item("Partial"), self.w.item("Context", claim=False)
        number, sha = self.pr(done, "--closes", done, "--remains", partial, "--related", related)
        merged = self.w.qew("pr", "merge", str(number))
        self.assertEqual(merged.code, 0, merged)
        self.assertEqual(self.state(done), "completed")
        self.assertEqual(self.evidence(done), [sha])
        self.assertEqual(self.state(partial), "claimed")
        self.assertIn(f"pr #{number}", merged.out)
        self.assertIn(done, merged.out)
        notes = [e["text"] for e in self.w.events(partial) if e["type"] == "commented"]
        self.assertTrue(any(f"pr #{number}" in n and "partial" in n for n in notes), notes)
        self.assertTrue(any(f"pr #{number}" in e["text"] for e in self.w.events(related) if e["type"] == "commented"))
        self.assertEqual([e for e in self.pr_events() if e["type"] == "land_pending"], [])

    def test_an_undeclared_pull_request_is_refused_before_the_forge_merge(self) -> None:
        """Prevents a merge of a pull request that never said what it does to the ledger."""
        item = self.w.item("Work")
        number, _ = self.pr(item, "--closes", item)
        self.w.qew("pr", "unlink", str(number), item)
        run = self.w.qew("pr", "merge", str(number))
        self.assertEqual(run.code, 2, run)
        self.assertIn("no link declaration", run.err)
        self.assertEqual(self.w.forge.pulls[number].state, "open")

    def test_bookkeeping_trouble_after_the_gate_never_fails_the_merge_it_is_recorded_as_pending(self) -> None:
        """Prevents a merge, already attested by the forge, being reported as failed because an item could not be completed."""
        epic = self.w.item("Epic", kind="epic")
        child = self.w.item("Child", parent=epic, claim=False)
        number, _ = self.pr(epic, "--closes", epic)
        merged = self.w.qew("pr", "merge", str(number))
        self.assertEqual(merged.code, 0, merged)
        self.assertEqual(self.w.forge.pulls[number].state, "merged")
        self.assertEqual(self.state(epic), "claimed")
        self.assertIn(child, merged.out)
        pending = [e for e in self.pr_events() if e["type"] == "land_pending"]
        self.assertEqual([e["item"] for e in pending], [epic])
        self.assertIn(child, pending[0]["why"])
        self.assertIn("qew land", merged.out)

    def test_land_finishes_a_pending_item_once_the_blocker_is_done_and_is_idempotent(self) -> None:
        """Prevents a flagged item staying open for good: `qew land N` is the repair, and repeating it changes nothing."""
        epic = self.w.item("Epic", kind="epic")
        child = self.w.item("Child", kind="issue", parent=epic, claim=False)
        number, _ = self.pr(epic, "--closes", epic)
        self.w.qew("pr", "merge", str(number))
        self.w.qew("start", child)
        sha = self.w.commit("child work")
        self.w.qew("evidence", child, "--change", sha)
        self.assertEqual(self.w.qew("complete", child).code, 0)
        repaired = self.w.qew("land", str(number))
        self.assertEqual(repaired.code, 0, repaired)
        self.assertEqual(self.state(epic), "completed")
        again = self.w.qew("land", str(number))
        self.assertEqual(again.code, 0, again)
        self.assertEqual(len([e for e in self.w.events(epic) if e["type"] == "completed"]), 1)

    def test_an_item_completed_meanwhile_is_satisfied_not_an_error(self) -> None:
        """Prevents a race (someone finished the item between the preflight and the merge) failing a merge the forge attested."""
        item = self.w.item("Work")
        number, sha = self.pr(item, "--closes", item)
        self.w.qew("evidence", item, "--change", sha)
        self.assertEqual(self.w.qew("complete", item).code, 0)
        merged = self.w.qew("pr", "merge", str(number))
        self.assertEqual(merged.code, 0, merged)
        self.assertEqual([e for e in self.pr_events() if e["type"] == "land_pending"], [])

    def test_union_members_get_their_own_evidence_never_the_whole_unions(self) -> None:
        """Prevents every item of a union citing every member's changes as proof."""
        first, second = self.w.item("First"), self.w.item("Second")
        one, sha_one = self.pr(first, "--closes", first)
        two, sha_two = self.pr(second, "--closes", second)  # its commit sits on top of the first member's
        self.w.forge.edit_pull(one, head_sha=sha_one)
        merged = self.w.qew("pr", "merge", str(one), str(two))
        self.assertEqual(merged.code, 0, merged)
        self.assertEqual(self.evidence(first), [sha_one])
        self.assertEqual(self.evidence(second), [sha_two])
        self.assertEqual((self.state(first), self.state(second)), ("completed", "completed"))

    def test_a_head_that_never_reached_main_is_never_recorded_as_evidence(self) -> None:
        """Prevents evidence that is not an ancestor of main being attached to a completed item."""
        item = self.w.item("Work")
        self.w.head = "branch-1"
        self.assertEqual(self.w.open(item, "--closes", item).code, 0)
        code = self.w.world.code
        git(code, "checkout", "-q", "-b", "side")
        stray = self.w.commit("only on the side branch")
        git(code, "checkout", "-q", "main")
        self.w.forge.edit_pull(1, head_sha=stray, draft=False)
        merged = self.w.qew("pr", "merge", "1")
        self.assertEqual(merged.code, 0, merged)
        self.assertEqual(self.evidence(item), [])
        self.assertEqual(self.state(item), "claimed")
        pending = [e for e in self.pr_events() if e["type"] == "land_pending"]
        self.assertEqual([e["item"] for e in pending], [item])
        self.assertIn("main", pending[0]["why"])


if __name__ == "__main__":
    unittest.main()
