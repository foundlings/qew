"""Walks of `qew project status`: a read-only drift report of the board against the ledger.

Adversary: unmirrored or mis-columned work going unnoticed, a report that writes,
and a guessed board when none is recorded.
"""

import json
import unittest

from qew import ledger_config
from qew.cmd_project import ensure
from qew.cmd_sync import sync_command
from qew.errors import QewError
from qew.project_status import status
from tests.ledger_harness import QUOTE, World
from tests.memory_forge import MemoryForge

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'
OFF = "\n[replication]\nauto_forge_sync = false\n"


class ProjectStatusWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(MIRRORS, replication=OFF)
        self.addCleanup(self.world.close)
        self.forge = MemoryForge("git.example")
        self.config = ledger_config.load(self.world.code)
        self.build = lambda mirror: self.forge

    def synced(self, count: int = 2):
        items = [self.world.create("--title", f"T{n}", "--quote", QUOTE) for n in range(count)]
        ensure(self.config, None, self.build)
        sync_command(self.config, None, None, False, build=self.build)
        return items

    def number_of(self, item: str) -> int:
        mirror = next((self.world.ledger / "mirrors" / item).glob("*.json"))
        return json.loads(mirror.read_text())["number"]

    def test_status_reports_missing_and_stale_cards_without_writing(self) -> None:
        """One card gone and one in the wrong column are both listed; nothing is written to forge or ledger."""
        first, second, third = self.synced(3)
        self.forge.columns.pop(self.number_of(first))
        self.forge.columns[self.number_of(second)] = "Done"
        self.forge.priorities[self.number_of(third)] = "P4"
        writes = self.forge.count("place") + self.forge.count("remove")
        text, code = status(self.config, None, True, self.build)
        row = json.loads(text)
        self.assertEqual(code, 1)
        self.assertEqual((row["missing"], row["stale_column"], row["stale_priority"]), ([first], [second], [third]))
        self.assertFalse(row["in_sync"])
        self.assertEqual(row["schema_version"], 1)
        self.assertIn("qew sync", row["fix"])
        self.assertEqual(self.forge.count("place") + self.forge.count("remove"), writes)

    def test_status_reports_a_card_of_the_boards_own_repository_that_no_item_mirrors(self) -> None:
        """Prevents 'in sync' while a closed stray issue's card sits in In progress: a card no ledger item owns is drift."""
        self.synced(1)
        self.forge.columns[99] = "In progress"
        text, code = status(self.config, None, True, self.build)
        row = json.loads(text)
        self.assertEqual((code, row["orphan"], row["in_sync"]), (1, ["#99"], False))
        self.assertIn("prune --orphans", row["fix"])
        plain, _ = status(self.config, None, False, self.build)
        self.assertIn("orphan #99", plain)

    def test_status_reports_in_sync_in_one_line(self) -> None:
        """Nothing drifted: one line, exit 0."""
        self.synced()
        text, code = status(self.config, None, False, self.build)
        self.assertEqual((code, len(text.splitlines())), (0, 1))
        self.assertTrue(text.endswith(": in sync"), text)

    def test_status_ignores_a_foreign_repository_card_that_collides(self) -> None:
        """A foreign card numbered like a missing local one must not make the report say in sync."""
        (item,) = self.synced(1)
        number = self.number_of(item)
        self.forge.columns.pop(number)
        from qew.forge import Card
        self.forge.foreign_cards.append(Card("F1", "acme/other", number, "Ready", "P2"))
        text, code = status(self.config, None, True, self.build)
        self.assertEqual((code, json.loads(text)["missing"]), (1, [item]))

    def test_status_refuses_a_missing_board_without_writing(self) -> None:
        """No recorded or configured board: refuse, name `qew project ensure`, guess nothing."""
        self.world.create("--title", "T", "--quote", QUOTE)
        text, code = status(self.config, None, False, self.build)
        self.assertEqual(code, 2)
        self.assertIn("qew project ensure", text)
        self.assertEqual(self.forge.calls, [])

    def test_status_on_a_forge_without_boards_says_so(self) -> None:
        """Unsupported is not a failure."""
        self.forge = MemoryForge("git.example", boards=False)
        self.world.create("--title", "T", "--quote", QUOTE)
        self.world.ledger.joinpath("boards").mkdir(exist_ok=True)
        ledger_slug = ledger_config.mirror_slug(self.config.mirrors[0])
        (self.world.ledger / "boards" / f"{ledger_slug}.json").write_text(json.dumps({"id": "B1", "url": "u", "columns": []}))
        text, code = status(self.config, None, False, self.build)
        self.assertEqual(code, 0)
        self.assertIn("boards unsupported", text)

    def test_a_bad_target_is_a_usage_error(self) -> None:
        """An unknown target is exit 2 through the same refusal as sync."""
        with self.assertRaises(QewError) as raised:
            status(self.config, "nowhere", False, self.build)
        self.assertEqual(raised.exception.exit_code, 2)


if __name__ == "__main__":
    unittest.main()
