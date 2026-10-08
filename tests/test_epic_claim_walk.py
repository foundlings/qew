"""Command walks for an epic's claim, derived from its direct children.

Behavior: while an open, non-epic child of an epic carries `in-progress`, the
epic carries `in-progress`, is assigned to exactly those children's claimants,
and its card shows In progress; when none does, Qew removes what it derived.
`start`, `complete`, `release`, `close` and `reopen` reconcile the parent epic;
`project ensure` reconciles every open epic; `project status` reports drift.

Adversary: an epic that looks idle while a child is being worked, a claim
derived from blocked-by edges, grandchildren or child epics (which are linked
all over), and a failed reconcile that fails the user's own command.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_campaign_project_walk import (
    graphql_pages,
    project_item,
    project_items_page,
    project_list_page,
    project_structure,
    response as raw_response,
)
from tests.test_lifecycle_walk import issue, response
from tests.test_plan_status_walk import child as plan_child
from tests.test_plan_status_walk import plan as plan_node
from tests.test_plan_status_walk import plan_page
from tests.test_plan_status_walk import pages as plan_pages

PAGE = {"hasNextPage": False, "endCursor": None}
CLAIM = ("P2", "task", "qew:approved", "in-progress")
EPIC = ("P2", "epic", "qew:approved")
ENSURE = ["project", "ensure", "--title", "Qew Factory", "--repo", "acme/widgets", "--json"]
STATUS_WRITE = raw_response(
    {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "ITEM"}}}}
)


def node(number, labels, *, state="OPEN", assignees=(), parent=None) -> dict:
    return {
        "id": f"ISSUE{number}",
        "number": number,
        "state": state,
        "parent": None if parent is None else {"number": parent},
        "labels": {"nodes": [{"name": name} for name in labels], "pageInfo": PAGE},
        "assignees": {
            "totalCount": len(assignees),
            "nodes": [{"login": login} for login in assignees],
        },
        "blockedBy": {"nodes": [], "pageInfo": PAGE},
        "closedByPullRequestsReferences": {"nodes": [], "pageInfo": PAGE},
    }


def issues_page(*nodes) -> dict:
    return {"data": {"repository": {"issues": {"nodes": list(nodes), "pageInfo": PAGE}}}}


def ensure(items, nodes, *writes, verified=None, command=ENSURE):
    return run_qew(
        [
            graphql_pages(project_list_page()),
            raw_response(project_structure()),
            graphql_pages(project_items_page(*items)),
            graphql_pages(issues_page(*nodes)),
            *writes,
            *([] if verified is None else [graphql_pages(project_items_page(*verified))]),
        ],
        command,
    )


def edit_of(calls, number):
    return next(call for call in calls if call[:3] == ["issue", "edit", str(number)])


def value(call, flag):
    return call[call.index(flag) + 1]


class EpicEnsureWalk(unittest.TestCase):
    def test_a_claimed_direct_child_claims_and_moves_its_epic(self) -> None:
        result, calls = ensure(
            [project_item("Backlog", 10), project_item("In progress", 11)],
            [node(10, EPIC), node(11, CLAIM, assignees=["alice"], parent=10)],
            {"stdout": "edited\n"},
            STATUS_WRITE,
            verified=[project_item("In progress", 10), project_item("In progress", 11)],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], ["epic:#10", "status:#10"])
        edit = edit_of(calls, 10)
        self.assertEqual(value(edit, "--add-label"), "in-progress")
        self.assertEqual(value(edit, "--add-assignee"), "alice")
        self.assertNotIn("--remove-assignee", edit)

    def test_a_legacy_upper_case_epic_label_is_an_epic(self) -> None:
        # GitHub treats EPIC and epic as one label; some repositories' epics carry EPIC.
        result, calls = ensure(
            [project_item("Backlog", 10), project_item("In progress", 11)],
            [node(10, ("P2", "EPIC", "qew:approved")), node(11, ("P2", "task", "qew:approved", "In-Progress"), assignees=["alice"], parent=10)],
            {"stdout": "edited\n"},
            STATUS_WRITE,
            verified=[project_item("In progress", 10), project_item("In progress", 11)],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], ["epic:#10", "status:#10"])
        self.assertEqual(value(edit_of(calls, 10), "--add-assignee"), "alice")

    def test_every_claimant_is_assigned_and_a_stale_assignee_is_removed(self) -> None:
        result, calls = ensure(
            [project_item("In progress", 10), project_item("In progress", 11), project_item("In progress", 12)],
            [
                node(10, (*EPIC, "in-progress"), assignees=["carol"]),
                node(11, CLAIM, assignees=["bob"], parent=10),
                node(12, CLAIM, assignees=["alice"], parent=10),
            ],
            {"stdout": "edited\n"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], ["epic:#10"])
        edit = edit_of(calls, 10)
        self.assertEqual(value(edit, "--add-assignee"), "alice,bob")
        self.assertEqual(value(edit, "--remove-assignee"), "carol")
        self.assertNotIn("--remove-label", edit)

    def test_an_epic_whose_children_all_stopped_loses_what_qew_derived(self) -> None:
        result, calls = ensure(
            [project_item("In progress", 10), project_item("Ready", 11)],
            [
                node(10, (*EPIC, "in-progress"), assignees=["alice"]),
                node(11, ("P2", "task", "qew:approved"), parent=10),
            ],
            {"stdout": "edited\n"},
            STATUS_WRITE,
            verified=[project_item("Backlog", 10), project_item("Ready", 11)],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        edit = edit_of(calls, 10)
        self.assertEqual(value(edit, "--remove-assignee"), "alice")
        self.assertEqual(value(edit, "--remove-label"), "in-progress")
        self.assertNotIn("--add-label", edit)

    def test_only_direct_non_epic_children_claim_an_epic(self) -> None:
        # Adversary: blocked-by edges, grandchildren and child epics are linked
        # all over; counting them would put every epic in progress.
        result, calls = ensure(
            [
                project_item("Backlog", 10), project_item("Backlog", 12),
                project_item("In progress", 13), project_item("In progress", 14),
            ],
            [
                node(10, EPIC),
                node(12, EPIC, parent=10),
                node(13, CLAIM, assignees=["alice"], parent=12),
                node(14, CLAIM, assignees=["bob"]),
            ],
            {"stdout": "edited\n"},
            STATUS_WRITE,
            verified=[
                project_item("Backlog", 10), project_item("In progress", 12),
                project_item("In progress", 13), project_item("In progress", 14),
            ],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], ["epic:#12", "status:#12"])
        self.assertEqual([call[2] for call in calls if call[:2] == ["issue", "edit"]], ["12"])

    def test_a_consistent_epic_and_a_closed_epic_are_left_alone(self) -> None:
        result, calls = ensure(
            [
                project_item("In progress", 10), project_item("In progress", 11),
                project_item("Done", 20),
            ],
            [
                node(10, (*EPIC, "in-progress"), assignees=["alice"]),
                node(11, CLAIM, assignees=["alice"], parent=10),
                node(20, (*EPIC, "in-progress"), state="CLOSED", assignees=["zed"]),
            ],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], [])
        self.assertEqual([call for call in calls if call[:2] == ["issue", "edit"]], [])

    def test_an_unclaimed_epic_with_a_human_assignee_is_not_touched(self) -> None:
        # Qew only mirrors an epic it manages: one with a claimed child or its own
        # in-progress label.
        result, calls = ensure(
            [project_item("Backlog", 10)], [node(10, EPIC, assignees=["pm"])]
        )

        self.assertEqual(json.loads(result.stdout)["changed"], [])
        self.assertEqual([call for call in calls if call[:2] == ["issue", "edit"]], [])

    def test_status_reports_an_epic_claim_that_ensure_would_fix(self) -> None:
        result, calls = run_qew(
            [
                graphql_pages(project_list_page()),
                graphql_pages(project_items_page(project_item("Backlog", 10), project_item("In progress", 11))),
                graphql_pages(
                    issues_page(node(10, EPIC), node(11, CLAIM, assignees=["alice"], parent=10))
                ),
            ],
            ["project", "status", "--title", "Qew Factory", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["stale_epic_claims"], [10])
        self.assertFalse(output["in_sync"])
        self.assertFalse(any("mutation" in " ".join(call) for call in calls))


def parent_response(epic_node: dict) -> dict:
    """The single-issue projection query used to read a parent epic."""
    page = {"hasNextPage": False, "endCursor": None}
    issue_node = {**epic_node, "blocking": {"nodes": [], "pageInfo": page}, "projectItems": {"nodes": [], "pageInfo": page}}
    return {"stdout": json.dumps({"data": {"repository": {"issue": issue_node}}})}


def overlay_ensure(items, nodes, *writes, verified=None):
    return run_qew(
        [
            graphql_pages(project_list_page()),
            raw_response(project_structure(managed_views=True)),
            graphql_pages(project_items_page(*items)),
            graphql_pages(issues_page(*nodes)),
            *writes,
            *([] if verified is None else [graphql_pages(project_items_page(*verified))]),
        ],
        ENSURE,
        repository_mode="overlay",
    )


MANAGED_CLAIM = ("P2", "task", "qew:approved", "qew:managed", "in-progress")


class EpicParentScopeWalk(unittest.TestCase):
    def test_overlay_ensure_reads_the_unmanaged_parent_of_a_managed_issue(self) -> None:
        # Adversary: an unmanaged epic with a managed, claimed child never shows as
        # in progress because overlay mode only reads managed issues.
        result, calls = overlay_ensure(
            [project_item("Backlog", 10), project_item("In progress", 11)],
            [node(11, MANAGED_CLAIM, assignees=["alice"], parent=10)],
            parent_response(node(10, ("P2", "EPIC", "qew:approved"))),
            {"stdout": "edited\n"},
            STATUS_WRITE,
            verified=[project_item("In progress", 10), project_item("In progress", 11)],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], ["epic:#10", "status:#10"])
        self.assertEqual(value(edit_of(calls, 10), "--add-assignee"), "alice")

    def test_a_managed_issue_without_a_parent_reads_nothing_extra(self) -> None:
        result, calls = overlay_ensure(
            [project_item("In progress", 11)],
            [node(11, MANAGED_CLAIM, assignees=["alice"])],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], [])
        self.assertEqual(len(calls), 4)

    def test_an_unmanaged_parent_that_is_not_an_epic_is_ignored(self) -> None:
        result, calls = overlay_ensure(
            [project_item("In progress", 11)],
            [node(11, MANAGED_CLAIM, assignees=["alice"], parent=10)],
            parent_response(node(10, ("P2", "task", "qew:approved"))),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], [])
        self.assertEqual([call for call in calls if call[:2] == ["issue", "edit"]], [])


def epic_response(*, claimed=(), children=(), labels=EPIC, assignees=()) -> dict:
    """The reconcile query for one epic: itself and its direct children."""
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "repository": {
                        "issue": {
                            "number": 10,
                            "state": "OPEN",
                            "labels": {"nodes": [{"name": name} for name in labels], "pageInfo": PAGE},
                            "assignees": {
                                "nodes": [{"login": login} for login in assignees],
                                "pageInfo": PAGE,
                            },
                            "subIssues": {
                                "nodes": [
                                    {
                                        "number": number,
                                        "state": state,
                                        "labels": {
                                            "nodes": [{"name": name} for name in names],
                                            "pageInfo": PAGE,
                                        },
                                        "assignees": {
                                            "nodes": [{"login": login} for login in people],
                                            "pageInfo": PAGE,
                                        },
                                    }
                                    for number, state, names, people in (*claimed, *children)
                                ],
                                "pageInfo": PAGE,
                            },
                        }
                    }
                }
            }
        )
    }


CHILD = {"parent": {"number": 10}}


class EpicLifecycleWalk(unittest.TestCase):
    def test_start_claims_the_parent_epic_for_the_starter(self) -> None:
        result, calls = run_qew(
            [
                response({**issue(), **CHILD}),
                {"stdout": "claimed\n"},
                response({**issue(assignees=["alexander"], labels=CLAIM), **CHILD}),
                epic_response(claimed=[(15, "OPEN", CLAIM, ["alexander"])]),
                {"stdout": "edited\n"},
            ],
            ["start", "15", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        epic_edit = edit_of(calls, 10)
        self.assertEqual(value(epic_edit, "--add-label"), "in-progress")
        self.assertEqual(value(epic_edit, "--add-assignee"), "alexander")

    def test_completing_the_last_claimed_child_releases_the_epic(self) -> None:
        result, calls = run_qew(
            [
                response({**issue(assignees=["alexander"], labels=["qew:approved", "in-progress"]), **CHILD}),
                {"stdout": "Closed\n"},
                {"stdout": "edited\n"},
                response({**issue(state="CLOSED", state_reason="COMPLETED", labels=["qew:approved"]), **CHILD}),
                epic_response(
                    labels=(*EPIC, "in-progress"),
                    assignees=["alexander"],
                    children=[
                        (15, "CLOSED", ("qew:approved",), []),
                        (16, "OPEN", ("P2", "task", "qew:approved"), []),
                    ],
                ),
                {"stdout": "edited\n"},
            ],
            ["complete", "15", "--reason", "done", "--no-commit", "docs", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        epic_edit = edit_of(calls, 10)
        self.assertEqual(value(epic_edit, "--remove-label"), "in-progress")
        self.assertEqual(value(epic_edit, "--remove-assignee"), "alexander")

    def test_work_without_a_parent_makes_no_epic_call(self) -> None:
        result, calls = run_qew(
            [
                response(issue()),
                {"stdout": "claimed\n"},
                response(issue(assignees=["alexander"], labels=CLAIM)),
            ],
            ["start", "15", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 3)

    def test_a_failing_reconcile_only_warns_and_never_fails_the_command(self) -> None:
        # Adversary: derived epic bookkeeping failing makes a successful claim
        # look failed and invites a retry.
        result, calls = run_qew(
            [
                response({**issue(), **CHILD}),
                {"stdout": "claimed\n"},
                response({**issue(assignees=["alexander"], labels=CLAIM), **CHILD}),
                {"stderr": "boom\n", "exit_code": 1},
            ],
            ["start", "15", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "started #15\n")
        self.assertIn("warning: epic claim", result.stderr)

    def test_a_truncated_child_list_is_never_guessed(self) -> None:
        truncated = json.loads(epic_response(claimed=[(15, "OPEN", CLAIM, ["alexander"])])["stdout"])
        truncated["data"]["repository"]["issue"]["subIssues"]["pageInfo"]["hasNextPage"] = True
        result, calls = run_qew(
            [
                response({**issue(), **CHILD}),
                {"stdout": "claimed\n"},
                response({**issue(assignees=["alexander"], labels=CLAIM), **CHILD}),
                {"stdout": json.dumps(truncated)},
            ],
            ["start", "15", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning: epic claim", result.stderr)
        self.assertFalse(any(call[:3] == ["issue", "edit", "10"] for call in calls))


def epic_issue(number=10, labels=("P2", "epic", "qew:approved")) -> dict:
    return {**issue(labels=list(labels)), "number": number}


def completing_child_responses(epic, epic_edit=None):
    return [
        response({**issue(assignees=["alexander"], labels=["qew:approved", "in-progress"]), **CHILD}),
        {"stdout": "Closed\n"},
        {"stdout": "edited\n"},
        response({**issue(state="CLOSED", state_reason="COMPLETED", labels=["qew:approved"]), **CHILD}),
        epic,
    ]


class EpicCompletionWalk(unittest.TestCase):
    ARGUMENTS = ["complete", "15", "--reason", "done", "--no-commit", "docs", "--repo", "acme/widgets"]

    def test_closing_the_last_open_child_completes_the_epic(self) -> None:
        result, calls = run_qew(
            [
                *completing_child_responses(
                    epic_response(
                        labels=("P2", "EPIC", "qew:approved", "in-progress"),
                        assignees=["alexander"],
                        children=[(15, "CLOSED", ("qew:approved",), []), (16, "CLOSED", ("qew:approved",), [])],
                    )
                ),
                response(epic_issue(labels=("P2", "EPIC", "qew:approved", "in-progress"))),
                plan_pages(plan_page(plan_node(10, plan_child(15, "A", "CLOSED", ["P2", "task"]), plan_child(16, "B", "CLOSED", ["P2", "task"])))),
                {"stdout": "closed\n"},
                {"stdout": "released\n"},
                response({**epic_issue(), **{"state": "CLOSED", "stateReason": "COMPLETED"}}),
            ],
            self.ARGUMENTS,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        close = next(call for call in calls if call[:3] == ["issue", "close", "10"])
        self.assertIn("All 2 child issues are closed", close[close.index("--comment") + 1])
        self.assertEqual(close[close.index("--reason") + 1], "completed")

    def test_an_epic_with_an_open_child_is_never_auto_completed(self) -> None:
        result, calls = run_qew(
            [
                *completing_child_responses(
                    epic_response(
                        children=[(15, "CLOSED", ("qew:approved",), []), (16, "OPEN", ("P2", "task"), [])],
                    )
                ),
            ],
            self.ARGUMENTS,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[:3] == ["issue", "close", "10"] for call in calls))

    def test_an_epic_without_a_single_priority_is_never_auto_completed(self) -> None:
        result, calls = run_qew(
            [
                *completing_child_responses(
                    epic_response(
                        labels=("epic", "qew:approved"),
                        children=[(15, "CLOSED", ("qew:approved",), [])],
                    )
                ),
            ],
            self.ARGUMENTS,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[:3] == ["issue", "close", "10"] for call in calls))

    def test_a_failing_auto_completion_only_warns(self) -> None:
        result, calls = run_qew(
            [
                *completing_child_responses(
                    epic_response(children=[(15, "CLOSED", ("qew:approved",), [])])
                ),
                {"stderr": "boom\n", "exit_code": 1},
            ],
            self.ARGUMENTS,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "completed #15\n")
        self.assertIn("warning: epic claim", result.stderr)


if __name__ == "__main__":
    unittest.main()
