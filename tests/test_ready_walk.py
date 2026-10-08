"""Command walks for the ready-work contract."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.command_harness import make_checkout


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReadyWalk(unittest.TestCase):
    def run_qew(self, response: dict, *extra_arguments: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fake_gh = Path(temporary_directory) / "gh"
            fake_gh.write_text(
                "#!/bin/sh\nprintf '%s' \"$QEW_FAKE_GH_RESPONSE\"\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            environment = os.environ.copy()
            environment["QEW_FAKE_GH_RESPONSE"] = json.dumps(response)
            environment["PATH"] = temporary_directory + os.pathsep + environment["PATH"]
            environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "qew",
                    "ready",
                    "--repo",
                    "acme/widgets",
                    "--json",
                    *extra_arguments,
                ],
                cwd=make_checkout(Path(temporary_directory)),
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_approval_gated_work_is_never_ready(self) -> None:
        """Work a human must authorize before it may start is not a to-do item."""
        response = graph_response(
            issue(
                20,
                "Approve: add an authority",
                labels=["P1", "task", "qew:approved", "needs:approval"],
            ),
            issue(21, "Plain work"),
        )

        result = self.run_qew(response)

        self.assertEqual(result.returncode, 0, result.stderr)
        numbers = [item["number"] for item in json.loads(result.stdout)["items"]]
        self.assertEqual(numbers, [21])

    def test_golden_walk_lists_only_actionable_work(self) -> None:
        """Open blockers and existing assignees must keep work out of ready."""
        response = graph_response(
            issue(11, "Build queue", blockers=[blocker(7, "CLOSED", "COMPLETED")]),
            issue(12, "Deploy queue", blockers=[blocker(11, "OPEN", None)]),
            issue(13, "Document queue", assignees=1),
        )

        result = self.run_qew(response)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "items": [
                    {
                        "number": 11,
                        "priority": "P2",
                        "title": "Build queue",
                        "type": "task",
                        "url": "https://example.test/acme/widgets/issues/11",
                    }
                ],
                "schema_version": 3,
                "total": 1,
                "truncated": False,
            },
        )

    def test_seam_walk_refuses_truncated_dependency_data(self) -> None:
        """An incomplete blocker list must never produce a guessed ready queue."""
        response = graph_response(issue(11, "Build queue", blockers_truncated=True))

        result = self.run_qew(response)

        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stdout, "")
        self.assertIn("indeterminate", result.stderr)
        self.assertIn("#11", result.stderr)

    def test_default_limit_is_bounded_and_reports_truncation(self) -> None:
        """Agent context must not silently grow past the documented default bound."""
        response = graph_response(*(issue(number, f"Work {number}") for number in range(1, 32)))

        result = self.run_qew(response)

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(len(output["items"]), 30)
        self.assertEqual(output["total"], 31)
        self.assertTrue(output["truncated"])
        self.assertIn("truncated", result.stderr)

    def test_zero_limit_returns_all_ready_work(self) -> None:
        """An explicit zero requests the complete, still-validated ready result."""
        response = graph_response(*(issue(number, f"Work {number}") for number in range(1, 32)))

        result = self.run_qew(response, "--limit", "0")

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(len(output["items"]), 31)
        self.assertFalse(output["truncated"])


def graph_response(*issues: dict) -> dict:
    return {
        "data": {
            "repository": {
                "issues": {
                    "nodes": list(issues),
                    "pageInfo": {"hasNextPage": False},
                }
            }
        }
    }


def issue(
    number: int,
    title: str,
    *,
    assignees: int = 0,
    blockers: list = None,
    blockers_truncated: bool = False,
    labels: list = None,
) -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://example.test/acme/widgets/issues/{number}",
        "assignees": {"totalCount": assignees},
        "labels": {
            "nodes": [
                {"name": name} for name in labels or ["P2", "task", "qew:approved"]
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
        "blockedBy": {
            "nodes": blockers or [],
            "pageInfo": {"hasNextPage": blockers_truncated},
        },
    }


def blocker(number: int, state: str, state_reason: str) -> dict:
    return {"number": number, "state": state, "stateReason": state_reason}


if __name__ == "__main__":
    unittest.main()
