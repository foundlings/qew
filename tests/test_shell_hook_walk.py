"""Command walks for `qew hook`, the shared shell-command hook behind every agent.

Behavior: after an agent runs `gh pr merge N`, Qew lands the merged pull
request; before an agent edits issues or opens a pull request with raw `gh`,
Qew warns and names the Qew command. The hook never blocks and never fails.

Adversary: a hook that blocks or crashes the agent, that executes or trusts
command text (a quoted example, a branch name, a lookalike), that acts outside
a Qew repository, or that lands against a pull request that is not merged.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_land_walk import CLEAN, STALE, pull_request
from tests.test_lifecycle_walk import FULL_SHA, compare_response, issue, pulls_response, response

REPOSITORY = {"stdout": json.dumps({"nameWithOwner": "acme/widgets"})}


def payload(command: str, event: str = "PreToolUse") -> str:
    return json.dumps(
        {"hook_event_name": event, "tool_name": "Bash", "tool_input": {"command": command}}
    )


def hook(event: str, agent: str, command: str, responses=(), **options):
    return run_qew(
        list(responses),
        ["hook", event, "--agent", agent],
        stdin=payload(command),
        **options,
    )


def message(result) -> str:
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


LANDING = [
    REPOSITORY,
    pull_request(),
    response(issue(**STALE)),
    response(issue(**STALE)),
    compare_response("ahead", FULL_SHA),
    pulls_response([(42, "2026-10-01T10:00:00Z")]),
    {"stdout": "edited\n"},
    {"stdout": "commented\n"},
    response(issue(**CLEAN)),
]


class PreShellHookWalk(unittest.TestCase):
    def test_raw_gh_issue_and_pr_writes_warn_with_the_qew_command(self) -> None:
        for command, expected in (
            ("gh issue edit 15 --add-label x", "qew set"),
            ("gh issue create --title t", "qew create"),
            ("gh issue close 15", "qew complete"),
            ("gh issue reopen 15", "qew reopen"),
            ("gh pr create --fill", "qew pr"),
            ("git status && gh issue edit 3 --title t", "qew set"),
            ("FOO=1 /opt/homebrew/bin/gh issue edit 3", "qew set"),
        ):
            with self.subTest(command=command):
                result, calls = hook("pre-shell", "claude", command)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, message(result))
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"], "PreToolUse")
                self.assertEqual(calls, [])

    def test_a_raw_pr_merge_is_pointed_at_qew_pr_merge(self) -> None:
        # Adversary: gh pr merge --auto merged at once with CI still pending; Qew
        # checks readiness, checklist and checks first.
        for command in ("gh pr merge 3 --merge", "gh pr merge --auto 3", "gh pr merge"):
            with self.subTest(command=command):
                result, calls = hook("pre-shell", "claude", command)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("qew pr merge", message(result))
                self.assertEqual(calls, [])

    def test_disabling_auto_merge_is_not_a_merge_and_stays_silent(self) -> None:
        result, calls = hook("pre-shell", "claude", "gh pr merge --disable-auto 3")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(calls, [])

    def test_reads_and_quoted_text_are_silent(self) -> None:
        # Adversary: a warning on every gh call trains the agent to ignore it, and
        # command text inside quotes is data, not a command.
        for command in (
            "gh issue view 3",
            "gh issue list",
            "gh pr view 3",
            'echo "gh issue edit 3"',
            "ls -la",
            "unterminated 'quote",
            "",
        ):
            with self.subTest(command=command):
                result, calls = hook("pre-shell", "claude", command)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(calls, [])

    def test_each_agent_gets_its_own_non_blocking_shape(self) -> None:
        command = "gh issue edit 15 --add-label x"
        codex, _ = hook("pre-shell", "codex", command)
        cursor, _ = hook("pre-shell", "cursor", command)
        opencode, _ = hook("pre-shell", "opencode", command)
        quiet_cursor, _ = hook("pre-shell", "cursor", "ls")

        self.assertIn("qew set", message(codex))
        decision = json.loads(cursor.stdout)
        self.assertEqual(decision["permission"], "allow")
        self.assertIn("qew set", decision["agent_message"])
        self.assertIn("qew set", opencode.stdout)
        self.assertNotIn("{", opencode.stdout)
        self.assertEqual(json.loads(quiet_cursor.stdout), {"permission": "allow"})

    def test_outside_a_qew_repository_the_hook_is_silent(self) -> None:
        result, calls = hook(
            "pre-shell", "claude", "gh issue edit 15 --add-label x", uninitialized=True
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(calls, [])

    def test_malformed_input_never_fails_the_agent(self) -> None:
        for text in ("", "not json", "[]", '{"tool_input":{"command":7}}', '{"tool_input":3}'):
            with self.subTest(text=text):
                result, calls = run_qew(
                    [], ["hook", "pre-shell", "--agent", "claude"], stdin=text
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(calls, [])


class PostShellHookWalk(unittest.TestCase):
    def test_gh_pr_merge_lands_the_pull_request(self) -> None:
        result, calls = hook("post-shell", "claude", "gh pr merge 42 --merge", LANDING)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("landed #15 from PR #42", message(result))
        self.assertEqual(
            json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"], "PostToolUse"
        )
        self.assertTrue(any(call[:2] == ["issue", "edit"] for call in calls))

    def test_an_explicit_repository_skips_discovery_and_a_url_names_its_own(self) -> None:
        flagged, flagged_calls = hook(
            "post-shell", "claude", "gh pr merge 42 -R acme/widgets", LANDING[1:]
        )
        url, url_calls = hook(
            "post-shell",
            "claude",
            "gh pr merge https://github.com/acme/widgets/pull/42",
            LANDING[1:],
        )

        for result in (flagged, url):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("landed #15 from PR #42", message(result))
        self.assertNotIn(["repo", "view"], [call[:2] for call in flagged_calls])
        self.assertNotIn(["repo", "view"], [call[:2] for call in url_calls])

    def test_cursor_runs_the_landing_and_prints_nothing(self) -> None:
        # Cursor's after-shell event cannot inject text for the model.
        result, calls = hook("post-shell", "cursor", "gh pr merge 42", LANDING)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertTrue(any(call[:2] == ["issue", "edit"] for call in calls))

    def test_opencode_gets_plain_text(self) -> None:
        result, _ = hook("post-shell", "opencode", "gh pr merge 42", LANDING)

        self.assertIn("landed #15 from PR #42", result.stdout)
        self.assertNotIn("{", result.stdout)

    def test_commands_that_do_not_name_a_merge_do_nothing(self) -> None:
        for command in (
            "ls",
            "gh pr merge",
            "gh pr merge feature-branch",
            "gh pr merge --auto 42",
            "gh pr view 42",
            'echo "gh pr merge 42"',
            "gh pr merge 0",
        ):
            with self.subTest(command=command):
                result, calls = hook("post-shell", "claude", command, LANDING)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(calls, [])

    def test_a_failing_landing_reports_the_command_and_never_fails_the_agent(self) -> None:
        result, _ = hook(
            "post-shell",
            "claude",
            "gh pr merge 42",
            [REPOSITORY, {"stderr": "boom\n", "exit_code": 1}],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("qew land 42", message(result))

    def test_another_logins_claim_is_reported_not_taken(self) -> None:
        result, calls = hook(
            "post-shell",
            "claude",
            "gh pr merge 42",
            [
                REPOSITORY,
                pull_request(),
                response(issue(**{**STALE, "assignees": ["mallory"]})),
            ],
        )

        self.assertIn("held by mallory", message(result))
        self.assertIn("--take-over", message(result))
        self.assertFalse(any(call[:2] == ["issue", "edit"] for call in calls))

    def test_outside_a_qew_repository_the_hook_is_silent(self) -> None:
        result, calls = hook(
            "post-shell", "claude", "gh pr merge 42", LANDING, uninitialized=True
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
