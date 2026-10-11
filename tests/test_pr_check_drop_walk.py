"""`qew pr check N --drop ID --reason TEXT` removes a checklist item the pull request will not finish: every other tick stays, the drop
and its reason are kept on the item and the pull request record, and the item itself stays open in the queue.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld

BODY = "## Progress\n\n- [x] RED test <!-- pr-progress:red -->\n- [ ] later work <!-- pr-progress:later -->\n- [ ] implementation <!-- pr-progress:green -->\n\nTrailer: kept as is.\n"


class CheckDropWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item()
        self.assertEqual(self.w.open(self.item).code, 0)
        self.w.forge.edit_pull(1, body=BODY)

    def body(self) -> str:
        return self.w.forge.pulls[1].body

    def test_a_box_is_dropped_the_rest_is_untouched_and_the_drop_is_recorded(self) -> None:
        """Prevents close-and-reopen to get rid of an item the PR will not finish: only that line goes, ticks stay, reason is kept."""
        run = self.w.qew("pr", "check", "1", "--drop", "later", "--reason", "moves to the follow-up item")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.body(), BODY.replace("- [ ] later work <!-- pr-progress:later -->\n", ""))
        self.assertIn("moves to the follow-up item", json.dumps(self.w.events(self.item)))
        record = next(m for m in self.w.ledger.load_mirrors(self.item) if m.get("kind") == "pr")
        self.assertIn("later", json.dumps(record["dropped"]))
        self.assertIn("moves to the follow-up item", json.dumps(record["dropped"]))
        self.assertEqual(self.w.ledger.snapshot().folds[self.item].state.value, "claimed")
        merged_ready = self.w.qew("pr", "check", "1", "--all")
        self.assertEqual(merged_ready.code, 0, merged_ready)
        self.assertEqual(self.body().count("- [ ]"), 0)

    def test_a_ticked_box_needs_force_and_the_refusals_write_nothing(self) -> None:
        """Prevents erasing finished work by accident: a ticked box, no reason, an unknown id, a mix with ticking, or a stranger change nothing."""
        before = self.body()
        ticked = self.w.qew("pr", "check", "1", "--drop", "red", "--reason", "x")
        self.assertEqual(ticked.code, 2, ticked)
        self.assertIn("--force", ticked.err)
        self.assertEqual(self.w.qew("pr", "check", "1", "--drop", "later").code, 2)
        self.assertEqual(self.w.qew("pr", "check", "1", "--drop", "nope", "--reason", "x").code, 2)
        self.assertEqual(self.w.qew("pr", "check", "1", "green", "--drop", "later", "--reason", "x").code, 2)
        self.assertEqual(self.w.qew("pr", "check", "1", "--drop", "later", "--reason", "x", actor="stranger").code, 3)
        self.assertEqual(self.body(), before)
        forced = self.w.qew("pr", "check", "1", "--drop", "red", "--reason", "x", "--force")
        self.assertEqual(forced.code, 0, forced)
        self.assertNotIn("pr-progress:red", self.body())

    def test_a_finished_pull_request_is_not_edited(self) -> None:
        """Prevents rewriting the record of a merged or closed pull request."""
        self.w.forge.edit_pull(1, state="closed")
        run = self.w.qew("pr", "check", "1", "--drop", "later", "--reason", "x")
        self.assertEqual(run.code, 2, run)
        self.assertEqual(self.body(), BODY)


if __name__ == "__main__":
    unittest.main()
