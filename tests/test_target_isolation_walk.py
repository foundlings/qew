"""Walks that keep board, selection and records from leaking between mirror targets.

Adversary: ensure for one target rewriting another's board record, a `select`
of one target applied to the next, and cards placed on the wrong board.
"""

import hashlib
import unittest

from qew import ledger_config
from qew.cmd_project import ensure
from qew.cmd_sync import sync_command
from tests.ledger_harness import World
from tests.memory_forge import MemoryForge

MIRRORS = """
[[mirror]]
forge = "forgejo"
repo = "https://a.example/o/a"
[[mirror]]
forge = "forgejo"
repo = "https://b.example/o/b"
select = "kind:epic"
"""


class TargetIsolationWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(MIRRORS, replication="\n[replication]\nauto_forge_sync = false\n")
        self.addCleanup(self.world.close)
        self.forges = {"a.example": MemoryForge("a.example"), "b.example": MemoryForge("b.example")}
        self.forges["a.example"].board_id = "A1"
        self.forges["b.example"].board_id = "B7"
        self.config = ledger_config.load(self.world.code)
        self.slug_a, self.slug_b = (ledger_config.mirror_slug(m) for m in self.config.mirrors)

    def build(self, mirror):
        return self.forges[mirror.repo.split("/")[2]]

    def board_file(self, slug: str):
        return self.world.ledger / "boards" / f"{slug}.json"

    def test_two_targets_use_their_own_boards(self) -> None:
        """Ensure on both records two boards; each sync places cards only on its own target's board."""
        w = self.world
        w.create("--kind", "epic", "--title", "E", "--draft")
        ensure(self.config, None, self.build)
        self.assertIn('"A1"', self.board_file(self.slug_a).read_text())
        self.assertIn('"B7"', self.board_file(self.slug_b).read_text())
        sync_command(self.config, None, None, False, build=self.build)
        self.assertEqual(self.forges["a.example"].count("place"), 1)
        self.assertEqual(self.forges["b.example"].count("place"), 1)

    def test_select_is_per_target(self) -> None:
        """Target B selects epics only; target A, with no select, still mirrors the plain issue."""
        w = self.world
        w.create("--kind", "epic", "--title", "E", "--draft")
        w.create("--kind", "issue", "--title", "I", "--draft")
        sync_command(self.config, None, None, False, build=self.build)
        self.assertEqual(len(self.forges["a.example"].issues), 2)
        self.assertEqual(len(self.forges["b.example"].issues), 1)

    def test_ensure_target_b_does_not_touch_target_a_record(self) -> None:
        """A's board record must be byte-identical after ensure runs for B only."""
        ensure(self.config, self.slug_a, self.build)
        before = hashlib.sha256(self.board_file(self.slug_a).read_bytes()).hexdigest()
        self.forges["b.example"].board_id = "B9"
        ensure(self.config, self.slug_b, self.build)
        self.assertEqual(hashlib.sha256(self.board_file(self.slug_a).read_bytes()).hexdigest(), before)
        self.assertTrue(self.board_file(self.slug_b).exists())


if __name__ == "__main__":
    unittest.main()
