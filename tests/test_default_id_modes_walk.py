"""A ledger that does not name its id modes gets letters for epics and base35-time for issues and tasks.

Each test is a scenario; its docstring names the failure it prevents. Explicit settings are never changed.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class DefaultIdModesWalk(unittest.TestCase):
    def test_a_ledger_without_id_mode_numbers_epics_with_letters_and_the_rest_with_time_ids(self) -> None:
        """Prevents epics getting 9 character time ids by default: phases read A, B .. Z, AA, issues and tasks stay time ids."""
        world = World()
        self.addCleanup(world.close)
        first = world.create("--kind", "epic", "--title", "First phase", "--quote", QUOTE)
        second = world.create("--kind", "epic", "--title", "Second phase", "--quote", QUOTE)
        issue = world.create("--kind", "issue", "--title", "Work", "--parent", first, "--quote", QUOTE)
        self.assertEqual((first, second), ("A", "B"))
        self.assertRegex(issue, r"^[0-9a-km-z]{9}$")

    def test_an_explicit_id_mode_is_never_changed_by_the_default(self) -> None:
        """Prevents surprising existing ledgers: id_mode = "base35-time" still makes time ids for epics."""
        world = World()
        self.addCleanup(world.close)
        config = world.ledger / "qew-ledger.toml"
        config.write_text(config.read_text().rstrip("\n") + '\nid_mode = "base35-time"\n')
        epic = world.create("--kind", "epic", "--title", "Explicit", "--quote", QUOTE)
        self.assertRegex(epic, r"^[0-9a-km-z]{9}$")

    def test_init_writes_the_default_table_explicitly(self) -> None:
        """Prevents a hidden default: a new ledger's qew-ledger.toml names epic = letters, issue and task = base35-time."""
        world = World()
        self.addCleanup(world.close)
        config = world.ledger / "qew-ledger.toml"
        config.unlink()
        self.assertEqual(world.run("init").returncode, 0)
        text = config.read_text()
        self.assertIn('epic = "letters"', text)
        self.assertIn('issue = "base35-time"', text)
        self.assertIn('task = "base35-time"', text)
        self.assertEqual(world.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
