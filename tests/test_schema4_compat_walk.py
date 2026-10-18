"""A schema 4 ledger keeps working on a qew that writes schema 5, and its migration never waits for anyone's claim.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


def downgrade(world: World) -> None:
    toml = world.ledger / "qew-ledger.toml"
    toml.write_text(toml.read_text().replace("schema_version = 7", "schema_version = 4"))


class Schema4CompatWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.item = self.world.create("--title", "Held work", "--quote", QUOTE)
        downgrade(self.world)

    def test_a_schema_4_ledger_is_still_readable_and_writable_until_migrated(self) -> None:
        """Prevents an install stranding every agent: reads, claims and plain creates work on schema 4."""
        for command in (("list",), ("ready",), ("check",), ("start", self.item), ("create", "--title", "More work")):
            with self.subTest(command=command):
                out = self.world.run(*command)
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_fields_wait_for_the_migration_so_an_older_qew_never_meets_the_new_header_key(self) -> None:
        """Prevents a schema 4 ledger gaining a header key the other checkouts' qew refuses: --field says to migrate first."""
        before = self.world.run("list", "--json").stdout
        out = self.world.run("create", "--title", "Fielded", "--field", "phase=1")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("migrate-layout", out.stderr)
        self.assertEqual(self.world.run("list", "--json").stdout, before)
        self.assertEqual(self.world.run("migrate-layout").returncode, 0)
        self.assertEqual(self.world.run("create", "--title", "Fielded", "--field", "phase=1").returncode, 0)

    def test_the_4_to_5_migration_proceeds_over_other_logins_claims_and_keeps_them(self) -> None:
        """Prevents the migration waiting for every lane to release: claims by other logins survive it untouched."""
        other = self.world.create("--title", "Second", "--quote", QUOTE)
        for name, item in (("elf3", self.item), ("elf5", other)):
            self.assertEqual(self.world.run("start", item, env={"QEW_ACTOR": name}).returncode, 0)
        events = {item: self.world.events(item) for item in (self.item, other)}
        out = self.world.run("migrate-layout")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("schema_version 4 -> 7", out.stdout)
        self.assertEqual({item: self.world.events(item) for item in (self.item, other)}, events)
        owners = {row["id"]: row.get("owner") for row in map(json.loads, self.world.run("list", "--json").stdout.splitlines())}
        self.assertEqual((owners[self.item], owners[other]), ("elf3", "elf5"))
        self.assertEqual(self.world.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
