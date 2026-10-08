"""Command walks for how human text names drafts.

Adversary: `show` prints `open` for a draft, and `plan status` prints `0/2
closed` while drafts sit unseen, so a reader cannot tell a draft from live work.
"""

import json
import unittest

from tests import test_plan_status_walk as plans
from tests.command_harness import run_qew
from tests.test_read_suite_walk import show_issue

REPO = ["--repo", "acme/widgets"]


class DraftTextWalk(unittest.TestCase):
    def test_show_says_when_the_issue_is_a_draft(self) -> None:
        drafted = show_issue(5, approved=False)
        drafted["labels"]["nodes"].append({"name": "qew:draft"})
        result, _ = run_qew(
            [{"stdout": json.dumps({"data": {"repository": {"issue": drafted}}})}], ["show", "5", *REPO]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], "#5 open draft  Inspect me")

    def test_show_of_live_work_is_unchanged(self) -> None:
        result, _ = run_qew(
            [{"stdout": json.dumps({"data": {"repository": {"issue": show_issue(5, approved=True)}}})}],
            ["show", "5", *REPO],
        )

        self.assertEqual(result.stdout.splitlines()[0], "#5 open  Inspect me")

    def test_plan_status_counts_drafts_in_text(self) -> None:
        result, _ = run_qew(
            [
                plans.pages(
                    plans.plan_page(
                        plans.plan(
                            1,
                            plans.child(5, "Idea", "OPEN", ["P2", "task", "qew:draft"]),
                            plans.child(6, "Idea two", "OPEN", ["P2", "task", "qew:draft"]),
                            plans.child(7, "Live", "OPEN", ["P2", "task", "qew:approved"]),
                        )
                    )
                )
            ],
            ["plan", "status", "1", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0/3 closed, 2 drafts", result.stdout)

    def test_a_draft_plan_is_never_completable_and_says_it_is_a_draft(self) -> None:
        # Adversary: a draft plan whose children are all closed offered
        # qew plan complete, which then refuses it.
        draft_plan = plans.plan(1, plans.child(7, "Done", "CLOSED", ["P2", "task", "qew:approved"]))
        draft_plan["labels"]["nodes"].append({"name": "qew:draft"})
        result, _ = run_qew(
            [plans.pages(plans.plan_page(draft_plan))], ["plan", "status", "1", *REPO, "--json"]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["draft"])
        self.assertFalse(output["completable"])
        self.assertIsNone(output["complete_command"])
        text, _ = run_qew([plans.pages(plans.plan_page(draft_plan))], ["plan", "status", "1", *REPO])
        self.assertIn("(draft plan)", text.stdout)

    def test_plan_status_text_without_drafts_is_unchanged(self) -> None:
        result, _ = run_qew(
            [
                plans.pages(
                    plans.plan_page(plans.plan(1, plans.child(7, "Live", "OPEN", ["P2", "task", "qew:approved"])))
                )
            ],
            ["plan", "status", "1", *REPO],
        )

        self.assertIn("0/1 closed\n", result.stdout)


if __name__ == "__main__":
    unittest.main()
