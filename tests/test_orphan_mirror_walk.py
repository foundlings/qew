"""A mirror file whose item is missing does not make the ledger unreadable.

Adversary (seen live on a ledger written by ~20 agents at once): a sync committed `mirrors/.../ID/` while the item `ID` never
reached the ledger, and every read (`search`, `pr open`, `list`) of every agent refused with 'belongs to no item' until someone
removed the stray file by hand. A mirror is only a view of an item, so a stray one is reported and nothing else stops.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World
from tests.spec_loader import load, spec


class OrphanMirrorWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        load(self.world, spec("A"))
        stray = self.world.home("mirrors", "STRAY")
        stray.mkdir(parents=True)
        (stray / "github-x.json").write_text(json.dumps({"forge": "github", "kind": "issue", "number": 7, "url": "https://github.com/o/r",
                                                         "issue_url": "https://github.com/o/r/issues/7", "node_id": "I_x"}))

    def test_reads_still_list_the_items(self) -> None:
        """Prevents one stray mirror from stopping every read: `list` answers and shows the item."""
        result = self.world.run("list")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("A", result.stdout)

    def test_check_names_the_stray_mirror_without_failing(self) -> None:
        """Prevents hiding it: `qew check` still names the file so someone removes it, but it is a note, not a failure."""
        result = self.world.run("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mirrors/STRAY: belongs to no item", result.stdout)

    def test_a_stray_events_directory_still_refuses(self) -> None:
        """Prevents loosening the real rule: history with no item stays an error."""
        ghost = self.world.home("events", "GHOST")
        ghost.mkdir()
        (ghost / "X.json").write_text("{}")
        result = self.world.run("list")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("events/GHOST: belongs to no item", result.stderr)


if __name__ == "__main__":
    unittest.main()
