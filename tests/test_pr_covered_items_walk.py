"""A pull request's covered items follow the work: `pr check --add` extends them and `pr sync N --items` repairs a record that froze at open.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class CoveredItemsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.root = self.w.item("Root", kind="epic")
        self.assertEqual(self.w.open(self.root).code, 0)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)

    def covered(self) -> list[str]:
        (name,) = self.w.mirror_files(self.root)
        return json.loads((self.w.world.ledger / "mirrors" / self.root / name).read_text())["items"]

    def child(self) -> str:
        made = self.w.item("Child", parent=self.root, claim=False)
        return made

    def test_a_child_created_after_open_blocks_the_merge_until_it_is_added(self) -> None:
        """Prevents a PR that gained work after opening being unmergeable even with every box ticked, with no command to cover the child."""
        child = self.child()
        self.assertEqual(self.covered(), [self.root])
        refused = self.w.qew("pr", "merge", "1")
        self.assertIn("open children", refused.err)
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", child).code, 0)
        self.assertEqual(self.covered(), [self.root, child])
        self.assertNotIn("open children", self.w.qew("pr", "merge", "1").err)

    def test_sync_items_re_derives_the_covered_items_of_a_frozen_record(self) -> None:
        """Prevents an existing PR, whose record froze before its children existed, having no repair short of hand-editing the mirror."""
        child = self.child()
        run = self.w.qew("pr", "sync", "1", "--items")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.covered(), [self.root, child])
        again = self.w.qew("pr", "sync", "1", "--items")
        self.assertEqual(again.code, 0, again)
        self.assertEqual(self.covered(), [self.root, child])
        self.assertNotIn("open children", self.w.qew("pr", "merge", "1").err)


if __name__ == "__main__":
    unittest.main()
