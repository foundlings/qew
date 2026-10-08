"""Command walks for the pull request of a plan that is still a draft.

A plan's epic pull request opens at planning time, when its integration issue is
a draft and nobody can claim it. The pull request carries qew:draft until its
issues are approved, and approving or reverting the issues keeps the label true.

Adversary: a plan pull request that can be readied or merged while its plan is
a draft, a label that outlives the approval, a revert that leaves the pull
request looking live, and `--ready` quietly opening a non-draft plan PR.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_draft_approve_walk import APPROVED, COMMENTED, EDITED, REPO, node as approve_node, target
from tests.test_draft_revert_walk import LIVE, node as revert_node
from tests.test_pull_request_walk import PullRequestWalk, claim_payload, connection, show_payload
from tests.test_pr_merge_walk import pull

QUOTE = "Alexander: 'approve the plan and release it into ready work for the team'"


def qew_pr(responses, *arguments):
    helper = PullRequestWalk("test_golden_walk_derives_title_body_and_exact_closing_reference")
    return helper._run_qew(responses, "synced", "pr", "--for", "15", "--repo", "acme/widgets", *arguments)


def draft_claim() -> dict:
    payload = claim_payload()
    issue = payload["data"]["repository"]["issue"]
    issue["assignees"] = connection()
    issue["labels"] = connection({"name": "qew:draft"}, {"name": "P1"}, {"name": "task"})
    return payload


def draft_show() -> dict:
    payload = show_payload()
    issue = payload["data"]["repository"]["issue"]
    issue["assignees"] = connection()
    issue["labels"] = connection({"name": "qew:draft"}, {"name": "P1"}, {"name": "task"})
    return payload


def pr_edits(calls):
    return [call for call in calls if call[:2] == ["pr", "edit"]]


class PullRequestForDraftIssueWalk(unittest.TestCase):
    def test_qew_pr_for_a_draft_issue_needs_no_claim_and_labels_the_pull_request(self) -> None:
        result, calls = qew_pr(
            [
                {"stdout": json.dumps(draft_claim())},
                {"stdout": json.dumps(draft_show())},
                {"stdout": "https://example.test/acme/widgets/pull/31\n"},
            ]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        create = calls[2]
        self.assertIn("--draft", create)
        self.assertEqual(create[create.index("--label") + 1], "qew:draft")

    def test_ready_is_refused_for_a_draft_issue_before_any_call(self) -> None:
        result, calls = qew_pr(
            [
                {"stdout": json.dumps(draft_claim())},
                {"stdout": json.dumps(draft_show())},
            ],
            "--ready",
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("always a draft", result.stderr)
        self.assertFalse([call for call in calls if call[:2] == ["pr", "create"]])


def labelled_pull(labels, draft=True) -> dict:
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "viewer": {"login": "alexander"},
                    "repository": {
                        "pullRequest": {
                            "number": 42,
                            "state": "OPEN",
                            "isDraft": draft,
                            "author": {"login": "alexander"},
                            "body": "",
                            "labels": {"nodes": [{"name": name} for name in labels], "pageInfo": {"hasNextPage": False}},
                        }
                    },
                }
            }
        )
    }


class PullRequestReadyDraftWalk(unittest.TestCase):
    def test_ready_is_refused_while_the_pull_request_is_labelled_qew_draft(self) -> None:
        result, calls = run_qew([labelled_pull(["qew:draft"])], ["pr", "ready", "42", *REPO])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("qew:draft", result.stderr)
        self.assertIn("qew draft approve", result.stderr)
        self.assertFalse([call for call in calls if call[:2] == ["pr", "ready"]])


PLAN_PR = {"number": 50, "state": "OPEN", "isDraft": True}


def with_pulls(response: dict, pulls: list) -> dict:
    value = json.loads(response["stdout"])
    value["data"]["repository"]["issue"]["closedByPullRequestsReferences"] = {
        "nodes": pulls,
        "pageInfo": {"hasNextPage": False},
    }
    return {"stdout": json.dumps(value)}


class ApproveAndRevertKeepThePullRequestLabelTrueWalk(unittest.TestCase):
    def test_approving_the_issues_removes_qew_draft_from_the_pull_request(self) -> None:
        plan_pr = {**PLAN_PR, "labels": {"nodes": [{"name": "qew:draft"}]}}
        result, calls = run_qew(
            [
                with_pulls(approve_node(7), [plan_pr]),
                EDITED, COMMENTED, target(7, APPROVED),
                pull(labels=("qew:draft",), draft=True, closing=((7, ("task", "qew:approved")),)),
                {"stdout": "edited\n"},
            ],
            ["draft", "approve", "7", "--quote", QUOTE, *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        (edit,) = pr_edits(calls)
        self.assertEqual(edit[2], "50")
        self.assertEqual(edit[edit.index("--remove-label") + 1], "qew:draft")

    def test_the_label_stays_while_another_closing_issue_is_still_a_draft(self) -> None:
        plan_pr = {**PLAN_PR, "labels": {"nodes": [{"name": "qew:draft"}]}}
        result, calls = run_qew(
            [
                with_pulls(approve_node(7), [plan_pr]),
                EDITED, COMMENTED, target(7, APPROVED),
                pull(
                    labels=("qew:draft",),
                    draft=True,
                    closing=((7, ("task", "qew:approved")), (8, ("task", "qew:draft"))),
                ),
            ],
            ["draft", "approve", "7", "--quote", QUOTE, *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pr_edits(calls), [])

    def test_reverting_an_issue_with_a_draft_pull_request_labels_the_pull_request(self) -> None:
        plan_pr = {**PLAN_PR, "labels": {"nodes": []}}
        result, calls = run_qew(
            [
                with_pulls(revert_node(7), [plan_pr]),
                EDITED, COMMENTED, target(7, ["P2", "task", "qew:draft"]),
                {"stdout": "edited\n"},
            ],
            ["draft", "revert", "7", "--reason", "plan changed", "--quote", QUOTE, *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        (edit,) = pr_edits(calls)
        self.assertEqual(edit[edit.index("--add-label") + 1], "qew:draft")

    def test_a_ready_pull_request_still_blocks_a_revert(self) -> None:
        live_pr = {"number": 50, "state": "OPEN", "isDraft": False, "labels": {"nodes": []}}
        result, calls = run_qew(
            [with_pulls(revert_node(7), [live_pr])],
            ["draft", "revert", "7", "--reason", "plan changed", "--quote", QUOTE, *REPO],
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("pull request", result.stderr)


if __name__ == "__main__":
    unittest.main()
