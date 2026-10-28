"""Walks of `qew sync --item`: the cards that one change moves move with it.

Adversary: a dependent stuck in Backlog after its need completed, an epic card
stale after a child was claimed, and a targeted sync rewriting cards that did not change.
"""

import unittest

from qew import ledger_config
from qew.cmd_project import ensure
from qew.cmd_sync import sync_command
from tests.ledger_harness import QUOTE, World
from tests.memory_forge import MemoryForge

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'
OFF = "\n[replication]\nauto_forge_sync = false\n"


class TargetedSyncWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(MIRRORS, replication=OFF)
        self.addCleanup(self.world.close)
        self.forge = MemoryForge("git.example")
        self.config = ledger_config.load(self.world.code)
        self.build = lambda mirror: self.forge

    def sync(self, item=None):
        return sync_command(self.config, None, item, False, build=self.build)

    def column_of(self, item: str) -> str:
        from json import loads
        number = loads(next((self.world.home("mirrors", item)).glob("*.json")).read_text())["number"]
        return self.forge.columns[number]

    def start_synced(self, *items: str) -> None:
        ensure(self.config, None, self.build)
        self.sync()

    def test_completing_a_need_moves_its_dependent_to_ready(self) -> None:
        """sync --item NEED puts NEED in Done and its dependent in Ready, not only NEED."""
        w = self.world
        need = w.create("--title", "need", "--quote", QUOTE)
        dependent = w.create("--title", "dependent", "--needs", need, "--quote", QUOTE)
        self.start_synced(need, dependent)
        self.assertEqual(self.column_of(dependent), "Backlog")
        w.run("start", need)
        w.run("evidence", need, "--change", w.commit())
        w.run("complete", need)
        self.sync(need)
        self.assertEqual((self.column_of(need), self.column_of(dependent)), ("Done", "Ready"))

    def test_claiming_a_child_moves_the_epic_card_and_release_moves_it_back(self) -> None:
        """The parent epic card follows its children without a full sync."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "epic", "--quote", QUOTE)
        child = w.create("--title", "child", "--parent", epic, "--quote", QUOTE)
        self.start_synced(epic, child)
        self.assertEqual(self.column_of(epic), "Backlog")
        w.run("start", child)  # an epic is never claimed here: claiming it would claim its children too
        self.sync(child)
        self.assertEqual((self.column_of(child), self.column_of(epic)), ("In progress", "In progress"))
        w.run("release", child, "--reason", "pause")
        self.sync(child)
        self.assertEqual(self.column_of(epic), "Backlog")

    def test_unchanged_cards_are_not_rewritten_by_a_second_targeted_sync(self) -> None:
        """A repeat issues no board write."""
        w = self.world
        need = w.create("--title", "need", "--quote", QUOTE)
        w.create("--title", "dependent", "--needs", need, "--quote", QUOTE)
        self.start_synced(need)
        w.run("start", need)
        self.sync(need)
        writes = self.forge.count("place")
        self.sync(need)
        self.assertEqual(self.forge.count("place"), writes)


if __name__ == "__main__":
    unittest.main()
