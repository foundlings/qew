"""`qew check` reads without the lock: a directory that shows before its item file (a create, or a merge of another checkout's commits,
halfway) is looked at again before it is called an orphan.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_check
from qew.ledger_store import Ledger
from tests.ledger_harness import World


class SettleWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World()
        self.addCleanup(self.w.close)
        self.ledger = Ledger(self.w.ledger, "TST", schema=int(os.environ.get("QEW_TEST_SCHEMA", "7")))

    def report(self, problems: list[str]) -> ledger_check.Report:
        return ledger_check.Report(problems, [], [], [], [], [])

    def test_an_orphan_that_vanishes_on_the_second_look_is_never_reported(self) -> None:
        """Prevents a create seen halfway (items/ID with no item.md yet) being reported as a structural problem."""
        seen = [self.report(["items/044aaaaaa: belongs to no item", "events/044aaaaaa: belongs to no item"]), self.report([])]
        with mock.patch.object(ledger_check, "_analyse_once", side_effect=seen) as once, mock.patch.object(ledger_check, "SETTLE_S", 5.0):
            report = ledger_check.analyse(self.ledger)
        self.assertEqual(report.problems, [])
        self.assertEqual(once.call_count, 2)

    def test_a_real_orphan_is_still_reported_after_the_settle_time(self) -> None:
        """Prevents the settle hiding an event directory whose item really is gone."""
        stuck = self.report(["events/044aaaaaa: belongs to no item"])
        with mock.patch.object(ledger_check, "_analyse_once", return_value=stuck), mock.patch.object(ledger_check, "SETTLE_S", 0.6):
            report = ledger_check.analyse(self.ledger)
        self.assertEqual(report.problems, ["events/044aaaaaa: belongs to no item"])

    def test_other_problems_are_reported_at_once(self) -> None:
        """Prevents a genuine problem waiting out the settle time."""
        bad = self.report(["items/044aaaaaa/item.md: dangling need X"])
        with mock.patch.object(ledger_check, "_analyse_once", return_value=bad) as once, mock.patch.object(ledger_check, "SETTLE_S", 5.0):
            ledger_check.analyse(self.ledger)
        self.assertEqual(once.call_count, 1)


if __name__ == "__main__":
    unittest.main()
