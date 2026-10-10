"""Walks of the v1 agent hooks: install, session prime, bypass refusal, push reminder.

Adversary: a hook that clobbers a teammate's configuration, a prime that eats the
agent's context, a bypass of the ledger through a forge CLI or API call, a refusal
that fires on a read, on quoted text or outside a Qew repository, and a hook that
breaks the agent's session.
"""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from tests.ledger_harness import PROJECT_ROOT, QUOTE, World


def feed(world: World, event: str, agent: str, command: str = None, raw: str = None, **extra):
    payload = raw if raw is not None else json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": command}, **extra}
        if agent != "cursor"
        else {"command": command, **extra}
    )
    return world.run("hook", event, "--agent", agent, stdin=payload)


def outside(*arguments: str, stdin: str = "") -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as directory:
        return subprocess.run(
            [sys.executable, "-m", "qew.cli", *arguments],
            cwd=directory,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")},
            capture_output=True, text=True, input=stdin, check=False, timeout=60,
        )


def guarded(event: str, agent: str) -> str:
    """The pinned hook command: one shell string that fails open when qew is not installed."""
    return (
        "command -v qew >/dev/null 2>&1 || "
        "{ echo '{\"systemMessage\":\"qew is not on PATH; qew hooks skipped\"}'; exit 0; }; "
        f"exec qew hook {event} --agent {agent}"
    )


def claude_entry(event: str) -> dict:
    return {"command": guarded(event, "claude"), "timeout": 10, "type": "command"}


def codex_entry(event: str) -> dict:
    return {"command": guarded(event, "codex"), "timeout": 10, "type": "command"}


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class GuardedCommandWalk(unittest.TestCase):
    """The emitted hook command runs under sh: missing qew skips with a warning, real refusals pass through."""

    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.world.run("hook", "install", "--claude", "--codex")
        self.settings = read(self.world.code / ".claude" / "settings.json")["hooks"]

    def command(self, event_group: str) -> str:
        return self.settings[event_group][0]["hooks"][0]["command"]

    def run_hook(self, command: str, path: str, stdin: str = "{}"):
        return subprocess.run(
            ["/bin/sh", "-c", command], cwd=self.world.code, input=stdin, capture_output=True, text=True,
            env={**os.environ, "PATH": path, "PYTHONPATH": str(PROJECT_ROOT / "src")}, check=False, timeout=60,
        )

    def test_no_args_key_and_a_single_command_string(self) -> None:
        for group in ("SessionStart", "PreToolUse", "PostToolUse"):
            entry = self.settings[group][0]["hooks"][0]
            self.assertNotIn("args", entry)
            self.assertEqual(entry["type"], "command")

    def test_a_missing_qew_fails_open_with_a_one_line_warning(self) -> None:
        for group in ("SessionStart", "PreToolUse", "PostToolUse"):
            result = self.run_hook(self.command(group), "/nonexistent")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"systemMessage": "qew is not on PATH; qew hooks skipped"})

    def test_a_real_refusal_still_blocks_when_qew_is_present(self) -> None:
        bin_dir = self.world.code.parent / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "qew"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} -m qew \"$@\"\n")
        wrapper.chmod(0o755)
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "gh issue close 3"}})
        result = self.run_hook(self.command("PreToolUse"), f"{bin_dir}:/usr/bin:/bin", payload)
        self.assertIn("deny", result.stdout + str(result.returncode))


class InstallWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.root = self.world.code

    def test_no_flag_installs_all_four_idempotently_and_privately(self) -> None:
        first = self.world.run("hook", "install")
        self.assertEqual(first.returncode, 0, first.stderr)
        claude = read(self.root / ".claude" / "settings.json")["hooks"]
        self.assertEqual(claude["SessionStart"], [{"hooks": [claude_entry("session-start")]}])
        self.assertEqual(claude["PreToolUse"], [{"matcher": "Bash", "hooks": [claude_entry("pre-tool")]}])
        self.assertEqual(claude["PostToolUse"], [{"matcher": "Bash", "hooks": [claude_entry("post-tool")]}])
        codex = read(self.root / ".codex" / "hooks.json")["hooks"]
        self.assertEqual(codex["SessionStart"], [{"hooks": [codex_entry("session-start")]}])
        self.assertEqual(codex["PreToolUse"], [{"matcher": "Bash", "hooks": [codex_entry("pre-tool")]}])
        self.assertEqual(codex["PostToolUse"], [{"matcher": "Bash", "hooks": [codex_entry("post-tool")]}])
        cursor = read(self.root / ".cursor" / "hooks.json")
        self.assertEqual(cursor["version"], 1)
        self.assertEqual(cursor["hooks"]["beforeShellExecution"][0]["command"], "qew hook pre-tool --agent cursor")
        self.assertEqual(cursor["hooks"]["afterShellExecution"][0]["command"], "qew hook post-tool --agent cursor")
        self.assertEqual(cursor["hooks"]["sessionStart"][0]["command"], "qew hook session-start --agent cursor")
        plugin = (self.root / ".opencode" / "plugins" / "qew-hooks.js").read_text(encoding="utf-8")
        self.assertIn("pre-tool", plugin)
        self.assertNotIn("pre-shell", plugin)
        for path in (".claude/settings.json", ".codex/hooks.json", ".cursor/hooks.json", ".opencode/plugins/qew-hooks.js"):
            self.assertEqual(stat.S_IMODE((self.root / path).stat().st_mode), 0o600, path)
        again = self.world.run("hook", "install")
        self.assertEqual(again.stdout.count("unchanged"), 4, again.stdout)
        self.assertIn("/hooks", first.stdout)

    def test_flags_select_agents(self) -> None:
        self.assertEqual(self.world.run("hook", "install", "--codex", "--cursor").returncode, 0)
        self.assertTrue((self.root / ".codex").is_dir() and (self.root / ".cursor").is_dir())
        self.assertFalse((self.root / ".claude").exists())
        self.assertFalse((self.root / ".opencode").exists())

    def test_foreign_entries_survive_install_and_remove_and_old_entries_are_dropped(self) -> None:
        foreign = {"matcher": "Bash", "hooks": [{"type": "command", "command": "audit-tool"}]}
        old_claude = {"matcher": "Bash", "hooks": [{"args": ["hook", "pre-shell", "--agent", "claude"], "command": "qew", "timeout": 10, "type": "command"}]}
        old_prime = {"hooks": [{"args": ["prime"], "command": "qew", "timeout": 10, "type": "command"}]}
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "settings.json").write_text(json.dumps({
            "permissions": {"allow": ["Read"]},
            "hooks": {"PreToolUse": [foreign, old_claude], "SessionStart": [old_prime]},
        }), encoding="utf-8")
        old_codex = {"matcher": "Bash", "hooks": [{"command": "qew hook post-shell --agent codex", "timeout": 60, "type": "command"}]}
        (self.root / ".codex").mkdir()
        (self.root / ".codex" / "hooks.json").write_text(json.dumps({"hooks": {"PostToolUse": [foreign, old_codex]}}), encoding="utf-8")
        (self.root / ".cursor").mkdir()
        (self.root / ".cursor" / "hooks.json").write_text(json.dumps({"version": 1, "hooks": {
            "beforeShellExecution": [{"command": "other"}, {"command": "qew hook pre-shell --agent cursor", "timeout": 10}],
            "sessionStart": [{"command": "qew prime --json"}],
        }}), encoding="utf-8")

        self.assertEqual(self.world.run("hook", "install", "--claude", "--codex", "--cursor").returncode, 0)
        claude = read(self.root / ".claude" / "settings.json")
        self.assertEqual(claude["permissions"], {"allow": ["Read"]})
        self.assertEqual(claude["hooks"]["PreToolUse"], [foreign, {"matcher": "Bash", "hooks": [claude_entry("pre-tool")]}])
        self.assertEqual(claude["hooks"]["SessionStart"], [{"hooks": [claude_entry("session-start")]}])
        self.assertEqual(read(self.root / ".codex" / "hooks.json")["hooks"]["PostToolUse"][0], foreign)
        cursor = read(self.root / ".cursor" / "hooks.json")["hooks"]
        self.assertEqual(cursor["beforeShellExecution"][0], {"command": "other"})
        self.assertNotIn("qew hook pre-shell --agent cursor", json.dumps(cursor))
        self.assertNotIn("qew prime", json.dumps(cursor))

        removed = self.world.run("hook", "remove", "--claude", "--codex", "--cursor")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(read(self.root / ".claude" / "settings.json"), {
            "permissions": {"allow": ["Read"]}, "hooks": {"PreToolUse": [foreign]}})
        self.assertEqual(read(self.root / ".codex" / "hooks.json"), {"hooks": {"PostToolUse": [foreign]}})
        self.assertEqual(read(self.root / ".cursor" / "hooks.json"),
                         {"version": 1, "hooks": {"beforeShellExecution": [{"command": "other"}]}})
        self.assertEqual(self.world.run("hook", "remove", "--claude").stdout, "unchanged claude hook\n")

    def test_opencode_replaces_its_own_plugin_but_never_an_unowned_file(self) -> None:
        plugins = self.root / ".opencode" / "plugins"
        plugins.mkdir(parents=True)
        target = plugins / "qew-hooks.js"
        target.write_text("// Managed by Qew: opencode-hooks-v1\nold pre-shell\n", encoding="utf-8")
        self.assertEqual(self.world.run("hook", "install", "--opencode").returncode, 0)
        self.assertIn("pre-tool", target.read_text(encoding="utf-8"))
        self.assertEqual(self.world.run("hook", "remove", "--opencode").stdout, "removed opencode hook\n")
        self.assertFalse(target.exists())
        target.write_text("// mine\n", encoding="utf-8")
        refused = self.world.run("hook", "install", "--opencode")
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(target.read_text(encoding="utf-8"), "// mine\n")

    def test_malformed_config_is_refused_without_rewriting(self) -> None:
        (self.root / ".claude").mkdir()
        original = '{"hooks":{"PreToolUse":"nope"}}\n'
        (self.root / ".claude" / "settings.json").write_text(original, encoding="utf-8")
        result = self.world.run("hook", "install", "--claude")
        self.assertEqual(result.returncode, 2)
        self.assertEqual((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"), original)

    def test_install_outside_a_repository_fails(self) -> None:
        result = outside("hook", "install")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not inside a Git or jj repository", result.stderr)


class SessionStartWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def context(self, agent: str = "claude") -> str:
        result = feed(self.world, "session-start", agent, raw="{}")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        if agent == "cursor":
            return output["additional_context"]
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        return output["hookSpecificOutput"]["additionalContext"]

    def test_prime_is_bounded_lists_a_few_ready_rows_counts_and_the_six_verbs(self) -> None:
        specs = [{"title": f"Ready work number {n} " + "long " * 30, "priority": n % 3} for n in range(30)]
        specs.append({"ref": "a", "title": "first"})
        specs.append({"title": "waits", "needs": ["@a"]})
        specs.append({"title": "second wait", "needs": ["@a"]})
        made = self.world.run("create", "--from-file", "-", "--quote", QUOTE, stdin=json.dumps(specs))
        self.assertEqual(made.returncode, 0, made.stderr)
        self.world.run("create", "--title", "just a draft", "--draft")
        for agent in ("claude", "codex", "cursor"):
            text = self.context(agent)
            self.assertLessEqual(len(text.encode("utf-8")), 2000)
            ready = text.split("Next ready:\n")[1].split("Active:")[0].splitlines()
            self.assertEqual(len(ready), 4, text)
            self.assertEqual(ready[-1], "... 28 more")
            self.assertIn("2 blocked", text)
            self.assertIn("1 draft", text)
            for verb in ("ready", "start", "evidence", "complete", "show", "draft"):
                self.assertIn(f"qew {verb}", text)

    def test_priority_order(self) -> None:
        low = self.world.create("--title", "later", "--priority", "3", "--quote", QUOTE)
        high = self.world.create("--title", "sooner", "--priority", "1", "--quote", QUOTE)
        text = self.context()
        self.assertLess(text.index("sooner"), text.index("later"))
        self.assertLess(text.index(high), text.index(low))

    def test_empty_ledger_still_primes_without_failing(self) -> None:
        text = self.context()
        self.assertIn("0 ready", text)

    def test_silent_outside_a_repository_and_on_an_unreadable_ledger(self) -> None:
        result = outside("hook", "session-start", "--agent", "claude", stdin="{}")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        (self.world.ledger / "items").mkdir()
        (self.world.ledger / "items" / "TST-BROKEN" / "item.md").parent.mkdir(exist_ok=True)
        (self.world.ledger / "items" / "TST-BROKEN" / "item.md").write_text("not a header", encoding="utf-8")
        broken = feed(self.world, "session-start", "claude", raw="{}")
        self.assertEqual((broken.returncode, broken.stdout), (0, ""))


REFUSED = (
    "gh issue create --title x", "gh issue edit 3 --add-label x", "gh issue close 3", "gh issue reopen 3",
    "gh issue comment 3 -b hi", "gh issue delete 3 --yes", "gh issue transfer 3 o/r", "gh issue pin 3",
    "gh issue lock 3", "gh label create bug", "gh label delete bug", "gh project create --owner o",
    "gh project item-add 1 --url x", "gh pr merge 5 --squash", "gh pr merge --auto 5",
    "gh api -X POST repos/o/r/issues -f title=x", "gh api repos/o/r/issues/3 -X PATCH -f state=closed",
    "gh api repos/o/r/labels -f name=x", "gh api repos/o/r/milestones --method POST",
    "gh api -X DELETE repos/o/r/issues/3/labels/bug", "gh api -X PUT repos/o/r/pulls/5/merge",
    "gh api orgs/o/projects --input body.json",
    "gh api graphql -f query='mutation { createIssue(input:{}) { issue { id } } }'",
    "gh api graphql -f query='mutation { addProjectV2ItemById(input:{}) { item { id } } }'",
    "glab issue create", "glab issue update 3", "glab issue note 3 -m hi", "glab label create",
    "glab api -X POST projects/1/issues -f title=x", "glab mr merge 3",
    "tea issues create", "tea issues close 3", "tea labels create", "tea milestones create",
    "tea pulls merge 3", "tea comment 3 hi",
    "fj issue create", "fj issue close 3", "fj pr merge 3",
    "curl -X POST https://api.github.com/repos/o/r/issues -d '{}'",
    "curl -X PATCH -H 'x: y' https://codeberg.org/api/v1/repos/o/r/issues/3 -d @f",
    "curl -d '{}' https://example.com/api/v1/repos/o/r/labels",
    "curl -X PUT https://gitlab.com/api/v4/projects/1/merge_requests/3/merge",
    "curl -X POST https://api.github.com/graphql -d '{\"query\":\"mutation { closeIssue(input:{}) { clientMutationId } }\"}'",
    "wget --method=POST --body-data=x https://h/api/v4/projects/1/issues",
    "http POST https://host/api/v1/repos/o/r/issues title=x",
    "cd sub && gh issue close 3", "GH_TOKEN=x gh issue close 3", "/usr/bin/gh issue close 3",
    'bash -c "gh issue close 3"', "gh issue close 3 | cat",
)
ALLOWED = (
    "gh issue list", "gh issue view 3", "gh pr view 3", "gh pr checks 3", "gh pr diff 3", "gh pr create --fill",
    "gh api repos/o/r/issues", "gh api repos/o/r/issues -X GET -f state=open",
    "gh api graphql -f query='query { viewer { login } }'", "gh api -X POST repos/o/r/git/refs -f ref=x",
    "gh label list", "gh project list", "glab issue list", "glab mr view 3", "tea issues list",
    "fj issue view 3", "curl https://api.github.com/repos/o/r/issues",
    "curl -X POST https://example.com/webhook -d x", "wget https://h/api/v1/repos/o/r/issues",
    'echo "gh issue edit 3"', "echo 'gh pr merge 3'", 'git commit -m "gh issue close 3"',
    "grep 'glab issue create' notes.txt", "ls -la", "unterminated 'quote", "",
)


class PreToolWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_bypasses_are_denied_with_the_agents_protocol_and_name_the_qew_command(self) -> None:
        for agent in ("claude", "codex"):
            for command in REFUSED:
                with self.subTest(agent=agent, command=command):
                    result = feed(self.world, "pre-tool", agent, command)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    decision = json.loads(result.stdout)["hookSpecificOutput"]
                    self.assertEqual(decision["hookEventName"], "PreToolUse")
                    self.assertEqual(decision["permissionDecision"], "deny")
                    self.assertIn("`qew ", decision["permissionDecisionReason"])

    def test_advice_matches_the_blocked_action(self) -> None:
        for command, advice in (
            ("gh issue create --title x", "qew create"), ("gh issue edit 3", "qew set"),
            ("gh issue close 3", "qew complete"), ("gh issue reopen 3", "qew reopen"),
            ("gh pr merge 5", "qew pr merge"), ("glab mr merge 5", "qew pr merge"),
            ("gh project item-add 1", "qew project"),
        ):
            with self.subTest(command=command):
                reason = json.loads(feed(self.world, "pre-tool", "claude", command).stdout)[
                    "hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn(advice, reason)

    def test_reads_and_quoted_examples_pass_silently(self) -> None:
        for agent in ("claude", "codex"):
            for command in ALLOWED:
                with self.subTest(agent=agent, command=command):
                    result = feed(self.world, "pre-tool", agent, command)
                    self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)

    def test_cursor_and_opencode_use_their_own_deny_shapes(self) -> None:
        denied = feed(self.world, "pre-tool", "cursor", "gh issue close 3")
        decision = json.loads(denied.stdout)
        self.assertEqual((denied.returncode, decision["permission"]), (0, "deny"))
        self.assertIn("qew complete", decision["agent_message"])
        self.assertEqual(json.loads(feed(self.world, "pre-tool", "cursor", "gh issue list").stdout), {"permission": "allow"})
        blocked = feed(self.world, "pre-tool", "opencode", "gh issue close 3")
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("qew complete", blocked.stdout)
        self.assertEqual(feed(self.world, "pre-tool", "opencode", "gh issue list").stdout, "")

    def test_nothing_is_refused_outside_a_qew_managed_repository(self) -> None:
        payload = json.dumps({"tool_input": {"command": "gh issue close 3"}})
        result = outside("hook", "pre-tool", "--agent", "claude", stdin=payload)
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        cursor = outside("hook", "pre-tool", "--agent", "cursor", stdin=json.dumps({"command": "gh issue close 3"}))
        self.assertEqual(json.loads(cursor.stdout), {"permission": "allow"})

    def test_malformed_payloads_never_fail_the_agent(self) -> None:
        for raw in ("", "not json", "[]", '{"tool_input":{"command":7}}', '{"tool_input":3}', "{}"):
            with self.subTest(raw=raw):
                result = feed(self.world, "pre-tool", "claude", raw=raw)
                self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)


class PostToolWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def reminder(self, command: str, agent: str = "claude") -> str:
        result = feed(self.world, "post-tool", agent, command)
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout:
            return ""
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_push_with_a_claimed_item_lacking_evidence_gets_a_one_line_reminder(self) -> None:
        item = self.world.create("--title", "Work", "--quote", QUOTE)
        self.assertEqual(self.world.run("start", item).returncode, 0)
        for command in ("git push origin main", "jj git push --bookmark x", "git -C . push"):
            with self.subTest(command=command):
                text = self.reminder(command)
                self.assertEqual(len(text.splitlines()), 1)
                self.assertIn("qew evidence", text)
                self.assertIn(item, text)
        self.assertEqual(self.reminder("ls"), "")
        self.assertEqual(self.reminder('git commit -m "git push"'), "")
        self.assertEqual(feed(self.world, "post-tool", "cursor", "git push").stdout, "")
        sha = self.world.commit("work")
        self.world.run("evidence", item, "--change", sha)
        self.assertEqual(self.reminder("git push"), "")

    def test_nothing_to_say_without_claims_or_outside_a_repository(self) -> None:
        self.assertEqual(self.reminder("git push"), "")
        result = outside("hook", "post-tool", "--agent", "claude", stdin=json.dumps({"tool_input": {"command": "git push"}}))
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_someone_elses_claim_is_not_ours_to_remind_about(self) -> None:
        item = self.world.create("--title", "Work", "--quote", QUOTE)
        self.world.run("start", item, env={"QEW_ACTOR": "someone-else"})
        self.assertEqual(self.reminder("git push"), "")


if __name__ == "__main__":
    unittest.main()
