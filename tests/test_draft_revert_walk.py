"""Command walks for returning approved work to draft.

Reverting holds live work back until it is approved again. Adversary: pulling
work out from under someone who claimed it, half a tree reverted because one
descendant was claimed, a lossy round trip that drops needs:approval, a revert
nobody can explain later, and an edit that did not stick reported as done.
"""

import json
import unittest

from tests.command_harness import REQUESTER_QUOTE, run_qew

REPO = ["--repo", "acme/widgets"]
LIVE = ["P2", "task", "qew:approved"]
DRAFT = ["P2", "task", "qew:draft"]
ARGS = ["--reason", "scope changed", "--quote", REQUESTER_QUOTE]


def node(number, labels=LIVE, children=(), state="OPEN", assignees=(), pulls=()):
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "repository": {
                        "issue": {
                            "number": number,
                            "state": state,
                            "labels": {
                                "nodes": [{"name": name} for name in labels],
                                "pageInfo": {"hasNextPage": False},
                            },
                            "assignees": {
                                "totalCount": len(assignees),
                                "nodes": [{"login": login} for login in assignees],
                            },
                            "closedByPullRequestsReferences": {
                                "nodes": [{"state": value} for value in pulls],
                                "pageInfo": {"hasNextPage": False},
                            },
                            "subIssues": {
                                "nodes": [{"number": child, "state": child_state} for child, child_state in children],
                                "pageInfo": {"hasNextPage": False},
                            },
                        }
                    }
                }
            }
        )
    }


def target(number, labels) -> dict:
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "repository": {
                        "issue": {
                            "number": number,
                            "title": "t",
                            "body": "b",
                            "state": "OPEN",
                            "parent": None,
                            "labels": {
                                "nodes": [{"name": name} for name in labels],
                                "pageInfo": {"hasNextPage": False},
                            },
                        }
                    }
                }
            }
        )
    }


EDITED = {"stdout": "edited\n"}
COMMENTED = {"stdout": "commented\n"}


def writes(calls):
    return [call for call in calls if call[:2] in (["issue", "edit"], ["issue", "comment"])]


class DraftRevertWalk(unittest.TestCase):
    def test_golden_walk_returns_one_issue_to_draft_and_records_why(self) -> None:
        result, calls = run_qew(
            [node(7), EDITED, COMMENTED, target(7, DRAFT)],
            ["draft", "revert", "7", *ARGS, *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "reverted #7 to draft\n")
        edit, comment = calls[1], calls[2]
        self.assertEqual(edit[edit.index("--add-label") + 1], "qew:draft")
        self.assertEqual(edit[edit.index("--remove-label") + 1], "qew:approved")
        body = comment[comment.index("--body") + 1]
        self.assertIn("scope changed", body)
        self.assertIn(REQUESTER_QUOTE, body)

    def test_an_approval_gate_survives_the_round_trip(self) -> None:
        gated = [*LIVE, "needs:approval"]
        result, calls = run_qew(
            [node(7, gated), EDITED, COMMENTED, target(7, ["P2", "task", "qew:draft", "needs:approval"])],
            ["draft", "revert", "7", *ARGS, *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("needs:approval", " ".join(calls[1]))

    def test_tree_reverts_open_live_descendants_and_skips_drafts_and_closed(self) -> None:
        epic = ["P1", "epic", "qew:approved"]
        result, calls = run_qew(
            [
                node(3, epic, children=[(7, "OPEN"), (8, "OPEN"), (9, "CLOSED")]),
                node(7),
                node(8, DRAFT),
                EDITED, COMMENTED, target(3, ["P1", "epic", "qew:draft"]),
                EDITED, COMMENTED, target(7, DRAFT),
            ],
            ["draft", "revert", "3", "--tree", *ARGS, *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual((output["reverted"], output["skipped"]), ([3, 7], [8]))

    def test_claimed_work_is_refused_and_names_who_holds_it(self) -> None:
        for name, value in (
            ("assignee", node(7, assignees=["mallory"])),
            ("in-progress", node(7, [*LIVE, "in-progress"])),
        ):
            with self.subTest(name):
                result, calls = run_qew([value], ["draft", "revert", "7", *ARGS, *REPO])

                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn("qew release", result.stderr)
                self.assertEqual(writes(calls), [])

    def test_one_claimed_descendant_refuses_the_whole_tree_before_any_write(self) -> None:
        result, calls = run_qew(
            [
                node(3, ["P1", "epic", "qew:approved"], children=[(7, "OPEN"), (8, "OPEN")]),
                node(7),
                node(8, assignees=["mallory"]),
            ],
            ["draft", "revert", "3", "--tree", *ARGS, *REPO],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("#8", result.stderr)
        self.assertEqual(writes(calls), [])

    def test_an_epic_with_a_derived_claim_is_refused(self) -> None:
        result, calls = run_qew(
            [node(3, ["P1", "epic", "qew:approved", "in-progress"], assignees=["alexander"])],
            ["draft", "revert", "3", *ARGS, *REPO],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(writes(calls), [])

    def test_work_in_review_is_refused(self) -> None:
        result, calls = run_qew([node(7, pulls=["OPEN"])], ["draft", "revert", "7", *ARGS, *REPO])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("pull request", result.stderr)
        self.assertEqual(writes(calls), [])

    def test_closed_work_is_refused(self) -> None:
        result, calls = run_qew([node(7, state="CLOSED")], ["draft", "revert", "7", *ARGS, *REPO])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(writes(calls), [])

    def test_a_lone_draft_is_refused_and_a_mixed_list_skips_it(self) -> None:
        lone, calls = run_qew([node(7, DRAFT)], ["draft", "revert", "7", *ARGS, *REPO])
        mixed, _ = run_qew(
            [node(7), node(8, DRAFT), EDITED, COMMENTED, target(7, DRAFT)],
            ["draft", "revert", "7", "8", *ARGS, *REPO, "--json"],
        )

        self.assertEqual(lone.returncode, 2, lone.stderr)
        self.assertIn("nothing to revert", lone.stderr)
        self.assertEqual(writes(calls), [])
        self.assertEqual(mixed.returncode, 0, mixed.stderr)
        self.assertEqual(json.loads(mixed.stdout)["skipped"], [8])

    def test_a_reason_and_a_human_quote_are_required_before_any_call(self) -> None:
        for extra in (["--quote", REQUESTER_QUOTE], ["--reason", "why"], ["--reason", " ", "--quote", REQUESTER_QUOTE]):
            with self.subTest(extra=extra):
                result, calls = run_qew([], ["draft", "revert", "7", *extra, *REPO])

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])

    def test_a_failure_after_the_first_write_lists_what_is_left(self) -> None:
        result, _ = run_qew(
            [
                node(3, ["P1", "epic", "qew:approved"], children=[(7, "OPEN")]),
                node(7),
                EDITED, COMMENTED, target(3, ["P1", "epic", "qew:draft"]),
                {"stderr": "boom", "exit_code": 1},
            ],
            ["draft", "revert", "3", "--tree", *ARGS, *REPO],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("reverted: #3", result.stderr)
        self.assertIn("not reverted: #7", result.stderr)

    def test_an_edit_that_did_not_stick_is_reported(self) -> None:
        result, _ = run_qew(
            [node(7), EDITED, COMMENTED, target(7, LIVE)], ["draft", "revert", "7", *ARGS, *REPO]
        )

        self.assertEqual(result.returncode, 3, result.stderr)


if __name__ == "__main__":
    unittest.main()
