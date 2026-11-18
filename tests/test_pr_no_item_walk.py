"""A pull request or draft can be opened with no ledger item: `pr open --no-item REASON --title TEXT` (schema 9).

Its record lives under `prs/`, and the pr verbs work on it as on any other.
Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class NoItemWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)

    def open(self, *extra: str):
        return self.w.qew("pr", "open", "--no-item", "docs typo", "--title", "Fix a docs typo", "--head", "docs-fix", "--base", "main", *extra)

    def test_open_creates_the_pull_request_and_a_record_under_prs_with_no_item(self) -> None:
        """Prevents the rule `a PR needs an item` forcing a placeholder item for work that has none."""
        run = self.open("--why", "readers hit it")
        self.assertEqual(run.code, 0, run)
        pull = self.w.forge.pulls[1]
        self.assertEqual(pull.title, "Fix a docs typo")
        self.assertIn("docs typo", pull.body)
        self.assertIn("readers hit it", pull.body)
        self.assertNotIn("Delivers", pull.body)
        self.assertEqual(self.w.mirror_files("anything"), [])
        shown = json.loads(self.w.qew("pr", "links", "1", "--json").out)
        self.assertEqual((shown["no_item"], shown["declared"]), ("docs typo", True))
        self.assertEqual(self.w.qew("check").code, 0)

    def test_the_verbs_work_on_a_pull_request_with_no_item(self) -> None:
        """Prevents a hostless record failing every verb that assumed an item to read it from."""
        self.open()
        self.assertEqual(self.w.qew("pr", "title", "1", "Fix the docs typo").code, 0)
        self.assertEqual(self.w.forge.pulls[1].title, "Fix the docs typo")
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)
        self.assertFalse(self.w.forge.pulls[1].draft)
        self.assertEqual(self.w.qew("pr", "merge", "1", "--check", "--ledger-only").code, 0)
        merged = self.w.qew("pr", "merge", "1")
        self.assertEqual(merged.code, 0, merged)
        self.assertEqual(self.w.forge.pulls[1].state, "merged")
        self.assertEqual(self.w.qew("land", "1").code, 0)

    def test_close_works_on_a_pull_request_with_no_item(self) -> None:
        """Prevents an abandoned no-item pull request being impossible to close through qew."""
        self.open()
        run = self.w.qew("pr", "close", "1")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.w.forge.pulls[1].state, "closed")

    def test_an_item_can_be_linked_afterwards(self) -> None:
        """Prevents a no-item pull request that turns out to deliver an item being stuck without one."""
        item = self.w.item("Work")
        self.open()
        self.assertEqual(self.w.qew("pr", "link", "1", "--closes", item).code, 0)
        shown = json.loads(self.w.qew("pr", "links", "1", "--json").out)
        self.assertEqual((shown["closes"], shown["no_item"]), ([item], None))

    def test_no_item_needs_a_title_and_no_item_argument_and_schema_9(self) -> None:
        """Prevents a nameless pull request, a contradictory call, or a record a schema 8 ledger cannot hold."""
        self.assertEqual(self.w.qew("pr", "open", "--no-item", "x", "--head", "h").code, 2)
        item = self.w.item("Work")
        self.assertEqual(self.w.qew("pr", "open", item, "--no-item", "x", "--title", "t", "--head", "h").code, 2)
        old = PrWorld(schema="8")
        self.addCleanup(old.close)
        run = old.qew("pr", "open", "--no-item", "x", "--title", "t", "--head", "h")
        self.assertEqual(run.code, 2, run)
        self.assertEqual(old.forge.pulls, {})


if __name__ == "__main__":
    unittest.main()
