"""Command-level walks for installing Qew's shell-command hooks into every agent.

Behavior: `qew hooks install` wires `qew hook pre-shell|post-shell` into each
agent, adds nothing at session start (it costs context in every session), and
removes only Qew's own earlier `qew prime` session-start entry.

Adversary: clobbering a teammate's hooks, leaving the old session-start prime
behind, rewriting an unchanged file, or overwriting an unowned plugin.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

CLAUDE_LEGACY = {
    "hooks": [{"args": ["prime"], "command": "qew", "timeout": 10, "type": "command"}]
}
CODEX_LEGACY = {
    "hooks": [
        {
            "command": "qew prime",
            "statusMessage": "Loading Qew work context",
            "timeout": 10,
            "type": "command",
        }
    ]
}
CURSOR_LEGACY = {"command": "qew prime --json"}
OLD_PLUGIN_MARKER = "// Managed by Qew: opencode-prime-v1"


def claude_group(event: str, timeout: int) -> dict:
    return {
        "matcher": "Bash",
        "hooks": [
            {
                "args": ["hook", event, "--agent", "claude"],
                "command": "qew",
                "timeout": timeout,
                "type": "command",
            }
        ],
    }


def codex_group(event: str, timeout: int) -> dict:
    return {
        "matcher": "Bash",
        "hooks": [
            {"command": f"qew hook {event} --agent codex", "timeout": timeout, "type": "command"}
        ],
    }


class HookInstallWalk(unittest.TestCase):
    def test_claude_installs_shell_hooks_keeps_others_and_drops_only_the_old_prime(self) -> None:
        with repository() as root:
            settings = root / ".claude" / "settings.json"
            settings.parent.mkdir()
            unrelated = {"matcher": "Bash", "hooks": [{"type": "command", "command": "audit-tool"}]}
            other_session = {"hooks": [{"type": "command", "command": "other-start"}]}
            settings.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [unrelated],
                            "SessionStart": [CLAUDE_LEGACY, other_session],
                        },
                        "permissions": {"allow": ["Read"]},
                    }
                ),
                encoding="utf-8",
            )

            installed = run_qew(root, "hooks", "install", "claude")

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(installed.stdout, "installed claude hook\n")
            configured = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(configured["permissions"], {"allow": ["Read"]})
            self.assertEqual(
                configured["hooks"]["PreToolUse"], [unrelated, claude_group("pre-shell", 10)]
            )
            self.assertEqual(configured["hooks"]["PostToolUse"], [claude_group("post-shell", 60)])
            self.assertEqual(configured["hooks"]["SessionStart"], [other_session])

            unchanged = run_qew(root, "hooks", "install", "claude")
            self.assertEqual(unchanged.stdout, "unchanged claude hook\n")

            removed = run_qew(root, "hooks", "remove", "claude")
            self.assertEqual(removed.stdout, "removed claude hook\n")
            self.assertEqual(
                json.loads(settings.read_text(encoding="utf-8")),
                {
                    "hooks": {"PreToolUse": [unrelated], "SessionStart": [other_session]},
                    "permissions": {"allow": ["Read"]},
                },
            )
            self.assertEqual(run_qew(root, "hooks", "remove", "claude").stdout, "unchanged claude hook\n")

    def test_claude_installs_without_any_session_start_entry(self) -> None:
        with repository() as root:
            run_qew(root, "hooks", "install", "claude")

            configured = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertNotIn("SessionStart", configured["hooks"])

    def test_claude_remove_also_deletes_a_leftover_prime_entry(self) -> None:
        with repository() as root:
            settings = root / ".claude" / "settings.json"
            settings.parent.mkdir()
            settings.write_text(json.dumps({"hooks": {"SessionStart": [CLAUDE_LEGACY]}}), encoding="utf-8")

            removed = run_qew(root, "hooks", "remove", "claude")

            self.assertEqual(removed.stdout, "removed claude hook\n")
            self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), {})

    def test_the_claude_hook_command_runs_qew_in_exec_form(self) -> None:
        with repository() as root:
            run_qew(root, "hooks", "install", "claude")
            group = json.loads((root / ".claude" / "settings.json").read_text())["hooks"]["PostToolUse"][0]
            entry = group["hooks"][0]
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake = fake_bin / "qew"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
            fake.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]

            run = subprocess.run(
                [entry["command"], *entry["args"]],
                cwd=root, env=environment, input="{}", capture_output=True, text=True, check=False,
            )

            self.assertEqual(run.stdout, "hook post-shell --agent claude\n")

    def test_claude_refuses_a_malformed_event_list_without_rewriting(self) -> None:
        with repository() as root:
            settings = root / ".claude" / "settings.json"
            settings.parent.mkdir()
            original = '{"hooks":{"PreToolUse":"not-a-list"}}\n'
            settings.write_text(original, encoding="utf-8")

            result = run_qew(root, "hooks", "install", "claude")

            self.assertEqual(result.returncode, 2)
            self.assertIn("PreToolUse must be a list", result.stderr)
            self.assertEqual(settings.read_text(encoding="utf-8"), original)

    def test_codex_installs_shell_hooks_and_asks_for_trust(self) -> None:
        with repository() as root:
            target = root / ".codex" / "hooks.json"
            target.parent.mkdir()
            unrelated = {"matcher": "Bash", "hooks": [{"type": "command", "command": "audit-tool"}]}
            target.write_text(
                json.dumps({"hooks": {"PreToolUse": [unrelated], "SessionStart": [CODEX_LEGACY]}}),
                encoding="utf-8",
            )

            installed = run_qew(root, "hooks", "install", "codex")

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(
                installed.stdout,
                "installed codex hook\nreview and trust it with /hooks before it will run\n",
            )
            hooks = json.loads(target.read_text(encoding="utf-8"))["hooks"]
            self.assertEqual(hooks["PreToolUse"], [unrelated, codex_group("pre-shell", 10)])
            self.assertEqual(hooks["PostToolUse"], [codex_group("post-shell", 60)])
            self.assertNotIn("SessionStart", hooks)
            self.assertEqual(run_qew(root, "hooks", "install", "codex").stdout.splitlines()[0], "unchanged codex hook")

            run_qew(root, "hooks", "remove", "codex")
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")), {"hooks": {"PreToolUse": [unrelated]}}
            )

    def test_codex_refuses_a_malformed_hooks_object(self) -> None:
        with repository() as root:
            target = root / ".codex" / "hooks.json"
            target.parent.mkdir()
            original = '{"hooks":[]}\n'
            target.write_text(original, encoding="utf-8")

            result = run_qew(root, "hooks", "install", "codex")

            self.assertEqual(result.returncode, 2)
            self.assertIn("Codex hooks must be an object", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_codex_install_supports_versioned_machine_output(self) -> None:
        with repository() as root:
            result = run_qew(root, "hooks", "install", "codex", "--json")

            self.assertEqual(
                json.loads(result.stdout),
                {"action": "installed", "agent": "codex", "schema_version": 1, "trust_required": True},
            )

    def test_cursor_installs_shell_hooks_and_drops_the_old_session_start(self) -> None:
        with repository() as root:
            target = root / ".cursor" / "hooks.json"
            target.parent.mkdir()
            unrelated = {"command": ".cursor/hooks/audit.sh"}
            target.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "hooks": {"afterFileEdit": [unrelated], "sessionStart": [CURSOR_LEGACY]},
                    }
                ),
                encoding="utf-8",
            )

            installed = run_qew(root, "hooks", "install", "cursor")

            self.assertEqual(installed.returncode, 0, installed.stderr)
            hooks = json.loads(target.read_text(encoding="utf-8"))["hooks"]
            self.assertEqual(hooks["afterFileEdit"], [unrelated])
            self.assertEqual(
                hooks["beforeShellExecution"],
                [{"command": "qew hook pre-shell --agent cursor", "timeout": 10}],
            )
            self.assertEqual(
                hooks["afterShellExecution"],
                [{"command": "qew hook post-shell --agent cursor", "timeout": 60}],
            )
            self.assertNotIn("sessionStart", hooks)
            self.assertEqual(run_qew(root, "hooks", "install", "cursor").stdout, "unchanged cursor hook\n")

            run_qew(root, "hooks", "remove", "cursor")
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"version": 1, "hooks": {"afterFileEdit": [unrelated]}},
            )

    def test_cursor_refuses_an_unsupported_schema_version(self) -> None:
        with repository() as root:
            target = root / ".cursor" / "hooks.json"
            target.parent.mkdir()
            original = '{"version":2,"hooks":{}}\n'
            target.write_text(original, encoding="utf-8")

            result = run_qew(root, "hooks", "install", "cursor")

            self.assertEqual(result.returncode, 2)
            self.assertIn("unsupported Cursor hooks version", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_opencode_installs_a_plugin_replacing_only_an_owned_prime_plugin(self) -> None:
        with repository() as root:
            plugins = root / ".opencode" / "plugins"
            plugins.mkdir(parents=True)
            unrelated = plugins / "keep.js"
            unrelated.write_text("export const keep = true\n", encoding="utf-8")
            old = plugins / "qew-prime.js"
            old.write_text(OLD_PLUGIN_MARKER + "\nexport const Old = {}\n", encoding="utf-8")

            installed = run_qew(root, "hooks", "install", "opencode")

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(installed.stdout, "installed opencode hook\n")
            self.assertTrue((plugins / "qew-hooks.js").is_file())
            self.assertFalse(old.exists())
            self.assertTrue(unrelated.is_file())
            self.assertEqual(run_qew(root, "hooks", "install", "opencode").stdout, "unchanged opencode hook\n")

            removed = run_qew(root, "hooks", "remove", "opencode")
            self.assertEqual(removed.stdout, "removed opencode hook\n")
            self.assertFalse((plugins / "qew-hooks.js").exists())
            self.assertTrue(unrelated.is_file())

    def test_opencode_leaves_an_unowned_prime_plugin_and_refuses_an_unowned_target(self) -> None:
        with repository() as root:
            plugins = root / ".opencode" / "plugins"
            plugins.mkdir(parents=True)
            mine = plugins / "qew-prime.js"
            mine.write_text("export const userPlugin = true\n", encoding="utf-8")

            installed = run_qew(root, "hooks", "install", "opencode")

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(mine.read_text(encoding="utf-8"), "export const userPlugin = true\n")

        with repository() as root:
            target = root / ".opencode" / "plugins" / "qew-hooks.js"
            target.parent.mkdir(parents=True)
            original = "export const userPlugin = true\n"
            target.write_text(original, encoding="utf-8")

            result = run_qew(root, "hooks", "install", "opencode")

            self.assertEqual(result.returncode, 2)
            self.assertIn("not managed by Qew", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_the_opencode_plugin_pipes_bash_commands_to_qew_and_never_throws(self) -> None:
        with repository() as root:
            run_qew(root, "hooks", "install", "opencode")
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake = fake_bin / "qew"
            fake.write_text(
                "#!/bin/sh\n"
                'if [ "$(cat "$QEW_MODE")" = fail ]; then exit 3; fi\n'
                "cat >> \"$QEW_STDIN\"; printf '\\n' >> \"$QEW_STDIN\"\n"
                'printf "%s\\n" "$*" >> "$QEW_CALLS"\n'
                'case "$2" in pre-shell) printf "WARN\\n";; post-shell) printf "LANDED\\n";; esac\n',
                encoding="utf-8",
            )
            fake.chmod(0o755)
            (root / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
            (root / "run.mjs").write_text(
                "import { writeFileSync } from 'node:fs'\n"
                "import { QewHooksPlugin } from './.opencode/plugins/qew-hooks.js'\n"
                "const hooks = await QewHooksPlugin({ directory: process.cwd() })\n"
                "const results = []\n"
                "for (const [mode, tool] of [['ok', 'bash'], ['ok', 'read'], ['fail', 'bash']]) {\n"
                "  writeFileSync(process.env.QEW_MODE, mode)\n"
                "  const output = { args: { command: 'gh issue edit 1' } }\n"
                "  const after = { title: 't', output: 'result', metadata: {} }\n"
                "  try {\n"
                "    await hooks['tool.execute.before']({ tool, sessionID: 's', callID: tool + mode }, output)\n"
                "    await hooks['tool.execute.after']({ tool, sessionID: 's', callID: tool + mode, args: output.args }, after)\n"
                "    results.push(after.output)\n"
                "  } catch (error) { results.push('threw: ' + error.message) }\n"
                "}\n"
                "process.stdout.write(JSON.stringify(results))\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["QEW_MODE"] = str(root / "mode")
            environment["QEW_CALLS"] = str(root / "calls")
            environment["QEW_STDIN"] = str(root / "stdin")
            environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]

            run = subprocess.run(
                ["node", str(root / "run.mjs")],
                cwd=root, env=environment, capture_output=True, text=True, check=False,
            )

            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout), ["result\n\nWARN\n\nLANDED", "result", "result"])
            self.assertEqual(
                (root / "calls").read_text(encoding="utf-8"),
                "hook pre-shell --agent opencode\nhook post-shell --agent opencode\n",
            )
            first = json.loads((root / "stdin").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(first["tool_input"]["command"], "gh issue edit 1")

    def test_install_all_covers_every_agent_and_remove_all_undoes_it(self) -> None:
        with repository() as root:
            installed = run_qew(root, "hooks", "install", "all")

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(
                installed.stdout.splitlines()[:4],
                [
                    "installed claude hook",
                    "installed codex hook",
                    "installed cursor hook",
                    "installed opencode hook",
                ],
            )
            for path in (
                ".claude/settings.json",
                ".codex/hooks.json",
                ".cursor/hooks.json",
                ".opencode/plugins/qew-hooks.js",
            ):
                self.assertTrue((root / path).is_file(), path)
            as_json = run_qew(root, "hooks", "install", "all", "--json")
            self.assertEqual(
                [(item["agent"], item["action"]) for item in json.loads(as_json.stdout)["agents"]],
                [("claude", "unchanged"), ("codex", "unchanged"), ("cursor", "unchanged"), ("opencode", "unchanged")],
            )

            removed = run_qew(root, "hooks", "remove", "all")
            self.assertEqual(removed.stdout.splitlines()[0], "removed claude hook")
            self.assertFalse((root / ".opencode" / "plugins" / "qew-hooks.js").exists())


class JujutsuWorkspaceWalk(unittest.TestCase):
    def test_install_and_remove_work_in_a_workspace_with_no_git_directory(self) -> None:
        # Adversary: hooks could not be installed in a jj-only checkout, and the
        # workaround (a private .git) is forbidden there.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".jj").mkdir()
            nested = root / "apps" / "web"
            nested.mkdir(parents=True)

            installed = run_qew(nested, "hooks", "install", "all")

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertTrue((root / ".claude" / "settings.json").is_file())
            self.assertTrue((root / ".opencode" / "plugins" / "qew-hooks.js").is_file())
            self.assertFalse((nested / ".claude").exists())
            self.assertFalse((root / ".git").exists())
            removed = run_qew(nested, "hooks", "remove", "claude")
            self.assertEqual(removed.stdout, "removed claude hook\n")

    def test_a_colocated_checkout_still_uses_its_root(self) -> None:
        with repository() as root:
            (root / ".jj").mkdir()
            nested = root / "crates"
            nested.mkdir()

            installed = run_qew(nested, "hooks", "install", "claude")

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertTrue((root / ".claude" / "settings.json").is_file())

    def test_outside_any_repository_the_install_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_qew(Path(directory), "hooks", "install", "claude")

            self.assertEqual(result.returncode, 2)
            self.assertIn("not inside a Git or jj repository", result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])


class repository:
    def __enter__(self) -> Path:
        self._directory = tempfile.TemporaryDirectory()
        root = Path(self._directory.name)
        result = subprocess.run(
            ["git", "init", "--quiet"], cwd=root, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
        return root

    def __exit__(self, *exception) -> None:
        self._directory.cleanup()


def run_qew(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "qew", *arguments],
        cwd=root, env=environment, capture_output=True, text=True, check=False,
    )


if __name__ == "__main__":
    unittest.main()
