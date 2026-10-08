"""Command walks for claiming and completing work."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.command_harness import PROJECT_HINT, make_checkout, project_reconciliation


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LifecycleWalk(unittest.TestCase):
    def run_qew(self, responses: list, *arguments: str):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            fake_gh = temporary / "gh"
            state_file = temporary / "state"
            calls_file = temporary / "calls.jsonl"
            fake_gh.write_text(fake_gh_program(), encoding="utf-8")
            fake_gh.chmod(0o755)
            environment = os.environ.copy()
            environment["QEW_FAKE_GH_RESPONSES"] = json.dumps(responses)
            environment["QEW_FAKE_GH_STATE"] = str(state_file)
            environment["QEW_FAKE_GH_CALLS"] = str(calls_file)
            environment["PATH"] = temporary_directory + os.pathsep + environment["PATH"]
            environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            result = subprocess.run(
                [sys.executable, "-m", "qew", *arguments],
                cwd=make_checkout(temporary),
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            calls = []
            if calls_file.exists():
                calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
            return result, calls

    def test_start_golden_walk_claims_and_verifies_ready_work(self) -> None:
        result, calls = self.run_qew(
            [
                response(issue()),
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                response(issue(assignees=["alexander"], labels=["qew:approved", "task", "P2", "in-progress"])),
            ],
            "start",
            "15",
            "--repo",
            "acme/widgets",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "started #15\n")
        self.assertEqual(result.stderr, PROJECT_HINT)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1][:3], ["issue", "edit", "15"])
        self.assertIn("--add-assignee", calls[1])
        self.assertIn("--add-label", calls[1])

    def test_global_machine_flags_render_versioned_lifecycle_json(self) -> None:
        """Agent loops may place global flags before the command without parsing prose."""
        result, calls = self.run_qew(
            [
                response(issue()),
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                response(issue(assignees=["alexander"], labels=["qew:approved", "task", "P2", "in-progress"])),
            ],
            "--repo",
            "acme/widgets",
            "--json",
            "start",
            "15",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "issue": 15,
                "project_reconciliation": project_reconciliation(),
                "schema_version": 1,
                "state": "started",
            },
        )
        self.assertEqual(len(calls), 3)

    def test_start_seam_walk_refuses_someone_elses_claim(self) -> None:
        result, calls = self.run_qew(
            [response(issue(assignees=["mallory"]))],
            "start",
            "15",
            "--repo",
            "acme/widgets",
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("claimed by mallory", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_start_refuses_approval_gated_work_without_writing(self) -> None:
        result, calls = self.run_qew(
            [response(issue(labels=["qew:approved", "task", "P2", "needs:approval"]))],
            "start",
            "15",
            "--repo",
            "acme/widgets",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("needs human approval", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_start_priority_golden_walk_triages_then_claims(self) -> None:
        result, calls = self.run_qew(
            [
                response(issue(labels=["qew:approved", "task"])),
                {"stdout": "updated priority\n"},
                response(issue(labels=["qew:approved", "task", "P2"])),
                {"stdout": "claimed\n"},
                response(
                    issue(
                        assignees=["alexander"],
                        labels=["qew:approved", "task", "P2", "in-progress"],
                    )
                ),
            ],
            "start",
            "15",
            "--priority",
            "P2",
            "--repo",
            "acme/widgets",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1][:3], ["issue", "edit", "15"])
        self.assertEqual(calls[1][-2:], ["--add-label", "P2"])
        self.assertIn("--add-assignee", calls[3])

    def test_start_priority_refuses_already_triaged_work_without_writing(self) -> None:
        result, calls = self.run_qew(
            [response(issue(labels=["qew:approved", "task", "P3"]))],
            "start",
            "15",
            "--priority",
            "P2",
            "--repo",
            "acme/widgets",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("only when priority is missing", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_start_priority_reports_partial_write_and_never_steals_racing_claim(self) -> None:
        result, calls = self.run_qew(
            [
                response(issue(labels=["qew:approved", "task"])),
                {"stdout": "updated priority\n"},
                response(
                    issue(
                        assignees=["mallory"],
                        labels=["qew:approved", "task", "P2", "in-progress"],
                    )
                ),
            ],
            "start",
            "15",
            "--priority",
            "P2",
            "--repo",
            "acme/widgets",
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("priority P2 was applied", result.stderr)
        self.assertIn("mallory", result.stderr)
        self.assertEqual(len(calls), 3)

    def test_complete_golden_walk_closes_and_releases_claim(self) -> None:
        result, calls = self.run_qew(
            [
                response(issue(assignees=["alexander"], labels=["qew:approved", "in-progress"])),
                {"stdout": "Closed issue acme/widgets#15\n"},
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                response(
                    issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        assignees=[],
                        labels=["qew:approved"],
                    )
                ),
            ],
            "complete",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Mutation walks pass.",
            "--no-commit",
            "Walk-only change outside the repository.",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "completed #15\n")
        self.assertEqual(calls[1][:3], ["issue", "close", "15"])
        comment = calls[1][calls[1].index("--comment") + 1]
        self.assertEqual(
            comment,
            "Mutation walks pass.\n\nNo commit: Walk-only change outside the repository.",
        )
        self.assertEqual(calls[2][:3], ["issue", "edit", "15"])
        self.assertIn("--remove-assignee", calls[2])
        self.assertIn("--remove-label", calls[2])

    def test_complete_records_a_commit_proven_on_the_landing_branch(self) -> None:
        # Prevents closing work as done while its commit never reached the branch.
        result, calls = self.run_qew(
            [
                response(issue(assignees=["alexander"], labels=["qew:approved", "in-progress"])),
                compare_response("ahead", FULL_SHA),
                pulls_response([(42, "2026-10-01T10:00:00Z"), (43, None)]),
                {"stdout": "Closed issue acme/widgets#15\n"},
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                response(
                    issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        assignees=[],
                        labels=["qew:approved"],
                    )
                ),
            ],
            "complete",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Mutation walks pass.",
            "--commit",
            FULL_SHA[:10],
            "--landed-on",
            "feat/collect",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            calls[1],
            ["api", f"repos/acme/widgets/compare/{FULL_SHA[:10]}...feat/collect"],
        )
        self.assertEqual(calls[2], ["api", f"repos/acme/widgets/commits/{FULL_SHA}/pulls"])
        self.assertEqual(calls[3][:3], ["issue", "close", "15"])
        comment = calls[3][calls[3].index("--comment") + 1]
        self.assertEqual(
            comment,
            f"Mutation walks pass.\n\nLanded: commit {FULL_SHA} on feat/collect\nPR: #42",
        )

    def test_complete_accepts_the_branch_tip_itself(self) -> None:
        # GitHub reports identical when the commit is the branch tip; that is landed.
        result, calls = self.run_qew(
            [
                response(issue(assignees=["alexander"], labels=["qew:approved", "in-progress"])),
                compare_response("identical", FULL_SHA),
                pulls_response([]),
                {"stdout": "Closed issue acme/widgets#15\n"},
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                response(
                    issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        assignees=[],
                        labels=["qew:approved"],
                    )
                ),
            ],
            "complete",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Tip landed.",
            "--commit",
            FULL_SHA,
            "--landed-on",
            "main",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        comment = calls[3][calls[3].index("--comment") + 1]
        self.assertEqual(comment, f"Tip landed.\n\nLanded: commit {FULL_SHA} on main")

    def test_complete_refuses_a_commit_that_is_not_on_the_branch(self) -> None:
        # The STR1 class: work closed as done while it sat unpushed or on a side branch.
        for status in ("behind", "diverged"):
            with self.subTest(status=status):
                result, calls = self.run_qew(
                    [
                        response(
                            issue(assignees=["alexander"], labels=["qew:approved", "in-progress"])
                        ),
                        compare_response(status, FULL_SHA),
                    ],
                    "complete",
                    "15",
                    "--repo",
                    "acme/widgets",
                    "--reason",
                    "Claims to be done.",
                    "--commit",
                    FULL_SHA,
                    "--landed-on",
                    "main",
                )

                self.assertEqual(result.returncode, 2)
                self.assertIn(f"commit {FULL_SHA} is not on main", result.stderr)
                self.assertEqual(len(calls), 2, "no write may follow a failed landing check")

    def test_complete_refuses_a_commit_or_branch_github_cannot_find(self) -> None:
        result, calls = self.run_qew(
            [
                response(issue(assignees=["alexander"], labels=["qew:approved", "in-progress"])),
                {"stderr": "gh: Not Found (HTTP 404)\n", "exit_code": 1},
            ],
            "complete",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Unpushed work.",
            "--commit",
            "deadbeef",
            "--landed-on",
            "main",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("deadbeef", result.stderr)
        self.assertIn("main", result.stderr)
        self.assertEqual(len(calls), 2)

    def test_complete_treats_malformed_compare_data_as_indeterminate(self) -> None:
        cases = {
            "missing status": {"stdout": json.dumps({"base_commit": {"sha": FULL_SHA}})},
            "other commit": compare_response("ahead", "f" * 40),
            "invalid pulls": None,
        }
        for name, compare in cases.items():
            with self.subTest(name):
                responses = [
                    response(issue(assignees=["alexander"], labels=["qew:approved", "in-progress"])),
                ]
                if compare is None:
                    responses += [compare_response("ahead", FULL_SHA), {"stdout": "{}"}]
                else:
                    responses.append(compare)
                result, calls = self.run_qew(
                    responses,
                    "complete",
                    "15",
                    "--repo",
                    "acme/widgets",
                    "--reason",
                    "Done.",
                    "--commit",
                    FULL_SHA,
                    "--landed-on",
                    "main",
                )

                self.assertEqual(result.returncode, 4, result.stderr)
                self.assertIn("indeterminate", result.stderr)
                self.assertNotIn("close", [call[1] for call in calls if len(call) > 1])

    def test_complete_requires_a_landed_commit_or_an_explicit_no_commit(self) -> None:
        # Prevents the old silent path: a reason alone never proves anything landed.
        for extra in (
            (),
            ("--commit", FULL_SHA),
            ("--landed-on", "main"),
            ("--commit", FULL_SHA, "--landed-on", "main", "--no-commit", "why"),
            ("--no-commit", "   "),
            ("--commit", "not-a-sha", "--landed-on", "main"),
            ("--commit", FULL_SHA, "--landed-on", "main...evil"),
            ("--commit", FULL_SHA, "--landed-on", "-x"),
        ):
            with self.subTest(extra=extra):
                result, calls = self.run_qew(
                    [],
                    "complete",
                    "15",
                    "--repo",
                    "acme/widgets",
                    "--reason",
                    "Done.",
                    *extra,
                )

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])

    def test_complete_reconciles_a_claim_left_by_github_auto_close(self) -> None:
        result, calls = self.run_qew(
            [
                response(
                    issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        assignees=["alexander"],
                        labels=["qew:approved", "in-progress"],
                    )
                ),
                compare_response("ahead", FULL_SHA),
                pulls_response([(42, "2026-10-01T10:00:00Z")]),
                {"stdout": "edited\n"},
                {"stdout": "commented\n"},
                response(
                    issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        assignees=[],
                        labels=["qew:approved"],
                    )
                ),
            ],
            "complete",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Merged linked PR.",
            "--commit",
            FULL_SHA,
            "--landed-on",
            "main",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "completed #15\n")
        self.assertEqual(calls[3][:3], ["issue", "edit", "15"])
        self.assertEqual(calls[4][:3], ["issue", "comment", "15"])
        comment = calls[4][calls[4].index("--body") + 1]
        self.assertEqual(
            comment, f"Merged linked PR.\n\nLanded: commit {FULL_SHA} on main\nPR: #42"
        )
        self.assertNotIn("close", [call[1] for call in calls if len(call) > 1])
        self.assertNotIn("reopen", [call[1] for call in calls if len(call) > 1])

    def test_complete_refuses_closed_issue_the_caller_never_claimed(self) -> None:
        # Prevents a silent exit 0 that drops the reason for work nobody claimed.
        result, calls = self.run_qew(
            [
                response(
                    issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        assignees=[],
                        labels=["qew:approved"],
                    )
                )
            ],
            "complete",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Never claimed.",
            "--commit",
            FULL_SHA,
            "--landed-on",
            "main",
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("no claim to complete", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_complete_releases_stale_claim_before_commenting(self) -> None:
        # Prevents a retry after a failed release from duplicating the comment.
        result, calls = self.run_qew(
            [
                response(
                    issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        assignees=["alexander"],
                        labels=["qew:approved", "in-progress"],
                    )
                ),
                {"stderr": "release failed\n", "exit_code": 1},
            ],
            "complete",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Merged linked PR.",
            "--no-commit",
            "Docs outside the repository.",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([call[1] for call in calls[1:]], ["edit"], "release must precede comment")

    def test_complete_seam_walk_refuses_work_not_claimed_by_caller(self) -> None:
        result, calls = self.run_qew(
            [response(issue())],
            "complete",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Should not land.",
            "--commit",
            FULL_SHA,
            "--landed-on",
            "main",
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("not claimed by alexander", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_complete_seam_walk_refuses_unreleased_claim_state(self) -> None:
        result, calls = self.run_qew(
            [
                response(issue(assignees=["alexander"], labels=["qew:approved", "in-progress"])),
                {"stdout": "Closed issue acme/widgets#15\n"},
                {"stdout": "https://example.test/acme/widgets/issues/15\n"},
                response(
                    issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        assignees=[],
                        labels=["qew:approved", "in-progress"],
                    )
                ),
            ],
            "complete",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Mutation walks pass.",
            "--no-commit",
            "Walk-only change outside the repository.",
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("verification failed", result.stderr)
        self.assertEqual(len(calls), 4)

    def test_close_walk_preserves_duplicate_and_abandoned_reasons(self) -> None:
        scenarios = [
            (["--duplicate-of", "12"], "duplicate of #12", "DUPLICATE", "duplicate"),
            (["--abandoned"], "abandoned", "NOT_PLANNED", "not planned"),
        ]
        reasons = ["Already tracked.", "No longer needed."]
        for (extra, output_reason, state_reason, gh_reason), reason in zip(scenarios, reasons):
            with self.subTest(output_reason):
                result, calls = self.run_qew(
                    [
                        response(issue(assignees=["alexander"], labels=["qew:approved", "in-progress"])),
                        {"stdout": "closed\n"},
                        {"stdout": "edited\n"},
                        response(
                            issue(
                                state="CLOSED",
                                state_reason=state_reason,
                                assignees=[],
                                labels=["qew:approved"],
                            )
                        ),
                    ],
                    "close",
                    "15",
                    "--repo",
                    "acme/widgets",
                    "--reason",
                    reason,
                    *extra,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"closed #15 as {output_reason}\n")
                self.assertIn(reason, calls[1])
                self.assertIn(gh_reason, calls[1])
                if state_reason == "DUPLICATE":
                    self.assertIn("--duplicate-of", calls[1])
                    self.assertIn("12", calls[1])

    def test_close_without_a_disposition_never_defaults_to_abandoned(self) -> None:
        # The observed mistake: `qew close ISSUE --reason` on finished work recorded it
        # as not planned. The caller must choose, and is told where finished work goes.
        result, calls = self.run_qew(
            [], "close", "15", "--repo", "acme/widgets", "--reason", "Somebody else did it."
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--abandoned", result.stderr)
        self.assertIn("--duplicate-of", result.stderr)
        self.assertIn("--delivered-by", result.stderr)
        self.assertIn("--superseded-by", result.stderr)
        self.assertIn("qew complete 15 --reason TEXT --commit SHA --landed-on BRANCH", result.stderr)
        self.assertEqual(calls, [])

    def test_close_completed_is_refused_and_names_the_replacement_commands(self) -> None:
        # An honest lane reaching for `close --completed` would close work as
        # COMPLETED with no claim and no landing record; name the commands that do.
        result, calls = self.run_qew(
            [],
            "close",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Explicit finish.",
            "--completed",
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("qew complete 15 --reason TEXT --commit SHA --landed-on BRANCH", result.stderr)
        self.assertIn("qew close 15 --reason TEXT --delivered-by SHA --landed-on BRANCH", result.stderr)
        self.assertEqual(calls, [])

    def test_close_delivered_by_records_the_proof_and_closes_as_completed(self) -> None:
        result, calls = self.run_qew(
            [
                response(issue(labels=["qew:approved"])),
                compare_response("ahead", FULL_SHA),
                pulls_response([(42, "2026-10-01T10:00:00Z")]),
                {"stdout": "closed\n"},
                {"stdout": "edited\n"},
                response(
                    issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        assignees=[],
                        labels=["qew:approved"],
                    )
                ),
            ],
            "close",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Peter shipped it.",
            "--delivered-by",
            FULL_SHA[:10],
            "--landed-on",
            "main",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"closed #15 as completed, delivered by {FULL_SHA[:7]} on main\n")
        self.assertEqual(calls[1], ["api", f"repos/acme/widgets/compare/{FULL_SHA[:10]}...main"])
        self.assertEqual(calls[3][:3], ["issue", "close", "15"])
        self.assertEqual(calls[3][calls[3].index("--reason") + 1], "completed")
        self.assertEqual(
            calls[3][calls[3].index("--comment") + 1],
            f"Peter shipped it.\n\nLanded: commit {FULL_SHA} on main\nPR: #42",
        )

    def test_close_delivered_by_refuses_unlanded_work_and_half_a_proof(self) -> None:
        result, calls = self.run_qew(
            [response(issue(labels=["qew:approved"])), compare_response("diverged", FULL_SHA)],
            "close",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Claims delivery.",
            "--delivered-by",
            FULL_SHA,
            "--landed-on",
            "main",
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(f"commit {FULL_SHA} is not on main", result.stderr)
        self.assertEqual(len(calls), 2, "no write may follow a failed landing check")

        for extra in (("--delivered-by", FULL_SHA), ("--landed-on", "main")):
            with self.subTest(extra=extra):
                result, calls = self.run_qew(
                    [], "close", "15", "--repo", "acme/widgets", "--reason", "Half.", *extra
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])

    def test_close_superseded_by_links_the_replacement_and_closes_as_completed(self) -> None:
        result, calls = self.run_qew(
            [
                response(issue(labels=["qew:approved"])),
                response(issue(number=12, labels=["qew:approved"])),
                {"stdout": "closed\n"},
                {"stdout": "edited\n"},
                response(
                    issue(
                        state="CLOSED",
                        state_reason="COMPLETED",
                        assignees=[],
                        labels=["qew:approved"],
                    )
                ),
            ],
            "close",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Folded into the larger rework.",
            "--superseded-by",
            "12",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "closed #15 as completed, superseded by #12\n")
        self.assertEqual(calls[2][:3], ["issue", "close", "15"])
        self.assertEqual(calls[2][calls[2].index("--reason") + 1], "completed")
        self.assertEqual(
            calls[2][calls[2].index("--comment") + 1],
            "Folded into the larger rework.\n\nSuperseded by #12",
        )

    def test_close_superseded_by_refuses_itself_and_every_other_disposition(self) -> None:
        result, calls = self.run_qew(
            [], "close", "15", "--repo", "acme/widgets", "--reason", "Loop.", "--superseded-by", "15"
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("cannot be superseded by itself", result.stderr)
        self.assertEqual(calls, [])

        result, calls = self.run_qew(
            [],
            "close",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Two answers.",
            "--superseded-by",
            "12",
            "--abandoned",
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(calls, [])

    def test_reopen_golden_walk_releases_every_stale_claim(self) -> None:
        result, calls = self.run_qew(
            [
                response(
                    issue(
                        state="CLOSED",
                        state_reason="NOT_PLANNED",
                        assignees=["alexander", "mallory"],
                        labels=["qew:approved", "in-progress"],
                    )
                ),
                {"stdout": "reopened\n"},
                {"stdout": "edited\n"},
                response(
                    issue(
                        state_reason="REOPENED",
                        assignees=[],
                        labels=["qew:approved"],
                    )
                ),
            ],
            "reopen",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "Work is required again.",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "reopened #15\n")
        self.assertEqual(calls[1][:3], ["issue", "reopen", "15"])
        self.assertIn("Work is required again.", calls[1])
        self.assertIn("alexander,mallory", calls[2])
        self.assertIn("--remove-label", calls[2])

    def test_reopen_open_issue_is_a_write_free_noop(self) -> None:
        result, calls = self.run_qew(
            [response(issue())],
            "reopen",
            "15",
            "--repo",
            "acme/widgets",
            "--reason",
            "No transition needed.",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "unchanged #15 (already open)\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(calls), 1)


def issue(
    *,
    number=15,
    state="OPEN",
    state_reason=None,
    assignees=None,
    labels=None,
    blockers=None,
) -> dict:
    return {
        "number": number,
        "state": state,
        "stateReason": state_reason,
        "title": "Bootstrap writes",
        "assignees": {
            "nodes": [{"login": login} for login in assignees or []],
            "pageInfo": {"hasNextPage": False},
        },
        "labels": {
            "nodes": [
                {"name": name}
                for name in labels or ["qew:approved", "task", "P2"]
            ],
            "pageInfo": {"hasNextPage": False},
        },
        "blockedBy": {
            "nodes": blockers or [],
            "pageInfo": {"hasNextPage": False},
        },
    }


FULL_SHA = "0123456789abcdef0123456789abcdef01234567"


def compare_response(status: str, base_sha: str) -> dict:
    return {"stdout": json.dumps({"status": status, "base_commit": {"sha": base_sha}})}


def pulls_response(pulls: list) -> dict:
    return {
        "stdout": json.dumps(
            [{"number": number, "merged_at": merged_at} for number, merged_at in pulls]
        )
    }


def response(issue_data: dict) -> dict:
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "viewer": {"login": "alexander"},
                    "repository": {"issue": issue_data},
                }
            }
        )
    }


def fake_gh_program() -> str:
    return f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["QEW_FAKE_GH_STATE"])
calls_path = Path(os.environ["QEW_FAKE_GH_CALLS"])
responses = json.loads(os.environ["QEW_FAKE_GH_RESPONSES"])
index = int(state_path.read_text() if state_path.exists() else "0")
with calls_path.open("a", encoding="utf-8") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
if index >= len(responses):
    print("unexpected gh call", file=sys.stderr)
    raise SystemExit(99)
response_data = responses[index]
state_path.write_text(str(index + 1), encoding="utf-8")
sys.stdout.write(response_data.get("stdout", ""))
sys.stderr.write(response_data.get("stderr", ""))
raise SystemExit(response_data.get("exit_code", 0))
"""


if __name__ == "__main__":
    unittest.main()
