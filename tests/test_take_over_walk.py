"""Command walks for deliberately taking over another login's claim.

Fixing someone's issue or finishing their work leaves their claim behind, and
only they could clear it. `--take-over --reason` makes the takeover an explicit,
recorded act.

Adversary: a takeover that happens by accident (a plain start or complete must
still refuse another login's claim), that leaves no record of whose claim was
taken and why, or that frees a claim and then fails to start the work.
"""

import unittest

from tests.command_harness import run_qew
from tests.test_lane_claim_walk import CLAIMED, comments
from tests.test_lifecycle_walk import FULL_SHA, compare_response, issue, pulls_response, response

REPO = ["--repo", "acme/widgets"]
RELEASED = ["qew:approved", "task", "P2"]
HELD = dict(assignees=["mallory"], labels=CLAIMED)


def body(call: list) -> str:
    return call[call.index("--body") + 1]


class TakeOverWalk(unittest.TestCase):
    def test_start_still_refuses_another_logins_claim_without_take_over(self) -> None:
        result, calls = run_qew([response(issue(**HELD))], ["start", "15", *REPO])

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("mallory", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_start_take_over_moves_the_claim_and_records_whose_it_was(self) -> None:
        result, calls = run_qew(
            [
                response(issue(**HELD)),
                response(issue(**HELD)),
                comments(),
                {"stdout": "edited\n"},
                {"stdout": "commented\n"},
                response(issue(labels=RELEASED)),
                response(issue(labels=RELEASED)),
                {"stdout": "claimed\n"},
                response(issue(assignees=["alexander"], labels=CLAIMED)),
            ],
            ["start", "15", "--take-over", "--reason", "mallory is out; finishing it", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "started #15\n")
        release_edit, record, claim = calls[3], calls[4], calls[7]
        self.assertEqual(release_edit[release_edit.index("--remove-assignee") + 1], "mallory")
        self.assertIn("taken over", body(record))
        self.assertIn("mallory is out; finishing it", body(record))
        self.assertIn("--add-assignee", claim)

    def test_start_take_over_needs_a_reason_before_any_call(self) -> None:
        result, calls = run_qew([], ["start", "15", "--take-over", *REPO])

        self.assertEqual(result.returncode, 2)
        self.assertIn("--reason", result.stderr)
        self.assertEqual(calls, [])

    def test_start_take_over_of_unclaimed_work_is_refused_without_writing(self) -> None:
        result, calls = run_qew(
            [response(issue())], ["start", "15", "--take-over", "--reason", "why not", *REPO]
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("nothing to take over", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_start_take_over_never_frees_work_that_may_not_start(self) -> None:
        # Adversary: release first, then discover the work needs approval, which
        # would strand it unclaimed.
        result, calls = run_qew(
            [response(issue(assignees=["mallory"], labels=[*CLAIMED, "needs:approval"]))],
            ["start", "15", "--take-over", "--reason", "why not", *REPO],
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_complete_still_refuses_another_logins_claim_without_take_over(self) -> None:
        result, calls = run_qew(
            [response(issue(**HELD))],
            ["complete", "15", "--reason", "done", "--no-commit", "no code", *REPO],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("--take-over", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_complete_take_over_closes_open_work_and_records_the_previous_holder(self) -> None:
        result, calls = run_qew(
            [
                response(issue(**HELD)),
                {"stdout": "closed\n"},
                {"stdout": "edited\n"},
                response(
                    issue(state="CLOSED", state_reason="COMPLETED", labels=["qew:approved"])
                ),
            ],
            ["complete", "15", "--take-over", "--reason", "Finished it.", "--no-commit", "docs", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        comment = calls[1][calls[1].index("--comment") + 1]
        self.assertIn("Finished it.", comment)
        self.assertIn("Taken over from mallory by alexander", comment)
        self.assertEqual(calls[2][calls[2].index("--remove-assignee") + 1], "mallory")

    def test_complete_take_over_clears_a_stale_claim_on_work_github_already_closed(self) -> None:
        # The case that started this: a merged PR closed the issue and left the
        # author's assignee and in-progress label behind.
        result, calls = run_qew(
            [
                response(
                    issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        assignees=["mallory"],
                        labels=["qew:approved", "in-progress"],
                    )
                ),
                compare_response("ahead", FULL_SHA),
                pulls_response([(42, "2026-10-01T10:00:00Z")]),
                {"stdout": "edited\n"},
                {"stdout": "commented\n"},
                response(
                    issue(state="CLOSED", state_reason="COMPLETED", labels=["qew:approved"])
                ),
            ],
            [
                "complete", "15", "--take-over", "--reason", "Merged linked PR.",
                "--commit", FULL_SHA, "--landed-on", "main", *REPO,
            ],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[3][calls[3].index("--remove-assignee") + 1], "mallory")
        self.assertIn("Taken over from mallory by alexander", body(calls[4]))

    def test_complete_take_over_without_another_logins_claim_is_refused(self) -> None:
        result, calls = run_qew(
            [response(issue(assignees=["alexander"], labels=CLAIMED))],
            ["complete", "15", "--take-over", "--reason", "x", "--no-commit", "y", *REPO],
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("nothing to take over", result.stderr)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
