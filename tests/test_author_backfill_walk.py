"""Command walks for backfilling author labels on existing issues.

`author:LOGIN` is added when Qew opens an issue (Qew 1.8.0), so earlier issues
have none, and the label is reserved so it cannot be set by hand. Backfill takes
the login from the opener GitHub records, never from anything typed in.

Adversary: attributing work to the wrong person, retagging issues that already
have an author, and reporting success while labels did not persist.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_init_walk import label_page, pages, response

REPO = ["--repo", "acme/widgets"]
PAGE = {"hasNextPage": False, "endCursor": None}
MANAGED = ("task", "P2", "qew:approved", "qew:managed")


def node(number, labels, author, state="OPEN") -> dict:
    return {
        "number": number,
        "state": state,
        "author": None if author is None else {"login": author},
        "labels": {"nodes": [{"name": name} for name in labels], "pageInfo": PAGE},
    }


def issues(*nodes) -> dict:
    page = {"data": {"repository": {"issues": {"nodes": list(nodes), "pageInfo": PAGE}}}}
    return response(json.dumps([page]))


def labels(*names) -> dict:
    return pages(label_page([(name, "EDEDED", "Opened through Qew") for name in names]))


BEFORE = [
    node(1, MANAGED, "jan-schering"),
    node(2, (*MANAGED, "author:alexander-stuermer"), "alexander-stuermer"),
    node(3, MANAGED, None),
    node(4, MANAGED, "danieledward-kim"),
    node(5, MANAGED, "jan-schering", state="CLOSED"),
]
AFTER = [
    node(1, (*MANAGED, "author:jan-schering"), "jan-schering"),
    node(2, (*MANAGED, "author:alexander-stuermer"), "alexander-stuermer"),
    node(3, MANAGED, None),
    node(4, (*MANAGED, "author:danieledward-kim"), "danieledward-kim"),
    node(5, (*MANAGED, "author:jan-schering"), "jan-schering", state="CLOSED"),
]


def edits(calls):
    return [call for call in calls if call[:2] == ["issue", "edit"]]


class AuthorBackfillWalk(unittest.TestCase):
    def test_labels_every_issue_without_an_author_from_its_recorded_opener(self) -> None:
        result, calls = run_qew(
            [
                issues(*BEFORE),
                labels("author:jan-schering", "author:alexander-stuermer"),
                response(""),
                response("edited"),
                response("edited"),
                response("edited"),
                issues(*AFTER),
            ],
            ["author", "backfill", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["labelled"], [1, 4, 5])
        self.assertEqual(output["created_labels"], ["author:danieledward-kim"])
        self.assertEqual(
            output["skipped"], [{"issue": 3, "reason": "the opener's account no longer exists"}]
        )
        created = [call for call in calls if call[:2] == ["label", "create"]]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][2], "author:danieledward-kim")
        self.assertNotIn("--force", created[0])
        applied = {call[2]: call[call.index("--add-label") + 1] for call in edits(calls)}
        self.assertEqual(
            applied,
            {"1": "author:jan-schering", "4": "author:danieledward-kim", "5": "author:jan-schering"},
        )

    def test_dry_run_reports_the_plan_and_writes_nothing(self) -> None:
        result, calls = run_qew(
            [issues(*BEFORE), labels("author:jan-schering")],
            ["author", "backfill", "--dry-run", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would label 3 issues", result.stdout)
        self.assertEqual(edits(calls), [])
        self.assertFalse(any(call[:2] == ["label", "create"] for call in calls))

    def test_nothing_to_backfill_is_a_quiet_read_only_run(self) -> None:
        result, calls = run_qew([issues(*AFTER[1:2])], ["author", "backfill", *REPO])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing to backfill", result.stdout)
        self.assertEqual(len(calls), 1)

    def test_a_login_that_cannot_be_a_label_is_indeterminate_before_any_write(self) -> None:
        result, calls = run_qew(
            [issues(node(7, MANAGED, "bad login,x")), labels()],
            ["author", "backfill", *REPO],
        )

        self.assertEqual(result.returncode, 4)
        self.assertEqual(edits(calls), [])
        self.assertFalse(any(call[:2] == ["label", "create"] for call in calls))

    def test_in_overlay_mode_unmanaged_issues_are_never_touched(self) -> None:
        result, calls = run_qew(
            [issues(node(8, ("task",), "jan-schering"), node(9, MANAGED, "jan-schering")),
             labels("author:jan-schering"), response("edited"),
             issues(node(8, ("task",), "jan-schering"), node(9, (*MANAGED, "author:jan-schering"), "jan-schering"))],
            ["author", "backfill", *REPO, "--json"],
            repository_mode="overlay",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["labelled"], [9])
        self.assertEqual([call[2] for call in edits(calls)], ["9"])

    def test_labels_that_did_not_persist_are_indeterminate(self) -> None:
        result, _ = run_qew(
            [
                issues(node(1, MANAGED, "jan-schering")),
                labels("author:jan-schering"),
                response("edited"),
                issues(node(1, MANAGED, "jan-schering")),
            ],
            ["author", "backfill", *REPO],
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("did not persist", result.stderr)


if __name__ == "__main__":
    unittest.main()
