"""Refusal walks for reserved Qew labels supplied as area labels.

Adversary: an area option is a free-form label channel. Passing a reserved
label (approval, priority, type, claim) through it would approve, re-prioritize,
or claim an issue without triage. Each walk names the label channel it closes.
"""

import json
from pathlib import Path
import tempfile
import unittest

from tests.command_harness import REQUESTER_QUOTE, run_qew

RESERVED = ["qew:approved", "qew:managed", "P0", "p3", "bug", "epic", "in-progress", "needs:approval", "Needs:Approval"]


class ReservedAreaWalk(unittest.TestCase):
    def test_create_refuses_reserved_area_before_any_write(self) -> None:
        for label in RESERVED:
            with self.subTest(label=label):
                result, calls = run_qew(
                    [],
                    [
                        "create",
                        "--quote",
                        REQUESTER_QUOTE,
                        "--repo",
                        "acme/widgets",
                        "--type",
                        "task",
                        "--title",
                        "Sneaky",
                        "--area",
                        label,
                    ],
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("reserved", result.stderr)
                self.assertEqual(calls, [])

    def test_set_refuses_reserved_area_add_and_remove_before_any_write(self) -> None:
        for option in ("--add-area", "--remove-area"):
            for label in ("qew:approved", "P0", "in-progress", "needs:approval"):
                with self.subTest(option=option, label=label):
                    result, calls = run_qew(
                        [],
                        ["set", "12", "--repo", "acme/widgets", option, label],
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("reserved", result.stderr)
                    self.assertEqual(calls, [])

    def test_apply_refuses_reserved_plan_area_before_any_write(self) -> None:
        for dry_run in ([], ["--dry-run"]):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as directory:
                plan = Path(directory) / "plan.jsonl"
                plan.write_text(
                    json.dumps(
                        {
                            "id": "a",
                            "title": "Sneaky",
                            "type": "task",
                            "quote": REQUESTER_QUOTE,
                            "areas": ["qew:approved"],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                result, calls = run_qew(
                    [], ["apply", str(plan), "--repo", "acme/widgets", *dry_run]
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("reserved", result.stderr)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
