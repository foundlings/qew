"""Bulk attach for importers: a manifest of attachments and attachments inside create --from-file plans.

Each test is a scenario; its docstring names the failure it prevents. One command is one ledger commit, a rerun adds
nothing, and a refusal anywhere writes nothing anywhere.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AttachBatchWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        self.items = [self.world.create("--kind", "issue", "--title", f"Item {n}", "--quote", QUOTE) for n in range(4)]
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def manifest(self, rows: list) -> Path:
        path = self.root / "manifest.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        return path

    def attached(self, item: str) -> list:
        return [e for e in self.world.events(item) if e["type"] == "attached"]

    def assertRefused(self, result) -> None:
        """A refusal by the command itself (exit 2), not argparse not knowing --manifest or the plan not knowing the field."""
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        for vague in ("invalid choice", "unrecognized arguments", "the following arguments are required", "unknown field"):
            self.assertNotIn(vague, result.stderr)

    def test_a_manifest_attaches_files_and_directories_to_many_items_in_one_commit_and_a_rerun_adds_nothing(self) -> None:
        """Prevents one commit per file: 4 items, a file each plus a tree, one ledger commit, an idempotent rerun."""
        for n in range(4):
            self.write(f"receipts/{n}.json", f'{{"n": {n}}}'.encode())
        self.write("frozen/a/x.rs", b"fn x() {}")
        self.write("frozen/a/b/y.rs", b"fn y() {}")
        rows = [{"item": self.items[n], "source": f"receipts/{n}.json", "name": f"receipts/{n}.json"} for n in range(4)]
        rows.append({"item": self.items[0], "source": "frozen", "name": "frozen"})
        manifest = self.manifest(rows)
        before = len(self.world.ledger_log())
        result = self.world.run("item", "attach", "--manifest", str(manifest))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.world.ledger_log()) - before, 1)
        self.assertEqual(len(self.attached(self.items[0])), 3)  # one receipt and two frozen files
        self.assertIn("frozen/a/b/y.rs", self.world.run("show", self.items[0]).stdout)
        events_before = sum(len(self.attached(i)) for i in self.items)
        again = self.world.run("item", "attach", "--manifest", str(manifest))
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(sum(len(self.attached(i)) for i in self.items), events_before)
        self.assertEqual(len(self.world.ledger_log()) - before, 1)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_one_bad_row_refuses_the_whole_manifest_and_names_its_line(self) -> None:
        """Prevents a half-applied import: a missing source on line 3 leaves every other row unwritten too."""
        good = self.write("ok.txt", b"ok")
        manifest = self.manifest([
            {"item": self.items[0], "source": str(good)},
            {"item": self.items[1], "source": str(good)},
            {"item": self.items[2], "source": str(self.root / "missing.txt")},
        ])
        result = self.world.run("item", "attach", "--manifest", str(manifest))
        self.assertRefused(result)
        self.assertIn("line 3", result.stderr)
        self.assertEqual(sum(len(self.attached(i)) for i in self.items), 0)
        self.assertFalse((self.world.ledger / "items" / self.items[0] / "attachments").exists())

    def test_two_rows_giving_one_name_two_different_files_are_refused(self) -> None:
        """Prevents silent last-row-wins inside a manifest: the same name with different bytes is an error."""
        first, second = self.write("one.txt", b"1"), self.write("two.txt", b"2")
        manifest = self.manifest([
            {"item": self.items[0], "source": str(first), "name": "same.txt"},
            {"item": self.items[0], "source": str(second), "name": "same.txt"},
        ])
        result = self.world.run("item", "attach", "--manifest", str(manifest))
        self.assertRefused(result)
        self.assertEqual(len(self.attached(self.items[0])), 0)

    def test_dry_run_lists_the_work_and_writes_nothing_and_unknown_keys_are_refused(self) -> None:
        """Prevents a typo going unnoticed: --dry-run only reports; a row key we do not know is refused naming the line."""
        file = self.write("d.txt", b"d")
        manifest = self.manifest([{"item": self.items[0], "source": str(file)}])
        dry = self.world.run("item", "attach", "--manifest", str(manifest), "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn("would attach", dry.stdout)
        self.assertEqual(len(self.attached(self.items[0])), 0)
        bad = self.manifest([{"item": self.items[0], "source": str(file), "colour": "red"}])
        refused = self.world.run("item", "attach", "--manifest", str(bad))
        self.assertRefused(refused)
        self.assertIn("line 1", refused.stderr)

    def test_a_create_plan_can_carry_attachments_for_its_new_items(self) -> None:
        """Prevents a second pass for the importer: items and their files arrive together, in one commit."""
        data = b"frozen source"
        file = self.write("frozen.rs", data)
        plan = self.root / "plan.jsonl"
        plan.write_text(json.dumps({"kind": "issue", "title": "With a file", "attachments": [{"source": str(file), "name": "src/frozen.rs"}]}) + "\n")
        before = len(self.world.ledger_log())
        result = self.world.run("create", "--from-file", str(plan), "--quote", QUOTE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.world.ledger_log()) - before, 1)
        created = result.stdout.split()[0]
        self.assertIn("src/frozen.rs", self.world.run("show", created).stdout)
        blob = self.world.ledger / "items" / created / "attachments" / f"{sha(data)}.rs"
        self.assertEqual(blob.read_bytes(), data)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_plan_with_a_bad_attachment_creates_no_item_at_all(self) -> None:
        """Prevents orphan items: an unreadable attachment source in any entry refuses the whole plan before any write."""
        plan = self.root / "bad.jsonl"
        plan.write_text(
            json.dumps({"kind": "issue", "title": "Fine"}) + "\n"
            + json.dumps({"kind": "issue", "title": "Broken", "attachments": [{"source": str(self.root / "nope.bin")}]}) + "\n"
        )
        before = len(self.world.run("list").stdout.splitlines())
        result = self.world.run("create", "--from-file", str(plan), "--quote", QUOTE)
        self.assertRefused(result)
        self.assertEqual(len(self.world.run("list").stdout.splitlines()), before)


if __name__ == "__main__":
    unittest.main()
