"""Command walks for creating work and changing dependency edges."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from tests.command_harness import (
    PROJECT_HINT,
    REQUESTER_QUOTE,
    author_responses,
    project_reconciliation,
    run_qew,
)


class GraphWriteWalk(unittest.TestCase):
    def test_overlay_create_marks_graph_membership(self) -> None:
        result, calls = run_qew(
            author_responses() + [{"stdout": "https://example.test/acme/widgets/issues/42\n"}],
            [
                "create",
                "--quote",
                REQUESTER_QUOTE,
                "--repo",
                "acme/widgets",
                "--type",
                "task",
                "--title",
                "Scoped work",
                "--json",
            ],
            repository_mode="overlay",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("qew:managed", calls[2])

    def test_create_labels_work_with_the_author_who_opened_it(self) -> None:
        # Behavior: every issue Qew opens carries author:<login> of the gh viewer,
        # so a board can filter label:qew:managed label:author:<login>.
        # Adversary: assignees mean "claimed" in Qew, so the author must never be
        # recorded as an assignee.
        result, calls = run_qew(
            author_responses("octocat")
            + [{"stdout": "https://example.test/acme/widgets/issues/42\n"}],
            ["create", "--quote", REQUESTER_QUOTE, "--repo", "acme/widgets", "--type", "task", "--title", "Mine"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[:2] for call in calls],
                         [["api", "graphql"], ["api", "graphql"], ["issue", "create"]])
        create = calls[-1]
        labels = [create[i + 1] for i, a in enumerate(create) if a == "--label"]
        self.assertIn("author:octocat", labels)
        self.assertNotIn("--assignee", create)

    def test_create_makes_a_missing_author_label_without_overwriting(self) -> None:
        # Adversary: gh refuses to apply a label the repository lacks, so the
        # first issue a new author opens would fail without the label.
        result, calls = run_qew(
            author_responses("octocat", label_exists=False)
            + [{"stdout": "https://example.test/acme/widgets/issues/42\n"}],
            ["create", "--quote", REQUESTER_QUOTE, "--repo", "acme/widgets", "--type", "task", "--title", "First"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        label_create = calls[2]
        self.assertEqual(label_create[:3], ["label", "create", "author:octocat"])
        self.assertEqual(label_create[label_create.index("--color") + 1], "EDEDED")
        self.assertNotIn("--force", label_create)
        self.assertEqual(calls[3][:2], ["issue", "create"])
        self.assertIn("author:octocat", calls[3])

    def test_create_refuses_a_hand_written_author_area(self) -> None:
        # Adversary: --area author:someone-else would attribute work to another
        # person; the author label is Qew state, derived from the gh viewer only.
        result, calls = run_qew(
            [],
            ["create", "--quote", REQUESTER_QUOTE, "--repo", "acme/widgets", "--type", "task", "--title", "Forged",
             "--area", "author:mallory"],
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("reserved", result.stderr)
        self.assertEqual(calls, [])

    def test_create_refuses_a_viewer_login_that_cannot_be_a_label(self) -> None:
        # Adversary: GitHub text is untrusted; a malformed login must not be
        # spliced into a label name.
        result, calls = run_qew(
            author_responses("bad login,x")
            + [{"stdout": "https://example.test/acme/widgets/issues/42\n"}],
            ["create", "--quote", REQUESTER_QUOTE, "--repo", "acme/widgets", "--type", "task", "--title", "Odd"],
        )

        self.assertEqual(result.returncode, 4)
        self.assertNotIn(["issue", "create"], [call[:2] for call in calls])

    def test_create_needs_approval_files_work_that_waits_on_a_human(self) -> None:
        # Adversary: approval work filed without the label lands in Ready and
        # gets started before a human authorizes it.
        result, calls = run_qew(
            author_responses() + [{"stdout": "https://example.test/acme/widgets/issues/42\n"}],
            ["create", "--quote", REQUESTER_QUOTE, "--repo", "acme/widgets", "--type", "bug",
             "--title", "Gate", "--needs-approval", "--area", "platform"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        labels = [calls[2][i + 1] for i, a in enumerate(calls[2]) if a == "--label"]
        self.assertIn("needs:approval", labels)
        self.assertIn("platform", labels)

    def test_create_approved_never_adds_the_label(self) -> None:
        result, calls = run_qew(
            author_responses() + [{"stdout": "https://example.test/acme/widgets/issues/42\n"}],
            ["create", "--quote", REQUESTER_QUOTE, "--approved", "--repo", "acme/widgets",
             "--type", "bug", "--title", "Plain"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("needs:approval", calls[2])

    def test_create_refuses_approved_with_needs_approval_before_any_call(self) -> None:
        # Adversary: contradictory flags silently pick one, so work a human never
        # authorized starts, or approved work stalls.
        result, calls = run_qew(
            [],
            ["create", "--quote", REQUESTER_QUOTE, "--approved", "--needs-approval",
             "--repo", "acme/widgets", "--type", "bug", "--title", "Both"],
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("contradict", result.stderr)
        self.assertEqual(calls, [])

    def test_set_needs_approval_adds_and_clears_the_label_and_verifies(self) -> None:
        added, add_calls = run_qew(
            [
                target_response(labels=["P2", "task", "qew:approved"]),
                {"stdout": "updated\n"},
                target_response(labels=["P2", "task", "qew:approved", "needs:approval"]),
            ],
            ["set", "12", "--repo", "acme/widgets", "--needs-approval"],
        )
        cleared, clear_calls = run_qew(
            [
                target_response(labels=["P2", "task", "qew:approved", "needs:approval"]),
                {"stdout": "updated\n"},
                target_response(labels=["P2", "task", "qew:approved"]),
            ],
            ["set", "12", "--repo", "acme/widgets", "--no-needs-approval"],
        )

        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(add_calls[1][add_calls[1].index("--add-label") + 1], "needs:approval")
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertEqual(
            clear_calls[1][clear_calls[1].index("--remove-label") + 1], "needs:approval"
        )

    def test_set_needs_approval_fails_when_the_label_did_not_stick(self) -> None:
        result, _ = run_qew(
            [
                target_response(labels=["P2", "task", "qew:approved"]),
                {"stdout": "updated\n"},
                target_response(labels=["P2", "task", "qew:approved"]),
            ],
            ["set", "12", "--repo", "acme/widgets", "--needs-approval"],
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("needs-approval", result.stderr)

    def test_set_refuses_contradictory_approval_flags(self) -> None:
        result, calls = run_qew(
            [], ["set", "12", "--repo", "acme/widgets", "--needs-approval", "--no-needs-approval"]
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])

    def test_json_create_also_names_the_reconciliation_command_on_stderr(self) -> None:
        # Adversary: agents call create with --json and never read the JSON hint,
        # so eleven issues stayed off the board with the hint ignored ten times.
        result, _ = run_qew(
            author_responses() + [{"stdout": "https://example.test/acme/widgets/issues/42\n"}],
            ["create", "--quote", REQUESTER_QUOTE, "--repo", "acme/widgets", "--type", "task", "--title", "Work", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, PROJECT_HINT)
        self.assertEqual(json.loads(result.stdout)["project_reconciliation"], project_reconciliation())

    def test_create_golden_walk_composes_approved_work(self) -> None:
        result, calls = run_qew(
            author_responses() + [{"stdout": "https://example.test/acme/widgets/issues/23\n"}],
            [
                "create",
                "--quote",
                REQUESTER_QUOTE,
                "--repo",
                "acme/widgets",
                "--type",
                "task",
                "--title",
                "Build adapter",
                "--priority",
                "P1",
                "--parent",
                "10",
                "--blocked-by",
                "9",
                "--discovered-from",
                "8",
                "--goal",
                "Provide one boundary.",
                "--approach",
                "Use structured calls.",
                "--done-when",
                "Golden walk passes.",
                "--json",
            ],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "number": 23,
                "project_reconciliation": project_reconciliation(),
                "schema_version": 1,
                "url": "https://example.test/acme/widgets/issues/23",
            },
        )
        self.assertEqual(calls[2][:2], ["issue", "create"])
        self.assertIn("qew:approved", calls[2])
        self.assertIn("--parent", calls[2])
        self.assertIn("--blocked-by", calls[2])
        body = calls[2][calls[2].index("--body") + 1]
        self.assertIn("### Goal\n\nProvide one boundary.", body)
        self.assertIn("- [ ] Golden walk passes.", body)
        self.assertIn("Discovered while working on #8.", body)

    def test_create_seam_walk_refuses_ambiguous_body(self) -> None:
        result, calls = run_qew(
            [],
            [
                "create",
                "--quote",
                REQUESTER_QUOTE,
                "--repo",
                "acme/widgets",
                "--type",
                "task",
                "--title",
                "Ambiguous",
                "--problem",
                "One framing",
                "--goal",
                "Another framing",
            ],
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("problem and goal", result.stderr)
        self.assertEqual(calls, [])

    def test_create_editor_golden_walk_uses_editor_body_without_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            editor = Path(temporary_directory) / "editor.py"
            marker = Path(temporary_directory) / "shell-marker"
            editor.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "Path(sys.argv[-1]).write_text('Long-form body.\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            result, calls = run_qew(
                author_responses() + [{"stdout": "https://example.test/acme/widgets/issues/24\n"}],
                [
                    "create",
                    "--quote",
                    REQUESTER_QUOTE,
                    "--repo",
                    "acme/widgets",
                    "--type",
                    "task",
                    "--title",
                    "Edited work",
                    "--edit",
                    "--json",
                ],
                {"EDITOR": f"{sys.executable} {editor} ; touch {marker}"},
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(
            calls[2][calls[2].index("--body") + 1],
            f"Long-form body.\n\n## Requested by\n\n> {REQUESTER_QUOTE}",
        )

    def test_create_warns_about_unmarked_code_without_altering_body(self) -> None:
        body = "Run qew ready --limit 5 against ./plans/work.jsonl."
        result, calls = run_qew(
            author_responses() + [{"stdout": "https://example.test/acme/widgets/issues/25\n"}],
            [
                "create",
                "--quote",
                REQUESTER_QUOTE,
                "--repo",
                "acme/widgets",
                "--type",
                "task",
                "--title",
                "Document invocation",
                "--goal",
                body,
            ],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unmarked code-shaped text: qew ready", result.stderr)
        self.assertIn("unmarked code-shaped text: --limit", result.stderr)
        self.assertIn("unmarked code-shaped text: ./plans/work.jsonl", result.stderr)
        created_body = calls[2][calls[2].index("--body") + 1]
        self.assertIn(body, created_body)

    def test_plan_create_golden_walk_creates_approved_parent_and_verifies_children(self) -> None:
        result, calls = run_qew(
            [
                *author_responses(),
                {"stdout": "https://example.test/acme/widgets/issues/30\n"},
                {"stdout": "updated\n"},
                target_response(number=12, parent=30),
                {"stdout": "updated\n"},
                target_response(number=13, parent=30),
            ],
            [
                "plan",
                "create",
                "--quote",
                REQUESTER_QUOTE,
                "--repo",
                "acme/widgets",
                "--title",
                "Plan: Reliable authoring",
                "--priority",
                "P1",
                "--children",
                "12,13",
                "--goal",
                "Author work safely.",
                "--done-when",
                "Children are attached.",
                "--json",
            ],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "children": [12, 13],
                "number": 30,
                "project_reconciliation": project_reconciliation(),
                "schema_version": 1,
                "url": "https://example.test/acme/widgets/issues/30",
            },
        )
        create_call = calls[2]
        self.assertIn("epic", create_call)
        self.assertIn("P1", create_call)
        self.assertIn("qew:approved", create_call)
        self.assertIn("### Goal\n\nAuthor work safely.", create_call[create_call.index("--body") + 1])
        self.assertEqual(calls[3][:3], ["issue", "edit", "12"])
        self.assertEqual(calls[5][:3], ["issue", "edit", "13"])

    def test_plan_create_seam_walk_reports_created_plan_and_attached_children_on_failure(self) -> None:
        # Behavior: a failed child attach names the created plan and attached children.
        # Adversary: a retry after an opaque error would create a second plan.
        result, calls = run_qew(
            [
                *author_responses(),
                {"stdout": "https://example.test/acme/widgets/issues/30\n"},
                {"stdout": "updated\n"},
                target_response(number=12, parent=30),
                {"exit_code": 1, "stderr": "boom\n"},
            ],
            [
                "plan",
                "create",
                "--quote",
                REQUESTER_QUOTE,
                "--repo",
                "acme/widgets",
                "--title",
                "Plan: Reliable authoring",
                "--priority",
                "P1",
                "--children",
                "12,13",
                "--goal",
                "Author work safely.",
                "--done-when",
                "Children are attached.",
            ],
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("#30", result.stderr)
        self.assertIn("attached: #12", result.stderr)
        self.assertIn("#13", result.stderr)
        self.assertEqual(len(calls), 6)

    def test_block_golden_walk_adds_and_verifies_edge(self) -> None:
        result, calls = run_qew(
            [graph_response({10: [], 12: []}), {"stdout": "updated\n"}, graph_response({10: [], 12: [10]})],
            ["block", "12", "--on", "10", "--repo", "acme/widgets", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "blocked_by": 10,
                "issue": 12,
                "project_reconciliation": project_reconciliation(),
                "schema_version": 1,
            },
        )
        self.assertEqual(calls[1][:3], ["issue", "edit", "12"])
        self.assertIn("--add-blocked-by", calls[1])

    def test_block_seam_walk_refuses_transitive_cycle_before_write(self) -> None:
        result, calls = run_qew(
            [graph_response({10: [12], 12: []})],
            ["block", "12", "--on", "10", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("cycle", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_plan_create_seam_walk_refuses_non_ascii_digit_children_with_usage_error(self) -> None:
        # Prevents isdigit() accepting "\u00b2" and int() crashing with a traceback (exit 1).
        result, calls = run_qew(
            [],
            [
                "plan",
                "create",
                "--quote",
                REQUESTER_QUOTE,
                "--repo",
                "acme/widgets",
                "--title",
                "Plan: Reliable authoring",
                "--priority",
                "P1",
                "--children",
                "12,\u00b2",
                "--goal",
                "Author work safely.",
                "--done-when",
                "Children are attached.",
            ],
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("children must be comma-separated positive issue numbers", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(calls, [])

    def test_unblock_golden_walk_removes_and_verifies_edge(self) -> None:
        result, calls = run_qew(
            [graph_response({10: [], 12: [10]}), {"stdout": "updated\n"}, graph_response({10: [], 12: []})],
            ["unblock", "12", "--from", "10", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "unblocked #12 from #10\n")
        self.assertEqual(calls[1][:3], ["issue", "edit", "12"])
        self.assertIn("--remove-blocked-by", calls[1])

    def test_set_golden_walk_updates_fields_and_verifies_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            body_file = Path(temporary_directory) / "body.md"
            body_file.write_text("Replacement body.\n", encoding="utf-8")
            result, calls = run_qew(
                [
                    target_response(
                        title="Old title",
                        body="Old body",
                        labels=["P2", "task", "area:old", "qew:approved"],
                        parent=4,
                    ),
                    {"stdout": "updated\n"},
                    target_response(
                        title="New title",
                        body="Replacement body.\n",
                        labels=["P1", "bug", "area:new", "qew:approved"],
                        parent=9,
                    ),
                ],
                [
                    "set",
                    "12",
                    "--repo",
                    "acme/widgets",
                    "--title",
                    "New title",
                    "--body-file",
                    str(body_file),
                    "--priority",
                    "P1",
                    "--type",
                    "bug",
                    "--add-area",
                    "area:new",
                    "--remove-area",
                    "area:old",
                    "--parent",
                    "9",
                    "--json",
                ],
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "changed": True,
                "issue": 12,
                "project_reconciliation": project_reconciliation(),
                "schema_version": 1,
            },
        )
        self.assertEqual(len(calls), 3)
        edit = calls[1]
        self.assertEqual(edit[:3], ["issue", "edit", "12"])
        self.assertIn("--remove-label", edit)
        self.assertIn("--add-label", edit)
        self.assertIn("--parent", edit)
        self.assertIn("--body", edit)

    def test_set_refusal_walk_rejects_closed_target_without_override(self) -> None:
        result, calls = run_qew(
            [target_response(state="CLOSED")],
            ["set", "12", "--repo", "acme/widgets", "--title", "Stale edit"],
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("closed", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_block_refusal_walk_requires_closed_override(self) -> None:
        result, calls = run_qew(
            [graph_response({10: [], 12: []}, states={12: "CLOSED"})],
            ["block", "12", "--on", "10", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--closed", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_unblock_golden_walk_allows_explicit_closed_target(self) -> None:
        result, calls = run_qew(
            [
                graph_response({10: [], 12: [10]}, states={12: "CLOSED"}),
                {"stdout": "updated\n"},
                graph_response({10: [], 12: []}, states={12: "CLOSED"}),
            ],
            ["unblock", "12", "--from", "10", "--repo", "acme/widgets", "--closed"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 3)


def graph_response(edges: dict, *, states: dict = None) -> dict:
    states = states or {}
    nodes = []
    for number, blockers in edges.items():
        nodes.append(
            {
                "number": number,
                "state": states.get(number, "OPEN"),
                "title": f"Work {number}",
                "labels": {
                    "nodes": [
                        {"name": "P2"},
                        {"name": "task"},
                        {"name": "qew:approved"},
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                },
                "blockedBy": {
                    "nodes": [{"number": blocker} for blocker in blockers],
                    "pageInfo": {"hasNextPage": False},
                },
            }
        )
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "repository": {
                        "issues": {
                            "nodes": nodes,
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            }
        )
    }


def target_response(
    *,
    number: int = 12,
    title: str = "Work 12",
    body: str = "Body",
    labels: list = None,
    parent: int = None,
    state: str = "OPEN",
) -> dict:
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "repository": {
                        "issue": {
                            "number": number,
                            "title": title,
                            "body": body,
                            "state": state,
                            "parent": None
                            if parent is None
                            else {"number": parent},
                            "labels": {
                                "nodes": [
                                    {"name": name}
                                    for name in labels or ["P2", "task", "qew:approved"]
                                ],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            },
                        }
                    }
                }
            }
        )
    }


if __name__ == "__main__":
    unittest.main()
