"""Walks of the precedence rules of the board status that the status model walk does not cover.

Adversary: work released and then reverted showing as Ready, and closed work lingering as Draft.
"""

import unittest

from qew import sync_plan
from qew.ledger_store import Ledger
from tests.ledger_harness import QUOTE, World


class StatusPrecedenceWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def status(self, item_id: str) -> str:
        return sync_plan.column(Ledger(self.world.ledger, "TST").snapshot(), item_id)

    def test_a_draft_with_approval_history_is_still_draft(self) -> None:
        """Prevents a picked-up draft: approved, then reverted, must show Draft, not Ready."""
        w = self.world
        item = w.create("--title", "T", "--quote", QUOTE)
        self.assertEqual(self.status(item), "Ready")
        self.assertEqual(w.run("draft", "revert", item, "--reason", "not yet", "--quote", QUOTE).returncode, 0)
        self.assertEqual(self.status(item), "Draft")

    def test_closed_work_is_closed_even_when_it_was_a_draft(self) -> None:
        """Prevents stale Draft or Ready cards for work that was closed."""
        w = self.world
        draft = w.create("--title", "never approved", "--draft")
        approved = w.create("--title", "approved", "--quote", QUOTE)
        for item in (draft, approved):
            self.assertEqual(w.run("close", item, "--reason", "not_planned").returncode, 0)
            self.assertEqual(self.status(item), "Closed")


if __name__ == "__main__":
    unittest.main()
