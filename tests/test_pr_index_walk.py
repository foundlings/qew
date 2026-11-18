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
        self.fillers = {self.w.item(f"Filler {n}", claim=False) for n in range(FILLER)}
        self.numbers: list[int] = []
        for n in range(1, 7):
            item = self.w.item(f"Work {n}")
            self.w.head = f"branch-{n}"
            self.assertEqual(self.w.open(item).code, 0)
            self.numbers.append(n)

    def loads(self, *argv: str, forge: object = None) -> dict[str, int]:
        """How many times each item's mirrors were loaded while `qew ARGV` ran."""
        per_item: dict[str, int] = {}
        real = Ledger.load_mirrors

        def counted(ledger: Ledger, item_id: str):  # type: ignore[no-untyped-def]
            per_item[item_id] = per_item.get(item_id, 0) + 1
            return real(ledger, item_id)

        with mock.patch.object(Ledger, "load_mirrors", counted):
            run = self.w.qew(*argv, forge=forge)  # type: ignore[arg-type]
        self.assertIn(run.code, (0, 2), run)  # a refusal is fine: the cost is what is measured
        return per_item

    def test_ledger_only_check_loads_each_pull_request_host_a_bounded_number_of_times(self) -> None:
        """Prevents the per-pull-request rescan that made a 26-PR union take 373 s: no item's mirrors are loaded once per number,
        and items that host no pull request are never loaded at all."""
        six = self.loads("pr", "merge", *map(str, self.numbers), "--check", "--ledger-only", forge=NoForge())
        self.assertLessEqual(max(six.values()), 3, six)
        self.assertEqual([n for n in six if n in self.fillers], [], "an item that hosts no pull request had its mirrors loaded")

    def test_forge_check_shares_the_same_index(self) -> None:
        """Prevents the forge-reading --check rescanning the ledger per pull request (218 s for 26)."""
        for number in self.numbers:
            self.w.qew("pr", "ready", str(number))
        six = self.loads("pr", "merge", *map(str, self.numbers), "--check")
        self.assertLessEqual(max(six.values()), 3, six)
        self.assertEqual([n for n in six if n in self.fillers], [])


if __name__ == "__main__":
    unittest.main()
