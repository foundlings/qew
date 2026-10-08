"""Command walks for repository discovery and complete GitHub reads."""

import json
import re
import unittest

from qew import github
from tests.command_harness import run_qew


def paginated_queries() -> dict:
    return {
        name: value
        for name, value in vars(github).items()
        if name.endswith("QUERY") and isinstance(value, str) and "after: $endCursor" in value
    }


def first_page_info_depth(query: str) -> int:
    """Brace depth, inside the paginated connection, of the first pageInfo."""
    opening = re.search(r"\w+\(first: \d+, after: \$endCursor[^)]*\)\s*\{", query)
    depth = 1
    for index in range(opening.end(), len(query)):
        if query.startswith("pageInfo", index):
            return depth
        depth += {"{": 1, "}": -1}.get(query[index], 0)
    return 0


class GitHubBoundaryWalk(unittest.TestCase):
    def test_every_paginated_query_puts_its_own_page_info_first(self) -> None:
        # Adversary: gh api --paginate follows the first pageInfo in the response.
        # A nested pageInfo (labels, blockers, field values) ahead of the
        # connection's own stops pagination after 100 results, so any board or
        # repository past 100 items fails with incomplete pagination.
        queries = paginated_queries()
        self.assertGreater(len(queries), 10)

        nested_first = sorted(
            name for name, query in queries.items() if first_page_info_depth(query) != 1
        )

        self.assertEqual(nested_first, [])

    def test_repository_is_discovered_when_repo_is_omitted(self) -> None:
        result, calls = run_qew(
            [
                {"stdout": '{"nameWithOwner":"acme/widgets"}\n'},
                pages(page([ready_issue(11)], has_next=False)),
            ],
            ["ready", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["items"][0]["number"], 11)
        self.assertEqual(calls[0][:2], ["repo", "view"])
        self.assertEqual(calls[1][:2], ["api", "graphql"])

    def test_repo_outside_github_character_set_is_refused_before_gh(self) -> None:
        # Adversary: gh api -F reads a local file for values starting with @,
        # so a repo like "@secret/x" would upload that file to GitHub.
        for value in ("@/etc/passwd/x", "acme/@notes", "ac me/widgets", "acme/wid;gets"):
            with self.subTest(value=value):
                result, calls = run_qew([], ["ready", "--repo", value])

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])

    def test_top_level_issue_pages_are_merged(self) -> None:
        result, calls = run_qew(
            [
                pages(
                    page([ready_issue(11)], has_next=True, cursor="cursor-1"),
                    page([ready_issue(12)], has_next=False),
                )
            ],
            ["ready", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [item["number"] for item in json.loads(result.stdout)["items"]],
            [11, 12],
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("--paginate", calls[0])

    def test_nested_blocker_pages_are_completed_before_readiness(self) -> None:
        issue = ready_issue(11)
        issue["blockedBy"] = {
            "nodes": [{"number": 8, "state": "CLOSED", "stateReason": "COMPLETED"}],
            "pageInfo": {"hasNextPage": True, "endCursor": "blocker-1"},
        }
        nested_page = {
            "data": {
                "repository": {
                    "issue": {
                        "blockedBy": {
                            "nodes": [{"number": 9, "state": "OPEN", "stateReason": None}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        }
        result, calls = run_qew(
            [pages(page([issue], has_next=False)), pages(nested_page)],
            ["ready", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["items"], [])
        self.assertEqual(len(calls), 2)

    def test_graphql_errors_fail_closed_with_the_server_message(self) -> None:
        result, calls = run_qew(
            [{"stdout": json.dumps({"errors": [{"message": "permission denied"}]})}],
            ["ready", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("GraphQL: permission denied", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_paginated_read_refuses_when_the_last_page_claims_more_data(self) -> None:
        result, calls = run_qew(
            [pages(page([ready_issue(11)], has_next=True, cursor="missing-page"))],
            ["ready", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("incomplete pagination", result.stderr)
        self.assertEqual(len(calls), 1)


def ready_issue(number: int) -> dict:
    return {
        "number": number,
        "title": f"Work {number}",
        "url": f"https://example.test/acme/widgets/issues/{number}",
        "assignees": {"totalCount": 0},
        "labels": {
            "nodes": [
                {"name": "P2"},
                {"name": "task"},
                {"name": "qew:approved"},
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
        "blockedBy": {
            "nodes": [],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }


def page(nodes: list, *, has_next: bool, cursor=None) -> dict:
    return {
        "data": {
            "repository": {
                "issues": {
                    "nodes": nodes,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                }
            }
        }
    }


def pages(*values: dict) -> dict:
    return {"stdout": json.dumps(list(values))}


if __name__ == "__main__":
    unittest.main()
