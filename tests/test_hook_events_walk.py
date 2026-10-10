"""Walks of the hook events across agents: refusal classes, output shapes, hostile input.

Adversary: a prefix or chain that evades the guard, an auto-merge landing with pending checks, a
guard that over-blocks reads, a shape an agent rejects (invalid output blocks the agent), and a hook
crash that breaks every shell call.
"""

import argparse
import contextlib
import io
import json
import sys
import unittest
from unittest import mock

from tests.ledger_harness import QUOTE, World  # first: puts src on sys.path
from qew import cmd_hook
from qew.errors import QewError
from tests.test_hook_v1_walk import feed

AGENTS = ("claude", "codex", "cursor", "opencode")


def decision(result) -> str:
    """The reason text of a deny, whatever the agent's protocol."""
    if not result.stdout:
        return ""
    try:
        body = json.loads(result.stdout)
    except ValueError:
        return result.stdout
    return body.get("hookSpecificOutput", {}).get("permissionDecisionReason") or body.get("agent_message") or ""


class RefusalClassesWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_a_prefix_or_chain_cannot_evade_the_guard_and_the_advice_names_the_qew_command(self) -> None:
        """Env prefixes, absolute binaries and `&&` chains must still be denied with the matching qew verb."""
        for command, advice in (
            ("git status && gh issue edit 3", "qew set"),
            ("FOO=1 /opt/homebrew/bin/gh issue edit 3", "qew set"),
            ("gh issue create --title t", "qew create"),
            ("gh issue close 3", "qew complete"),
            ("gh issue reopen 3", "qew reopen"),
            ("gh issue comment 3 -b hi", "qew"),
        ):
            with self.subTest(command=command):
                reason = decision(feed(self.world, "pre-tool", "claude", command))
                self.assertIn(advice, reason)
                self.assertIn("Blocked", reason)

    def test_merge_forms_point_at_qew_pr_merge_and_disable_auto_is_allowed(self) -> None:
        """Auto-merge must not land with pending checks; cancelling auto-merge must not be blocked."""
        for command in ("gh pr merge 3 --merge", "gh pr merge --auto 3", "gh pr merge"):
            with self.subTest(command=command):
                self.assertIn("qew pr merge", decision(feed(self.world, "pre-tool", "claude", command)))
        for command in ("gh pr merge 3 --disable-auto", "gh pr create --fill"):
            with self.subTest(command=command):
                quiet = feed(self.world, "pre-tool", "claude", command)
                self.assertEqual((quiet.returncode, quiet.stdout), (0, ""))


class OutputShapeWalk(unittest.TestCase):
    """Each agent receives only its documented shape."""

    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_one_bypass_command_gets_four_agent_shapes(self) -> None:
        """A shape an agent does not understand blocks it, so all four are pinned."""
        results = {agent: feed(self.world, "pre-tool", agent, "gh issue close 3") for agent in AGENTS}
        for agent in ("claude", "codex"):
            body = json.loads(results[agent].stdout)
            self.assertEqual(set(body), {"hookSpecificOutput"})
            self.assertEqual(set(body["hookSpecificOutput"]),
                             {"hookEventName", "permissionDecision", "permissionDecisionReason"})
            self.assertEqual(results[agent].returncode, 0)
        cursor = json.loads(results["cursor"].stdout)
        self.assertEqual(set(cursor), {"permission", "user_message", "agent_message"})
        self.assertEqual(cursor["permission"], "deny")
        self.assertEqual(results["opencode"].returncode, 2)
        self.assertNotIn("{", results["opencode"].stdout)
        self.assertIn("qew complete", results["opencode"].stdout)
        quiet = feed(self.world, "pre-tool", "cursor", "ls")
        self.assertEqual(json.loads(quiet.stdout), {"permission": "allow"})
        self.assertEqual(quiet.stdout.strip(), '{"permission": "allow"}')

    def test_session_start_shapes_per_agent(self) -> None:
        """Context injection must use each agent's own key."""
        for agent in ("claude", "codex"):
            body = json.loads(feed(self.world, "session-start", agent, raw="{}").stdout)
            self.assertEqual(set(body["hookSpecificOutput"]), {"hookEventName", "additionalContext"})
            self.assertEqual(body["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertEqual(set(json.loads(feed(self.world, "session-start", "cursor", raw="{}").stdout)), {"additional_context"})
        text = feed(self.world, "session-start", "opencode", raw="{}").stdout
        self.assertTrue(text.startswith("Qew ledger:"))

    def test_post_tool_shapes_per_agent(self) -> None:
        """Cursor's after-shell event prints nothing; opencode gets plain text; Claude and Codex get PostToolUse context."""
        item = self.world.create("--title", "Work", "--quote", QUOTE)
        self.world.run("start", item)
        for agent in ("claude", "codex"):
            body = json.loads(feed(self.world, "post-tool", agent, "git push").stdout)["hookSpecificOutput"]
            self.assertEqual((body["hookEventName"], item in body["additionalContext"]), ("PostToolUse", True))
        self.assertEqual(feed(self.world, "post-tool", "cursor", "git push").stdout, "")
        plain = feed(self.world, "post-tool", "opencode", "git push")
        self.assertEqual(plain.returncode, 0)
        self.assertIn("qew evidence", plain.stdout)
        self.assertNotIn("{", plain.stdout)


class HostileInputWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_malformed_payloads_exit_zero_silently_for_every_event_and_agent(self) -> None:
        """A hook crash must not break every shell call; Cursor's pre-tool answers an explicit allow."""
        payloads = ("", "not json", "[]", '{"tool_input":{"command":7}}', '{"tool_input":3,"cwd":5}')
        before = sorted(p.name for p in self.world.ledger.rglob("*") if p.is_file())
        for agent in AGENTS:
            for event in ("pre-tool", "post-tool"):
                for raw in payloads:
                    with self.subTest(agent=agent, event=event, raw=raw):
                        result = feed(self.world, event, agent, raw=raw)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        expected = '{"permission": "allow"}\n' if (agent, event) == ("cursor", "pre-tool") else ""
                        self.assertEqual(result.stdout, expected)
            for raw in payloads:
                with self.subTest(agent=agent, event="session-start", raw=raw):
                    result = feed(self.world, "session-start", agent, raw=raw)
                    self.assertEqual(result.returncode, 0, result.stderr)
        after = sorted(p.name for p in self.world.ledger.rglob("*") if p.is_file())
        self.assertEqual(before, after)

    def test_an_unparseable_ledger_keeps_post_tool_silent(self) -> None:
        """A broken ledger must not fail the agent's push."""
        (self.world.ledger / "items").mkdir()
        (self.world.ledger / "items" / "TST-BROKEN" / "item.md").parent.mkdir(exist_ok=True)
        (self.world.ledger / "items" / "TST-BROKEN" / "item.md").write_text("not a header", encoding="utf-8")
        result = feed(self.world, "post-tool", "claude", "git push")
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_an_internal_exception_is_swallowed_for_events_but_install_stays_loud(self) -> None:
        """Only the three event commands may swallow a failure; install and remove must not."""
        event = argparse.Namespace(command="hook", hook_command="pre-tool", agent="claude", json=False)
        out = io.StringIO()
        with mock.patch.object(cmd_hook, "_answer", side_effect=RuntimeError("boom")), \
                mock.patch.object(sys, "stdin", io.StringIO("{}")), contextlib.redirect_stdout(out):
            self.assertTrue(cmd_hook.run(event))
        self.assertEqual(out.getvalue(), "")
        install = argparse.Namespace(command="hook", hook_command="install", json=False,
                                     claude=True, codex=False, cursor=False, opencode=False, git=False)
        with mock.patch.object(cmd_hook, "_checkout_root", side_effect=QewError("not inside a Git or jj repository", 2)):
            with self.assertRaises(QewError):
                cmd_hook.run(install)


if __name__ == "__main__":
    unittest.main()
