"""Command walks for stale claims on closed issues in `project status`.

A merge that never passed through an agent (UI click, auto-merge, merge queue)
closes an issue but leaves its assignee and `in-progress` label behind, and the
board card says Done. Nothing showed those leftovers until `project status`.

Adversary: closed work that still looks claimed, hidden because the board is
otherwise in sync.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_campaign_project_walk import (
    graphql_pages,
    project_item,
    project_items_page,
    project_list_page,
    repository_issues_page,
)

ARGUMENTS = ["project", "status", "--repo", "acme/widgets", "--json"]


def closed_page(labels=("P2", "task", "qew:approved"), assignees=0, state="CLOSED") -> dict:
    page = repository_issues_page(labels, assignees=assignees)
    page["data"]["repository"]["issues"]["nodes"][0]["state"] = state
    return page


def status(page: dict):
    return run_qew(
        [
            graphql_pages(project_list_page()),
            graphql_pages(project_items_page(project_item("Done", 5))),
            graphql_pages(page),
        ],
        ARGUMENTS,
        project=31,
    )


class StaleClaimWalk(unittest.TestCase):
    def test_closed_issue_with_an_in_progress_label_is_reported_with_the_fix(self) -> None:
        result, calls = status(closed_page(labels=("P2", "task", "qew:approved", "in-progress")))

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["stale_claims"], [5])
        self.assertTrue(output["in_sync"])
        self.assertIn("qew land", output["claim_fix"])
        self.assertFalse(any("mutation" in " ".join(call) for call in calls))

    def test_closed_issue_with_an_assignee_is_reported(self) -> None:
        result, _ = status(closed_page(assignees=1))

        self.assertEqual(json.loads(result.stdout)["stale_claims"], [5])

    def test_clean_closed_and_open_claimed_issues_are_not_stale(self) -> None:
        clean, _ = status(closed_page())
        claimed, _ = run_qew(
            [
                graphql_pages(project_list_page()),
                graphql_pages(project_items_page(project_item("In progress", 5))),
                graphql_pages(
                    closed_page(labels=("P2", "task", "qew:approved", "in-progress"), state="OPEN")
                ),
            ],
            ARGUMENTS,
            project=31,
        )

        self.assertEqual(json.loads(clean.stdout)["stale_claims"], [])
        self.assertEqual(json.loads(claimed.stdout)["stale_claims"], [])
        self.assertIsNone(json.loads(clean.stdout)["claim_fix"])

    def test_human_output_lists_stale_claims_even_when_the_board_is_in_sync(self) -> None:
        result, _ = run_qew(
            [
                graphql_pages(project_list_page()),
                graphql_pages(project_items_page(project_item("Done", 5))),
                graphql_pages(closed_page(assignees=1)),
            ],
            ["project", "status", "--repo", "acme/widgets"],
            project=31,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("in sync", result.stdout)
        self.assertIn("closed but still claimed: #5", result.stdout)
        self.assertIn("qew land", result.stdout)


if __name__ == "__main__":
    unittest.main()
