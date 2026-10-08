"""Command walks for moving Project cards on every mutation."""

import json
import unittest

from tests import test_campaign_project_walk as board
from tests import test_lifecycle_walk as lifecycle
from tests import test_pull_request_walk as pull_request
from tests.command_harness import PROJECT_HINT, REQUESTER_QUOTE, author_responses, run_qew

PAGE = {"hasNextPage": False, "endCursor": None}
STATUS_WRITE = board.response(
    {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "ITEM"}}}}
)


def add_item(number: int) -> dict:
    return board.response({"data": {"addProjectV2ItemById": {"item": {"id": f"ITEM{number}"}}}})


def card_issue(
    number: int,
    *,
    labels: list,
    state: str = "OPEN",
    assignees: int = 0,
    blockers: tuple = (),
    blockers_closed: bool = False,
    blocking: tuple = (),
    on_board: dict = None,
    pull_requests: tuple = (),
) -> dict:
    """One per-issue projection response, shaped like the real query."""
    items = []
    if on_board is not None:
        items.append(
            {
                "id": f"ITEM{number}",
                "project": {"number": 31},
                "fieldValues": {
                    "nodes": [
                        {
                            "__typename": "ProjectV2ItemFieldSingleSelectValue",
                            "name": value,
                            "field": {"name": name},
                        }
                        for name, value in on_board.items()
                    ],
                    "pageInfo": PAGE,
                },
            }
        )
    node = {
        "id": f"ISSUE{number}",
        "number": number,
        "state": state,
        "labels": {"nodes": [{"name": name} for name in labels], "pageInfo": PAGE},
        "assignees": {"totalCount": assignees},
        "blockedBy": {
            "nodes": [{"number": item, "state": "CLOSED" if blockers_closed else "OPEN", "stateReason": None} for item in blockers],
            "pageInfo": PAGE,
        },
        "blocking": {"nodes": [{"number": item} for item in blocking], "pageInfo": PAGE},
        "closedByPullRequestsReferences": {
            "nodes": [{"number": item, "state": "OPEN"} for item in pull_requests],
            "pageInfo": PAGE,
        },
        "projectItems": {"nodes": items, "pageInfo": PAGE},
    }
    return board.response({"data": {"repository": {"issue": node}}})


def board_lookup() -> list:
    return [board.graphql_pages(board.project_list_page()), board.response(board.project_structure())]


def option_writes(calls: list) -> list:
    return [item for call in calls for item in call if item.startswith("optionId=")]


PR_RESPONSES = [
    {"stdout": json.dumps(pull_request.claim_payload())},
    {"stdout": json.dumps(pull_request.show_payload())},
    {"stdout": "https://example.test/acme/widgets/pull/31\n"},
]
PR_ARGUMENTS = ["pr", "--for", "15", "--repo", "acme/widgets", "--head", "feat/15-add-export", "--json"]
CLAIMED = ["qew:approved", "enhancement", "P1", "in-progress"]


class ProjectSyncWalk(unittest.TestCase):
    def test_pr_waits_for_github_to_link_the_new_pull_request(self) -> None:
        # Adversary: GitHub links a closing reference asynchronously. Synced at
        # once, the card of a fresh PR stayed In progress and project status
        # reported it stale (StripeExportForSAP#1 / PR #2).
        result, calls = run_qew(
            [
                *PR_RESPONSES,
                *board_lookup(),
                card_issue(15, labels=CLAIMED, assignees=1, on_board={"Status": "In progress", "Priority": "P1"}),
                card_issue(
                    15,
                    labels=CLAIMED,
                    assignees=1,
                    on_board={"Status": "In progress", "Priority": "P1"},
                    pull_requests=(31,),
                ),
                STATUS_WRITE,
            ],
            PR_ARGUMENTS,
            project=31,
            environment_overrides={"QEW_LINK_RETRY_SCALE": "0"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["project_sync"]["issues"], [15])
        self.assertEqual(option_writes(calls), ["optionId=S4"])

    def test_pr_warns_when_github_never_links_the_pull_request(self) -> None:
        # The status is never guessed: without the reference the card keeps the
        # derived status and the command names the reconciliation.
        unlinked = card_issue(15, labels=CLAIMED, assignees=1, on_board={"Status": "In progress", "Priority": "P1"})
        result, calls = run_qew(
            [*PR_RESPONSES, *board_lookup(), unlinked, unlinked, unlinked, unlinked],
            PR_ARGUMENTS,
            project=31,
            environment_overrides={"QEW_LINK_RETRY_SCALE": "0"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(option_writes(calls), [])
        self.assertIn("GitHub has not linked PR #31 to #15", result.stderr)
        self.assertIn("qew project ensure", result.stderr)

    def test_start_moves_the_claimed_card_to_in_progress(self) -> None:
        # Adversary: after qew start the card sat in Ready and In progress showed
        # zero until someone remembered project ensure.
        claimed = ["qew:approved", "task", "P2", "in-progress"]
        result, calls = run_qew(
            [
                lifecycle.response(lifecycle.issue()),
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                lifecycle.response(lifecycle.issue(assignees=["alexander"], labels=claimed)),
                *board_lookup(),
                card_issue(15, labels=claimed, assignees=1, on_board={"Status": "Ready", "Priority": "P2"}),
                STATUS_WRITE,
            ],
            ["start", "15", "--repo", "acme/widgets", "--json"],
            project=31,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["project_sync"], {"issues": [15], "project": 31, "title": "Qew Factory"})
        self.assertNotIn("project_reconciliation", output)
        self.assertEqual(result.stderr, "")
        self.assertEqual(option_writes(calls), ["optionId=S3"])

    def test_sync_finds_the_board_by_number_after_a_rename(self) -> None:
        claimed = ["qew:approved", "task", "P2", "in-progress"]
        result, _ = run_qew(
            [
                lifecycle.response(lifecycle.issue()),
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                lifecycle.response(lifecycle.issue(assignees=["alexander"], labels=claimed)),
                board.graphql_pages(board.project_list_page("Renamed Board")),
                board.response(board.project_structure()),
                card_issue(15, labels=claimed, assignees=1, on_board={"Status": "In progress", "Priority": "P2"}),
            ],
            ["start", "15", "--repo", "acme/widgets", "--json"],
            project=31,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["project_sync"],
            {"issues": [15], "project": 31, "title": "Renamed Board"},
        )

    def test_complete_also_moves_the_cards_of_issues_it_unblocks(self) -> None:
        # Adversary: closing a blocker leaves its dependents in Backlog.
        result, calls = run_qew(
            [
                lifecycle.response(
                    lifecycle.issue(assignees=["alexander"], labels=["qew:approved", "in-progress"])
                ),
                {"stdout": "Closed issue acme/widgets#15\n"},
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                lifecycle.response(
                    lifecycle.issue(
                        state="CLOSED", state_reason="COMPLETED", assignees=[], labels=["qew:approved"]
                    )
                ),
                *board_lookup(),
                card_issue(
                    15,
                    labels=["qew:approved", "task", "P2"],
                    state="CLOSED",
                    blocking=(16,),
                    on_board={"Status": "In progress", "Priority": "P2"},
                ),
                STATUS_WRITE,
                card_issue(
                    16,
                    labels=["qew:approved", "task", "P2"],
                    blockers=(15,),
                    blockers_closed=True,
                    on_board={"Status": "Backlog", "Priority": "P2"},
                ),
                STATUS_WRITE,
            ],
            [
                "complete",
                "15",
                "--repo",
                "acme/widgets",
                "--reason",
                "Done.",
                "--no-commit",
                "Board-only walk.",
                "--json",
            ],
            project=31,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["project_sync"]["issues"], [15, 16])
        self.assertEqual(option_writes(calls), ["optionId=S5", "optionId=S2"])

    def test_create_adds_the_card_with_priority_and_status(self) -> None:
        # Adversary: eleven new issues stayed off the board until an ensure run.
        result, calls = run_qew(
            [
                *author_responses(),
                {"stdout": "https://example.test/acme/widgets/issues/42\n"},
                *board_lookup(),
                card_issue(42, labels=["qew:approved", "task", "P1"]),
                add_item(42),
                STATUS_WRITE,
                STATUS_WRITE,
            ],
            ["create", "--quote", REQUESTER_QUOTE, "--repo", "acme/widgets", "--type", "task", "--title", "Work", "--priority", "P1", "--json"],
            project=31,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["project_sync"]["issues"], [42])
        self.assertEqual(option_writes(calls), ["optionId=P1", "optionId=S2"])
        self.assertTrue(any("addProjectV2ItemById" in item for call in calls for item in call))

    def test_a_board_without_the_draft_column_names_project_ensure(self) -> None:
        # Adversary: a board made before drafts fails every draft card sync with
        # an opaque "missing Status option Draft" and no way forward.
        result, _ = run_qew(
            [
                *author_responses(),
                {"stdout": "https://example.test/acme/widgets/issues/42\n"},
                board.graphql_pages(board.project_list_page()),
                board.response(board.project_structure(without_draft=True)),
                card_issue(42, labels=["qew:draft", "task", "P1"]),
                add_item(42),
                STATUS_WRITE,
            ],
            ["create", "--draft", "--quote", REQUESTER_QUOTE, "--repo", "acme/widgets", "--type", "task", "--title", "Idea", "--priority", "P1", "--json"],
            project=31,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project sync failed", result.stderr)
        self.assertIn("run qew project ensure", result.stderr)

    def test_overlay_sync_skips_issues_that_are_not_managed(self) -> None:
        # Adversary: a card move drags an unmanaged issue onto the overlay board.
        labels = ["qew:approved", "task", "P2", "in-progress"]
        result, calls = run_qew(
            [
                lifecycle.response(lifecycle.issue()),
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                lifecycle.response(lifecycle.issue(assignees=["alexander"], labels=labels)),
                *board_lookup(),
                card_issue(15, labels=labels, assignees=1),
            ],
            ["start", "15", "--repo", "acme/widgets", "--json"],
            project=31,
            repository_mode="overlay",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["project_sync"]["issues"], [])
        self.assertEqual(option_writes(calls), [])
        self.assertFalse(any("addProjectV2ItemById" in item for call in calls for item in call))

    def test_sync_failure_warns_keeps_the_hint_and_never_fails_the_command(self) -> None:
        # Adversary: a Project outage turns a successful claim into an error, so
        # the agent retries a non-idempotent start.
        claimed = ["qew:approved", "task", "P2", "in-progress"]
        result, _ = run_qew(
            [
                lifecycle.response(lifecycle.issue()),
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                lifecycle.response(lifecycle.issue(assignees=["alexander"], labels=claimed)),
                board.graphql_pages(board.project_list_page()),
            ],
            ["start", "15", "--repo", "acme/widgets", "--json"],
            project=99,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning: project sync failed: no project numbered 99", result.stderr)
        self.assertTrue(result.stderr.endswith(PROJECT_HINT))
        self.assertIn("project_reconciliation", json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
