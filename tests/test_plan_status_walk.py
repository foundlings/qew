"""Command walks for dependency-aware plan rollups."""

import json
import unittest

from tests.command_harness import run_qew
from tests.command_harness import project_reconciliation
from tests.test_lifecycle_walk import issue as lifecycle_issue
from tests.test_lifecycle_walk import response as lifecycle_response


class PlanStatusWalk(unittest.TestCase):
    def test_plan_complete_golden_walk_verifies_all_children_then_closes(self) -> None:
        result, calls = run_qew(
            [
                lifecycle_response(lifecycle_issue(labels=["P2", "epic", "qew:approved"])),
                pages(
                    plan_page(
                        plan(
                            15,
                            child(20, "Done", "CLOSED", ["P2", "task", "qew:approved"]),
                        )
                    )
                ),
                {"stdout": "closed\n"},
                {"stdout": "released\n"},
                lifecycle_response(
                    lifecycle_issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        labels=["P2", "epic", "qew:approved"],
                    )
                ),
            ],
            [
                "plan",
                "complete",
                "15",
                "--reason",
                "Every child landed.",
                "--repo",
                "acme/widgets",
                "--json",
            ],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "changed": True,
                "next_command": "qew campaign status",
                "issue": 15,
                "project_reconciliation": project_reconciliation(),
                "schema_version": 1,
                "state": "completed",
            },
        )
        self.assertEqual(calls[2][:3], ["issue", "close", "15"])

    def test_plan_complete_refuses_an_open_child_without_writing(self) -> None:
        result, calls = run_qew(
            [
                lifecycle_response(lifecycle_issue(labels=["P2", "epic", "qew:approved"])),
                pages(
                    plan_page(
                        plan(
                            15,
                            child(20, "Still open", "OPEN", ["P2", "task", "qew:approved"]),
                        )
                    )
                ),
            ],
            [
                "plan",
                "complete",
                "15",
                "--reason",
                "Too soon.",
                "--repo",
                "acme/widgets",
            ],
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("1 child remains open", result.stderr)
        self.assertEqual(len(calls), 2)
    def test_golden_walk_rolls_up_children_and_selects_next_ready_work(self) -> None:
        response = pages(
            plan_page(
                plan(
                    1,
                    child(5, "Closed", "CLOSED", ["P0", "task", "qew:approved"]),
                    child(6, "Claimed", "OPEN", ["P0", "task", "qew:approved"], assignees=1),
                    child(7, "Blocked", "OPEN", ["P0", "task", "qew:approved"], blockers=[9]),
                    child(8, "Next", "OPEN", ["P1", "task", "qew:approved"]),
                    child(9, "Later", "OPEN", ["P2", "task", "qew:approved"]),
                    child(10, "Needs triage", "OPEN", ["qew:approved"]),
                )
            )
        )

        result, calls = run_qew(
            [response],
            ["plan", "status", "1", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "children": {
                    "blocked": 1,
                    "claimed": 1,
                    "closed": 1,
                    "draft": 0,
                    "needs_approval": 0,
                    "ready": 2,
                    "total": 6,
                    "untriaged": 1,
                },
                "completable": False,
                "draft": False,
                "complete_command": None,
                "next": {
                    "number": 8,
                    "priority": "P1",
                    "reason": "highest-priority ready child",
                    "title": "Next",
                    "type": "task",
                    "url": "https://example.test/acme/widgets/issues/8",
                },
                "number": 1,
                "schema_version": 1,
                "state": "open",
                "title": "Plan",
                "url": "https://example.test/acme/widgets/issues/1",
            },
        )
        self.assertEqual(len(calls), 1)

    def test_plan_status_counts_approval_gated_children_and_never_selects_them(self) -> None:
        response = pages(
            plan_page(
                plan(
                    1,
                    child(5, "Gate", "OPEN", ["P0", "task", "qew:approved", "needs:approval"]),
                    child(6, "Next", "OPEN", ["P1", "task", "qew:approved"]),
                )
            )
        )

        result, _ = run_qew(
            [response],
            ["plan", "status", "1", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["children"]["needs_approval"], 1)
        self.assertEqual(output["children"]["ready"], 1)
        self.assertEqual(output["next"]["number"], 6)

    def test_plan_status_names_completion_command_when_every_child_is_closed(self) -> None:
        result, _ = run_qew(
            [
                pages(
                    plan_page(
                        plan(
                            1,
                            child(5, "Closed", "CLOSED", ["P1", "task", "qew:approved"]),
                        )
                    )
                )
            ],
            ["plan", "status", "1", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["completable"])
        self.assertEqual(
            output["complete_command"],
            "qew plan complete 1 --reason TEXT",
        )

    def test_seam_walk_refuses_incomplete_child_dependencies(self) -> None:
        blocked_child = child(
            7,
            "Maybe blocked",
            "OPEN",
            ["P1", "task", "qew:approved"],
            blockers=[9],
        )
        blocked_child["blockedBy"]["pageInfo"]["hasNextPage"] = True

        result, _ = run_qew(
            [pages(plan_page(plan(1, blocked_child)))],
            ["plan", "status", "1", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("indeterminate", result.stderr)


def child(
    number: int,
    title: str,
    state: str,
    labels: list,
    *,
    assignees: int = 0,
    blockers: list = None,
) -> dict:
    return {
        "number": number,
        "title": title,
        "state": state,
        "url": f"https://example.test/acme/widgets/issues/{number}",
        "assignees": {"totalCount": assignees},
        "labels": {
            "nodes": [{"name": label} for label in labels],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
        "blockedBy": {
            "nodes": [
                {"number": blocker, "state": "OPEN", "stateReason": None}
                for blocker in blockers or []
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }


def plan(number: int, *children: dict) -> dict:
    return {
        "number": number,
        "title": "Plan",
        "state": "OPEN",
        "url": f"https://example.test/acme/widgets/issues/{number}",
        "labels": {
            "nodes": [{"name": "P1"}, {"name": "epic"}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
        "subIssues": {
            "nodes": list(children),
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }


def plan_page(*plans: dict) -> dict:
    return {
        "data": {
            "repository": {
                "issues": {
                    "nodes": list(plans),
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
    }


def pages(*values: dict) -> dict:
    return {"stdout": json.dumps(list(values))}


if __name__ == "__main__":
    unittest.main()
