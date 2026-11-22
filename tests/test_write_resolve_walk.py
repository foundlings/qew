"""A write verb that names an item by its exact id goes to that item: it does not list the ledger or read every item header.

Adversary: `qew comment ID` on a 28,000-item ledger spending 3 s of an 8 s write, under the ledger lock, listing every item
directory and reading every header to resolve an id that is already a directory name; fifteen writers queue behind that.
"""

import re
import unittest

from tests.ledger_harness import QUOTE, World

TRACE = {"QEW_TRACE": "1"}
LISTED = re.compile(r"list items")
SCANNED = re.compile(r"scan item headers \(full\)")


class WriteResolveWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.first = self.world.create("--title", "first", "--quote", QUOTE)

    def comment(self, item: str) -> str:
        run = self.world.run("comment", item, "--body", "a note", env=TRACE)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stderr

    def test_a_cold_comment_names_the_listing_and_the_header_scan_in_its_trace(self) -> None:
        """Prevents the cost being invisible: the trace says when a write lists items or reads every header."""
        err = self.comment(self.first)
        self.assertRegex(err, LISTED)
        self.assertRegex(err, SCANNED)


if __name__ == "__main__":
    unittest.main()
