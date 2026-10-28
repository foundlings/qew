"""Imported completions with evidence events show their evidence in the header; broken headers can be rebuilt.

Each test is a scenario; its docstring names the failure it prevents. An imported history entry carries its own events
(approved, claimed, evidence, completed); strict evidence needs the header the way a live completion writes it.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

NOW = "2024-05-01T10:00:00Z"


def header(world: World, item: str) -> dict:
    text = (world.home("items", item) / "item.md").read_text(encoding="utf-8")
    return dict(line.split(": ", 1) for line in text.split("\n---\n")[0].splitlines() if ": " in line)


def history(item_id: str, change_ids: list[str], vcs: str = "git") -> dict:
    return {"id": item_id, "kind": "issue", "title": f"Old work {item_id}", "events": [
        {"type": "approved", "at": NOW, "actor": "importer", "quote": QUOTE},
        {"type": "claimed", "at": NOW, "actor": "importer", "owner": "importer"},
        {"type": "evidence", "at": NOW, "actor": "importer", "vcs": vcs, "repo": "local:code", "change_ids": change_ids},
        {"type": "completed", "at": NOW, "actor": "importer"},
    ]}


class ImportEvidenceWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def load(self, *items):
        return self.world.run("create", "--from-file", "-", "--import", stdin=json.dumps(list(items)))

    def test_the_import_writes_the_header_from_the_entry_evidence(self) -> None:
        """Prevents 154 red checks: landed ids become `evidence:`, ids that never landed become `historical` with the import reason."""
        w = self.world
        landed = w.commit("the work")
        subprocess.run(["git", "-C", str(w.code), "checkout", "-q", "-b", "side"], check=True)
        side = w.commit("never merged")
        subprocess.run(["git", "-C", str(w.code), "checkout", "-q", "main"], check=True)
        result = self.load(history("IA", [landed]), history("IB", [side]), history("IC", [landed, side]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(header(w, "IA")["evidence"], landed)
        self.assertEqual(header(w, "IC")["evidence"], landed)
        self.assertEqual(header(w, "IB")["evidence_absent"], "historical: imported without resolvable evidence")
        checked = w.run("check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def broken(self) -> tuple[str, str]:
        w = self.world
        landed = w.commit("the work")
        self.assertEqual(self.load(history("IA", [landed]), history("IB", ["kk0000000001"])).returncode, 0)
        for item in ("IA", "IB"):
            path = w.home("items", item) / "item.md"
            path.write_text("\n".join(l for l in path.read_text().split("\n") if not l.startswith(("evidence:", "evidence_absent:"))), encoding="utf-8")
        return landed, "kk0000000001"

    def test_repair_evidence_rebuilds_missing_headers_from_the_events(self) -> None:
        """Prevents a red ledger with no way back: the repair command rebuilds headers from the events, once, previewable."""
        w = self.world
        landed, unknown = self.broken()
        self.assertEqual(w.run("check").returncode, 1)
        preview = w.run("repair-evidence", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("would repair 2", preview.stdout)
        self.assertEqual(w.run("check").returncode, 1)
        done = w.run("repair-evidence")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(header(w, "IA")["evidence"], landed)
        self.assertEqual(header(w, "IB")["evidence"], unknown)  # not known here: kept as cited, `check` only notes it
        self.assertEqual(w.run("check").returncode, 0)
        self.assertIn("nothing to repair", w.run("repair-evidence").stdout)

    def test_evidence_manifest_fills_a_missing_header_instead_of_calling_it_unchanged(self) -> None:
        """Prevents 'unchanged 154' on items whose header lacks ids their events already cite: the header is filled, no duplicate event."""
        w = self.world
        landed, _unknown = self.broken()
        manifest = w.base / "m.jsonl"
        manifest.write_text(json.dumps({"item": "IA", "change_ids": [landed]}) + "\n", encoding="utf-8")
        events_before = len(w.events("IA"))
        done = w.run("evidence", "--manifest", str(manifest))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("recorded 1", done.stdout)
        self.assertEqual(header(w, "IA")["evidence"], landed)
        self.assertEqual(len(w.events("IA")), events_before)


if __name__ == "__main__":
    unittest.main()
