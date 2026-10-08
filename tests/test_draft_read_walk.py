"""Command walks for reading and discussing drafts.

Adversary: a draft that no read can find (every routine read matches on
qew:approved), a list that silently shows only the first page of drafts, a
comment that quietly drops its body, and `show` that cannot tell a draft apart.
"""

import json
import os
import tempfile
import unittest

from tests.command_harness import run_qew
from tests.test_read_suite_walk import issue, issue_page, pages, show_issue, response

REPO = ["--repo", "acme/widgets"]


def draft(number: int, title: str, labels=("P2", "task", "qew:draft"), parent=None) -> dict:
    value = issue(number, title, labels=list(labels))
    value["parent"] = None if parent is None else {"number": parent}
    return value


class DraftListWalk(unittest.TestCase):
    def test_draft_list_shows_open_drafts_with_type_and_parent(self) -> None:
        result, calls = run_qew(
            [
                pages(
                    issue_page(
                        draft(7, "Idea", parent=3),
                        draft(3, "Big idea", labels=("P1", "epic", "qew:draft")),
                    )
                )
            ],
            ["draft", "list", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(
            [(item["number"], item["type"], item["parent"]) for item in output["items"]],
            [(3, "epic", None), (7, "task", 3)],
        )
        self.assertIn("qew:draft", calls[0][calls[0].index("-f") + 1] if "-f" in calls[0] else " ".join(calls[0]))

    def test_draft_list_ignores_a_closed_or_non_draft_issue(self) -> None:
        closed = draft(8, "Gone")
        closed["state"] = "CLOSED"
        result, _ = run_qew(
            [pages(issue_page(closed, draft(9, "Plain", labels=("P2", "task", "qew:approved"))))],
            ["draft", "list", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["items"], [])

    def test_draft_list_only_under_a_parent(self) -> None:
        result, _ = run_qew(
            [pages(issue_page(draft(7, "Idea", parent=3), draft(8, "Other", parent=4)))],
            ["draft", "list", "--parent", "3", *REPO, "--json"],
        )

        self.assertEqual([item["number"] for item in json.loads(result.stdout)["items"]], [7])

    def test_draft_list_in_overlay_mode_asks_only_for_managed_drafts(self) -> None:
        result, calls = run_qew(
            [pages(issue_page())], ["draft", "list", *REPO, "--json"], repository_mode="overlay"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"qew:draft", "qew:managed"', " ".join(calls[0]))


class ShowDraftWalk(unittest.TestCase):
    def test_show_reports_whether_the_issue_is_a_draft(self) -> None:
        drafted = show_issue(5, approved=False)
        drafted["labels"]["nodes"].append({"name": "qew:draft"})
        plain = show_issue(5, approved=True)
        results = []
        for value in (drafted, plain):
            result, _ = run_qew(
                [{"stdout": json.dumps({"data": {"repository": {"issue": value}}})}],
                ["show", "5", *REPO, "--json"],
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            results.append(json.loads(result.stdout)["draft"])

        self.assertEqual(results, [True, False])


class CommentWalk(unittest.TestCase):
    def test_comment_adds_the_body_to_the_issue(self) -> None:
        result, calls = run_qew(
            [{"stdout": "https://example.test/acme/widgets/issues/15#issuecomment-1\n"}],
            ["comment", "15", "--body", "Looks right.", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "commented on #15\n")
        self.assertEqual(calls[0][:3], ["issue", "comment", "15"])
        self.assertEqual(calls[0][calls[0].index("--body") + 1], "Looks right.")

    def test_comment_reads_a_body_file(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write("From a file.\n")
        try:
            result, calls = run_qew(
                [{"stdout": "https://example.test/x\n"}],
                ["comment", "15", "--body-file", handle.name, *REPO],
            )
        finally:
            os.unlink(handle.name)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0][calls[0].index("--body") + 1], "From a file.\n")

    def test_comment_refuses_a_missing_empty_or_double_body_before_any_call(self) -> None:
        for extra in ([], ["--body", "  "], ["--body", "x", "--body-file", "y"]):
            with self.subTest(extra=extra):
                result, calls = run_qew([], ["comment", "15", *extra, *REPO])

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
