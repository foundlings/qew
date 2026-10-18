"""Walks of `qew pr draft`, the inverse of `qew pr ready`: a lane that marked a pull request ready too early moves it back.

Adversary: a draft reported when the forge did not change, a finished pull request touched, and a teammate's pull request
moved back to draft by someone who neither opened it nor holds its claims.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class DraftWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item()
        self.assertEqual(self.w.open(self.item).code, 0)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)

    def recorded_draft(self) -> object:
        return next(m for m in self.w.ledger.load_mirrors(self.item) if m["kind"] == "pr")["draft"]

    def test_a_ready_pull_request_goes_back_to_draft_and_the_ledger_follows(self) -> None:
        """Prevents a lane being unable to retract a ready mark, or the ledger still calling the pull request ready."""
        run = self.w.qew("pr", "draft", "1")
        self.assertEqual((run.code, run.out.strip()), (0, "pr #1 draft"), run)
        self.assertTrue(self.w.forge.pulls[1].draft)
        self.assertIs(self.recorded_draft(), True)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0, "and it can be readied again")

    def test_json_and_an_already_draft_pull_request_writes_nothing(self) -> None:
        """Prevents redundant forge writes and a json shape that differs from `pr ready`."""
        first = self.w.qew("pr", "draft", "1", "--json")
        self.assertEqual(json.loads(first.out), {"number": 1, "draft": True, "changed": True, "schema_version": 1})
        again = self.w.qew("pr", "draft", "1")
        self.assertEqual(again.code, 0)
        self.assertIn("unchanged", again.out)
        self.assertEqual(self.w.forge.count("draft_pr"), 1)

    def test_a_merged_or_closed_pull_request_is_refused_and_never_written(self) -> None:
        """Prevents touching finished pull requests."""
        for state in ("merged", "closed"):
            with self.subTest(state=state):
                self.w.forge.edit_pull(1, state=state)
                self.assertEqual(self.w.qew("pr", "draft", "1").code, 2)
        self.assertEqual(self.w.forge.count("draft_pr"), 0)

    def test_a_forge_that_accepts_but_stays_ready_is_an_indeterminate_failure(self) -> None:
        """Prevents false success when the forge ignored the change."""
        self.w.forge.draft_ignored = True
        run = self.w.qew("pr", "draft", "1")
        self.assertEqual(run.code, 4, run)
        self.assertIn("still ready", run.err)
        self.assertIs(self.recorded_draft(), False)

    def test_someone_who_neither_opened_it_nor_holds_the_claims_is_refused(self) -> None:
        """Prevents one agent bouncing another's pull request: the ownership rule of `pr ready` applies."""
        run = self.w.qew("pr", "draft", "1", actor="stranger")
        self.assertEqual(run.code, 3, run)
        self.assertFalse(self.w.forge.pulls[1].draft)


if __name__ == "__main__":
    unittest.main()
