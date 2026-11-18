"""The pull request description carries the declared qew items, in one machine-owned block (schema 9).

The ledger is the source of truth: the block is a projection that qew writes and never reads back.
Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld

BLOCK = re.compile(r"<!-- qew:links v1 -->.*?<!-- /qew:links -->", re.S)
CLOSING = re.compile(r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[: ]+\S*#\d+", re.I)


class ProjectionWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)
        self.main = self.w.item("Main work")
        self.other = self.w.item("Other work", claim=False)
        self.third = self.w.item("Third work", claim=False)

    def body(self, number: int = 1) -> str:
        return self.w.forge.pulls[number].body

    def block(self, number: int = 1) -> str:
        found = BLOCK.search(self.body(number))
        self.assertIsNotNone(found, self.body(number))
        return found.group(0) if found else ""

    def test_open_writes_every_declared_item_with_its_title_after_the_sections(self) -> None:
        """Prevents a description that says what and why but not which ledger items it is about, or lists only the first item."""
        run = self.w.open(self.main, "--closes", self.main, "--remains", self.other, self.third, "--why", "because", "--testing", "walk")
        self.assertEqual(run.code, 0, run)
        shown = self.block()
        for item, title in ((self.main, "Main work"), (self.other, "Other work"), (self.third, "Third work")):
            self.assertIn(item, shown)
            self.assertIn(title, shown)
        body = self.body()
        self.assertLess(body.index("## Testing"), body.index("<!-- qew:links v1 -->"))
        self.assertRegex(shown, rf"(?i)closes[^\n]*{self.main}")
        self.assertRegex(shown, rf"(?i)remains[^\n]*{self.other}")

    def test_link_and_unlink_rewrite_only_the_block(self) -> None:
        """Prevents the description going stale after a re-declaration, or a rewrite touching the text around the block."""
        self.w.open(self.main, "--closes", self.main, "--why", "because")
        before = self.body()
        outside = BLOCK.sub("", before)
        self.assertEqual(self.w.qew("pr", "link", "1", "--related", self.other).code, 0)
        self.assertIn(self.other, self.block())
        self.assertEqual(BLOCK.sub("", self.body()), outside)
        self.assertEqual(self.w.qew("pr", "unlink", "1", self.other).code, 0)
        self.assertNotIn(self.other, self.block())

    def test_the_block_never_carries_a_closing_keyword_even_for_a_hostile_title(self) -> None:
        """Prevents the forge closing an issue when the pull request merges, through a title that says `Fixes #12`."""
        hostile = self.w.item("Fixes #12 and closes #13", claim=False)
        self.w.open(self.main, "--closes", self.main, "--related", hostile)
        self.assertIsNone(CLOSING.search(self.body()), self.body())
        self.assertIn(hostile, self.block())

    def test_a_forge_that_is_down_never_loses_the_declaration(self) -> None:
        """Prevents a forge outage blocking `pr link` or dropping the link the ledger is the source of truth for."""
        self.w.open(self.main, "--closes", self.main)
        self.w.forge.down = True
        run = self.w.qew("pr", "link", "1", "--related", self.other)
        self.assertEqual(run.code, 0, run)
        self.assertIn("pr sync", run.err)
        self.assertEqual(json.loads(self.w.qew("pr", "links", "1", "--json").out)["related"], [self.other])
        self.w.forge.down = False
        self.assertEqual(self.w.qew("pr", "sync", "1").code, 0)
        self.assertIn(self.other, self.block())

    def test_pr_sync_rewrites_a_block_edited_on_the_forge_and_says_so(self) -> None:
        """Prevents a hand-edited block on the forge standing as the record: the ledger wins and the drift is reported."""
        self.w.open(self.main, "--closes", self.main)
        tampered = BLOCK.sub("<!-- qew:links v1 -->\nCloses: NOTHING\n<!-- /qew:links -->", self.body()) + "\nreviewer note\n"
        self.w.forge.edit_pull(1, body=tampered)
        run = self.w.qew("pr", "sync", "1")
        self.assertEqual(run.code, 0, run)
        self.assertIn("drift", run.err)
        self.assertNotIn("NOTHING", self.body())
        self.assertIn(self.main, self.block())
        self.assertIn("reviewer note", self.body())

    def test_a_schema_8_description_has_no_block(self) -> None:
        """Prevents the compat mode changing the body a 6.x agent expects."""
        old = PrWorld(schema="8")
        self.addCleanup(old.close)
        item = old.item("Work")
        old.open(item)
        self.assertNotIn("qew:links", old.forge.pulls[1].body)


if __name__ == "__main__":
    unittest.main()
