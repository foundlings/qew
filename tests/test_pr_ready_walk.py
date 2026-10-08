"""Command walks for marking a draft pull request ready for review.

`qew pr` opens a draft, and the only way to finish the flow was `gh pr ready`.

Adversary: marking another login's pull request ready by accident, touching a
merged or closed pull request, and reporting success while it is still a draft.
"""

import json
import unittest

from tests.command_harness import run_qew

REPO = ["--repo", "acme/widgets"]


def pull_request(*, state="OPEN", draft=True, author="alexander", number=42, claims=None) -> dict:
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "viewer": {"login": "alexander"},
                    "repository": {
                        "pullRequest": {
                            "number": number,
                            "state": state,
                            "isDraft": draft,
                            "author": None if author is None else {"login": author},
                            "closingIssuesReferences": {
                                "nodes": [
                                    {
                                        "number": number_,
                                        "assignees": {
                                            "totalCount": len(logins),
                                            "nodes": [{"login": login} for login in logins],
                                        },
                                    }
                                    for number_, logins in (claims or [])
                                ],
                                "pageInfo": {"hasNextPage": False},
                            },
                        }
                    },
                }
            }
        )
    }


def writes(calls):
    return [call for call in calls if call[:2] == ["pr", "ready"]]


class PullRequestReadyWalk(unittest.TestCase):
    def test_marks_the_callers_draft_ready_and_verifies(self) -> None:
        result, calls = run_qew(
            [pull_request(), {"stdout": "ready\n"}, pull_request(draft=False)],
            ["pr", "ready", "42", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"changed": True, "pull_request": 42, "schema_version": 1, "state": "ready"},
        )
        self.assertEqual(writes(calls), [["pr", "ready", "42", "--repo", "acme/widgets"]])

    def test_human_output_names_the_pull_request(self) -> None:
        result, _ = run_qew(
            [pull_request(), {"stdout": "ready\n"}, pull_request(draft=False)],
            ["pr", "ready", "42", *REPO],
        )

        self.assertEqual(result.stdout, "marked PR #42 ready\n")

    def test_an_already_ready_pull_request_is_unchanged_and_written_nothing(self) -> None:
        result, calls = run_qew([pull_request(draft=False)], ["pr", "ready", "42", *REPO, "--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["changed"])
        self.assertEqual(writes(calls), [])
        self.assertEqual(len(calls), 1)

    def test_merged_and_closed_pull_requests_are_refused_without_writing(self) -> None:
        for state in ("MERGED", "CLOSED"):
            with self.subTest(state=state):
                result, calls = run_qew([pull_request(state=state)], ["pr", "ready", "42", *REPO])

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(state.lower(), result.stderr)
                self.assertEqual(writes(calls), [])

    def test_another_logins_pull_request_is_refused_without_writing(self) -> None:
        # Adversary: a lane marks a teammate's draft ready before they finish it.
        result, calls = run_qew(
            [pull_request(author="mallory")], ["pr", "ready", "42", *REPO]
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("mallory", result.stderr)
        self.assertEqual(writes(calls), [])

    def test_a_teammates_draft_is_readied_by_the_login_that_holds_every_claim(self) -> None:
        # The lane that builds an approved plan holds its integration issue.
        claims = [(7, ["alexander"])]
        result, calls = run_qew(
            [
                pull_request(author="peter", claims=claims),
                {"stdout": "ready\n"},
                pull_request(author="peter", draft=False, claims=claims),
            ],
            ["pr", "ready", "42", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(writes(calls)), 1)

    def test_a_teammates_draft_names_the_take_over_when_a_claim_is_missing(self) -> None:
        result, calls = run_qew(
            [pull_request(author="peter", claims=[(7, ["mallory"])])], ["pr", "ready", "42", *REPO]
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("--take-over", result.stderr)
        self.assertEqual(writes(calls), [])

    def test_a_pull_request_that_stays_a_draft_is_indeterminate(self) -> None:
        result, _ = run_qew(
            [pull_request(), {"stdout": "ready\n"}, pull_request()],
            ["pr", "ready", "42", *REPO],
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("still a draft", result.stderr)

    def test_a_non_positive_number_is_refused_before_any_call(self) -> None:
        for number in ("0", "-3"):
            with self.subTest(number=number):
                result, calls = run_qew([], ["pr", "ready", number, *REPO])

                self.assertEqual(result.returncode, 2)
                self.assertEqual(calls, [])

    def test_creating_a_pull_request_still_works_without_the_subcommand(self) -> None:
        result, calls = run_qew([], ["pr", "--for", "0", *REPO])

        self.assertNotIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
