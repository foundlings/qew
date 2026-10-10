"""Walks that make a failure name the command that repairs it.

Adversary: an opaque "missing option" dead end after a board lacks a column,
and a failed sync that leaves the operator guessing how mirrors catch up.
"""

import unittest

from qew import ledger_config
from qew.cmd_sync import sync_command
from qew.errors import QewError
from qew.forge import COLUMNS, ForgeRejected
from tests.ledger_harness import World
from tests.memory_forge import MemoryForge
from tests.test_forge_github_walk import SPEC, GitHubWalk

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'


class RepairHintWalk(GitHubWalk):
    def test_sync_onto_a_board_without_a_column_names_project_ensure(self) -> None:
        """A board made before a column existed must say how to repair it."""
        board = self.backend.ensure_board(COLUMNS)
        issue = self.backend.create_issue(SPEC)
        field = next(f for f in self.fake.project(board.id)["fields"] if f["name"] == "Status")
        field["options"] = [o for o in field["options"] if o["name"] != "Draft"]
        with self.assertRaises(ForgeRejected) as raised:
            self.backend.place_on_board(board, issue.number, "Draft")
        self.assertIn("`Draft`", raised.exception.detail)
        self.assertIn("qew project ensure", raised.exception.detail)


class SyncFailureHintWalk(unittest.TestCase):
    def test_a_failed_sync_ends_with_the_refresh_command(self) -> None:
        """The error after a partial sync says what to run next; the ledger work is not rolled back."""
        w = World(MIRRORS, replication="\n[replication]\nauto_forge_sync = false\n")
        self.addCleanup(w.close)
        item = w.create("--title", "T")
        forge = MemoryForge("git.example")
        forge.down = True
        config = ledger_config.load(w.code)
        with self.assertRaises(QewError) as raised:
            sync_command(config, None, None, False, build=lambda mirror: forge)
        self.assertIn("qew sync", str(raised.exception))
        self.assertTrue((w.ledger / "items" / f"{item}" / "item.md").exists())


if __name__ == "__main__":
    unittest.main()
