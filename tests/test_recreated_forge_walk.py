"""A forge repository that was deleted and recreated: sync recovers, the dry run says so, stale divergences clear.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import cmd_sync, ledger_config
from qew.forge_pull import pull_target
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.ledger_harness import QUOTE, World
from tests.memory_forge import MemoryForge

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\ndivergences = \"report\"\n"


class RecreatedForgeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(MIRRORS, replication=OFF)
        self.addCleanup(self.world.close)
        self.forge = MemoryForge("git.example")
        self.config = ledger_config.load(self.world.code)
        self.ledger = Ledger(self.world.ledger, "TST")
        mirror = self.config.mirrors[0]
        self.target = Target(mirror, ledger_config.mirror_slug(mirror), self.forge)
        self.items = [self.world.create("--kind", "issue", "--title", f"Item {n}", "--quote", QUOTE) for n in range(3)]
        sync_target(self.ledger, self.target, self.config)

    def wipe(self) -> None:
        for number in list(self.forge.issues):
            self.forge.delete_issue(number)

    def open_divergences(self) -> int:
        return sum(len(self.ledger.snapshot().folds[item].divergences) for item in self.items)

    def sync(self, dry_run: bool = False) -> str:
        return cmd_sync.sync_command(self.config, None, None, dry_run, build=lambda mirror: self.forge)

    def test_a_plain_sync_recreates_every_issue_when_the_forge_was_wiped(self) -> None:
        """Prevents a recreated repo costing one failure and one conflict per item and a hand-run restore."""
        self.wipe()
        output = self.sync()
        self.assertEqual(len(self.forge.issues), 3, output)
        self.assertEqual(self.open_divergences(), 0, output)
        self.assertIn("recreated=3", output)
        numbers = {r["number"] for item in self.items for r in self.ledger.load_mirrors(item)}
        self.assertEqual(numbers, set(self.forge.issues))

    def test_one_deleted_issue_is_still_a_divergence_not_a_recreate(self) -> None:
        """Prevents the wipe rule swallowing a single deliberate deletion: a human settles that one."""
        self.forge.delete_issue(2)
        self.sync()
        self.assertEqual(self.open_divergences(), 1)
        self.assertEqual(len(self.forge.issues), 2)

    def test_the_dry_run_lists_the_creates_a_wiped_forge_needs(self) -> None:
        """Prevents `nothing to sync` while every mirrored issue is missing on the forge."""
        self.wipe()
        output = self.sync(dry_run=True)
        self.assertEqual(sum(1 for line in output.splitlines() if line.startswith("create ")), 3, output)
        self.assertEqual(len(self.forge.issues), 0)

    def test_a_missing_issue_divergence_clears_when_the_issue_is_back(self) -> None:
        """Prevents an exists/missing divergence outliving the cause and needing a manual resolve."""
        saved = self.forge.issues[2]
        self.forge.delete_issue(2)
        pull_target(self.ledger, self.target)
        self.assertEqual(self.open_divergences(), 1)
        self.forge.issues[2] = saved
        pull_target(self.ledger, self.target)
        self.assertEqual(self.open_divergences(), 0)


if __name__ == "__main__":
    unittest.main()
