"""Walks of imported history: events with explicit import provenance.

Adversary: historical work that cannot cite a resolvable change id failing `check`
forever, or the import marker becoming a way for ordinary completions to skip the
evidence gate.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

NOW = "2024-05-01T10:00:00Z"


def imported(item_id: str, completed: dict) -> dict:
    return {
        "id": item_id,
        "kind": "issue",
        "title": f"Old work {item_id}",
        "events": [
            {"type": "approved", "at": NOW, "actor": "importer", "quote": QUOTE},
            {"type": "completed", "at": NOW, "actor": "importer", **completed},
        ],
    }


PROVENANCE = {"provenance": "import", "original_ids": ["abc123", "def456"]}


class ImportWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def load(self, *items, flag=True):
        arguments = ["create", "--from-file", "-"] + (["--import"] if flag else [])
        return self.world.run(*arguments, stdin=json.dumps(list(items)))

    def test_imported_completions_are_reported_as_a_count_not_as_errors(self) -> None:
        result = self.load(imported("A1", PROVENANCE), imported("A2", PROVENANCE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("completed", self.world.run("show", "A1").stdout)
        checked = self.world.run("check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn("imported without evidence: 2", checked.stdout)
        events = self.world.events("A1")
        self.assertEqual(events[-1]["original_ids"], ["abc123", "def456"])
        self.assertEqual(events[-1]["at"], NOW)

    def test_events_in_a_file_are_refused_without_the_import_flag(self) -> None:
        result = self.load(imported("A1", PROVENANCE), flag=False)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.world.ledger / "items").exists())

    def test_a_completion_without_provenance_or_evidence_is_refused_at_import(self) -> None:
        result = self.load(imported("A1", {}))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.world.ledger / "items" / "A1.md").exists())

    def test_only_the_import_provenance_value_is_accepted(self) -> None:
        result = self.load(imported("A1", {"provenance": "trust-me"}))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.world.ledger / "items" / "A1.md").exists())

    def test_new_completions_keep_the_strict_gate_and_never_write_provenance(self) -> None:
        w = self.world
        item = w.create("--title", "New", "--quote", QUOTE)
        w.run("start", item)
        self.assertEqual(w.run("complete", item).returncode, 2)
        w.run("evidence", item, "--change", w.commit())
        self.assertEqual(w.run("complete", item).returncode, 0)
        self.assertNotIn("provenance", w.events(item)[-1])

    def test_a_hand_written_completion_without_provenance_still_fails_check(self) -> None:
        w = self.world
        self.load(imported("A1", PROVENANCE))
        path = w.ledger / "events" / "A1"
        last = sorted(path.glob("*.json"))[-1]
        data = json.loads(last.read_text())
        data.pop("provenance")
        last.write_text(json.dumps(data))
        self.assertEqual(w.run("check").returncode, 1)


if __name__ == "__main__":
    unittest.main()
