"""A `qew batch` start row accepts `needs_on`, the same staging `qew start --needs-on` records.

Adversary: an agent that claims in a batch having to fall back to one call per claim to stage work on a collection branch, a
row that stages a need with no evidence on the branch, and a bad value that half-applies the file.
"""

import json
import unittest

from tests.ledger_harness import World, git


class BatchNeedsOnWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        w = self.world
        self.need = w.create("--title", "Spec group")
        self.work = w.create("--title", "Implement", "--needs", self.need)
        git(w.code, "checkout", "-q", "-b", "adu-fat")
        self.on_branch = w.commit("spec on the collection branch")
        git(w.code, "checkout", "-q", "main")
        w.run("start", self.need)
        self.path = w.base / "rows.jsonl"

    def batch(self, *rows: object) -> object:
        self.path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        return self.world.run("batch", "--manifest", str(self.path))

    def test_a_start_row_with_needs_on_stages_the_claim_in_the_batch(self) -> None:
        """Prevents the fallback to separate calls: the claim event names the branch and the staged need."""
        self.world.run("evidence", self.need, "--change", self.on_branch)
        done = self.batch({"op": "start", "item": self.work, "needs_on": "adu-fat"})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)  # type: ignore[attr-defined]
        claim = next(event for event in self.world.events(self.work) if event["type"] == "claimed")
        self.assertEqual((claim["needs_on"], claim["staged_needs"]), ("adu-fat", self.need))

    def test_a_row_whose_need_is_not_on_the_branch_is_refused_and_nothing_applies(self) -> None:
        """Prevents waving a need through in a batch: no evidence on the branch refuses the file, and no row applies."""
        before = len(self.world.events(self.work))
        done = self.batch({"op": "start", "item": self.work, "needs_on": "adu-fat"})
        self.assertNotEqual(done.returncode, 0)  # type: ignore[attr-defined]
        self.assertEqual(len(self.world.events(self.work)), before)

    def test_needs_on_must_be_text(self) -> None:
        """Prevents a number or list being taken for a branch name."""
        done = self.batch({"op": "start", "item": self.work, "needs_on": 7})
        self.assertNotEqual(done.returncode, 0)  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
