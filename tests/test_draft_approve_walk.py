"""Command walks for releasing drafts into approved work.

Approving removes qew:draft and adds qew:approved, so the work may be started.
Adversary: approving something that is not a draft, a tree approval that skips
or double-writes a descendant, writes before every target is validated, a
partial failure that leaves the user guessing, and an approval with no human
quote behind it.
"""

import json
import unittest

from tests.command_harness import REQUESTER_QUOTE, run_qew

REPO = ["--repo", "acme/widgets"]
DRAFT = ["P2", "task", "qew:draft"]


def node(number: int, labels=DRAFT, children=(), state="OPEN", more_children=False) -> dict:
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
                            "subIssues": {
                                "nodes": [{"number": child, "state": child_state} for child, child_state in children],
                                "pageInfo": {"hasNextPage": more_children},
                            },
                        }
                    }
                }
            }
        )
    }


def target(number: int, labels) -> dict:
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
APPROVED = ["P2", "task", "qew:approved"]


class DraftApproveWalk(unittest.TestCase):
    def test_golden_walk_approves_one_draft_and_records_the_quote(self) -> None:
        result, calls = run_qew(
            [node(7), EDITED, COMMENTED, target(7, APPROVED)],
            ["draft", "approve", "7", "--quote", REQUESTER_QUOTE, *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "approved #7\n")
        edit, comment = calls[1], calls[2]
        self.assertEqual(edit[edit.index("--add-label") + 1], "qew:approved")
        self.assertEqual(edit[edit.index("--remove-label") + 1], "qew:draft")
        self.assertIn(REQUESTER_QUOTE, comment[comment.index("--body") + 1])

    def test_tree_approves_open_draft_descendants_and_skips_the_rest(self) -> None:
        epic = ["P1", "epic", "qew:draft"]
        result, calls = run_qew(
            [
                node(3, epic, children=[(7, "OPEN"), (8, "OPEN"), (9, "CLOSED")]),
                node(7, children=[(10, "OPEN")]),
                node(8, APPROVED),
                node(10),
                EDITED, COMMENTED, target(3, ["P1", "epic", "qew:approved"]),
                EDITED, COMMENTED, target(7, APPROVED),
                EDITED, COMMENTED, target(10, APPROVED),
            ],
            ["draft", "approve", "3", "--tree", "--quote", REQUESTER_QUOTE, *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["approved"], [3, 7, 10])
        edited = [call[2] for call in calls if call[:2] == ["issue", "edit"]]
        self.assertEqual(edited, ["3", "7", "10"])

    def test_a_lone_target_that_is_not_a_draft_is_refused_before_any_write(self) -> None:
        result, calls = run_qew(
            [node(8, APPROVED)], ["draft", "approve", "8", "--quote", REQUESTER_QUOTE, *REPO]
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("#8 is not a draft", result.stderr)
        self.assertNotIn("edit", [call[1] for call in calls])

    def test_a_target_already_approved_is_skipped_when_others_remain(self) -> None:
        result, calls = run_qew(
            [node(7), node(8, APPROVED), EDITED, COMMENTED, target(7, APPROVED)],
            ["draft", "approve", "7", "8", "--quote", REQUESTER_QUOTE, *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual((output["approved"], output["skipped"]), ([7], [8]))

    def test_rerunning_a_tree_after_a_partial_failure_finishes_the_rest(self) -> None:
        # Adversary: the message says rerun the same command, but the root is
        # now approved, so the rerun refused it and left the children drafts.
        result, calls = run_qew(
            [
                node(3, ["P1", "epic", "qew:approved"], children=[(7, "OPEN")]),
                node(7),
                EDITED, COMMENTED, target(7, APPROVED),
            ],
            ["draft", "approve", "3", "--tree", "--quote", REQUESTER_QUOTE, *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["approved"], [7])

    def test_a_closed_draft_cannot_be_approved(self) -> None:
        result, calls = run_qew(
            [node(7, state="CLOSED")], ["draft", "approve", "7", "--quote", REQUESTER_QUOTE, *REPO]
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_a_human_quote_is_required_before_any_call(self) -> None:
        for extra in ([], ["--quote", "ok"]):
            with self.subTest(extra=extra):
                result, calls = run_qew([], ["draft", "approve", "7", *extra, *REPO])

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])

    def test_a_drafts_approval_gate_is_kept(self) -> None:
        labels = ["P2", "task", "qew:draft", "needs:approval"]
        result, calls = run_qew(
            [node(7, labels), EDITED, COMMENTED, target(7, ["P2", "task", "qew:approved", "needs:approval"])],
            ["draft", "approve", "7", "--quote", REQUESTER_QUOTE, *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("needs:approval", " ".join(calls[1]))

    def test_truncated_children_are_indeterminate_before_any_write(self) -> None:
        result, calls = run_qew(
            [node(3, ["P1", "epic", "qew:draft"], children=[(7, "OPEN")], more_children=True)],
            ["draft", "approve", "3", "--tree", "--quote", REQUESTER_QUOTE, *REPO],
        )

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_a_failure_after_the_first_write_lists_what_is_left(self) -> None:
        result, calls = run_qew(
            [
                node(3, ["P1", "epic", "qew:draft"], children=[(7, "OPEN")]),
                node(7),
                EDITED, COMMENTED, target(3, ["P1", "epic", "qew:approved"]),
                {"stderr": "boom", "exit_code": 1},
            ],
            ["draft", "approve", "3", "--tree", "--quote", REQUESTER_QUOTE, *REPO],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("approved: #3", result.stderr)
        self.assertIn("not approved: #7", result.stderr)
        self.assertIn("rerun", result.stderr)

    def test_a_failed_comment_is_reported_with_the_command_that_records_it(self) -> None:
        # Adversary: the label moved but the quote was never recorded, and a
        # rerun skips the issue, so nothing ever shows who approved it.
        result, calls = run_qew(
            [node(7), EDITED, {"stderr": "boom", "exit_code": 1}, target(7, APPROVED)],
            ["draft", "approve", "7", "--quote", REQUESTER_QUOTE, *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["unrecorded"], [7])
        self.assertIn("qew comment 7", result.stderr)
        self.assertIn("not recorded", result.stderr)

    def test_skipped_issues_are_named_in_the_human_output(self) -> None:
        result, _ = run_qew(
            [node(7), node(8, APPROVED), EDITED, COMMENTED, target(7, APPROVED)],
            ["draft", "approve", "7", "8", "--quote", REQUESTER_QUOTE, *REPO],
        )

        self.assertIn("approved #7", result.stdout)
        self.assertIn("skipped #8", result.stdout)

    def test_a_partial_failure_names_the_board_refresh(self) -> None:
        result, _ = run_qew(
            [
                node(3, ["P1", "epic", "qew:draft"], children=[(7, "OPEN")]),
                node(7),
                EDITED, COMMENTED, target(3, ["P1", "epic", "qew:approved"]),
                {"stderr": "boom", "exit_code": 1},
            ],
            ["draft", "approve", "3", "--tree", "--quote", REQUESTER_QUOTE, *REPO],
        )

        self.assertIn("qew project ensure", result.stderr)

    def test_an_edit_that_did_not_stick_is_reported_not_assumed(self) -> None:
        result, _ = run_qew(
            [node(7), EDITED, COMMENTED, target(7, DRAFT)],
            ["draft", "approve", "7", "--quote", REQUESTER_QUOTE, *REPO],
        )

        self.assertEqual(result.returncode, 3, result.stderr)


if __name__ == "__main__":
    unittest.main()
