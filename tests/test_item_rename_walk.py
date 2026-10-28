"""qew item rename: move an item to another id, keeping the old id as an alias.

Each test is a scenario; its docstring names the failure it prevents (PIE, an epic minted from the wrong letter, had to
become the next letter without losing its children, files or history).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

DRAFTED = [{"type": "drafted", "at": "2026-01-01T00:00:00Z", "actor": "import"}]


class ItemRenameWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        config = self.world.ledger / "qew-ledger.toml"
        config.write_text(config.read_text().rstrip("\n") + '\n[id_mode]\nepic = "letters"\nissue = "base35-time"\ntask = "base35-time"\n')
        plan = "\n".join(json.dumps({"kind": "epic", "title": f"Phase {n}", "id": n, "events": DRAFTED}) for n in ("A", "B", "PIE"))
        self.assertEqual(self.world.run("create", "--from-file", "-", "--import", stdin=plan).returncode, 0)
        self.child = self.world.create("--kind", "issue", "--title", "Child of PIE", "--parent", "PIE", "--quote", QUOTE)
        self.source = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.source, ignore_errors=True))
        (self.source / "plan.txt").write_bytes(b"the plan")
        self.assertEqual(self.world.run("item", "attach", "PIE", str(self.source / "plan.txt")).returncode, 0)

    def rename(self, *arguments: str):
        return self.world.run("item", "rename", *arguments)

    def test_the_item_moves_to_the_next_letter_with_children_files_and_history(self) -> None:
        """Prevents losing anything in a rename: the child's parent, the attachment, the events and the old id (as alias)."""
        result = self.rename("PIE")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("C", result.stdout.split())  # the outlier PIE is not the sequence: the next letter after B is C
        self.assertFalse((self.world.home("items", "PIE") / "item.md").exists())
        shown = self.world.run("show", "C").stdout
        self.assertIn("plan.txt", shown)
        self.assertIn("aliases: PIE", (self.world.home("items", "C") / "item.md").read_text())
        self.assertIn("parent: C", self.world.run("show", self.child).stdout)
        self.assertEqual(self.world.run("show", "PIE").returncode, 0)  # the old id still resolves
        self.assertEqual(self.world.run("check").returncode, 0, self.world.run("check").stdout)
        target = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        self.assertEqual(self.world.run("item", "extract", "C", str(target)).returncode, 0)
        self.assertEqual((target / "plan.txt").read_bytes(), b"the plan")
        self.assertEqual(len([line for line in self.world.ledger_log() if "rename" in line]), 1)

    def test_after_reserves_a_letter_that_lives_outside_the_ledger(self) -> None:
        """Prevents colliding with a phase document: --after C makes the new id D."""
        result = self.rename("PIE", "--after", "C")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.world.home("items", "D") / "item.md").exists())

    def test_a_letters_kind_accepts_only_the_next_letter(self) -> None:
        """Prevents a rename breaking the strict order: asking for Z when C is next is refused and names C."""
        refused = self.rename("PIE", "Z")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertNotIn("invalid choice", refused.stderr)
        self.assertIn("C", refused.stderr)
        self.assertTrue((self.world.home("items", "PIE") / "item.md").exists())
        self.assertEqual(self.rename("PIE", "C").returncode, 0)

    def test_dry_run_writes_nothing_and_a_claim_by_another_login_blocks_the_rename(self) -> None:
        """Prevents surprises: --dry-run only says what would move; another login's claim stops it with exit 3."""
        dry = self.rename("PIE", "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn("would rename", dry.stdout)
        self.assertTrue((self.world.home("items", "PIE") / "item.md").exists())
        self.assertEqual(self.world.run("draft", "approve", "PIE", "--quote", QUOTE).returncode, 0)
        self.assertEqual(self.world.run("start", "PIE", env={"QEW_ACTOR": "second"}).returncode, 0)
        blocked = self.rename("PIE")
        self.assertEqual(blocked.returncode, 3, blocked.stdout + blocked.stderr)
        self.assertTrue((self.world.home("items", "PIE") / "item.md").exists())

    def test_an_unknown_item_is_refused(self) -> None:
        """Prevents a silent no-op."""
        missing = self.rename("NOPE")
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertNotIn("invalid choice", missing.stderr)


if __name__ == "__main__":
    unittest.main()
