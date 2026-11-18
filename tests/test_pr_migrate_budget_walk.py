"""`qew pr migrate` spends the forge's REST quota only where a body is needed: open pull requests, within a budget and a floor.

A merged pull request's items are landed or pending by ledger state, so its body is never read; the report says how many forge reads were made.
Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import forge_http, pr_record
from tests.pr_harness import PrWorld


class BudgetWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="8")
        self.addCleanup(self.w.close)
        self.count = 0
        self.items: list[str] = []
        for _ in range(3):
            item = self.w.item(f"Work {self.count}")
            self.count += 1
            self.w.head = f"branch-{self.count}"
            self.assertEqual(self.w.open(item).code, 0)
            self.items.append(item)

    def reads(self) -> int:
        return len([c for c in self.w.forge.calls if c == ("read_pr",)])

    def migrate(self, *extra: str) -> dict:
        before = self.reads()
        run = self.w.qew("pr", "migrate", "--dry-run", "--json", *extra)
        self.assertEqual(run.code, 0, run)
        payload = json.loads(run.out)
        payload["made"] = self.reads() - before
        return payload

    def test_a_merged_pull_request_is_never_read_and_keeps_only_what_the_ledger_proves(self) -> None:
        """Prevents thousands of merged pull requests costing a REST read each: the ledger already says what they delivered."""
        record = pr_record.find(self.w.ledger, 1, None)
        pr_record.update_mirror(self.w.ledger, record, state="merged")
        self.assertEqual(self.w.qew("evidence", self.items[0], "--change", self.w.commit("x")).code, 0)
        self.assertEqual(self.w.qew("complete", self.items[0]).code, 0)
        shown = self.migrate()
        self.assertEqual(shown["made"], 2)  # the two open ones only
        row = next(r for r in shown["pull_requests"] if r["number"] == 1)
        self.assertEqual({(i["item"], i["role"]) for i in row["items"]}, {(self.items[0], "closes")})
        record = pr_record.find(self.w.ledger, 2, None)
        pr_record.update_mirror(self.w.ledger, record, state="merged")
        unfinished = next(r for r in self.migrate()["pull_requests"] if r["number"] == 2)
        self.assertEqual({i["role"] for i in unfinished["items"]}, {"remains"})  # merged, not completed, box state unknown: never closes

    def test_offline_reads_nothing_and_flags_the_open_pull_requests_whose_boxes_it_cannot_see(self) -> None:
        """Prevents a migration with no forge reading the forge, or treating unknown boxes as delivered."""
        shown = self.migrate("--offline")
        self.assertEqual((shown["made"], shown["forge_reads"]), (0, 0))
        self.assertTrue(all({i["role"] for i in r["items"]} == {"remains"} for r in shown["pull_requests"]))
        self.assertTrue(all(any("box state" in f for f in r["flags"]) for r in shown["pull_requests"]))

    def test_the_read_budget_stops_the_reads_and_says_so(self) -> None:
        """Prevents a run past its budget silently continuing: the rest are flagged and the report names the stop."""
        shown = self.migrate("--max-forge-calls", "1")
        self.assertEqual((shown["made"], shown["forge_reads"]), (1, 1))
        self.assertIn("budget", shown["stopped"])
        self.assertEqual(len([r for r in shown["pull_requests"] if any("box state" in f for f in r["flags"])]), 2)

    def test_a_low_rate_limit_stops_before_the_first_read(self) -> None:
        """Prevents the migration burning the shared quota the merge gate needs: below the floor it stops and reports."""
        with mock.patch.object(forge_http.WATCH, "remaining", 100):
            shown = self.migrate()
        self.assertEqual(shown["made"], 0)
        self.assertIn("rate limit", shown["stopped"])

    def test_the_text_report_prints_the_forge_reads(self) -> None:
        """Prevents the cost of a run being invisible."""
        run = self.w.qew("pr", "migrate", "--dry-run")
        self.assertIn("forge reads: 3", run.out)


if __name__ == "__main__":
    unittest.main()
