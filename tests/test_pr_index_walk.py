"""Judging many pull requests together costs one pass over the ledger's mirrors, not one pass per pull request.

A union of 26 pull requests on a 60,000 event ledger took 373 s because every number re-scanned every item's mirrors.
Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_store import Ledger
from tests.pr_harness import PrWorld
from tests.test_pr_preflight_walk import NoForge

FILLER = 20


class IndexWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        for n in range(FILLER):
            self.w.item(f"Filler {n}", claim=False)
        self.numbers: list[int] = []
        for n in range(1, 7):
            item = self.w.item(f"Work {n}")
            self.w.head = f"branch-{n}"
            self.assertEqual(self.w.open(item).code, 0)
            self.numbers.append(n)

    def mirror_loads(self, *argv: str, forge: object = None) -> int:
        calls = 0
        real = Ledger.load_mirrors

        def counted(ledger: Ledger, item_id: str):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            return real(ledger, item_id)

        with mock.patch.object(Ledger, "load_mirrors", counted):
            run = self.w.qew(*argv, forge=forge)  # type: ignore[arg-type]
        self.assertIn(run.code, (0, 2), run)  # a refusal is fine: the cost is what is measured
        return calls

    def test_ledger_only_check_loads_mirrors_once_per_item_however_many_pull_requests(self) -> None:
        """Prevents the per-pull-request rescan that made a 26-PR union take 373 s: six numbers must cost about what one does."""
        one = self.mirror_loads("pr", "merge", "1", "--check", "--ledger-only", forge=NoForge())
        six = self.mirror_loads("pr", "merge", *map(str, self.numbers), "--check", "--ledger-only", forge=NoForge())
        self.assertLess(six, one * 2, f"one number loaded mirrors {one} times, six numbers {six}: the cost grows with the PR count")

    def test_forge_check_shares_the_same_index(self) -> None:
        """Prevents the forge-reading --check rescanning the ledger per pull request (218 s for 26)."""
        for number in self.numbers:
            self.w.qew("pr", "ready", str(number))
        one = self.mirror_loads("pr", "merge", "1", "--check")
        six = self.mirror_loads("pr", "merge", *map(str, self.numbers), "--check")
        self.assertLess(six, one * 2, f"one number loaded mirrors {one} times, six numbers {six}")


if __name__ == "__main__":
    unittest.main()
