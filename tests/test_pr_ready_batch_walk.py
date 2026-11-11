"""Walks of `qew pr ready A B C`: several draft pull requests marked ready in one command.

Adversary: a batch that costs one ledger snapshot and one serial forge write per pull request, a refusal on one member that
hides or blocks the others, a report that loses the order of the numbers given, and a ledger left calling a ready pull
request a draft.
"""

import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_store import Ledger
from tests.pr_harness import PrWorld


class ReadyBatchWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        for n in (1, 2, 3):
            item = self.w.item(f"Work {n}")
            self.w.head = f"branch-{n}"
            self.assertEqual(self.w.open(item).code, 0)

    def drafts(self) -> list[bool]:
        return [self.w.forge.pulls[n].draft for n in (1, 2, 3)]

    def test_several_drafts_become_ready_in_the_order_given(self) -> None:
        """Prevents a batch that marks only the first, or reports out of order."""
        run = self.w.qew("pr", "ready", "3", "1", "2")
        self.assertEqual((run.code, run.out.splitlines()), (0, ["pr #3 ready", "pr #1 ready", "pr #2 ready"]), run)
        self.assertEqual(self.drafts(), [False, False, False])

    def test_json_of_several_is_one_list_in_the_order_given(self) -> None:
        run = self.w.qew("pr", "ready", "2", "1", "--json")
        self.assertEqual(run.code, 0, run)
        self.assertEqual([row["number"] for row in json.loads(run.out)], [2, 1])

    def test_a_batch_takes_one_snapshot_and_writes_the_forge_side_by_side(self) -> None:
        """Prevents the cost of one ledger snapshot and one serial forge write per pull request."""
        snapshots: list[int] = []
        flying = [0, 0]  # in flight now, most in flight at once
        lock = threading.Lock()
        snapshot, ready = Ledger.snapshot, self.w.forge.mark_pull_request_ready

        def counted(ledger: Ledger, *args: object, **kwargs: object):
            snapshots.append(1)
            return snapshot(ledger, *args, **kwargs)

        def slow(number: int):
            with lock:
                flying[0] += 1
                flying[1] = max(flying)
            time.sleep(0.2)
            try:
                return ready(number)
            finally:
                with lock:
                    flying[0] -= 1

        with mock.patch.object(Ledger, "snapshot", counted), mock.patch.object(self.w.forge, "mark_pull_request_ready", slow):
            run = self.w.qew("pr", "ready", "1", "2", "3")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(len(snapshots), 1)
        self.assertGreater(flying[1], 1)

    def test_one_refused_member_does_not_stop_the_others_and_is_named_with_its_exit_code(self) -> None:
        """Prevents one merged pull request hiding or blocking the rest: the others are marked, the refusal is reported last."""
        self.w.forge.edit_pull(2, state="merged")
        run = self.w.qew("pr", "ready", "1", "2", "3")
        self.assertEqual(run.code, 2, run)
        self.assertIn("pr #2", run.err)
        self.assertEqual(self.drafts(), [False, True, False])
        self.assertEqual(run.out.splitlines(), ["pr #1 ready", "pr #3 ready"])

    def test_the_first_refusal_in_the_order_given_is_the_one_reported(self) -> None:
        self.w.forge.edit_pull(3, state="closed")
        self.w.forge.edit_pull(1, state="merged")
        run = self.w.qew("pr", "ready", "3", "1", "2")
        self.assertEqual(run.code, 2, run)
        self.assertLess(run.err.index("pr #3"), run.err.index("pr #1"))

    def test_every_member_is_recorded_as_no_longer_a_draft_in_the_ledger(self) -> None:
        """Prevents a ledger that still calls a ready pull request a draft."""
        self.assertEqual(self.w.qew("pr", "ready", "1", "2", "3").code, 0)
        records = [m for item in self.w.ledger.item_ids() for m in self.w.ledger.load_mirrors(item) if m["kind"] == "pr"]
        self.assertEqual([r["draft"] for r in records], [False, False, False])


if __name__ == "__main__":
    unittest.main()
