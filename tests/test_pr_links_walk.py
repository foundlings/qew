"""`qew pr link|unlink|links`: a pull request's link declaration lives in the ledger as one event file per change (schema 9).

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class LinksWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)
        self.main = self.w.item("Main work")
        self.other = self.w.item("Other work", claim=False)
        self.third = self.w.item("Third work", claim=False)
        self.assertEqual(self.w.open(self.main).code, 0)

    def files(self) -> list[Path]:
        return sorted((self.w.world.ledger / "prs").rglob("*.json")) if (self.w.world.ledger / "prs").is_dir() else []

    def links(self, number: str = "1") -> dict:
        run = self.w.qew("pr", "links", number, "--json")
        self.assertEqual(run.code, 0, run)
        return json.loads(run.out)

    def test_each_role_is_one_event_file_and_reads_back(self) -> None:
        """Prevents links held in a shared mutable file two clones would conflict on, or a role lost on the way back."""
        run = self.w.qew("pr", "link", "1", "--closes", self.main, "--remains", self.other, "--related", self.third)
        self.assertEqual(run.code, 0, run)
        self.assertEqual(len(self.files()), 3)
        shown = self.links()
        self.assertEqual((shown["closes"], shown["remains"], shown["related"]), ([self.main], [self.other], [self.third]))
        self.assertTrue(shown["declared"])
        self.assertIsNone(shown["no_item"])

    def test_commas_and_several_ids_per_flag_are_split(self) -> None:
        """Prevents a list `A,B` being read as one unknown item."""
        run = self.w.qew("pr", "link", "1", "--closes", f"{self.main},{self.other}")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(sorted(self.links()["closes"]), sorted([self.main, self.other]))

    def test_repeating_a_link_writes_nothing_and_a_new_role_replaces_the_old(self) -> None:
        """Prevents idempotent calls piling up events, and a re-role leaving the item under two roles."""
        self.w.qew("pr", "link", "1", "--closes", self.main)
        before = self.files()
        again = self.w.qew("pr", "link", "1", "--closes", self.main)
        self.assertEqual(again.code, 0, again)
        self.assertIn("unchanged", again.out)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.w.qew("pr", "link", "1", "--remains", self.main).code, 0)
        shown = self.links()
        self.assertEqual((shown["closes"], shown["remains"]), ([], [self.main]))

    def test_unlink_removes_and_is_idempotent(self) -> None:
        """Prevents a stale link that cannot be taken back, or an error on removing what is already gone."""
        self.w.qew("pr", "link", "1", "--closes", self.main, "--related", self.other)
        self.assertEqual(self.w.qew("pr", "unlink", "1", self.other).code, 0)
        self.assertEqual(self.links()["related"], [])
        before = self.files()
        again = self.w.qew("pr", "unlink", "1", self.other)
        self.assertEqual(again.code, 0, again)
        self.assertEqual(self.files(), before)

    def test_no_item_needs_a_reason_and_excludes_item_links(self) -> None:
        """Prevents a PR that claims to relate to nothing and to something at once."""
        self.assertEqual(self.w.qew("pr", "link", "1", "--no-item", "").code, 2)
        self.assertEqual(self.w.qew("pr", "link", "1", "--no-item", "docs typo", "--closes", self.main).code, 2)
        run = self.w.qew("pr", "link", "1", "--no-item", "docs typo")
        self.assertEqual(run.code, 0, run)
        shown = self.links()
        self.assertEqual((shown["no_item"], shown["declared"], shown["closes"]), ("docs typo", True, []))

    def test_linking_items_supersedes_a_no_item_declaration(self) -> None:
        """Prevents a PR left declared as relating to nothing after items were attached."""
        self.w.qew("pr", "link", "1", "--no-item", "docs typo")
        self.assertEqual(self.w.qew("pr", "link", "1", "--closes", self.main).code, 0)
        self.assertIsNone(self.links()["no_item"])

    def test_an_unknown_or_finished_item_is_refused_for_closes(self) -> None:
        """Prevents a typo or an already completed item being declared as what the PR finishes."""
        self.assertEqual(self.w.qew("pr", "link", "1", "--closes", "NOPE99").code, 2)
        self.assertEqual(self.files(), [])

    def test_a_pull_request_with_no_declaration_reads_as_undeclared(self) -> None:
        """Prevents silence being read as a declaration."""
        shown = self.links()
        self.assertFalse(shown["declared"])
        self.assertEqual((shown["closes"], shown["remains"], shown["related"]), ([], [], []))

    def test_link_needs_something_to_declare(self) -> None:
        """Prevents a bare `pr link N` passing as a declaration."""
        self.assertEqual(self.w.qew("pr", "link", "1").code, 2)

    def test_a_schema_8_ledger_refuses_politely_and_writes_nothing(self) -> None:
        """Prevents a 7.0.0 binary installed before the cutover writing link files an older qew cannot read."""
        old = PrWorld(schema="8")
        self.addCleanup(old.close)
        item = old.item("Main work")
        self.assertEqual(old.open(item).code, 0)
        run = old.qew("pr", "link", "1", "--closes", item)
        self.assertEqual(run.code, 2, run)
        self.assertIn("migrate-layout", run.err)
        self.assertFalse((old.world.ledger / "prs").exists())


if __name__ == "__main__":
    unittest.main()
