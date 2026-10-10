"""`qew migrate-layout`: one step from schema 1 or 2 to the latest layout, where every item's file lives in its own directory.

Each test is a scenario; its docstring names the failure it prevents. Items are `items/<ID>/item.md` (the ID is only in the
directory name), beside their `attachments/`. Nothing but the file location and the schema_version line changes.
"""

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

LATEST = "schema_version = 5"


def to_old_layout(world: World, version: int) -> dict[str, bytes]:
    """Turn the world's ledger into a schema `version` one (items/<ID>.md); returns each item's bytes by id."""
    items = world.ledger / "items"
    before: dict[str, bytes] = {}
    for entry in sorted(items.iterdir()):
        source = entry / "item.md" if entry.is_dir() else entry
        if source.is_file():
            item_id = entry.name if entry.is_dir() else entry.stem
            before[item_id] = source.read_bytes()
            if entry.is_dir():
                source.unlink()
                if not any(entry.iterdir()):
                    entry.rmdir()
                (items / f"{item_id}.md").write_bytes(before[item_id])
    (world.ledger / "qew-ledger.toml").write_text(f'name = "test"  # keep me\nprefix = "TST"\nschema_version = {version}\n', encoding="utf-8")
    return before


def digest(root: Path) -> str:
    hashed = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".qew" not in p.parts):
        hashed.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return hashed.hexdigest()


class MigrateLayoutWalk(unittest.TestCase):
    def ledger(self, version: int, items: int = 3) -> tuple[World, dict[str, bytes]]:
        world = World()
        self.addCleanup(world.close)
        for number in range(items):
            world.create("--kind", "issue", "--title", f"Existing {number}", "--quote", QUOTE)
        return world, to_old_layout(world, version)

    def test_schema_1_and_2_both_move_to_the_latest_in_one_go_with_identical_item_bytes(self) -> None:
        """Prevents intermediate steps and rewritten content: from 1 or 2 straight to 5, every item's bytes unchanged."""
        for version in (1, 2):
            with self.subTest(schema=version):
                world, before = self.ledger(version)
                preview = world.run("migrate-layout", "--dry-run")
                self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
                self.assertIn(f"schema_version {version} -> 5", preview.stdout)
                self.assertIn("3 item files", preview.stdout)
                self.assertTrue((world.ledger / "items" / f"{next(iter(before))}.md").is_file())
                done = world.run("migrate-layout")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual((world.ledger / "qew-ledger.toml").read_text().count(LATEST), 1)
                self.assertIn("# keep me", (world.ledger / "qew-ledger.toml").read_text())
                self.assertEqual(sorted(p.name for p in (world.ledger / "items").iterdir()), sorted(before))
                for item_id, content in before.items():
                    self.assertEqual((world.ledger / "items" / item_id / "item.md").read_bytes(), content)
                self.assertEqual(world.run("check").returncode, 0)
                self.assertIn("already", world.run("migrate-layout").stdout)

    def test_attachments_stay_beside_the_item_file(self) -> None:
        """Prevents splitting an item: its attachments directory and its file end up in the same directory."""
        world, before = self.ledger(2, items=1)
        item = next(iter(before))
        (world.ledger / "items" / item / "attachments").mkdir(parents=True)
        (world.ledger / "items" / item / "attachments" / "x.txt").write_bytes(b"data")
        self.assertEqual(world.run("migrate-layout").returncode, 0)
        self.assertTrue((world.ledger / "items" / item / "item.md").is_file())
        self.assertEqual((world.ledger / "items" / item / "attachments" / "x.txt").read_bytes(), b"data")

    def test_every_other_command_refuses_an_older_layout_naming_the_migration(self) -> None:
        """Prevents misreading: on schema 1 or 2 only migrate-layout works."""
        for version in (1, 2):
            with self.subTest(schema=version):
                world, _ = self.ledger(version, items=1)
                refused = world.run("list")
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                self.assertIn("qew migrate-layout", refused.stderr)

    def test_it_refuses_while_another_login_holds_a_claim_and_leaves_every_byte_alone(self) -> None:
        """Prevents declaring a layout under someone's feet: a foreign claim rolls the whole migration back."""
        world = World()
        self.addCleanup(world.close)
        item = world.create("--kind", "issue", "--title", "Held", "--quote", QUOTE)
        self.assertEqual(world.run("start", item, env={"QEW_ACTOR": "someone-else"}).returncode, 0)
        to_old_layout(world, 2)
        before = digest(world.ledger)
        refused = world.run("migrate-layout")
        self.assertEqual(refused.returncode, 3, refused.stdout + refused.stderr)
        self.assertIn("someone-else", refused.stderr)
        self.assertEqual(digest(world.ledger), before)

    def test_it_refuses_when_both_paths_already_exist(self) -> None:
        """Prevents overwriting: an item with items/ID.md and items/ID/item.md is a conflict to settle by hand."""
        world, before = self.ledger(2, items=1)
        item = next(iter(before))
        (world.ledger / "items" / item).mkdir(exist_ok=True)
        (world.ledger / "items" / item / "item.md").write_bytes(b"other")
        snapshot = digest(world.ledger)
        refused = world.run("migrate-layout")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn(item, refused.stderr)
        self.assertEqual(digest(world.ledger), snapshot)

    def test_new_items_are_written_into_their_directory_and_init_starts_at_the_latest(self) -> None:
        """Prevents a fresh ledger needing a migration: items land at items/ID/item.md, never items/ID.md."""
        world = World()
        self.addCleanup(world.close)
        item = world.create("--kind", "issue", "--title", "Fresh", "--quote", QUOTE)
        self.assertTrue((world.ledger / "items" / item / "item.md").is_file())
        self.assertFalse((world.ledger / "items" / f"{item}.md").exists())
        with tempfile.TemporaryDirectory() as root:
            from tests.test_init_walk import init
            from tests.ledger_harness import git
            code = Path(root) / "code"
            code.mkdir()
            git(code, "init", "-q", "-b", "main")
            self.assertEqual(init(code, "--ledger", "../ledger").returncode, 0)
            self.assertIn(LATEST, (Path(root) / "ledger" / "qew-ledger.toml").read_text())


if __name__ == "__main__":
    unittest.main()
