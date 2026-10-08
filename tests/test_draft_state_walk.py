"""Command walks proving a qew:draft issue is never workable.

A draft is filed and discussed but must not be worked until the draft mark is
removed. Adversary: a human adds qew:approved in the GitHub UI to an issue that
still carries qew:draft, and some consumer of the labels treats it as workable.
"""

import json
import unittest

from tests import test_plan_status_walk as plans
from tests import test_prime_walk as prime
from tests import test_read_suite_walk as reads
from tests.command_harness import run_qew
from tests.test_lifecycle_walk import issue as lifecycle_issue
from tests.test_lifecycle_walk import response as lifecycle_response

REPO = ["--repo", "acme/widgets"]
DRAFT = ["P2", "task", "qew:approved", "qew:draft"]


class DraftStateWalk(unittest.TestCase):
    def test_a_draft_is_never_listed_as_ready(self) -> None:
        result, _ = run_qew(
            [
                {
                    "stdout": json.dumps(
                        {
                            "data": {
                                "repository": {
                                    "issues": {
                                        "nodes": [
                                            {
                                                "number": 5,
                                                "title": "Draft",
                                                "url": "https://example.test/acme/widgets/issues/5",
                                                "assignees": {"totalCount": 0},
                                                "labels": {
                                                    "nodes": [{"name": name} for name in DRAFT],
                                                    "pageInfo": {"hasNextPage": False},
                                                },
                                                "blockedBy": {
                                                    "nodes": [],
                                                    "pageInfo": {"hasNextPage": False},
                                                },
                                            }
                                        ],
                                        "pageInfo": {"hasNextPage": False},
                                    }
                                }
                            }
                        }
                    )
                }
            ],
            ["ready", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["items"], [])

    def test_a_draft_is_left_out_of_list_and_prime(self) -> None:
        listed, _ = run_qew(
            [
                reads.pages(
                    reads.issue_page(
                        reads.issue(5, "Draft", labels=DRAFT),
                        reads.issue(1, "Ready", labels=["P2", "task", "qew:approved"]),
                    )
                )
            ],
            ["list", *REPO, "--json"],
        )
        primed, _ = run_qew(
            [
                prime.pages(
                    prime.prime_page(
                        prime.issue(5, "Draft work", DRAFT),
                        prime.issue(8, "Plain work", ["P2", "task", "qew:approved"]),
                    )
                )
            ],
            ["prime", *REPO, "--json"],
        )

        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual([item["number"] for item in reads.json_lines(listed.stdout)], [1])
        self.assertEqual(primed.returncode, 0, primed.stderr)
        context = json.loads(primed.stdout)["additional_context"]
        self.assertIn("#8", context)
        self.assertNotIn("#5", context)

    def test_plan_status_counts_a_draft_child_and_never_selects_it(self) -> None:
        result, _ = run_qew(
            [
                plans.pages(
                    plans.plan_page(
                        plans.plan(
                            1,
                            plans.child(5, "Draft", "OPEN", ["P0", "task", "qew:approved", "qew:draft"]),
                            plans.child(6, "Next", "OPEN", ["P1", "task", "qew:approved"]),
                        )
                    )
                )
            ],
            ["plan", "status", "1", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["children"]["draft"], 1)
        self.assertEqual(output["next"]["number"], 6)

    def test_start_refuses_a_draft_and_names_the_approval_command(self) -> None:
        result, calls = run_qew(
            [lifecycle_response(lifecycle_issue(labels=DRAFT))], ["start", "15", *REPO]
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("qew draft approve", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_complete_refuses_a_draft_without_writing(self) -> None:
        result, calls = run_qew(
            [lifecycle_response(lifecycle_issue(labels=DRAFT))],
            ["complete", "15", "--reason", "done", "--no-commit", "no code", *REPO],
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("qew draft approve", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_a_draft_epic_is_never_completed_by_plan_complete(self) -> None:
        result, calls = run_qew(
            [lifecycle_response(lifecycle_issue(labels=["P2", "epic", "qew:approved", "qew:draft"]))],
            ["plan", "complete", "15", "--reason", "done", *REPO],
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("draft", result.stderr)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
