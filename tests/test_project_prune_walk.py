"""Walks of `qew project prune --from OWNER/REPO` on a board shared by two repositories.

Adversary: deleting the board's own cards or a look-alike repository's, a loose
source match, and a delete that did not stick being reported as success.
"""

import unittest

from qew import ledger_config
from qew.errors import QewError
from qew.forge import Card
from qew.project_prune import prune
from tests.ledger_harness import World
from tests.memory_forge import MemoryForge

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\nproject = 1\n'


class ProjectPruneWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(MIRRORS, replication="\n[replication]\nauto_forge_sync = false\n")
        self.addCleanup(self.world.close)
        self.forge = MemoryForge("git.example")
        self.forge.columns = {1: "Ready", 2: "Done", 3: "Ready"}
        self.forge.foreign_cards = [
            Card("F1", "acme/other", 1, "Ready", ""), Card("F2", "ACME/Other", 2, "Done", ""),
            Card("F3", "acme/other-two", 3, "Ready", ""),
        ]
        self.config = ledger_config.load(self.world.code)
        self.build = lambda mirror: self.forge

    def test_prune_deletes_exactly_the_named_repositorys_cards_and_verifies(self) -> None:
        """Two foreign cards go (any case); the local cards and the look-alike stay, confirmed by a re-read."""
        text = prune(self.config, None, "acme/other", self.build)
        self.assertTrue(text.endswith("removed 2"), text)
        self.assertEqual([card.id for card in self.forge.foreign_cards], ["F3"])
        self.assertEqual(sorted(self.forge.columns), [1, 2, 3])
        self.assertEqual(self.forge.count("delete_card"), 2)

    def test_prune_refuses_the_boards_own_repository_before_any_call(self) -> None:
        """Pruning the target's own repository would empty its board."""
        with self.assertRaises(QewError) as raised:
            prune(self.config, None, "O/R", self.build)
        self.assertEqual(raised.exception.exit_code, 2)
        self.assertEqual(self.forge.calls, [])

    def test_prune_refuses_malformed_sources_before_any_call(self) -> None:
        """A loose match could delete the wrong cards."""
        for source in ("other", "a/b/c", "", "a b/c", "acme/"):
            with self.assertRaises(QewError, msg=source) as raised:
                prune(self.config, None, source, self.build)
            self.assertEqual(raised.exception.exit_code, 2, source)
        self.assertEqual(self.forge.calls, [])

    def test_prune_with_nothing_to_remove_deletes_nothing(self) -> None:
        """Idempotent: a second run removes nothing and issues no delete."""
        prune(self.config, None, "acme/other", self.build)
        deletes = self.forge.count("delete_card")
        text = prune(self.config, None, "acme/other", self.build)
        self.assertTrue(text.endswith("removed 0"), text)
        self.assertEqual(self.forge.count("delete_card"), deletes)

    def test_cards_that_remain_after_deletes_are_indeterminate(self) -> None:
        """A delete the forge accepted but did not carry out is reported, not success."""
        self.forge.stubborn_cards = True
        with self.assertRaises(QewError) as raised:
            prune(self.config, None, "acme/other", self.build)
        self.assertIn("indeterminate", str(raised.exception))
        self.assertEqual(raised.exception.exit_code, 1)


if __name__ == "__main__":
    unittest.main()
