"""Schema 7 declares the committed baselines layout: a ledger on 6 keeps working until it is migrated, 7 is refused by a qew that predates it.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class SchemaSevenWalk(unittest.TestCase):
    def ledger(self, version: int) -> World:
        world = World(ledger_vcs="git")
        self.addCleanup(world.close)
        world.create("--title", "Existing", "--quote", QUOTE)
        toml = world.ledger / "qew-ledger.toml"
        toml.write_text(toml.read_text().replace("schema_version = 7", f"schema_version = {version}"))
        return world

    def test_a_schema_6_ledger_works_and_migrates_to_7_by_declaring_it(self) -> None:
        """Prevents forcing a layout change on a ledger nobody migrated: schema 6 keeps working, and `migrate-layout` is the
        one step that declares 7, once."""
        world = self.ledger(6)
        self.assertEqual(world.run("list").returncode, 0)
        preview = world.run("migrate-layout", "--dry-run")
        self.assertIn("schema_version 6 -> 7", preview.stdout)
        done = world.run("migrate-layout")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("schema_version = 7", (world.ledger / "qew-ledger.toml").read_text())
        self.assertIn("already schema_version 7", world.run("migrate-layout").stdout)

    def test_a_ledger_newer_than_this_qew_is_refused_naming_the_upgrade(self) -> None:
        """Prevents an older qew working on a layout it does not know: schema 8 is refused, and the message says to upgrade."""
        world = self.ledger(8)
        result = world.run("list")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("upgrade qew", result.stderr)


if __name__ == "__main__":
    unittest.main()
