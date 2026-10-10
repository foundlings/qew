"""Strict evidence: a completed item shows its proof in its own header, or a typed reason why there is none.

Each test is a scenario; its docstring names the failure it prevents. `evidence` lists the change ids as cited (git commits
here), `evidence_absent` is `KIND: reason` with KIND one of no-code, historical, superseded-by:ITEM or children. Events stay
the history; the header is what a reader of the item sees.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World
from tests.lifecycle_support import started


class StrictEvidenceWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def header(self, item: str) -> dict:
        text = (self.world.ledger / "items" / item / "item.md").read_text(encoding="utf-8")
        return dict(line.split(": ", 1) for line in text.split("\n---\n")[0].splitlines() if ": " in line)

    def test_a_completed_item_lists_every_cited_change_in_its_header(self) -> None:
        """Prevents proof living only in event files: the ids are in item.md and in show, several allowed."""
        w = self.world
        item = started(w)
        first, second = w.commit("first part"), w.commit("second part")
        self.assertEqual(w.run("evidence", item, "--change", first, "--change", second).returncode, 0)
        done = w.run("complete", item)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.header(item)["evidence"], f"{first} {second}")
        self.assertNotIn("evidence_absent", self.header(item))
        shown = json.loads(w.run("show", item, "--json").stdout)
        self.assertEqual(shown["evidence_ids"], [first, second])
        self.assertEqual(w.run("check").returncode, 0)

    def test_no_commit_becomes_the_typed_absence_no_code(self) -> None:
        """Prevents a silent no-evidence completion: --no-commit WHY is recorded as `no-code: WHY` in the header."""
        w = self.world
        item = started(w)
        self.assertEqual(w.run("complete", item, "--no-commit", "delivered outside the repository").returncode, 0)
        self.assertEqual(self.header(item)["evidence_absent"], "no-code: delivered outside the repository")
        self.assertNotIn("evidence", self.header(item))
        self.assertEqual(json.loads(w.run("show", item, "--json").stdout)["evidence_absent"],
                         {"kind": "no-code", "reason": "delivered outside the repository"})
        self.assertEqual(w.run("check").returncode, 0)

    def test_historical_and_superseded_absences_need_a_reason_and_a_real_item(self) -> None:
        """Prevents vague absences: --absent KIND --reason TEXT, superseded-by names an existing item, bad kinds are refused."""
        w = self.world
        old, newer, other = started(w, title="Old"), started(w, title="Newer"), started(w, title="Other")
        done = w.run("complete", old, "--absent", "historical", "--reason", "done before the ledger existed")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.header(old)["evidence_absent"], "historical: done before the ledger existed")
        replaced = w.run("complete", other, "--absent", f"superseded-by:{newer}", "--reason", "folded into the newer item")
        self.assertEqual(replaced.returncode, 0, replaced.stdout + replaced.stderr)
        self.assertEqual(self.header(other)["evidence_absent"], f"superseded-by:{newer}: folded into the newer item")
        third = started(w, title="Third")
        for arguments, needle in ((("--absent", "historical"), "--reason"), (("--absent", "whatever", "--reason", "x"), "no-code"),
                                  (("--absent", "superseded-by:NOPE", "--reason", "x"), "NOPE"), (("--absent", "no-code", "--reason", " "), "--reason")):
            refused = w.run("complete", third, *arguments)
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertIn(needle, refused.stderr)
        self.assertNotIn("completed", [e["type"] for e in w.events(third)])

    def test_an_epic_finished_by_its_children_carries_the_absence_children(self) -> None:
        """Prevents epics being the loophole: finished by their children is itself a typed absence."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Phase", "--quote", QUOTE)
        child = w.create("--kind", "task", "--title", "Part", "--parent", epic, "--quote", QUOTE)
        self.assertEqual(w.run("start", child).returncode, 0)
        self.assertEqual(w.run("complete", child, "--no-commit", "docs only").returncode, 0)
        self.assertEqual(self.header(epic).get("evidence_absent", "")[:10], "children: ")  # the last child finished it
        self.assertTrue(self.header(epic)["evidence_absent"].startswith("children: "))
        self.assertEqual(w.run("check").returncode, 0)

    def test_check_rejects_a_completed_item_whose_header_has_neither_proof_nor_absence(self) -> None:
        """Prevents a hand edit or merge dropping the proof: strict check names the item."""
        w = self.world
        item = started(w)
        self.assertEqual(w.run("complete", item, "--no-commit", "docs only").returncode, 0)
        path = w.ledger / "items" / item / "item.md"
        path.write_text("\n".join(l for l in path.read_text().split("\n") if not l.startswith("evidence_absent")), encoding="utf-8")
        broken = w.run("check")
        self.assertEqual(broken.returncode, 1, broken.stdout + broken.stderr)
        self.assertIn(item, broken.stdout + broken.stderr)
        self.assertIn("evidence", broken.stdout + broken.stderr)

    def test_check_resolves_the_header_ids_against_the_target_branch(self) -> None:
        """Prevents proof that went missing: a cited change that is no longer an ancestor of the target fails the check."""
        w = self.world
        item = started(w)
        sha = w.commit("the work")
        self.assertEqual(w.run("evidence", item, "--change", sha).returncode, 0)
        self.assertEqual(w.run("complete", item).returncode, 0)
        self.assertEqual(w.run("check").returncode, 0)
        subprocess.run(["git", "-C", str(w.code), "reset", "-q", "--hard", "HEAD~1"], check=True)
        lost = w.run("check")
        self.assertEqual(lost.returncode, 1, lost.stdout + lost.stderr)
        self.assertIn(f"evidence {sha}", lost.stdout)


if __name__ == "__main__":
    unittest.main()
