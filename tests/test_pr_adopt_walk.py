"""Walks of adopting an existing pull request (`qew pr open` over a head that already has one) and `qew pr close`.

Adversary: a crash between the forge accepting a pull request and the ledger recording it leaves an orphan that
re-opening can never fix; adopting must not take over a stranger's pull request; closing must not touch a
finished one or one the caller does not own, and must not report closed when the forge stayed open.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import pr_body
from qew.forge import PullRequestSpec
from tests.pr_harness import PrWorld


class AdoptWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)

    def orphan(self, item: str, head: str = "feature", draft: bool = True, marker_item: str = "") -> int:
        """A pull request qew opened on the forge whose ledger record never got written."""
        issue = next(m for m in self.w.ledger.load_mirrors(item) if m["kind"] == "issue")
        body = pr_body.build_body(marker_item or item, issue["number"], "work", None, None, "")
        return self.w.forge.open_pull_request(PullRequestSpec(head, "main", "Work", body, draft, "tester")).number

    def test_reopening_a_head_adopts_its_orphan_instead_of_opening_a_second_pull_request(self) -> None:
        """Prevents an orphan blocking its head for good: the open is recorded against the pull request that exists."""
        item = self.w.item("Parse headers")
        number = self.orphan(item)
        run = self.w.open(item)
        self.assertEqual(run.code, 0, run)
        self.assertIn(f"pr #{number}", run.out)
        self.assertIn("adopted", run.out)
        self.assertEqual(len(self.w.forge.pulls), 1)
        self.assertEqual(self.w.forge.count("open_pr"), 1)
        files = self.w.mirror_files(item)
        self.assertEqual(len(files), 1)
        record = json.loads((self.w.world.ledger / "mirrors" / item / files[0]).read_text())
        self.assertEqual((record["number"], record["draft"], record["state"], record["items"], record["opener"]), (number, True, "open", [item], "tester"))
        self.assertEqual(self.w.qew("pr", "ready", str(number)).code, 0)

    def test_a_pull_request_that_is_not_this_items_is_never_adopted(self) -> None:
        """Prevents taking over a stranger's pull request (no marker) or another item's on the same head."""
        item = self.w.item("Mine")
        other = self.w.item("Theirs")
        stranger = self.w.forge.open_pull_request(PullRequestSpec("feature", "main", "Not qew", "plain text", True, "x")).number
        run = self.w.open(item)
        self.assertEqual(run.code, 2, run)
        self.assertIn(f"#{stranger}", run.err)
        self.assertIn("qew pr close", run.err)
        self.w.forge.close_pull_request(stranger)
        self.orphan(other)
        run = self.w.open(item)
        self.assertEqual(run.code, 2, run)
        self.assertIn(other, run.err)
        self.assertEqual((self.w.mirror_files(item), self.w.forge.count("open_pr")), ([], 3))

    def test_a_closed_pull_request_on_the_head_does_not_block_a_fresh_open(self) -> None:
        """Prevents adopting a finished pull request."""
        item = self.w.item()
        number = self.orphan(item)
        self.w.forge.close_pull_request(number)
        run = self.w.open(item)
        self.assertEqual(run.code, 0, run)
        self.assertEqual(len(self.w.forge.pulls), 2)
        self.assertNotIn("adopted", run.out)


class CloseWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item()
        self.assertEqual(self.w.open(self.item).code, 0)

    def test_close_closes_the_forge_pull_request_and_records_it_in_the_ledger(self) -> None:
        """Prevents a pull request that stays open on the forge or looks open in the ledger after a close."""
        run = self.w.qew("pr", "close", "1")
        self.assertEqual((run.code, run.out.strip()), (0, "pr #1 closed"))
        self.assertEqual(self.w.forge.pulls[1].state, "closed")
        record = json.loads((self.w.world.ledger / "mirrors" / self.item / self.w.mirror_files(self.item)[0]).read_text())
        self.assertEqual(record["state"], "closed")
        self.assertEqual(json.loads(self.w.qew("pr", "close", "1", "--json").out), {"number": 1, "closed": True, "changed": False, "schema_version": 1})
        self.assertEqual(self.w.forge.count("close_pr"), 1)

    def test_a_merged_pull_request_cannot_be_closed(self) -> None:
        """Prevents rewriting a merged pull request's record as closed."""
        self.w.forge.edit_pull(1, state="merged")
        run = self.w.qew("pr", "close", "1")
        self.assertEqual(run.code, 2, run)
        self.assertEqual(self.w.forge.count("close_pr"), 0)

    def test_nobody_but_the_owner_closes_a_pull_request(self) -> None:
        """Prevents closing a teammate's pull request by accident."""
        run = self.w.qew("pr", "close", "1", actor="other")
        self.assertEqual(run.code, 3, run)
        self.assertEqual(self.w.forge.pulls[1].state, "open")

    def test_a_forge_that_stays_open_after_the_close_is_an_indeterminate_failure(self) -> None:
        """Prevents false success when the forge ignored the change."""
        self.w.forge.close_ignored = True
        run = self.w.qew("pr", "close", "1")
        self.assertEqual(run.code, 4, run)
        self.assertIn("still open", run.err)
        record = json.loads((self.w.world.ledger / "mirrors" / self.item / self.w.mirror_files(self.item)[0]).read_text())
        self.assertEqual(record["state"], "open")


if __name__ == "__main__":
    unittest.main()
