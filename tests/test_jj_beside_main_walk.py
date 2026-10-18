"""A jj ledger whose working copy sits beside `main` still takes writes: the chain is merged into main, never refused.

Each test is a scenario; its docstring names the failure it prevents (seen live on foundlings-ledger: every write failed with
'Refusing to move bookmark backwards or sideways: main' and left another commit stranded off main).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World, jj, jj_available


@unittest.skipUnless(jj_available(), "jj is not installed")
class JjBesideMainWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="jj")
        self.addCleanup(self.world.close)
        self.first = self.world.create("--title", "First", "--quote", QUOTE)

    def strand(self) -> None:
        """What the live ledger looked like: `main` moved to a commit beside the one the working copy sits on."""
        jj(self.world.ledger, "new", "main-", "-m", "beside")
        side = jj(self.world.ledger, "log", "-r", "@", "--no-graph", "-T", "commit_id").strip()
        jj(self.world.ledger, "new", "main", "-m", "working copy")
        jj(self.world.ledger, "bookmark", "set", "main", "-r", side, "--allow-backwards")

    def test_a_write_beside_main_is_merged_into_main(self) -> None:
        """Prevents the stuck ledger: the write succeeds, main holds both the commit it moved to and the new write, and the
        earlier item is still there."""
        self.strand()
        second = self.world.create("--title", "Second", "--quote", QUOTE)
        listed = self.world.run("list").stdout
        self.assertIn(self.first, listed)
        self.assertIn(second, listed)
        message = jj(self.world.ledger, "log", "-r", "main", "--no-graph", "-T", "description")
        self.assertIn("qew: merge", message)
        self.assertEqual(jj(self.world.ledger, "log", "-r", "main::@ ~ main", "--no-graph", "-T", "commit_id").strip().count("\n"), 0)

    def test_nothing_is_left_off_main_after_the_write(self) -> None:
        """Prevents a stranded chain: after the write every non-empty commit is an ancestor of main."""
        self.strand()
        self.world.create("--title", "Second", "--quote", QUOTE)
        left = jj(self.world.ledger, "log", "-r", "(::@ | heads(all())) ~ ::main ~ empty()", "--no-graph", "-T", "commit_id ++ \"\\n\"").strip()
        self.assertEqual(left, "")


if __name__ == "__main__":
    unittest.main()
