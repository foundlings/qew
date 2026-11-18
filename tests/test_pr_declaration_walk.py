"""`qew pr open` declares what the pull request does to ledger items (schema 9); schema 8 keeps the old open.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class DeclarationWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)

    def links(self, number: str = "1") -> dict:
        return json.loads(self.w.qew("pr", "links", number, "--json").out)

    def test_open_without_a_declaration_is_refused_with_the_command_to_run(self) -> None:
        """Prevents a pull request existing with no statement of what it does to the ledger, and a refusal that names no fix."""
        item = self.w.item("Work")
        run = self.w.open(item)
        self.assertEqual(run.code, 2, run)
        self.assertIn(f"--closes {item}", run.err)
        self.assertEqual(self.w.forge.pulls, {})

    def test_open_with_closes_records_the_declaration_in_the_ledger(self) -> None:
        """Prevents the declaration living only in forge text, or being dropped between the forge call and the ledger."""
        item = self.w.item("Work")
        other = self.w.item("Other", claim=False)
        run = self.w.open(item, "--closes", item, "--remains", other)
        self.assertEqual(run.code, 0, run)
        shown = self.links()
        self.assertEqual((shown["closes"], shown["remains"], shown["declared"]), ([item], [other], True))

    def test_the_item_opened_for_must_be_named_in_the_declaration(self) -> None:
        """Prevents a pull request opened for an item that its declaration says nothing about."""
        item = self.w.item("Work")
        other = self.w.item("Other", claim=False)
        run = self.w.open(item, "--closes", other)
        self.assertEqual(run.code, 2, run)
        self.assertIn(item, run.err)
        self.assertEqual(self.w.forge.pulls, {})

    def test_an_unknown_item_in_the_declaration_is_refused_before_the_forge(self) -> None:
        """Prevents a pull request opened on the forge whose declaration then fails to record."""
        item = self.w.item("Work")
        run = self.w.open(item, "--closes", item, "--remains", "NOPE99")
        self.assertEqual(run.code, 2, run)
        self.assertEqual(self.w.forge.pulls, {})

    def test_remains_on_an_epic_does_not_cover_its_open_children_but_closes_does_not_pass(self) -> None:
        """Prevents a partial-slice pull request being judged as if it finished the epic, and a closes of an epic with open children passing."""
        epic = self.w.item("Epic", kind="epic")
        self.w.item("Child", parent=epic, claim=False)
        partial = self.w.open(epic, "--remains", epic)
        self.assertEqual(partial.code, 0, partial)
        self.assertEqual(self.w.qew("pr", "merge", "1", "--check", "--ledger-only").code, 0)
        self.w.head = "second"
        second = self.w.open(epic, "--closes", epic)
        self.assertEqual(second.code, 0, second)
        refused = self.w.qew("pr", "merge", "2", "--check", "--ledger-only")
        self.assertEqual(refused.code, 2, refused)
        self.assertIn("open children", refused.err)

    def test_schema_8_keeps_the_plain_open_and_refuses_declaration_flags(self) -> None:
        """Prevents the compat mode changing what a 6.x agent does, or silently ignoring flags it cannot record."""
        old = PrWorld(schema="8")
        self.addCleanup(old.close)
        item = old.item("Work")
        self.assertEqual(old.open(item, "--closes", item).code, 2)
        self.assertEqual(old.forge.pulls, {})
        self.assertEqual(old.open(item).code, 0)


if __name__ == "__main__":
    unittest.main()
