"""Rebuild a ledger from a forge alone, and a forge from a ledger alone.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from qew.ledger_import import import_target
from qew.ledger_store import Ledger
from qew.restore import restore_target
from qew.sync import Target, sync_target
from tests.ledger_harness import QUOTE, World
from tests.memory_forge import MemoryForge

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"


def build(world: World, forge: MemoryForge):
    config = ledger_config.load(world.code)
    ledger = Ledger(world.ledger, "TST")
    mirror = config.mirrors[0]
    return config, ledger, Target(mirror, ledger_config.mirror_slug(mirror), forge)


class ImportRestoreWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.source = World(MIRRORS, replication=OFF)
        self.addCleanup(self.source.close)
        self.forge = MemoryForge("git.example")
        self.config, self.ledger, self.target = build(self.source, self.forge)
        w = self.source
        self.epic = w.create("--kind", "epic", "--title", "Epic", "--quote", QUOTE, "--priority", "1")
        self.first = w.create("--kind", "issue", "--title", "First", "--parent", self.epic, "--label", "area:x", "--quote", QUOTE)
        self.second = w.create("--kind", "issue", "--title", "Second", "--parent", self.epic, "--needs", self.first, "--body", "Details here")
        w.run("start", self.first)
        sync_target(self.ledger, self.target, self.config)

    def fresh(self):
        world = World(MIRRORS, replication=OFF)
        self.addCleanup(world.close)
        return (world, *build(world, self.forge))

    def describe(self, world: World) -> dict:
        rows = json.loads(world.run("list", "--json").stdout)
        return {r["id"]: (r["kind"], r["title"], r["state"], tuple(r["needs"]), r["parent"], r["priority"]) for r in rows}

    def test_importing_the_forge_alone_rebuilds_the_same_ledger(self) -> None:
        """Prevents a forge being a dead end: ids, kinds, states, parents, needs and priorities come back from the issues alone."""
        world, config, ledger, target = self.fresh()
        report = import_target(ledger, target)
        self.assertEqual(report.new_items, 3)
        self.assertEqual(self.describe(world), self.describe(self.source))
        self.assertEqual(len(list((world.ledger / "mirrors").glob("*/*.json"))), 3)
        self.assertEqual(world.run("check").returncode, 0)

    def test_importing_twice_creates_nothing_new(self) -> None:
        """Prevents duplicate items on a repeated import."""
        world, config, ledger, target = self.fresh()
        import_target(ledger, target)
        before = self.describe(world)
        self.assertEqual(import_target(ledger, target).new_items, 0)
        self.assertEqual(self.describe(world), before)

    def test_issues_without_the_qew_marker_become_fresh_drafts(self) -> None:
        """Prevents work filed by humans being lost on import: foreign issues arrive as drafts with their labels."""
        number = self.forge.add_foreign_issue("Human filed", "see me", labels=("bug",))
        world, config, ledger, target = self.fresh()
        import_target(ledger, target)
        drafts = json.loads(world.run("draft", "list", "--json").stdout)
        self.assertIn("Human filed", [d["title"] for d in drafts])
        self.assertEqual(self.forge.issues[number].number, number)

    def test_restoring_into_an_empty_forge_recreates_every_issue_with_links(self) -> None:
        """Prevents a lost forge repository meaning lost mirrors: a new empty forge is rebuilt from the ledger alone."""
        empty = MemoryForge("git.example")
        target = Target(self.target.mirror, self.target.slug, empty)
        report = restore_target(self.ledger, target, self.config)
        self.assertEqual(len(empty.issues), 3)
        self.assertEqual(report.failures, [])
        self.assertTrue(empty.links)
        again = restore_target(self.ledger, target, self.config)
        self.assertEqual(len(empty.issues), 3)
        self.assertEqual((again.created, again.updated), (0, 0))

    def test_a_forge_that_dies_mid_restore_resumes_without_duplicates(self) -> None:
        """Prevents duplicates after a crash: the second run adopts what the first created through the body marker."""
        empty = MemoryForge("git.example")
        target = Target(self.target.mirror, self.target.slug, empty)
        original_create = empty.create_issue
        calls = {"n": 0}

        def flaky(spec):
            calls["n"] += 1
            if calls["n"] == 2:
                empty.down = True
            return original_create(spec)

        empty.create_issue = flaky
        first = restore_target(self.ledger, target, self.config)
        self.assertTrue(first.failures)
        empty.down = False
        empty.create_issue = original_create
        restore_target(self.ledger, target, self.config)
        self.assertEqual(len(empty.issues), 3)


if __name__ == "__main__":
    unittest.main()
