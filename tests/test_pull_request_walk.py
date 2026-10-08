"""Command walks for creating a pull request from claimed work."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.command_harness import PROJECT_HINT, make_checkout, project_reconciliation


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PullRequestWalk(unittest.TestCase):
    def test_golden_walk_derives_title_body_and_exact_closing_reference(self) -> None:
        result, gh_calls = self._run_qew(
            [
                {"stdout": json.dumps(claim_payload())},
                {"stdout": json.dumps(show_payload())},
                {"stdout": "https://example.test/acme/widgets/pull/31\n"},
            ],
            "synced",
            "pr",
            "--for",
            "15",
            "--repo",
            "acme/widgets",
            "--testing",
            "`python3 -m unittest discover -s tests`",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "created draft PR #31 for #15\n")
        self.assertEqual(result.stderr, PROJECT_HINT)
        create = gh_calls[2]
        self.assertEqual(create[:2], ["pr", "create"])
        self.assertIn("--draft", create)
        # The opener owns the pull request, as the claimant owns the issue.
        self.assertEqual(value_after(create, "--assignee"), "@me")
        self.assertEqual(value_after(create, "--head"), "feat/15-add-export")
        self.assertEqual(value_after(create, "--title"), "feat: Add export workflow")
        body = value_after(create, "--body")
        self.assertIn("## What\n\nGenerate export files.", body)
        self.assertIn("## Why\n\nOperators need portable reports.", body)
        self.assertIn("## Testing\n\n`python3 -m unittest discover -s tests`", body)
        self.assertEqual(body.count("Fixes #15"), 1)
        self.assertEqual(body.count("Part of #1"), 1)

    def test_pr_supports_trailing_machine_output_flag(self) -> None:
        result, _ = self._run_qew(
            [
                {"stdout": json.dumps(claim_payload())},
                {"stdout": json.dumps(show_payload())},
                {"stdout": "https://example.test/acme/widgets/pull/31\n"},
            ],
            "synced",
            "pr",
            "--for",
            "15",
            "--repo",
            "acme/widgets",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "draft": True,
                "issue": 15,
                "number": 31,
                "project_reconciliation": project_reconciliation(),
                "schema_version": 1,
                "url": "https://example.test/acme/widgets/pull/31",
            },
        )

    def test_seam_walk_refuses_a_branch_that_is_not_pushed(self) -> None:
        result, gh_calls = self._run_qew(
            [],
            "ahead",
            "pr",
            "--for",
            "15",
            "--repo",
            "acme/widgets",
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("must exactly match its upstream", result.stderr)
        self.assertEqual(gh_calls, [])

    def test_pr_warns_about_unmarked_code_without_altering_body(self) -> None:
        testing = "Run python3 -m unittest --failfast from ./tests."
        result, gh_calls = self._run_qew(
            [
                {"stdout": json.dumps(claim_payload())},
                {"stdout": json.dumps(show_payload())},
                {"stdout": "https://example.test/acme/widgets/pull/32\n"},
            ],
            "synced",
            "pr",
            "--for",
            "15",
            "--repo",
            "acme/widgets",
            "--testing",
            testing,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unmarked code-shaped text: python3 -m", result.stderr)
        self.assertIn("unmarked code-shaped text: --failfast", result.stderr)
        self.assertIn("unmarked code-shaped text: ./tests", result.stderr)
        self.assertIn(testing, value_after(gh_calls[2], "--body"))

    def test_refusal_walk_rejects_inline_and_cross_repository_closing_references(self) -> None:
        # Adversary: untrusted issue text is copied into the PR body; merging must
        # not close unrelated work through a mid-sentence or cross-repo keyword.
        cases = [
            "This also fixes #16 in passing.",
            "Resolves acme/other#7 upstream.",
            "See notes; closed: #16",
            "Closes https://github.com/acme/other/issues/9",
            "It fixes acme/widgets#16 too.",
        ]
        for text in cases:
            with self.subTest(text=text):
                result, gh_calls = self._run_qew(
                    [
                        {"stdout": json.dumps(claim_payload())},
                        {"stdout": json.dumps(show_payload())},
                    ],
                    "synced",
                    "pr",
                    "--for",
                    "15",
                    "--repo",
                    "acme/widgets",
                    "--what",
                    text,
                )

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("closing reference to unrelated work", result.stderr)
                self.assertFalse([c for c in gh_calls if c[:2] == ["pr", "create"]])

    def test_explicit_head_skips_every_git_probe(self) -> None:
        result, gh_calls = self._run_qew(
            [
                {"stdout": json.dumps(claim_payload())},
                {"stdout": json.dumps(show_payload())},
                {"stdout": "https://example.test/acme/widgets/pull/31\n"},
            ],
            "broken",
            "pr", "--for", "15", "--repo", "acme/widgets", "--head", "feat/15-add-export",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(value_after(gh_calls[2], "--head"), "feat/15-add-export")

    def test_explicit_issue_accepts_a_branch_that_names_no_issue(self) -> None:
        result, gh_calls = self._run_qew(
            [
                {"stdout": json.dumps(claim_payload())},
                {"stdout": json.dumps(show_payload())},
                {"stdout": "https://example.test/acme/widgets/pull/31\n"},
            ],
            "synced",
            "pr", "--for", "15", "--repo", "acme/widgets", "--head", "fix/aws-profile-source-only",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(value_after(gh_calls[2], "--head"), "fix/aws-profile-source-only")

    def test_neither_issue_nor_numbered_branch_still_asks_for_the_issue(self) -> None:
        result, gh_calls = self._run_qew(
            [], "synced",
            "pr", "--repo", "acme/widgets", "--head", "fix/aws-profile-source-only",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("pass --for ISSUE", result.stderr)
        self.assertEqual(gh_calls, [])

    def test_head_unrelated_to_the_issue_is_still_refused(self) -> None:
        result, gh_calls = self._run_qew(
            [], "synced",
            "pr", "--for", "15", "--repo", "acme/widgets", "--head", "feat/99-other",
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("unrelated", result.stderr)
        self.assertIn("#99", result.stderr)
        self.assertIn("#15", result.stderr)
        self.assertEqual(gh_calls, [])

    def test_failed_git_probe_names_its_command_and_exit_status(self) -> None:
        result, _ = self._run_qew(
            [], "detached", "pr", "--for", "15", "--repo", "acme/widgets"
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("detached", result.stderr)
        self.assertIn("--head", result.stderr)
        result, _ = self._run_qew(
            [], "broken", "pr", "--for", "15", "--repo", "acme/widgets"
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("`git symbolic-ref --quiet --short HEAD` failed (exit 128)", result.stderr)

    def test_jujutsu_workspace_uses_its_bookmark_and_never_runs_git(self) -> None:
        responses = [
            {"stdout": json.dumps(claim_payload())},
            {"stdout": json.dumps(show_payload())},
            {"stdout": "https://example.test/acme/widgets/pull/31\n"},
        ]
        result, gh_calls = self._run_qew(
            responses, "synced", "pr", "--for", "15", "--repo", "acme/widgets",
            vcs_marker=".jj",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(value_after(gh_calls[2], "--head"), "feat/15-add-export")

    def test_jujutsu_bookmark_ahead_of_origin_is_refused(self) -> None:
        result, gh_calls = self._run_qew(
            [], "ahead", "pr", "--for", "15", "--repo", "acme/widgets", vcs_marker=".jj"
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("must exactly match its upstream", result.stderr)
        self.assertEqual(gh_calls, [])

    def test_jujutsu_failed_probe_names_its_command_and_exit_status(self) -> None:
        result, _ = self._run_qew(
            [], "broken", "pr", "--for", "15", "--repo", "acme/widgets", vcs_marker=".jj"
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("`jj bookmark list -r @", result.stderr)
        self.assertIn("failed (exit 1)", result.stderr)

    def _run_qew(self, responses: list, git_mode: str, *arguments: str, vcs_marker: str = ".git"):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            gh_calls = temporary / "gh-calls.jsonl"
            gh_state = temporary / "gh-state"
            fake_gh = temporary / "gh"
            fake_gh.write_text(fake_gh_program(), encoding="utf-8")
            fake_gh.chmod(0o755)
            fake_git = temporary / "git"
            fake_git.write_text(fake_git_program(), encoding="utf-8")
            fake_git.chmod(0o755)
            fake_jj = temporary / "jj"
            fake_jj.write_text(fake_jj_program(), encoding="utf-8")
            fake_jj.chmod(0o755)
            environment = os.environ.copy()
            environment["QEW_FAKE_GH_CALLS"] = str(gh_calls)
            environment["QEW_FAKE_GH_RESPONSES"] = json.dumps(responses)
            environment["QEW_FAKE_GH_STATE"] = str(gh_state)
            environment["QEW_FAKE_GIT_MODE"] = git_mode
            environment["PATH"] = str(temporary) + os.pathsep + environment["PATH"]
            environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            result = subprocess.run(
                [sys.executable, "-m", "qew", *arguments],
                cwd=make_checkout(temporary, vcs_marker=vcs_marker),
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            calls = []
            if gh_calls.exists():
                calls = [json.loads(line) for line in gh_calls.read_text().splitlines()]
            return result, calls


def value_after(arguments: list, flag: str) -> str:
    return arguments[arguments.index(flag) + 1]


def claim_payload() -> dict:
    return {
        "data": {
            "viewer": {"login": "alexander"},
            "repository": {
                "issue": {
                    "number": 15,
                    "state": "OPEN",
                    "stateReason": None,
                    "assignees": connection({"login": "alexander"}),
                    "labels": connection(
                        {"name": "qew:approved"},
                        {"name": "in-progress"},
                        {"name": "P1"},
                        {"name": "enhancement"},
                    ),
                    "blockedBy": connection(),
                }
            },
        }
    }


def show_payload() -> dict:
    issue = {
        "number": 15,
        "title": "Add export workflow",
        "body": (
            "### Goal\n\nOperators need portable reports.\n\n"
            "### Approach\n\nGenerate export files."
        ),
        "state": "OPEN",
        "url": "https://example.test/acme/widgets/issues/15",
        "parent": {"number": 1, "title": "Plan", "state": "OPEN"},
        "assignees": connection({"login": "alexander"}),
        "labels": connection(
            {"name": "qew:approved"},
            {"name": "in-progress"},
            {"name": "P1"},
            {"name": "enhancement"},
        ),
        "blockedBy": connection(),
        "blocking": connection(),
        "subIssues": connection(),
        "comments": {
            "nodes": [],
            "pageInfo": {"hasPreviousPage": False, "startCursor": None},
        },
    }
    return {"data": {"repository": {"issue": issue}}}


def connection(*nodes: dict) -> dict:
    return {"nodes": list(nodes), "pageInfo": {"hasNextPage": False, "endCursor": None}}


def fake_gh_program() -> str:
    return f'''#!{sys.executable}
import json
import os
from pathlib import Path
import sys

calls = Path(os.environ["QEW_FAKE_GH_CALLS"])
with calls.open("a", encoding="utf-8") as output:
    output.write(json.dumps(sys.argv[1:]) + "\\n")
responses = json.loads(os.environ["QEW_FAKE_GH_RESPONSES"])
state = Path(os.environ["QEW_FAKE_GH_STATE"])
index = int(state.read_text() if state.exists() else "0")
if index >= len(responses):
    raise SystemExit(99)
response = responses[index]
state.write_text(str(index + 1), encoding="utf-8")
sys.stdout.write(response.get("stdout", ""))
sys.stderr.write(response.get("stderr", ""))
raise SystemExit(response.get("exit_code", 0))
'''


def fake_git_program() -> str:
    return '''#!/bin/sh
if [ -d .jj ]; then echo "git must not run in a jj workspace: $*" >&2; exit 99; fi
if [ "$QEW_FAKE_GIT_MODE" = broken ]; then exit 128; fi
case "$1 $2" in
  "symbolic-ref --quiet")
    if [ "$QEW_FAKE_GIT_MODE" = detached ]; then exit 1; fi
    printf '%s\\n' 'feat/15-add-export' ;;
  "status --porcelain") ;;
  "rev-parse --abbrev-ref") printf '%s\\n' 'origin/feat/15-add-export' ;;
  "rev-list --left-right")
    if [ "$QEW_FAKE_GIT_MODE" = ahead ]; then printf '%s\\n' '0 1'; else printf '%s\\n' '0 0'; fi ;;
  *) printf '%s\\n' "unexpected git call: $*" >&2; exit 98 ;;
esac
'''


def fake_jj_program() -> str:
    return '''#!/bin/sh
if [ "$QEW_FAKE_GIT_MODE" = broken ]; then echo "no repo" >&2; exit 1; fi
case "$1 $2" in
  "bookmark list") printf '%s\\n' 'feat/15-add-export' ;;
  "log --no-graph")
    case "$4" in
      remote_bookmarks*) printf '%s\\n' abc123 ;;
      *..*) if [ "$QEW_FAKE_GIT_MODE" = ahead ]; then printf '%s\\n' def456; fi ;;
    esac ;;
  *) printf '%s\\n' "unexpected jj call: $*" >&2; exit 98 ;;
esac
'''


if __name__ == "__main__":
    unittest.main()
