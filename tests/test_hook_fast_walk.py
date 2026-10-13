"""`qew hook pre-tool` answers a command no guard rule can act on without loading the CLI, ledger config or snapshot code; the guard's
own rules are unchanged; `qew hook install --claude --skip-pre-tool` leaves PreToolUse alone for a repo that calls the guard itself.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import hook_guard, hook_words
from tests.ledger_harness import PROJECT_ROOT, World


def pre_tool(cwd, command: str, *flags: str, agent: str = "claude") -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}})
    return subprocess.run([sys.executable, *flags, "-m", "qew.entry", "hook", "pre-tool", "--agent", agent], input=payload, cwd=cwd,
                          capture_output=True, text=True, check=False, timeout=60,
                          env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "QEW_ACTOR": "tester"})


class PreToolFastPathWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World()
        self.addCleanup(self.w.close)

    def test_a_plain_command_is_answered_without_importing_the_cli(self) -> None:
        """Prevents every Bash call paying for the CLI, config and snapshot imports when no rule can refuse it."""
        result = pre_tool(self.w.code, "ls -la src", "-X", "importtime")
        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
        self.assertNotIn("qew.cli", result.stderr)
        self.assertNotIn("qew.hook_guard", result.stderr)
        cursor = pre_tool(self.w.code, "ls", agent="cursor")
        self.assertEqual(json.loads(cursor.stdout), {"permission": "allow"})

    def test_every_command_the_guard_refuses_is_still_refused_through_the_entry(self) -> None:
        """Prevents the fast path letting a bypass through: plain, path-qualified, nested in sh -c, and quote-split forms."""
        for command in ("gh issue create --title x", "/usr/bin/gh issue close 3", "sh -c 'gh issue edit 3'", 'g"h" issue edit 3',
                        "curl -X POST https://api.github.com/repos/a/b/issues -d x", "glab issue close 3"):
            with self.subTest(command=command):
                result = pre_tool(self.w.code, command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_the_fast_path_words_cover_every_binary_the_guard_knows(self) -> None:
        """Prevents a new guarded binary being added to hook_guard without the fast path learning it (it would be silently allowed)."""
        known = set(hook_guard._CLI_RULES) | set(hook_guard._HTTPIE) | {"curl", "wget", "push"}
        self.assertEqual([name for name in sorted(known) if hook_words.inert(name)], [])


class PushDecisionWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World()
        self.addCleanup(self.w.close)
        self.bin = self.w.base / "fakebin"
        self.bin.mkdir()

    def fake_jj(self, body: str) -> dict[str, str]:
        script = self.bin / "jj"
        script.write_text("#!/bin/sh\n" + body)
        script.chmod(0o755)
        return {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}

    def decide(self, env: dict[str, str]) -> subprocess.CompletedProcess:
        payload = json.dumps({"tool_name": "Bash", "cwd": str(self.w.code), "tool_input": {"command": "jj git push --bookmark x"}})
        return subprocess.run([sys.executable, "-m", "qew.entry", "hook", "pre-tool", "--agent", "claude"], input=payload, cwd=self.w.code,
                              capture_output=True, text=True, check=False, timeout=60,
                              env={**os.environ, **env, "PYTHONPATH": str(PROJECT_ROOT / "src"), "QEW_ACTOR": "tester"})

    def test_the_push_decision_never_snapshots_the_working_copy(self) -> None:
        """Prevents the decision taking jj's working-copy lock (and scanning every file) behind other agents' jj commands."""
        record = self.w.base / "argv"
        env = self.fake_jj(f'echo "$@" >> "{record}"\nexit 0\n')
        result = self.decide(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = record.read_text().splitlines()
        self.assertTrue(calls)
        self.assertTrue(all("--ignore-working-copy" in call.split() for call in calls), calls)

    def test_a_jj_that_hangs_ends_the_decision_within_the_bound_and_says_so(self) -> None:
        """Prevents a stuck jj holding the hook past the caller's cap, which fails the push closed with no reason."""
        env = self.fake_jj("exec sleep 120\n")
        started = time.monotonic()
        result = self.decide(env)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not decide", result.stdout)
        self.assertNotIn('"deny"', result.stdout)


class SkipPreToolWalk(unittest.TestCase):
    def test_install_can_leave_pretooluse_alone_and_only_for_claude(self) -> None:
        """Prevents a repo that calls the guard from its own gate script getting the pre-tool entry back on every reinstall."""
        w = World()
        self.addCleanup(w.close)
        result = w.run("hook", "install", "--claude", "--skip-pre-tool")
        self.assertEqual(result.returncode, 0, result.stderr)
        hooks = json.loads((w.code / ".claude" / "settings.json").read_text())["hooks"]
        self.assertEqual(sorted(hooks), ["PostToolUse", "SessionStart"])
        self.assertEqual(w.run("hook", "install", "--skip-pre-tool").returncode, 2)
        self.assertEqual(w.run("hook", "install", "--codex", "--skip-pre-tool").returncode, 2)


if __name__ == "__main__":
    unittest.main()
