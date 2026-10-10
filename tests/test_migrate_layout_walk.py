"""`qew migrate-layout`: the explicit step that makes a ledger schema 2 (items may own attachment directories).

Each test is a scenario; its docstring names the failure it prevents. Nothing is rewritten: schema 2 only declares the
layout, so a binary that predates it refuses the ledger loudly instead of misreading it.
"""

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".qew" not in p.parts and p.name != "qew-ledger.toml"):
        digest.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return digest.hexdigest()


class MigrateLayoutWalk(unittest.TestCase):
    def schema1(self) -> World:
        world = World()
        self.addCleanup(world.close)
        (world.ledger / "qew-ledger.toml").write_text('name = "test"  # keep me\nprefix = "TST"\nschema_version = 1\n', encoding="utf-8")
        return world

    def version(self, world: World) -> str:
        return next(l for l in (world.ledger / "qew-ledger.toml").read_text().splitlines() if l.startswith("schema_version"))

    def test_the_migration_declares_schema_two_without_touching_a_single_item_or_event(self) -> None:
        """Prevents a layout change that rewrites history: items, events and mirrors stay byte for byte, only the version moves."""
        world = self.schema1()
        item = world.create("--kind", "issue", "--title", "Existing", "--quote", QUOTE)
        before = tree_digest(world.ledger)
        preview = world.run("migrate-layout", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("schema_version 1 -> 2", preview.stdout)
        self.assertEqual(self.version(world), "schema_version = 1")
        done = world.run("migrate-layout")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.version(world), "schema_version = 2")
        self.assertIn("# keep me", (world.ledger / "qew-ledger.toml").read_text())
        self.assertEqual(tree_digest(world.ledger), before)
        self.assertEqual(world.run("check").returncode, 0)
        self.assertIn("already", world.run("migrate-layout").stdout)
        self.assertIn(item, world.run("list").stdout)

    def test_attaching_to_a_schema_one_ledger_is_refused_naming_the_migration(self) -> None:
        """Prevents a ledger that an older qew would misread: the first attachment needs the declared layout."""
        world = self.schema1()
        item = world.create("--kind", "issue", "--title", "Needs a file", "--quote", QUOTE)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
            handle.write(b"evidence")
        self.addCleanup(os.unlink, handle.name)
        refused = world.run("item", "attach", item, handle.name)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("qew migrate-layout", refused.stderr)
        self.assertEqual(world.run("migrate-layout").returncode, 0)
        self.assertEqual(world.run("item", "attach", item, handle.name).returncode, 0)

    def test_it_refuses_while_another_login_holds_a_claim(self) -> None:
        """Prevents declaring a layout under someone's feet: a claim by another login blocks it, like migrate-ids."""
        world = self.schema1()
        item = world.create("--kind", "issue", "--title", "Held", "--quote", QUOTE)
        self.assertEqual(world.run("start", item, env={"QEW_ACTOR": "someone-else"}).returncode, 0)
        refused = world.run("migrate-layout")
        self.assertEqual(refused.returncode, 3, refused.stdout + refused.stderr)
        self.assertIn("someone-else", refused.stderr)
        self.assertEqual(self.version(world), "schema_version = 1")


if __name__ == "__main__":
    unittest.main()
