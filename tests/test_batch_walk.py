"""`qew batch --manifest FILE`: many mutations of different kinds under one lock, one snapshot and one commit.

Adversary: a bulk reconcile that pays the whole write path (lock, fetch, full snapshot, commit, push) once per row, a
half-applied file after one bad row, and a row that works on stale state left by the row before it.
Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class BatchWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World(ledger_vcs="git")
        self.addCleanup(self.w.close)
        self.a, self.b, self.c, self.d, self.e = (self.w.create("--title", name, "--quote", QUOTE) for name in "ABCDE")
        self.path = self.w.base / "batch.jsonl"

    def manifest(self, *rows: object) -> None:
        self.path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    def shown(self, item: str) -> dict:
        return json.loads(self.w.run("show", item, "--json").stdout)

    def commits(self) -> int:
        return int(subprocess.run(["git", "-C", str(self.w.ledger), "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout)

    def test_every_kind_of_row_applies_in_order_in_one_commit(self) -> None:
        """Prevents N lock-fetch-commit round trips: start, evidence, complete, close, comment, set and reopen share one commit,
        and a later row sees the state the earlier row left (start A, then complete A)."""
        sha = self.w.commit("work for B")
        self.manifest(
            {"op": "start", "item": self.a},
            {"op": "complete", "item": self.a, "absent": "historical", "reason": "shipped before the ledger"},
            {"op": "start", "item": self.b},
            {"op": "evidence", "item": self.b, "change_ids": [sha]},
            {"op": "complete", "item": self.b},
            {"op": "close", "item": self.c, "reason": "not_planned", "note": "out of scope"},
            {"op": "comment", "item": self.d, "text": "checked by the reconcile", "key": "reconcile-1"},
            {"op": "set", "item": self.e, "add_label": ["audited"], "priority": 1},
            {"op": "reopen", "item": self.c, "reason": "closed by mistake"},
        )
        before = self.commits()
        result = self.w.run("batch", "--manifest", str(self.path))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.commits(), before + 1)
        self.assertIn("applied 9 rows", result.stdout)
        self.assertEqual([self.shown(item)["state"] for item in (self.a, self.b, self.c, self.d)], ["completed", "completed", "approved", "approved"])
        self.assertIn("audited", self.shown(self.e)["labels"])
        self.assertIn("checked by the reconcile", json.dumps(self.shown(self.d)))
        self.assertEqual(self.w.run("check").returncode, 0)

    def test_one_failing_row_leaves_the_ledger_exactly_as_it_was(self) -> None:
        """Prevents a half-applied reconcile: rows 1 and 2 are valid, row 3 completes work nobody claimed; nothing is written."""
        self.manifest({"op": "close", "item": self.a, "reason": "not_planned"},
                      {"op": "comment", "item": self.b, "text": "hello"},
                      {"op": "complete", "item": self.c, "reason": "never claimed"})
        before = self.commits()
        result = self.w.run("batch", "--manifest", str(self.path))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("line 3", result.stderr)
        self.assertEqual(self.commits(), before)
        self.assertEqual(self.shown(self.a)["state"], "approved")
        self.assertEqual(self.w.ledger_dirty(), [])

    def test_a_malformed_row_is_refused_by_line_before_any_row_runs(self) -> None:
        """Prevents a typo in row 4 being found only after rows 1-3 ran: unknown op, unknown field and a missing item all name the line."""
        for rows, needle in (
            ([{"op": "close", "item": self.a, "reason": "not_planned"}, {"op": "explode", "item": self.b}], "line 2"),
            ([{"op": "comment", "item": self.a, "text": "x", "colour": "red"}], "colour"),
            ([{"op": "close", "reason": "not_planned"}], "line 1"),
            ([{"op": "close", "item": "TST-nope", "reason": "not_planned"}], "line 1"),
        ):
            with self.subTest(rows=rows):
                self.manifest(*rows)
                result = self.w.run("batch", "--manifest", str(self.path))
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(needle, result.stderr)
                self.assertEqual(self.shown(self.a)["state"], "approved")
        self.assertEqual(self.w.ledger_dirty(), [])

    def test_dry_run_runs_every_row_and_writes_nothing(self) -> None:
        """Prevents --dry-run promising what the real run refuses: it applies the rows against the ledger and undoes them."""
        self.manifest({"op": "close", "item": self.a, "reason": "not_planned"}, {"op": "comment", "item": self.b, "text": "x"})
        before = self.commits()
        dry = self.w.run("batch", "--manifest", str(self.path), "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn("would apply 2 rows", dry.stdout)
        self.assertEqual((self.commits(), self.shown(self.a)["state"], self.w.ledger_dirty()), (before, "approved", []))
        self.manifest({"op": "complete", "item": self.c, "reason": "never claimed"})
        refused = self.w.run("batch", "--manifest", str(self.path), "--dry-run")
        self.assertEqual(refused.returncode, 2)

    def test_the_snapshot_is_built_once_not_once_per_row(self) -> None:
        """Prevents the cost that makes bulk work slow on a big ledger: each row reloading every item and event file."""
        self.manifest(*({"op": "comment", "item": item, "text": f"note {n}"} for n, item in enumerate((self.a, self.b, self.c, self.d, self.e))),
                      *({"op": "close", "item": item, "reason": "not_planned"} for item in (self.a, self.b)))
        result = self.w.run("batch", "--manifest", str(self.path), env={"QEW_TRACE": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr.count("ledger snapshot (full)"), 1, result.stderr)
        self.assertEqual([self.shown(item)["state"] for item in (self.a, self.b, self.c)], ["closed", "closed", "approved"])

    def test_a_row_naming_the_same_item_twice_sees_the_first_rows_result(self) -> None:
        """Prevents stale reuse: a comment key posted twice in one file is the same comment, and close then close is refused by line."""
        self.manifest({"op": "comment", "item": self.a, "text": "once", "key": "k"}, {"op": "comment", "item": self.a, "text": "once", "key": "k"})
        self.assertEqual(self.w.run("batch", "--manifest", str(self.path)).returncode, 0)
        self.assertEqual(json.dumps(self.shown(self.a)).count("once"), 1)
        self.manifest({"op": "close", "item": self.b, "reason": "not_planned"}, {"op": "close", "item": self.b, "reason": "not_planned"})
        result = self.w.run("batch", "--manifest", str(self.path))
        self.assertEqual(result.returncode, 2)
        self.assertIn("line 2", result.stderr)
        self.assertEqual(self.shown(self.b)["state"], "approved")


if __name__ == "__main__":
    unittest.main()
