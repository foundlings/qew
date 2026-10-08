"""Command walks for landing a merged pull request's closing issues.

A merged PR closes its issue on GitHub but leaves the claim (assignee and
`in-progress`) and a stale board card behind. `qew land` runs the completion
bookkeeping for the caller's own claims, with the merge commit as proof.

Adversary: closing issues taken from PR body text instead of GitHub's own
closing references, another login's claim cleared without a deliberate
takeover, and a merge that GitHub has not actually recorded.
"""

import json
import unittest

from tests.command_harness import PROJECT_HINT, run_qew
from tests.test_lifecycle_walk import FULL_SHA, compare_response, issue, pulls_response, response

REPO = ["--repo", "acme/widgets"]
STALE = dict(
    state="CLOSED",
    state_reason="COMPLETED",
    assignees=["alexander"],
    labels=["qew:approved", "in-progress"],
)
CLEAN = dict(state="CLOSED", state_reason="COMPLETED", labels=["qew:approved"])


def pull_request(
    *, state="MERGED", base="main", sha=FULL_SHA, issues=(15,), has_next=False, number=42
) -> dict:
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "number": number,
                            "state": state,
                            "baseRefName": base,
                            "mergeCommit": None if sha is None else {"oid": sha},
                            "closingIssuesReferences": {
                                "nodes": [{"number": value} for value in issues],
                                "pageInfo": {"hasNextPage": has_next},
                            },
                        }
                    }
                }
            }
        )
    }


class LandWalk(unittest.TestCase):
    def test_golden_walk_completes_the_callers_stale_claim_with_the_merge_commit(self) -> None:
        result, calls = run_qew(
            [
                pull_request(),
                response(issue(**STALE)),
                response(issue(**STALE)),
                compare_response("ahead", FULL_SHA),
                pulls_response([(42, "2026-10-01T10:00:00Z")]),
                {"stdout": "edited\n"},
                {"stdout": "commented\n"},
                response(issue(**CLEAN)),
            ],
            ["land", "42", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "landed #15 from PR #42\n")
        self.assertEqual(result.stderr, PROJECT_HINT)
        edit, comment = calls[5], calls[6]
        self.assertIn("--remove-assignee", edit)
        self.assertIn(f"Landed: commit {FULL_SHA} on main", comment[comment.index("--body") + 1])

    def test_json_reports_landed_held_and_clean_issues(self) -> None:
        result, _ = run_qew(
            [
                pull_request(issues=(15, 16, 17)),
                response(issue(**STALE)),
                response(issue(**STALE)),
                compare_response("ahead", FULL_SHA),
                pulls_response([(42, "2026-10-01T10:00:00Z")]),
                {"stdout": "edited\n"},
                {"stdout": "commented\n"},
                response(issue(**CLEAN)),
                response({**issue(**{**STALE, "assignees": ["mallory"]}), "number": 16}),
                response({**issue(**CLEAN), "number": 17}),
            ],
            ["land", "42", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["landed"], [15])
        self.assertEqual(output["clean"], [17])
        self.assertEqual(output["held"], [{"issue": 16, "by": ["mallory"]}])
        self.assertEqual(output["state"], "landed")

    def test_another_logins_claim_is_reported_never_touched(self) -> None:
        # Adversary: automation silently taking over a teammate's claim.
        result, calls = run_qew(
            [pull_request(), response(issue(**{**STALE, "assignees": ["mallory"]}))],
            ["land", "42", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#15 is held by mallory", result.stdout)
        self.assertIn("qew complete 15 --take-over", result.stdout)
        self.assertEqual(len(calls), 2)

    def test_an_unmerged_pull_request_is_a_quiet_no_op(self) -> None:
        result, calls = run_qew([pull_request(state="OPEN", sha=None)], ["land", "42", *REPO])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not merged", result.stdout)
        self.assertEqual(len(calls), 1)

    def test_already_clean_issues_are_left_alone(self) -> None:
        result, calls = run_qew(
            [pull_request(), response(issue(**CLEAN))], ["land", "42", *REPO]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing to land", result.stdout)
        self.assertEqual(len(calls), 2)

    def test_an_issue_github_left_open_is_left_alone(self) -> None:
        # A PR merged into a non-default branch does not close its issue.
        result, calls = run_qew(
            [
                pull_request(base="feat/stack"),
                response(issue(assignees=["alexander"], labels=["qew:approved", "in-progress"])),
            ],
            ["land", "42", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#15 is still open", result.stdout)
        self.assertEqual(len(calls), 2)

    def test_truncated_closing_references_are_indeterminate(self) -> None:
        result, calls = run_qew([pull_request(has_next=True)], ["land", "42", *REPO])

        self.assertEqual(result.returncode, 4)
        self.assertIn("incomplete pagination", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_a_merged_pull_request_without_a_valid_merge_commit_is_indeterminate(self) -> None:
        result, calls = run_qew([pull_request(sha="not-a-sha")], ["land", "42", *REPO])

        self.assertEqual(result.returncode, 4)
        self.assertEqual(len(calls), 1)

    def test_a_non_positive_pull_request_number_is_refused_before_any_call(self) -> None:
        result, calls = run_qew([], ["land", "0", *REPO])

        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
