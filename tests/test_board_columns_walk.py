"""The board column of an item, one rule per row, in the reference tool's order.

Adversary: a rule drifting (an imported draft shown as Backlog, a claimed epic shown as Ready) because the derivation
lives in one function and nothing pinned each row of it.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_graph, ledger_store, sync_plan
from tests.ledger_harness import QUOTE, World


class BoardColumnsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World()
        self.addCleanup(self.w.close)
        self.ledger = ledger_store.open_ledger(self.w.ledger, "TST")

    def make(self, *arguments: str) -> str:
        return self.w.create(*arguments, *([] if "--quote" in arguments else ["--draft"]))

    def column(self, item: str) -> str:
        return sync_plan.column(self.ledger.snapshot(), item)

    def claim(self, item: str) -> None:
        self.assertEqual(self.w.run("start", item).returncode, 0)

    def test_each_rule_in_the_reference_order(self) -> None:
        """Draft, epic, needs approval, in review, in progress, ready, backlog, done: each from its own item."""
        w = self.w
        draft = self.make("--draft", "--title", "not approved")
        imported_like = self.make("--draft", "--title", "filed on the forge")
        epic_idle = self.make("--kind", "epic", "--title", "idle epic", "--quote", QUOTE)
        epic_busy = self.make("--kind", "epic", "--title", "busy epic", "--quote", QUOTE)
        child = self.make("--title", "claimed child", "--parent", epic_busy, "--quote", QUOTE)
        gated = self.make("--title", "needs a human", "--label", ledger_graph.APPROVAL_LABEL, "--quote", QUOTE)
        reviewed = self.make("--title", "has an open pull request", "--quote", QUOTE)
        claimed = self.make("--title", "claimed", "--quote", QUOTE)
        ready = self.make("--title", "ready", "--quote", QUOTE)
        blocker = self.make("--title", "blocker", "--quote", QUOTE)
        blocked = self.make("--title", "blocked", "--needs", blocker, "--quote", QUOTE)
        finished = self.make("--title", "finished", "--quote", QUOTE)
        closed = self.make("--title", "closed", "--quote", QUOTE)
        self.ledger.write_mirror(reviewed, "t", {"forge": "github", "url": "u", "kind": "pr", "number": 7, "state": "open"})
        self.claim(child)
        self.claim(claimed)
        self.claim(finished)
        w.run("evidence", finished, "--change", w.commit())
        self.assertEqual(w.run("complete", finished).returncode, 0)
        self.assertEqual(w.run("close", closed, "--reason", "not_planned").returncode, 0)
        expected = {
            draft: "Draft", imported_like: "Draft", epic_idle: "Backlog", epic_busy: "In progress", gated: "Needs approval",
            reviewed: "In review", claimed: "In progress", child: "In progress", ready: "Ready", blocker: "Ready",
            blocked: "Backlog", finished: "Done", closed: "Closed",
        }
        self.assertEqual({item: self.column(item) for item in expected}, expected)

    def test_every_close_reason_lands_in_closed_and_completed_stays_done(self) -> None:
        """Prevents work abandoned, duplicated or replaced looking as delivered as work that shipped."""
        w = self.w
        keeper = self.make("--title", "the original", "--quote", QUOTE)
        reasons = {"not_planned": (), "duplicate": ("--of", keeper), "superseded": ("--by", keeper)}
        dropped = {}
        for reason, link in reasons.items():
            item = self.make("--title", f"dropped {reason}", "--quote", QUOTE)
            self.assertEqual(w.run("close", item, "--reason", reason, *link).returncode, 0)
            dropped[item] = "Closed"
        self.assertEqual({item: self.column(item) for item in dropped}, dropped)


if __name__ == "__main__":
    unittest.main()
