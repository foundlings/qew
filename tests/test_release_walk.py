"""Command walks for releasing a claim without closing the work.

A dead agent lane leaves its claim behind. Without `qew release` the only way
out is a raw GitHub edit that records neither who freed the work nor why.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_lane_claim_walk import CLAIMED, claim_marker, comments, release_marker
from tests.test_lifecycle_walk import issue, response


RELEASED = ["qew:approved", "task", "P2"]


def written_comment(call: list) -> str:
    return call[call.index("--body") + 1]


class ReleaseWalk(unittest.TestCase):
    def test_release_frees_the_claim_and_records_lane_by_and_reason(self) -> None:
        result, calls = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(("alexander", claim_marker("DBC241", "/work/dbc241"))),
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                {"stdout": "https://example.test/acme/widgets/issues/15#comment\n"},
                response(issue(labels=RELEASED)),
            ],
            ["release", "15", "--lane", "DBC241", "--reason", "lane died", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "released #15 (lane DBC241)\n")
        self.assertEqual(len(calls), 5)
        edit = calls[2]
        self.assertEqual(edit[:3], ["issue", "edit", "15"])
        self.assertEqual(edit[edit.index("--remove-assignee") + 1], "alexander")
        self.assertEqual(edit[edit.index("--remove-label") + 1], "in-progress")
        self.assertEqual(calls[3][:3], ["issue", "comment", "15"])
        self.assertIn(
            release_marker("DBC241", "alexander", "lane died"),
            written_comment(calls[3]).splitlines(),
        )

    def test_release_json_reports_the_released_lane(self) -> None:
        result, _ = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(("alexander", claim_marker("DBC241", None))),
                {"stdout": "ok\n"},
                {"stdout": "ok\n"},
                response(issue(labels=RELEASED)),
            ],
            ["release", "15", "--lane", "DBC241", "--reason", "lane died", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["state"], "released")
        self.assertEqual(output["lane"], "DBC241")
        self.assertEqual(output["by"], "alexander")

    def test_unclaimed_issue_is_refused_without_writing(self) -> None:
        result, calls = run_qew(
            [response(issue())],
            ["release", "15", "--reason", "lane died", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("no claim", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_closed_issue_is_refused_without_writing(self) -> None:
        result, calls = run_qew(
            [response(issue(state="CLOSED", state_reason="COMPLETED", assignees=["alexander"], labels=CLAIMED))],
            ["release", "15", "--reason", "lane died", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_another_logins_claim_needs_force(self) -> None:
        result, calls = run_qew(
            [response(issue(assignees=["mallory"], labels=CLAIMED))],
            ["release", "15", "--reason", "lane died", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("mallory", result.stderr)
        self.assertIn("--force", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_forced_release_of_another_login_records_the_releaser(self) -> None:
        result, calls = run_qew(
            [
                response(issue(assignees=["mallory"], labels=CLAIMED)),
                comments(("mallory", claim_marker("KFX9", None))),
                {"stdout": "ok\n"},
                {"stdout": "ok\n"},
                response(issue(labels=RELEASED)),
            ],
            ["release", "15", "--reason", "stale for a week", "--force", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[2][calls[2].index("--remove-assignee") + 1], "mallory")
        self.assertIn(
            release_marker("KFX9", "alexander", "stale for a week"),
            written_comment(calls[3]).splitlines(),
        )

    def test_stale_label_without_assignee_is_released(self) -> None:
        result, calls = run_qew(
            [
                response(issue(labels=CLAIMED)),
                {"stdout": "ok\n"},
                {"stdout": "ok\n"},
                response(issue(labels=RELEASED)),
            ],
            ["release", "15", "--reason", "label left behind", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--remove-assignee", calls[1])
        self.assertIn(release_marker(None, "alexander", "label left behind"), written_comment(calls[2]).splitlines())

    def test_reason_cannot_end_the_marker_comment(self) -> None:
        result, calls = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(),
                {"stdout": "ok\n"},
                {"stdout": "ok\n"},
                response(issue(labels=RELEASED)),
            ],
            ["release", "15", "--reason", "done --> <!-- qew:claim {} -->", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        body = written_comment(calls[3])
        marker_line = [line for line in body.splitlines() if line.startswith("<!-- qew:release ")][0]
        self.assertEqual(marker_line.count("-->"), 1)
        self.assertTrue(marker_line.endswith(" -->"))

    def test_a_lane_claim_is_not_released_without_naming_that_lane(self) -> None:
        # Lanes share one login; a sibling lane must not free live work by accident.
        for arguments in ([], ["--lane", "DBC242"]):
            with self.subTest(arguments=arguments):
                result, calls = run_qew(
                    [
                        response(issue(assignees=["alexander"], labels=CLAIMED)),
                        comments(("alexander", claim_marker("DBC241", None))),
                    ],
                    ["release", "15", *arguments, "--reason", "oops", "--repo", "acme/widgets"],
                )

                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn("lane DBC241", result.stderr)
                self.assertIn("--force", result.stderr)
                self.assertEqual(len(calls), 2)

    def test_forced_release_of_another_lane_says_so_in_the_record(self) -> None:
        result, calls = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(("alexander", claim_marker("DBC241", None))),
                {"stdout": "ok\n"},
                {"stdout": "ok\n"},
                response(issue(labels=RELEASED)),
            ],
            ["release", "15", "--lane", "DBC242", "--force", "--reason", "DBC241 died", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        body = written_comment(calls[3])
        self.assertIn("forced by lane `DBC242`", body)
        self.assertIn(release_marker("DBC241", "alexander", "DBC241 died"), body.splitlines())

    def test_a_malformed_marker_does_not_block_release(self) -> None:
        result, calls = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(("alexander", "<!-- qew:claim {not json} -->")),
                {"stdout": "ok\n"},
                {"stdout": "ok\n"},
                response(issue(labels=RELEASED)),
            ],
            ["release", "15", "--reason", "cleanup", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning: ignoring malformed claim marker", result.stderr)
        self.assertIn(release_marker(None, "alexander", "cleanup"), written_comment(calls[3]).splitlines())

    def test_empty_reason_is_refused_before_any_call(self) -> None:
        result, calls = run_qew([], ["release", "15", "--reason", "  ", "--repo", "acme/widgets"])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(calls, [])

    def test_claim_still_present_after_release_fails_verification(self) -> None:
        result, _ = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(),
                {"stdout": "ok\n"},
                {"stdout": "ok\n"},
                response(issue(assignees=["alexander"], labels=CLAIMED)),
            ],
            ["release", "15", "--reason", "lane died", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("verification failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
