"""Command walks for applying a JSONL plan as drafts.

A large plan is applied from a reviewed file. With --draft every issue and epic
it creates is a draft, so nothing is workable until `qew draft approve --tree`.

Adversary: a draft plan whose items carry qew:approved or needs:approval (so
they leak into ready work or the gate column), and an approved line that quietly
wins over --draft.
"""

import json
from pathlib import Path
import tempfile
import unittest

from tests.command_harness import REQUESTER_QUOTE, author_responses, run_qew

REPO = ["--repo", "acme/widgets"]


def labels(call: list) -> list:
    return [call[index + 1] for index, argument in enumerate(call) if argument == "--label"]


def write_plan(directory: Path, *lines: dict) -> Path:
    plan = directory / "plan.jsonl"
    plan.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return plan


class ApplyDraftWalk(unittest.TestCase):
    def test_every_created_issue_and_epic_is_a_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = write_plan(
                directory,
                {"id": "plan", "title": "Plan", "type": "epic", "quote": REQUESTER_QUOTE},
                {"id": "task", "title": "Task", "type": "task", "quote": REQUESTER_QUOTE},
            )
            result, calls = run_qew(
                [
                    {"stdout": "[]"},
                    *author_responses(),
                    {"stdout": "https://example.test/acme/widgets/issues/30\n"},
                    {"stdout": "[]"},
                    {"stdout": "https://example.test/acme/widgets/issues/31\n"},
                ],
                ["apply", str(plan), *REPO, "--draft", "--state", str(directory / "state.json"), "--throttle", "0"],
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        created = [call for call in calls if call[:2] == ["issue", "create"]]
        self.assertEqual(len(created), 2)
        for call in created:
            issued = labels(call)
            self.assertIn("qew:draft", issued)
            self.assertNotIn("qew:approved", issued)
            self.assertNotIn("needs:approval", issued)
        self.assertIn("epic", labels(created[0]))

    def test_an_approved_line_is_refused_under_draft_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = write_plan(
                Path(temporary),
                {"id": "task", "title": "Task", "type": "task", "approved": True, "quote": REQUESTER_QUOTE},
            )
            result, calls = run_qew([], ["apply", str(plan), *REPO, "--draft"])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--draft", result.stderr)
        self.assertEqual(calls, [])

    def test_a_dry_run_with_draft_writes_nothing_and_says_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = write_plan(
                Path(temporary), {"id": "task", "title": "Task", "type": "task", "quote": REQUESTER_QUOTE}
            )
            result, calls = run_qew([], ["apply", str(plan), *REPO, "--draft", "--dry-run", "--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])
        self.assertTrue(json.loads(result.stdout)["operations"][0]["draft"])


if __name__ == "__main__":
    unittest.main()
