"""`qew complete` has no way round its gate.

Adversary: a flag or environment variable that skips the claim, children or evidence check, a completion
written by hand into the ledger, and the import marker used outside an import.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class CompleteGateWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World()
        self.addCleanup(self.w.close)

    def started(self, title: str = "Work", *needs: str) -> str:
        item = self.w.create("--title", title, "--quote", QUOTE, *(["--needs", *needs] if needs else []))
        self.assertEqual(self.w.run("start", item).returncode, 0)
        return item

    def test_no_flag_and_no_environment_variable_skips_the_gate(self) -> None:
        """Prevents a bypass that a script or an agent could turn on."""
        item = self.started()
        for argv, env in ((["complete", item, "--force"], None), (["complete", item, "--no-evidence"], None),
                          (["complete", item], {"QEW_FORCE": "1", "QEW_SKIP_GATE": "1", "QEW_NO_GATE": "1"})):
            result = self.w.run(*argv, env=env)
            self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("completed", [e["type"] for e in self.w.events(item)])

    def test_a_completed_event_written_by_hand_without_evidence_fails_check(self) -> None:
        """Prevents a hand-edited ledger passing for finished work."""
        item = self.started()
        directory = self.w.ledger / "events" / item
        last = sorted(directory.glob("*.json"))[-1]
        (directory / f"{last.stem[:-1]}z.json").write_text(json.dumps({"type": "completed", "at": "2026-01-01T00:00:00Z", "actor": "mallory"}))
        result = self.w.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("completed without an evidence event", result.stdout)

    def test_import_provenance_without_the_original_ids_fails_check(self) -> None:
        """Prevents the import marker becoming a one-field bypass written outside --import."""
        item = self.started()
        directory = self.w.ledger / "events" / item
        last = sorted(directory.glob("*.json"))[-1]
        (directory / f"{last.stem[:-1]}z.json").write_text(
            json.dumps({"type": "completed", "at": "2026-01-01T00:00:00Z", "actor": "mallory", "provenance": "import"})
        )
        result = self.w.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("original_ids", result.stdout)

    def test_complete_refuses_a_draft_an_unclaimed_a_blocked_and_a_closed_item(self) -> None:
        draft = self.w.create("--title", "Draft")
        approved = self.w.create("--title", "Approved", "--quote", QUOTE)
        closed = self.started("Closed")
        self.w.run("close", closed, "--reason", "not_planned")
        need = self.started("Need")
        self.w.run("evidence", need, "--change", self.w.commit())
        self.assertEqual(self.w.run("complete", need).returncode, 0)
        blocked = self.started("Blocked", need)
        self.assertEqual(self.w.run("reopen", need, "--reason", "needed again").returncode, 0)
        for item, fragment in ((draft, "draft"), (approved, "unclaimed"), (closed, "closed"), (blocked, need)):
            with self.subTest(item=item):
                result = self.w.run("complete", item)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(fragment, result.stderr)


if __name__ == "__main__":
    unittest.main()
