"""The schema 4 migration fills the evidence header of completed items from what their events recorded.

Each test is a scenario; its docstring names the failure it prevents. A cited change that still resolves and landed on the
target stays; where none do, the item gets the typed absence `historical` (or the kind its completion recorded) with the
reason the importer or the bulk path left.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_ids import event_id
from tests.ledger_harness import QUOTE, World
from tests.lifecycle_support import started


def header(world: World, item: str) -> dict:
    text = (world.ledger / "items" / item / "item.md").read_text(encoding="utf-8")
    return dict(line.split(": ", 1) for line in text.split("\n---\n")[0].splitlines() if ": " in line)


def strip_to_schema_3(world: World) -> None:
    """Make the ledger look like one written before evidence headers existed."""
    for path in (world.ledger / "items").glob("*/item.md"):
        path.write_text("\n".join(l for l in path.read_text().split("\n") if not l.startswith(("evidence:", "evidence_absent:"))), encoding="utf-8")
    (world.ledger / "qew-ledger.toml").write_text('name = "test"\nprefix = "TST"\nschema_version = 3\n', encoding="utf-8")


def inject(world: World, item: str, event_type: str, **data) -> None:
    directory = world.ledger / "events" / item
    newest = sorted(path.stem for path in directory.glob("*.json"))[-1]
    body = {"type": event_type, "at": "2026-01-01T00:00:00Z", "actor": "carol", **data}
    (directory / f"{event_id(newest)}.json").write_text(json.dumps(body), encoding="utf-8")


class EvidenceBackfillWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_every_completed_item_gets_ids_or_a_typed_absence_and_the_check_passes(self) -> None:
        """Prevents a migration that leaves old completions without proof or invents proof: ids that resolve stay, the rest are typed."""
        w = self.world
        landed = started(w, title="Landed")
        sha = w.commit("landed work")
        w.run("evidence", landed, "--change", sha)
        self.assertEqual(w.run("complete", landed).returncode, 0)
        lost = started(w, title="Lost")
        gone = w.commit("rewritten away")
        w.run("evidence", lost, "--change", gone)
        self.assertEqual(w.run("complete", lost).returncode, 0)
        subprocess.run(["git", "-C", str(w.code), "reset", "-q", "--hard", "HEAD~1"], check=True)
        nocode = started(w, title="No code")
        self.assertEqual(w.run("complete", nocode, "--no-commit", "a decision").returncode, 0)
        bulk = w.create("--kind", "task", "--title", "Bulk", "--quote", QUOTE)
        inject(w, bulk, "completed", bulk_quote="Alexander: 'done per measure-board.py (#570)'", reason="done per measure-board.py (#570)")
        epic = w.create("--kind", "epic", "--title", "Phase", "--quote", QUOTE)
        child = w.create("--kind", "task", "--title", "Part", "--parent", epic, "--quote", QUOTE)
        w.run("start", child)
        self.assertEqual(w.run("complete", child, "--no-commit", "docs").returncode, 0)
        imported = w.create("--kind", "task", "--title", "Imported", "--quote", QUOTE)
        inject(w, imported, "completed", provenance="forge-import")
        strip_to_schema_3(w)
        self.assertEqual(w.run("list").returncode, 2)
        preview = w.run("migrate-layout", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("evidence headers", preview.stdout)
        self.assertNotIn("evidence_absent", (w.ledger / "items" / landed / "item.md").read_text())
        done = w.run("migrate-layout")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(header(w, landed)["evidence"], sha)
        self.assertTrue(header(w, lost)["evidence_absent"].startswith("historical: "))
        self.assertNotIn("evidence", header(w, lost))
        self.assertEqual(header(w, nocode)["evidence_absent"], "no-code: a decision")
        self.assertEqual(header(w, bulk)["evidence_absent"], "historical: done per measure-board.py (#570)")
        self.assertEqual(header(w, epic)["evidence_absent"], "children: all 1 children finished")
        self.assertEqual(header(w, imported)["evidence_absent"], "historical: imported without resolvable evidence")
        self.assertEqual(w.run("check").returncode, 0, w.run("check").stdout)
        self.assertIn("already", w.run("migrate-layout").stdout)


if __name__ == "__main__":
    unittest.main()
