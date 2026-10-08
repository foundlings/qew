"""Command walks for validated, resumable plan application."""

import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from tests.command_harness import (
    REQUESTER_QUOTE,
    author_responses,
    project_reconciliation,
    run_qew,
)
from tests.test_campaign_project_walk import (
    graphql_pages,
    milestone,
    plan_issue_response,
    project_items_page,
    project_list_page,
    project_structure,
    response,
    rest_pages,
)


class ApplyWalk(unittest.TestCase):
    def test_apply_dry_run_validates_throttle_without_sleeping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = Path(temporary_directory) / "plan.jsonl"
            plan.write_text(
                json.dumps({"id": "task", "title": "Task", "type": "task", "quote": REQUESTER_QUOTE}) + "\n",
                encoding="utf-8",
            )
            started = time.monotonic()
            result, calls = run_qew(
                [],
                [
                    "apply",
                    str(plan),
                    "--repo",
                    "acme/widgets",
                    "--throttle",
                    "5s",
                    "--dry-run",
                    "--json",
                ],
            )
            elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(calls, [])

    def test_apply_refuses_invalid_throttle_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = Path(temporary_directory) / "plan.jsonl"
            plan.write_text(
                json.dumps({"id": "task", "title": "Task", "type": "task", "quote": REQUESTER_QUOTE}) + "\n",
                encoding="utf-8",
            )
            result, calls = run_qew(
                [],
                [
                    "apply",
                    str(plan),
                    "--repo",
                    "acme/widgets",
                    "--throttle",
                    "eventually",
                ],
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("throttle", result.stderr)
        self.assertEqual(calls, [])

    def test_apply_throttles_between_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            plan = directory / "plan.jsonl"
            state = directory / "state.json"
            plan.write_text(
                "\n".join(
                    [
                        json.dumps({"id": "one", "title": "One", "type": "task", "quote": REQUESTER_QUOTE}),
                        json.dumps({"id": "two", "title": "Two", "type": "task", "quote": REQUESTER_QUOTE}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            started = time.monotonic()
            result, calls = run_qew(
                [
                    {"stdout": "[]"},
                    *author_responses(),
                    {"stdout": "https://example.test/acme/widgets/issues/30\n"},
                    {"stdout": "[]"},
                    {"stdout": "https://example.test/acme/widgets/issues/31\n"},
                ],
                [
                    "apply",
                    str(plan),
                    "--repo",
                    "acme/widgets",
                    "--state",
                    str(state),
                    "--throttle",
                    "250ms",
                ],
            )
            elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 6)
        self.assertGreaterEqual(elapsed, 0.2)

    def test_dry_run_golden_walk_validates_without_github_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = Path(temporary_directory) / "plan.jsonl"
            plan.write_text(
                '\n'.join(
                    [
                        json.dumps({"id": "plan", "title": "Nested plan", "type": "epic", "quote": REQUESTER_QUOTE}),
                        json.dumps(
                            {
                                "id": "task",
                                "title": "First task",
                                "type": "task", "quote": REQUESTER_QUOTE,
                                "parent": "plan",
                                "blocked-by": [9],
                                "goal": "Prove application.",
                                "done-when": ["Dry-run is write-free."],
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result, calls = run_qew(
                [],
                ["apply", str(plan), "--repo", "acme/widgets", "--dry-run", "--json"],
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["schema_version"], 1)
        self.assertEqual(
            output["operations"],
            [
                {"action": "create", "id": "plan", "title": "Nested plan", "type": "epic"},
                {"action": "create", "id": "task", "title": "First task", "type": "task"},
                {"action": "parent", "issue": "task", "parent": "plan"},
                {"action": "block", "blocked_by": 9, "issue": "task"},
            ],
        )
        self.assertEqual(calls, [])

    def test_apply_seam_walk_accepts_long_chain_without_recursion_error(self) -> None:
        # Adversary: a 3000-deep blocker chain overflows a recursive cycle search.
        size = 3000
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = Path(temporary_directory) / "chain.jsonl"
            lines = []
            for index in range(size):
                entry = {"id": f"n{index}", "title": "Link", "type": "task", "quote": REQUESTER_QUOTE}
                if index + 1 < size:
                    entry["blocked-by"] = [f"n{index + 1}"]
                lines.append(json.dumps(entry))
            plan.write_text("\n".join(lines) + "\n", encoding="utf-8")

            result, calls = run_qew(
                [],
                ["apply", str(plan), "--repo", "acme/widgets", "--dry-run"],
                timeout=60,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(calls, [])

    def test_apply_seam_walk_rejects_local_cycle_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = Path(temporary_directory) / "cycle.jsonl"
            plan.write_text(
                '\n'.join(
                    [
                        json.dumps({"id": "a", "title": "A", "type": "task", "quote": REQUESTER_QUOTE, "blocked-by": ["b"]}),
                        json.dumps({"id": "b", "title": "B", "type": "task", "quote": REQUESTER_QUOTE, "blocked-by": ["a"]}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result, calls = run_qew(
                [],
                ["apply", str(plan), "--repo", "acme/widgets", "--dry-run"],
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("cycle", result.stderr)
        self.assertEqual(calls, [])

    def test_apply_refuses_explicit_id_colliding_with_generated_line_key(self) -> None:
        # Behavior: every entry key, generated or explicit, is unique.
        # Adversary: an explicit id equal to another entry's generated key
        # ("line-2") would silently skip an entry and mis-resolve references.
        orders = {
            "generated-then-explicit": [
                {"title": "Anonymous", "type": "task", "quote": REQUESTER_QUOTE},
                {"id": "line-1", "title": "Explicit", "type": "task", "quote": REQUESTER_QUOTE},
            ],
            "explicit-then-generated": [
                {"id": "line-2", "title": "Explicit", "type": "task", "quote": REQUESTER_QUOTE},
                {"title": "Anonymous", "type": "task", "quote": REQUESTER_QUOTE},
            ],
        }
        for name, entries in orders.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as temporary_directory:
                plan = Path(temporary_directory) / "plan.jsonl"
                plan.write_text(
                    "\n".join(json.dumps(entry) for entry in entries) + "\n",
                    encoding="utf-8",
                )
                result, calls = run_qew(
                    [],
                    ["apply", str(plan), "--repo", "acme/widgets", "--throttle", "0"],
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("duplicate id: line-", result.stderr)
                self.assertEqual(calls, [])

    def test_apply_golden_walk_resumes_without_duplicate_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            plan = directory / "plan.jsonl"
            state = directory / "state.json"
            plan.write_text(
                '\n'.join(
                    [
                        json.dumps({"id": "plan", "title": "Nested plan", "type": "epic", "quote": REQUESTER_QUOTE}),
                        json.dumps({"id": "task", "title": "First task", "type": "task", "quote": REQUESTER_QUOTE, "parent": "plan"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            interrupted, first_calls = run_qew(
                [
                    {"stdout": "[]"},
                    *author_responses(),
                    {"stdout": "https://example.test/acme/widgets/issues/30\n"},
                    {"stdout": "[]"},
                    {"stderr": "temporary failure\n", "exit_code": 1},
                ],
                ["apply", str(plan), "--repo", "acme/widgets", "--state", str(state), "--throttle", "0"],
            )
            self.assertEqual(interrupted.returncode, 3, interrupted.stderr)
            self.assertTrue(state.exists(), "apply must checkpoint completed creations")
            checkpoint = json.loads(state.read_text(encoding="utf-8"))
            marker = checkpoint["created"]["plan"]["marker"]

            resumed, second_calls = run_qew(
                [
                    issue_body_response(30, marker),
                    {"stdout": "[]"},
                    *author_responses(),
                    {"stdout": "https://example.test/acme/widgets/issues/31\n"},
                    {"stdout": "updated\n"},
                ],
                ["apply", str(plan), "--repo", "acme/widgets", "--state", str(state), "--throttle", "0", "--json"],
            )

        self.assertEqual(first_calls[3][:2], ["issue", "create"])
        self.assertEqual(first_calls[5][:2], ["issue", "create"])
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(second_calls[0][:2], ["issue", "view"])
        self.assertEqual(second_calls[1][:2], ["issue", "list"])
        self.assertEqual(second_calls[4][:2], ["issue", "create"])
        self.assertEqual(second_calls[5][:3], ["issue", "edit", "31"])
        output = json.loads(resumed.stdout)
        self.assertEqual(output["created"], {"plan": 30, "task": 31})
        self.assertEqual(output["project_reconciliation"], project_reconciliation())

    def test_resume_after_lost_checkpoint_adopts_marked_issue_without_duplicate(self) -> None:
        # Adversary: Qew dies after `gh issue create` but before the checkpoint
        # is saved; resume must find the marker instead of creating again.
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            plan = directory / "plan.jsonl"
            state = directory / "state.json"
            plan.write_text(
                json.dumps({"id": "solo", "title": "Solo task", "type": "task", "quote": REQUESTER_QUOTE}) + "\n",
                encoding="utf-8",
            )
            marker = expected_marker(plan, "solo")

            resumed, calls = run_qew(
                marked_lookup([lookup_issue(30, "Solo task", marker)])
                + [{"stdout": "https://example.test/acme/widgets/issues/31\n"}],
                ["apply", str(plan), "--repo", "acme/widgets", "--state", str(state), "--throttle", "0", "--json"],
            )

        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)["created"], {"solo": 30})
        self.assertNotIn(["issue", "create"], [call[:2] for call in calls])

    def test_resume_refuses_ambiguous_marker_matches_before_creating(self) -> None:
        # Adversary: two issues carry the marker; guessing one could link the
        # wrong work, so apply must refuse and write nothing.
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            plan = directory / "plan.jsonl"
            state = directory / "state.json"
            plan.write_text(
                json.dumps({"id": "solo", "title": "Solo task", "type": "task", "quote": REQUESTER_QUOTE}) + "\n",
                encoding="utf-8",
            )
            marker = expected_marker(plan, "solo")

            resumed, calls = run_qew(
                marked_lookup(
                    [lookup_issue(30, "Solo task", marker), lookup_issue(31, "Solo task", marker)]
                )
                + [{"stdout": "https://example.test/acme/widgets/issues/32\n"}],
                ["apply", str(plan), "--repo", "acme/widgets", "--state", str(state), "--throttle", "0"],
            )

        self.assertEqual(resumed.returncode, 2, resumed.stderr)
        self.assertIn("ambiguous", resumed.stderr)
        self.assertNotIn(["issue", "create"], [call[:2] for call in calls])
        self.assertFalse(state.exists())

    def test_resume_refuses_checkpoint_when_completed_parent_write_is_missing(self) -> None:
        """A local checkpoint must never override the live GitHub work graph."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            plan = directory / "plan.jsonl"
            state = directory / "state.json"
            plan.write_text(
                "\n".join(
                    [
                        json.dumps({"id": "plan", "title": "Plan", "type": "epic", "quote": REQUESTER_QUOTE}),
                        json.dumps(
                            {
                                "id": "task",
                                "title": "Task",
                                "type": "task", "quote": REQUESTER_QUOTE,
                                "parent": "plan",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            interrupted, _ = run_qew(
                [
                    {"stdout": "[]"},
                    *author_responses(),
                    {"stdout": "https://example.test/acme/widgets/issues/30\n"},
                    {"stdout": "[]"},
                    {"stdout": "https://example.test/acme/widgets/issues/31\n"},
                    {"stderr": "temporary failure\n", "exit_code": 1},
                ],
                ["apply", str(plan), "--repo", "acme/widgets", "--state", str(state), "--throttle", "0"],
            )
            self.assertEqual(interrupted.returncode, 3, interrupted.stderr)
            checkpoint = json.loads(state.read_text(encoding="utf-8"))
            checkpoint["parents"] = ["task:plan"]
            state.write_text(json.dumps(checkpoint), encoding="utf-8")

            resumed, _ = run_qew(
                [
                    issue_body_response(30, checkpoint["created"]["plan"]["marker"]),
                    issue_body_response(31, checkpoint["created"]["task"]["marker"]),
                    graphql_pages(apply_graph_page(parent=None)),
                ],
                ["apply", str(plan), "--repo", "acme/widgets", "--state", str(state), "--throttle", "0"],
            )

        self.assertEqual(resumed.returncode, 2)
        self.assertIn("parent checkpoint mismatch", resumed.stderr)

    def test_resume_refuses_noncanonical_provenance_marker(self) -> None:
        """A body substring is not proof that Qew created work for this plan digest."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            plan = directory / "plan.jsonl"
            state = directory / "state.json"
            plan.write_text(
                json.dumps({"id": "task", "title": "Task", "type": "task", "quote": REQUESTER_QUOTE}) + "\n",
                encoding="utf-8",
            )
            first, _ = run_qew(
                [{"stdout": "[]"}, *author_responses(), {"stdout": "https://example.test/acme/widgets/issues/30\n"}],
                ["apply", str(plan), "--repo", "acme/widgets", "--state", str(state), "--throttle", "0"],
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            checkpoint = json.loads(state.read_text(encoding="utf-8"))
            checkpoint["created"]["task"]["marker"] = "Goal"
            state.write_text(json.dumps(checkpoint), encoding="utf-8")

            resumed, _ = run_qew(
                [issue_body_response(30, "Goal")],
                ["apply", str(plan), "--repo", "acme/widgets", "--state", str(state), "--throttle", "0"],
            )

        self.assertEqual(resumed.returncode, 2)
        self.assertIn("provenance", resumed.stderr)

    def test_apply_golden_walk_checkpoints_campaign_and_project_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            plan = directory / "campaign.jsonl"
            state = directory / "state.json"
            plan.write_text(
                json.dumps(
                    {
                        "id": "plan",
                        "title": "Plan: Qew v2",
                        "type": "epic", "quote": REQUESTER_QUOTE,
                        "priority": "P2",
                        "campaign": {"title": "Qew v2"},
                        "project": "Qew Factory",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            item = projected_item(30)
            first, first_calls = run_qew(
                [
                    {"stdout": "[]"},
                    *author_responses(),
                    {"stdout": "https://example.test/acme/widgets/issues/30\n"},
                    plan_issue_response(30),
                    response({"number": 30, "milestone": None}),
                    rest_pages([milestone(2, "Qew v2")]),
                    response("assigned\n"),
                    response({"number": 30, "milestone": {"number": 2, "title": "Qew v2"}}),
                    graphql_pages(project_list_page()),
                    response(project_structure()),
                    graphql_pages(project_items_page(item)),
                    graphql_pages(projected_issue_page(30)),
                ],
                ["apply", str(plan), "--repo", "acme/widgets", "--state", str(state), "--throttle", "0", "--json"],
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertTrue(state.exists(), "campaign/project apply must checkpoint")
            checkpoint = json.loads(state.read_text(encoding="utf-8"))
            marker = checkpoint["created"]["plan"]["marker"]
            resumed, second_calls = run_qew(
                [
                    issue_body_response(30, marker),
                    response({"number": 30, "milestone": {"number": 2, "title": "Qew v2"}}),
                    graphql_pages(project_list_page()),
                    graphql_pages(project_items_page(item)),
                ],
                ["apply", str(plan), "--repo", "acme/widgets", "--state", str(state), "--throttle", "0", "--json"],
            )

        self.assertEqual(json.loads(first.stdout)["campaigns"], {"plan:Qew v2": 2})
        self.assertEqual(json.loads(first.stdout)["projects"], {"Qew Factory": 31})
        self.assertEqual(checkpoint["campaigns"], {"plan:Qew v2": 2})
        self.assertEqual(checkpoint["projects"], {"Qew Factory": 31})
        self.assertEqual(len(first_calls), 13)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(len(second_calls), 4)

    def test_apply_dry_run_includes_campaign_and_project_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = Path(temporary_directory) / "campaign.jsonl"
            plan.write_text(
                json.dumps(
                    {
                        "id": "plan",
                        "title": "Plan: Qew v2",
                        "type": "epic", "quote": REQUESTER_QUOTE,
                        "campaign": {"title": "Qew v2", "due-on": "2027-03-31"},
                        "project": "Qew Factory",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result, calls = run_qew(
                [],
                ["apply", str(plan), "--repo", "acme/widgets", "--dry-run", "--json"],
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["operations"],
            [
                {"action": "create", "id": "plan", "title": "Plan: Qew v2", "type": "epic"},
                {"action": "campaign", "issue": "plan", "title": "Qew v2"},
                {"action": "project", "title": "Qew Factory"},
            ],
        )
        self.assertEqual(calls, [])


def expected_marker(plan: Path, key: str) -> str:
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"<!-- qew:apply {digest}:{key_digest} -->"


def marked_lookup(issues: list) -> list:
    return [{"stdout": json.dumps(issues)}]


def lookup_issue(number: int, title: str, marker: str) -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://example.test/acme/widgets/issues/{number}",
        "body": f"### Goal\n\nWork.\n\n{marker}",
    }


def issue_body_response(number: int, marker: str) -> dict:
    return {
        "stdout": json.dumps(
            {
                "body": f"### Goal\\n\\nNested work.\\n\\n{marker}",
                "number": number,
            }
        )
    }


def projected_item(number: int) -> dict:
    return {
        "id": f"ITEM{number}",
        "content": {"__typename": "Issue", "id": f"ISSUE{number}", "number": number},
        "fieldValues": {
            "nodes": [
                {"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": "Backlog", "field": {"name": "Status"}},
                {"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": "P2", "field": {"name": "Priority"}},
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }


def projected_issue_page(number: int) -> dict:
    return {
        "data": {
            "repository": {
                "issues": {
                    "nodes": [
                        {
                            "id": f"ISSUE{number}",
                            "number": number,
                            "state": "OPEN",
                            "labels": {
                                "nodes": [{"name": "P2"}, {"name": "epic"}],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            },
                            "assignees": {"totalCount": 0},
                            "blockedBy": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            },
                            "closedByPullRequestsReferences": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            },
                        }
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
    }


def apply_graph_page(parent: int = None) -> dict:
    return {
        "data": {
            "repository": {
                "issues": {
                    "nodes": [
                        apply_graph_issue(30, None),
                        apply_graph_issue(31, parent),
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
    }


def apply_graph_issue(number: int, parent: int = None) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "state": "OPEN",
        "parent": None if parent is None else {"number": parent},
        "labels": {
            "nodes": [{"name": "P2"}, {"name": "task"}, {"name": "qew:approved"}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
        "blockedBy": {
            "nodes": [],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }


if __name__ == "__main__":
    unittest.main()
