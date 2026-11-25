"""A hostless and item-linked record for one PR resolves to one target identity."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import pr_links
from tests.pr_harness import PrWorld


class DuplicateRecordWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)
        self.item = self.w.item("Work")
        self.assertEqual(self.w.open(self.item, "--related", self.item).code, 0)
        self.head = self.w.commit("work")
        self.w.forge.edit_pull(1, head_sha=self.head)
        linked = next(mirror for mirror in self.w.ledger.load_mirrors(self.item) if mirror["kind"] == "pr")
        self.target = linked["_target"]
        hostless = {
            name: linked[name]
            for name in ("forge", "url", "number", "pr_url", "head", "opener", "draft", "state", "synced_at")
        }
        pr_links.declare_pr(self.w.ledger, self.target, hostless)

    def test_links_link_and_merge_select_the_same_target_with_duplicate_records(self) -> None:
        """Prevents a duplicate hostless record from blocking declaration reads, writes, or the merge preflight."""
        shown = self.w.qew("pr", "links", "1", "--target", self.target, "--json")
        self.assertEqual(shown.code, 0, shown)

        linked = self.w.qew("pr", "link", "1", "--remains", self.item, "--target", self.target)
        self.assertEqual(linked.code, 0, linked)
        self.assertIn(f"remains {self.item}", linked.out)

        readied = self.w.qew("pr", "ready", "1", "--target", self.target)
        self.assertEqual(readied.code, 0, readied)
        merged = self.w.qew("pr", "merge", "1", "--target", self.target)
        self.assertEqual(merged.code, 0, merged)
        self.assertEqual(self.w.forge.merged, [(1, self.head)])


if __name__ == "__main__":
    unittest.main()
