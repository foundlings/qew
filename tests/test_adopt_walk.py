"""Command walks for adopting Qew-created issues that lack qew:managed.

Issues created by `qew apply` in overlay mode before it marked them managed carry
`qew:approved` and Qew's provenance marker but not `qew:managed`, so overlay mode
ignores them, and `qew:` labels are reserved so nothing could add the label.

Adversary: adopting pre-Qew issues, adopting by a marker an unapproved issue
merely quotes, and reporting success while the label did not persist.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_init_walk import response

REPO = ["--repo", "acme/widgets"]
PAGE = {"hasNextPage": False, "endCursor": None}
DIGEST = "ab" * 32
MARKER = f"<!-- qew:apply {DIGEST}:0123456789abcdef -->"
APPROVED = ("task", "P2", "qew:approved")


def node(number, labels, author="danieledward-kim", body=None, state="OPEN") -> dict:
    return {
        "number": number,
        "state": state,
        "author": None if author is None else {"login": author},
        "body": f"### Goal\n\nDo it.\n\n{MARKER}\n" if body is None else body,
        "labels": {"nodes": [{"name": name} for name in labels], "pageInfo": PAGE},
    }


def issues(*nodes) -> dict:
    page = {"data": {"repository": {"issues": {"nodes": list(nodes), "pageInfo": PAGE}}}}
    return response(json.dumps([page]))


def edits(calls):
    return [call for call in calls if call[:2] == ["issue", "edit"]]


BEFORE = [
    node(1, APPROVED),
    node(2, (*APPROVED, "qew:managed")),
    node(3, APPROVED, body="Written long before Qew existed."),
    node(4, ("task", "P2"), body=f"quoted {MARKER}"),
    node(5, APPROVED, author="jan-schering"),
    node(6, APPROVED, state="CLOSED"),
]
AFTER = [
    node(1, (*APPROVED, "qew:managed")),
    node(2, (*APPROVED, "qew:managed")),
    node(3, APPROVED, body="Written long before Qew existed."),
    node(4, ("task", "P2"), body=f"quoted {MARKER}"),
    node(5, APPROVED, author="jan-schering"),
    node(6, (*APPROVED, "qew:managed"), state="CLOSED"),
]


class AdoptWalk(unittest.TestCase):
    def test_adopts_only_approved_issues_with_a_provenance_marker(self) -> None:
        result, calls = run_qew(
            [issues(*BEFORE), response("edited"), response("edited"), issues(*AFTER)],
            ["adopt", "--author", "danieledward-kim", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["adopted"], [1, 6])
        self.assertEqual([call[2] for call in edits(calls)], ["1", "6"])
        self.assertTrue(all(call[call.index("--add-label") + 1] == "qew:managed" for call in edits(calls)))

    def test_without_author_every_opener_is_in_scope(self) -> None:
        after = [*AFTER[:4], node(5, (*APPROVED, "qew:managed"), author="jan-schering"), AFTER[5]]
        result, calls = run_qew(
            [issues(*BEFORE), response("edited"), response("edited"), response("edited"), issues(*after)],
            ["adopt", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["adopted"], [1, 5, 6])

    def test_a_marker_must_stand_alone_on_a_line(self) -> None:
        # Adversary: untrusted body text that merely quotes the marker.
        result, calls = run_qew(
            [issues(node(7, APPROVED, body=f"see {MARKER} for details"))], ["adopt", *REPO]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing to adopt", result.stdout)
        self.assertEqual(edits(calls), [])

    def test_a_malformed_marker_is_not_a_marker(self) -> None:
        for bad in (
            "<!-- qew:apply abc:0123456789abcdef -->",
            f"<!-- qew:apply {DIGEST}:0123 -->",
            f"<!-- qew:apply {DIGEST.upper()}:0123456789abcdef -->",
        ):
            with self.subTest(marker=bad):
                result, calls = run_qew([issues(node(7, APPROVED, body=bad))], ["adopt", *REPO])

                self.assertIn("nothing to adopt", result.stdout)
                self.assertEqual(edits(calls), [])

    def test_dry_run_writes_nothing(self) -> None:
        result, calls = run_qew([issues(*BEFORE)], ["adopt", "--dry-run", *REPO])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would adopt 3 issues", result.stdout)
        self.assertEqual(edits(calls), [])

    def test_an_invalid_author_is_refused_before_any_call(self) -> None:
        result, calls = run_qew([], ["adopt", "--author", "bad login", *REPO])

        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])

    def test_labels_that_did_not_persist_are_indeterminate(self) -> None:
        result, _ = run_qew(
            [issues(node(1, APPROVED)), response("edited"), issues(node(1, APPROVED))],
            ["adopt", *REPO],
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("did not persist", result.stderr)


if __name__ == "__main__":
    unittest.main()
