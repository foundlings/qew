"""Walks of the seven-status model (`sync_plan.column`): one derivation for sync, board and ready.

Adversary: a human-gate label hidden by a claim, an epic shown startable, a merged pull
request keeping a card in review.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_graph, sync_plan
from qew.forge import COLUMNS
from qew.ledger_store import Ledger
from tests.ledger_harness import QUOTE, World


class StatusModelWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def status(self, item_id: str) -> str:
        snapshot = Ledger(self.world.ledger, "TST").snapshot()
        return sync_plan.column(snapshot, item_id)

    def make(self, *arguments: str) -> str:
        return self.world.create("--title", "T", *arguments, *([] if "--quote" in arguments else ["--draft"]))

    def test_the_seven_statuses_in_board_order(self) -> None:
        """Prevents a board whose option order or names drift from the documented model."""
        self.assertEqual(
            COLUMNS, ("Draft", "Backlog", "Ready", "Needs approval", "Gated", "In progress", "In review", "Done")
        )

    def test_one_ledger_shows_every_status(self) -> None:
        """Prevents sync, the board and `qew ready` disagreeing about the same item."""
        w = self.world
        draft = self.make("--kind", "issue")
        blocked_by = self.make("--kind", "issue", "--quote", QUOTE)
        blocked = self.make("--kind", "issue", "--needs", blocked_by, "--quote", QUOTE)
        gated = self.make("--kind", "issue", "--label", "needs:approval", "--quote", QUOTE)
        active = self.make("--kind", "issue", "--quote", QUOTE)
        reviewed = self.make("--kind", "issue", "--quote", QUOTE)
        finished = self.make("--kind", "issue", "--quote", QUOTE)
        w.run("start", active)
        w.run("start", reviewed)
        Ledger(w.ledger, "TST").write_mirror(
            reviewed, "forgejo-x-pr-3", {"kind": "pr", "number": 3, "state": "open"}
        )
        w.run("start", finished)
        w.run("evidence", finished, "--change", w.commit())
        w.run("complete", finished)
        self.assertEqual(
            [self.status(i) for i in (draft, blocked_by, blocked, gated, active, reviewed, finished)],
            ["Draft", "Ready", "Backlog", "Needs approval", "In progress", "In review", "Done"],
        )
        snapshot = Ledger(w.ledger, "TST").snapshot()
        self.assertEqual(ledger_graph.ready(snapshot), [blocked_by], "Ready on the board is exactly what qew ready offers")

    def test_the_human_gate_label_outranks_a_claim_and_a_review(self) -> None:
        """Prevents claimed work that still needs a human yes from looking in progress."""
        w = self.world
        item = self.make("--kind", "issue", "--label", "needs:approval", "--quote", QUOTE)
        w.run("start", item)
        Ledger(w.ledger, "TST").write_mirror(item, "forgejo-x-pr-4", {"kind": "pr", "number": 4})
        self.assertEqual(self.status(item), "Needs approval")

    def test_an_epic_is_backlog_until_a_child_is_claimed(self) -> None:
        """Prevents an unstartable planning container sitting in Ready."""
        w = self.world
        epic = self.make("--kind", "epic", "--quote", QUOTE)
        child = self.make("--kind", "issue", "--parent", epic, "--quote", QUOTE)
        self.assertEqual(self.status(epic), "Backlog")
        w.run("start", child)
        self.assertEqual(self.status(epic), "In progress")
        w.run("release", child, "--reason", "pause")
        self.assertEqual(self.status(epic), "Backlog")

    def test_a_merged_pull_request_does_not_keep_the_card_in_review(self) -> None:
        """Prevents a card stuck in review after its pull request merged."""
        w = self.world
        item = self.make("--kind", "issue", "--quote", QUOTE)
        w.run("start", item)
        ledger = Ledger(w.ledger, "TST")
        ledger.write_mirror(item, "forgejo-x-pr-5", {"kind": "pr", "number": 5, "state": "merged"})
        self.assertEqual(self.status(item), "In progress")


if __name__ == "__main__":
    unittest.main()
