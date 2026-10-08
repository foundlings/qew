"""Command walks for the per-login claim cap.

Agent fleets run many lanes under one login; without a cap, idle loops claim
more work than can run at once. The cap is counted per calling login because
`.qew/config.json` is shared by everyone who works in the repository.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_lifecycle_walk import issue, response


BASE = {"mode": "repository", "schema_version": 1}
CLAIMED = ["qew:approved", "task", "P2", "in-progress"]


def held(count: int) -> dict:
    return {"stdout": json.dumps({"data": {"repository": {"issues": {"totalCount": count}}}})}


class ClaimCapWalk(unittest.TestCase):
    def test_start_at_the_cap_is_refused_before_any_write(self) -> None:
        result, calls = run_qew(
            [response(issue()), held(2)],
            ["start", "15", "--repo", "acme/widgets"],
            config={**BASE, "max_active_claims": 2},
        )

        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertIn("max_active_claims 2", result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertFalse(any(call[:2] == ["issue", "edit"] for call in calls))
        self.assertIn("login=alexander", calls[1])

    def test_start_below_the_cap_claims_as_before(self) -> None:
        result, calls = run_qew(
            [
                response(issue()),
                held(1),
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                response(issue(assignees=["alexander"], labels=CLAIMED)),
            ],
            ["start", "15", "--repo", "acme/widgets"],
            config={**BASE, "max_active_claims": 2},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "started #15\n")
        self.assertEqual(calls[2][:3], ["issue", "edit", "15"])

    def test_priority_triage_is_not_written_at_the_cap(self) -> None:
        result, calls = run_qew(
            [response(issue(labels=["qew:approved", "task"])), held(3)],
            ["start", "15", "--priority", "P2", "--repo", "acme/widgets"],
            config={**BASE, "max_active_claims": 3},
        )

        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertEqual(len(calls), 2)

    def test_without_a_cap_no_count_is_read(self) -> None:
        result, calls = run_qew(
            [
                response(issue()),
                {"stdout": "ok\n"},
                response(issue(assignees=["alexander"], labels=CLAIMED)),
            ],
            ["start", "15", "--repo", "acme/widgets"],
            config=BASE,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 3)

    def test_malformed_cap_is_invalid_configuration(self) -> None:
        for value in (0, -1, "3", True, 2.5):
            with self.subTest(value=value):
                result, calls = run_qew(
                    [],
                    ["start", "15", "--repo", "acme/widgets"],
                    config={**BASE, "max_active_claims": value},
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("max_active_claims", result.stderr)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
