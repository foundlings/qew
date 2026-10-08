"""Walks of the derived item author: the actor of the first recorded event.

Adversary: re-attribution by a later edit, an author taken from a typed
argument, and an actor that cannot be a label half-labelling a sync.
"""

import json
import shutil
import unittest

from qew import ledger_config
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.ledger_harness import World
from tests.memory_forge import MemoryForge

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'


class AuthorWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(MIRRORS)
        self.addCleanup(self.world.close)

    def sync(self, forge: MemoryForge):
        config = ledger_config.load(self.world.code)
        mirror = config.mirrors[0]
        return sync_target(Ledger(self.world.ledger, "TST"), Target(mirror, ledger_config.mirror_slug(mirror), forge), config)

    def test_author_comes_from_the_first_event_actor_only(self) -> None:
        """A later edit by another actor must not re-attribute the item; create takes no --author."""
        w = self.world
        item = w.create("--title", "T")
        self.assertEqual(w.run("create", "--title", "X", "--author", "mallory").returncode, 2)
        w.run("set", item, "--priority", "1", env={"QEW_ACTOR": "bob"})
        shown = json.loads(w.run("show", item, "--json").stdout)
        self.assertEqual(shown["author"], "tester")
        self.assertIn("author: tester", w.run("show", item).stdout)

    def test_list_filter_matches_case_insensitively(self) -> None:
        """`--author TESTER` must find tester's items and hide another actor's."""
        w = self.world
        mine = w.create("--title", "mine")
        self.assertEqual(w.run("create", "--title", "theirs", env={"QEW_ACTOR": "carol"}).returncode, 0)
        listed = w.run("list", "--author", "TESTER").stdout
        self.assertIn(mine, listed)
        self.assertEqual(len(listed.strip().splitlines()), 1)

    def test_sync_labels_the_issue_with_the_author(self) -> None:
        """The forge issue carries author:LOGIN so the board can be filtered by who opened the work."""
        self.world.create("--title", "T")
        forge = MemoryForge("git.example")
        self.sync(forge)
        self.assertIn("author:tester", forge.issues[1].labels)

    def test_actor_that_cannot_be_a_label_fails_before_any_forge_call(self) -> None:
        """An actor such as `bad login,x` is reported against the item; no half-labelled issue appears."""
        w = self.world
        item = w.create("--title", "T")  # `create` itself refuses such an actor now; a hand-merged event can still carry one
        event = next((w.ledger / "events" / item).glob("*.json"))
        event.write_text(event.read_text().replace('"actor": "tester"', '"actor": "bad login,x"'))
        forge = MemoryForge("git.example")
        report = self.sync(forge)
        self.assertEqual(forge.count("create"), 0)
        self.assertEqual(len(report.failures), 1)
        self.assertIn("author", report.failures[0])

    def test_item_without_events_has_unknown_author_and_no_label(self) -> None:
        """No recorded first event means no guess: never mirrored as a label."""
        w = self.world
        item = w.create("--title", "T")
        shutil.rmtree(w.ledger / "events" / item)
        forge = MemoryForge("git.example")
        self.sync(forge)
        self.assertFalse([label for label in forge.issues[1].labels if label.startswith("author:")])
        self.assertNotEqual(w.run("show", item).returncode, 0, "reads refuse a ledger whose item has no events")


if __name__ == "__main__":
    unittest.main()
