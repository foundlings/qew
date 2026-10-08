"""Command walks for canonical vocabulary and graph diagnostics."""

import json
import time
import unittest

from tests.command_harness import run_qew


class ModelOutputWalk(unittest.TestCase):
    def test_github_label_names_are_case_insensitive(self) -> None:
        result, _ = run_qew(
            [pages(ready_page(ready_issue(21, "Plan", ["p1", "TASK", "QEW:APPROVED", "EPIC"])))],
            ["ready", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["items"], [])

    def test_ready_is_priority_sorted_with_typed_vocabulary(self) -> None:
        result, calls = run_qew(
            [
                pages(
                    ready_page(
                        ready_issue(20, "Later", ["P3", "task", "qew:approved"]),
                        ready_issue(21, "First", ["P0", "bug", "qew:approved"]),
                    )
                )
            ],
            ["ready", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "items": [
                    {
                        "number": 21,
                        "priority": "P0",
                        "title": "First",
                        "type": "bug",
                        "url": "https://example.test/acme/widgets/issues/21",
                    },
                    {
                        "number": 20,
                        "priority": "P3",
                        "title": "Later",
                        "type": "task",
                        "url": "https://example.test/acme/widgets/issues/20",
                    },
                ],
                "schema_version": 3,
                "total": 2,
                "truncated": False,
            },
        )
        self.assertEqual(len(calls), 1)

    def test_graph_check_reports_cycles_contradictions_and_unicode_findings(self) -> None:
        nodes = [
            graph_issue(
                8,
                "Review \u202Etxt.exe",
                ["P1", "P3", "task", "enhancement", "qew:approved"],
                [9],
            ),
            graph_issue(9, "Dependency", ["P2", "task", "qew:approved"], [8]),
        ]
        result, calls = run_qew(
            [pages(graph_page(*nodes))],
            ["graph", "check", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 2)
        self.assertNotEqual(result.stdout, "", result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "contradictions": [
                    {"issue": 8, "kind": "multiple_priorities", "labels": ["P1", "P3"]},
                    {
                        "issue": 8,
                        "kind": "multiple_types",
                        "labels": ["enhancement", "task"],
                    },
                ],
                "cycles": [[8, 9, 8]],
                "schema_version": 1,
                "text_findings": [{"bidi_control": True, "issue": 8}],
                "valid": False,
            },
        )
        self.assertEqual(len(calls), 1)

    def test_graph_check_diamond_layers_finish_in_linear_time(self) -> None:
        # Adversary: layered diamonds have 2**40 paths; path enumeration never ends.
        layers = 40
        nodes = []
        for layer in range(layers):
            below = [] if layer == layers - 1 else [2 * layer + 3, 2 * layer + 4]
            nodes.append(graph_issue(2 * layer + 1, "Left", ["P2", "task"], below))
            nodes.append(graph_issue(2 * layer + 2, "Right", ["P2", "task"], below))

        started = time.monotonic()
        result, _ = run_qew(
            [pages(graph_page(*nodes))],
            ["graph", "check", "--repo", "acme/widgets", "--json"],
            timeout=20,
        )
        elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["valid"])
        self.assertLess(elapsed, 1.0)

    def test_graph_check_long_chain_is_valid_without_recursion_error(self) -> None:
        # Adversary: a 3000-deep blocker chain overflows a recursive search.
        size = 3000
        nodes = [
            graph_issue(number, "Link", ["P2", "task"], [number + 1] if number < size else [])
            for number in range(1, size + 1)
        ]

        result, _ = run_qew(
            [pages(graph_page(*nodes))],
            ["graph", "check", "--repo", "acme/widgets", "--json"],
            timeout=20,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["valid"])

    def test_graph_check_golden_walk_accepts_a_clean_dag(self) -> None:
        nodes = [
            graph_issue(8, "Foundation", ["P1", "task", "qew:approved"], []),
            graph_issue(9, "Consumer", ["P2", "enhancement", "qew:approved"], [8]),
        ]
        result, calls = run_qew(
            [pages(graph_page(*nodes))],
            ["graph", "check", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["valid"])
        self.assertEqual(len(calls), 1)


def ready_issue(number: int, title: str, labels: list) -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://example.test/acme/widgets/issues/{number}",
        "assignees": {"totalCount": 0},
        "labels": {
            "nodes": [{"name": label} for label in labels],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
        "blockedBy": {
            "nodes": [],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }


def ready_page(*issues: dict) -> dict:
    return {
        "data": {
            "repository": {
                "issues": {
                    "nodes": list(issues),
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
    }


def graph_issue(number: int, title: str, labels: list, blockers: list) -> dict:
    return {
        "number": number,
        "state": "OPEN",
        "title": title,
        "labels": {
            "nodes": [{"name": label} for label in labels],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
        "blockedBy": {
            "nodes": [{"number": blocker} for blocker in blockers],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }


def graph_page(*issues: dict) -> dict:
    return ready_page(*issues)


def pages(*values: dict) -> dict:
    return {"stdout": json.dumps(list(values))}


if __name__ == "__main__":
    unittest.main()
