"""`qew hook pre-tool` answers a command no guard rule can act on without loading the CLI, ledger config or snapshot code; the guard's
own rules are unchanged; `qew hook install --claude --skip-pre-tool` leaves PreToolUse alone for a repo that calls the guard itself.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import subprocess
import sys
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
