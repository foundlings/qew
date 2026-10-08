"""Command walks for lane-named claims.

Several agent lanes often share one GitHub login, so an assignment alone cannot
say which lane holds an issue. These walks prevent a second lane from silently
"claiming" work another lane already holds under the same login.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_lifecycle_walk import issue, response
from tests.test_read_suite_walk import connection, show_issue
from tests.test_read_suite_walk import response as show_response


CLAIMED = ["qew:approved", "task", "P2", "in-progress"]


def claim_marker(lane: str, workspace, kind=None) -> str:
    value = {"lane": lane, "workspace": workspace}
    if kind is not None:
        value["kind"] = kind
    return "<!-- qew:claim " + json.dumps(value) + " -->"


def release_marker(lane, by: str, reason: str) -> str:
    return "<!-- qew:release " + json.dumps({"lane": lane, "by": by, "reason": reason}) + " -->"


def comments(*nodes, has_previous: bool = False, cursor=None) -> dict:
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "repository": {
                        "issue": {
                            "comments": {
                                "nodes": [
                                    {"author": {"login": author}, "body": body}
                                    for author, body in nodes
                                ],
                                "pageInfo": {
                                    "hasPreviousPage": has_previous,
                                    "startCursor": cursor,
                                },
                            }
                        }
                    }
                }
            }
        )
    }


def claimed_show_issue() -> dict:
    value = show_issue(12, approved=True)
    value["labels"] = connection([{"name": name} for name in ("P2", "task", "qew:approved", "in-progress")])
    return value


class LaneClaimWalk(unittest.TestCase):
    def test_start_with_lane_records_and_verifies_the_lane_claim(self) -> None:
        marker = claim_marker("DBC241", "/work/dbc241", "compute")
        result, calls = run_qew(
            [
                response(issue()),
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                {"stdout": "https://example.test/acme/widgets/issues/15#comment\n"},
                comments(("alexander", "Claimed by lane `DBC241`.\n\n" + marker)),
            ],
            ["start", "15", "--lane", "DBC241", "--workspace", "/work/dbc241", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "started #15 for lane DBC241\n")
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[1][:3], ["issue", "edit", "15"])
        self.assertEqual(calls[3][:3], ["issue", "comment", "15"])
        body = calls[3][calls[3].index("--body") + 1]
        self.assertIn(marker, body.splitlines())

    def test_start_lane_json_names_lane_and_workspace(self) -> None:
        result, _ = run_qew(
            [
                response(issue()),
                {"stdout": "ok\n"},
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                {"stdout": "ok\n"},
                comments(("alexander", claim_marker("DBC241", None, "compute"))),
            ],
            ["start", "15", "--lane", "DBC241", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["lane"], "DBC241")
        self.assertIsNone(output["workspace"])
        self.assertEqual(output["state"], "started")

    def test_second_lane_is_refused_and_named_without_writing(self) -> None:
        result, calls = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(("alexander", claim_marker("DBC241", "/work/dbc241"))),
            ],
            ["start", "15", "--lane", "DBC242", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("lane DBC241", result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertFalse(any(call[:2] in (["issue", "edit"], ["issue", "comment"]) for call in calls))

    def test_same_lane_again_keeps_already_claimed_by_you(self) -> None:
        result, calls = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(("alexander", claim_marker("DBC241", None))),
            ],
            ["start", "15", "--lane", "DBC241", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertEqual(len(calls), 2)

    def test_marker_from_a_non_assignee_is_ignored(self) -> None:
        # Anyone may comment; a forged marker must not move the claim.
        result, _ = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(
                    ("alexander", claim_marker("DBC241", None)),
                    ("mallory", claim_marker("EVIL1", None)),
                ),
            ],
            ["start", "15", "--lane", "DBC241", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 5, result.stderr)

    def test_lane_less_claim_by_you_refuses_a_lane_start(self) -> None:
        result, calls = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(
                    ("alexander", claim_marker("DBC241", None)),
                    ("alexander", release_marker("DBC241", "alexander", "lane died")),
                ),
            ],
            ["start", "15", "--lane", "DBC242", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("without a lane", result.stderr)
        self.assertEqual(len(calls), 2)

    def test_race_the_oldest_marker_wins_for_both_racers(self) -> None:
        # Lane A verifies before lane B's marker exists; lane B verifies after
        # both markers exist. Both must agree on one winner: the oldest marker.
        a_result, _ = run_qew(
            [
                response(issue()),
                {"stdout": "ok\n"},
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                {"stdout": "ok\n"},
                comments(("alexander", claim_marker("DBC241", None))),
            ],
            ["start", "15", "--lane", "DBC241", "--repo", "acme/widgets"],
        )
        b_result, _ = run_qew(
            [
                response(issue()),
                {"stdout": "ok\n"},
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                {"stdout": "ok\n"},
                comments(
                    ("alexander", claim_marker("DBC241", None)),
                    ("alexander", claim_marker("DBC242", None)),
                ),
            ],
            ["start", "15", "--lane", "DBC242", "--repo", "acme/widgets"],
        )

        self.assertEqual(a_result.returncode, 0, a_result.stderr)
        self.assertEqual(b_result.returncode, 3, b_result.stderr)
        self.assertIn("lane DBC241", b_result.stderr)

    def test_a_later_marker_after_ours_does_not_take_the_claim(self) -> None:
        result, _ = run_qew(
            [
                response(issue()),
                {"stdout": "ok\n"},
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                {"stdout": "ok\n"},
                comments(
                    ("alexander", claim_marker("DBC241", None)),
                    ("alexander", claim_marker("DBC242", None)),
                ),
            ],
            ["start", "15", "--lane", "DBC241", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_marker_after_a_release_starts_a_new_claim(self) -> None:
        payload = show_response({"data": {"repository": {"issue": claimed_show_issue()}}})
        result, _ = run_qew(
            [
                payload,
                comments(
                    ("worker", claim_marker("OLD1", None)),
                    ("worker", release_marker("OLD1", "worker", "lane died")),
                    ("worker", claim_marker("NEW1", "/work/new1")),
                    ("worker", claim_marker("LATE1", None)),
                ),
            ],
            ["show", "12", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["claim"]["lane"], "NEW1")

    def test_start_without_lane_on_a_lane_claim_names_the_lane(self) -> None:
        # An agent loop reads exit 5 as "I hold it"; a sibling lane must not.
        result, calls = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(("alexander", claim_marker("DBC241", None))),
            ],
            ["start", "15", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("lane DBC241", result.stderr)
        self.assertFalse(any(call[:2] in (["issue", "edit"], ["issue", "comment"]) for call in calls))

    def test_start_without_lane_on_your_lane_less_claim_keeps_exit_5(self) -> None:
        result, _ = run_qew(
            [
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                comments(),
            ],
            ["start", "15", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 5, result.stderr)

    def test_malformed_marker_is_ignored_with_a_warning(self) -> None:
        payload = show_response({"data": {"repository": {"issue": claimed_show_issue()}}})
        result, _ = run_qew(
            [
                payload,
                comments(
                    ("worker", "<!-- qew:claim {not json} -->"),
                    ("worker", "<!-- qew:claim " + json.dumps({"lane": "a--b", "workspace": None}) + " -->"),
                    ("worker", claim_marker("DBC241", None)),
                ),
            ],
            ["show", "12", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["claim"]["lane"], "DBC241")
        self.assertIn("warning: ignoring malformed claim marker", result.stderr)

    def test_start_kind_token_is_recorded_in_the_marker_and_shown(self) -> None:
        marker = claim_marker("DBC241", None, "token")
        result, calls = run_qew(
            [
                response(issue()),
                {"stdout": "ok\n"},
                response(issue(assignees=["alexander"], labels=CLAIMED)),
                {"stdout": "ok\n"},
                comments(("alexander", marker)),
            ],
            ["start", "15", "--lane", "DBC241", "--kind", "token", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["kind"], "token")
        body = calls[3][calls[3].index("--body") + 1]
        self.assertIn(marker, body.splitlines())

    def test_marker_without_kind_reads_as_compute(self) -> None:
        payload = show_response({"data": {"repository": {"issue": claimed_show_issue()}}})
        result, _ = run_qew(
            [payload, comments(("worker", claim_marker("DBC241", None)))],
            ["show", "12", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["claim"]["kind"], "compute")

    def test_invalid_lane_or_workspace_is_refused_before_any_call(self) -> None:
        for arguments in (
            ["--lane", "a--b"],
            ["--lane", "-x"],
            ["--lane", "DBC241", "--workspace", "relative/path"],
            ["--lane", "DBC241", "--workspace", "/tmp/x-->y"],
            ["--workspace", "/work/a"],
            ["--kind", "token"],
            ["--lane", "DBC241", "--kind", "gpu"],
        ):
            with self.subTest(arguments=arguments):
                result, calls = run_qew([], ["start", "15", *arguments, "--repo", "acme/widgets"])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])

    def test_show_exposes_the_current_lane_claim(self) -> None:
        value = show_issue(12, approved=True)
        value["labels"] = connection([{"name": name} for name in ("P2", "task", "qew:approved", "in-progress")])
        payload = show_response({"data": {"repository": {"issue": value}}})
        result, calls = run_qew(
            [payload, comments(("worker", claim_marker("DBC241", "/work/dbc241")))],
            ["show", "12", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["claim"],
            {"by": "worker", "kind": "compute", "lane": "DBC241", "workspace": "/work/dbc241"},
        )
        self.assertEqual(len(calls), 2)
        human, _ = run_qew(
            [payload, comments(("worker", claim_marker("DBC241", "/work/dbc241")))],
            ["show", "12", "--repo", "acme/widgets"],
        )
        self.assertIn("claim: lane DBC241 by worker (workspace /work/dbc241)", human.stdout)

    def test_show_unclaimed_issue_has_null_claim_without_extra_read(self) -> None:
        payload = show_response({"data": {"repository": {"issue": show_issue(12, approved=True)}}})
        result, calls = run_qew([payload], ["show", "12", "--repo", "acme/widgets", "--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)["claim"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
