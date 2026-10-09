"""Walks of `qew hook install|remove`: ownership, repository roots, malformed config.

Adversary: an install that clobbers a teammate's hooks, a remove that litters a clean checkout,
a root resolved to the wrong directory, and a half-valid config repaired destructively.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.ledger_harness import PROJECT_ROOT, World

AGENTS = ("claude", "codex", "cursor", "opencode")
FILES = {
    "claude": ".claude/settings.json",
    "codex": ".codex/hooks.json",
    "cursor": ".cursor/hooks.json",
    "opencode": ".opencode/plugins/qew-hooks.js",
}
FOREIGN = {"matcher": "Bash", "hooks": [{"type": "command", "command": "audit-tool"}]}
FOREIGN_SESSION = {"hooks": [{"type": "command", "command": "greet"}]}


def run_in(directory: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "qew.cli", *arguments], cwd=directory, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "QEW_ACTOR": "tester"}, check=False, timeout=60,
    )


def listing(root: Path) -> list:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class RemoveOnCleanCheckoutWalk(unittest.TestCase):
    def test_remove_writes_nothing_and_says_unchanged_for_every_agent(self) -> None:
        """A no-op removal must not litter a clean checkout with empty agent directories."""
        world = World()
        self.addCleanup(world.close)
        for agent in AGENTS:
            with self.subTest(agent=agent):
                before = listing(world.code)
                result = world.run("hook", "remove", f"--{agent}")
                self.assertEqual((result.returncode, result.stdout), (0, f"unchanged {agent} hook\n"), result.stderr)
                self.assertEqual(listing(world.code), before)
        everything = world.run("hook", "remove")
        self.assertEqual(everything.stdout, "".join(f"unchanged {a} hook\n" for a in AGENTS))
        self.assertEqual(listing(world.code), before)


class OwnershipWalk(unittest.TestCase):
    """Install and remove touch only Qew-owned entries, per agent."""

    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.root = self.world.code

    def seed(self, relative: str, value) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_claude_foreign_groups_survive_install_rerun_and_remove(self) -> None:
        """A teammate's PreToolUse and SessionStart hooks must survive a Qew install and remove."""
        old = {"matcher": "Bash", "hooks": [{"args": ["hook", "pre-shell", "--agent", "claude"], "command": "qew", "timeout": 10, "type": "command"}]}
        original = {"theme": "dark", "hooks": {"PreToolUse": [FOREIGN], "SessionStart": [FOREIGN_SESSION]}}
        path = self.seed(".claude/settings.json", {**original, "hooks": {"PreToolUse": [FOREIGN, old], "SessionStart": [FOREIGN_SESSION]}})
        self.assertEqual(self.world.run("hook", "install", "--claude").stdout, "installed claude hook\n")
        installed = read(path)
        self.assertEqual(installed["hooks"]["PreToolUse"][0], FOREIGN)
        self.assertEqual(len(installed["hooks"]["PreToolUse"]), 2)
        self.assertEqual(installed["hooks"]["SessionStart"][0], FOREIGN_SESSION)
        self.assertEqual(self.world.run("hook", "install", "--claude").stdout, "unchanged claude hook\n")
        self.assertEqual(self.world.run("hook", "remove", "--claude").stdout, "removed claude hook\n")
        self.assertEqual(read(path), original)

    def test_codex_remove_prunes_to_the_foreign_entry_only(self) -> None:
        """Remove must leave `{hooks: {PreToolUse: [foreign]}}` and no empty containers behind."""
        path = self.seed(".codex/hooks.json", {"hooks": {"PreToolUse": [FOREIGN]}})
        self.assertEqual(self.world.run("hook", "install", "--codex").returncode, 0)
        self.assertEqual(len(read(path)["hooks"]["PreToolUse"]), 2)
        self.assertEqual(self.world.run("hook", "remove", "--codex").stdout, "removed codex hook\n")
        self.assertEqual(read(path), {"hooks": {"PreToolUse": [FOREIGN]}})

    def test_cursor_keeps_foreign_entry_and_version_one(self) -> None:
        """Remove must keep a foreign afterFileEdit entry and `version: 1` while dropping old Qew entries."""
        path = self.seed(".cursor/hooks.json", {"version": 1, "hooks": {
            "afterFileEdit": [{"command": "fmt"}], "sessionStart": [{"command": "qew prime --json"}]}})
        self.assertEqual(self.world.run("hook", "install", "--cursor").returncode, 0)
        installed = read(path)["hooks"]
        self.assertEqual(installed["afterFileEdit"], [{"command": "fmt"}])
        self.assertEqual(installed["sessionStart"], [{"command": "qew hook session-start --agent cursor", "timeout": 10}])
        self.assertEqual(self.world.run("hook", "remove", "--cursor").returncode, 0)
        self.assertEqual(read(path), {"version": 1, "hooks": {"afterFileEdit": [{"command": "fmt"}]}})

    def test_opencode_old_plugin_dropped_only_when_marked_and_unrelated_file_untouched(self) -> None:
        """An unmarked file with Qew's old name is someone else's; install still succeeds and leaves it."""
        plugins = self.root / ".opencode" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "keep.js").write_text("// keep\n", encoding="utf-8")
        old = plugins / "qew-prime.js"
        old.write_text("// Managed by Qew: opencode-prime-v1\n", encoding="utf-8")
        self.assertEqual(self.world.run("hook", "install", "--opencode").returncode, 0)
        self.assertFalse(old.exists())
        self.assertEqual((plugins / "keep.js").read_text(encoding="utf-8"), "// keep\n")
        old.write_text("// mine, not Qew's\n", encoding="utf-8")
        again = self.world.run("hook", "install", "--opencode")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(old.read_text(encoding="utf-8"), "// mine, not Qew's\n")
        self.assertEqual(self.world.run("hook", "remove", "--opencode").returncode, 0)
        self.assertTrue(old.exists() and (plugins / "keep.js").exists())

    def test_remove_of_a_file_holding_only_a_leftover_qew_entry_leaves_an_empty_object(self) -> None:
        """A stale Qew entry must not survive remove."""
        old = {"hooks": [{"args": ["prime"], "command": "qew", "timeout": 10, "type": "command"}]}
        path = self.seed(".claude/settings.json", {"hooks": {"SessionStart": [old]}})
        self.assertEqual(self.world.run("hook", "remove", "--claude").stdout, "removed claude hook\n")
        self.assertEqual(read(path), {})


class MalformedConfigWalk(unittest.TestCase):
    """A half-valid config is refused with exit 2 and a named reason, byte-identical afterwards."""

    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def refuse(self, relative: str, original: str, agent: str, reason: str, verb: str = "install") -> None:
        path = self.world.code / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(original, encoding="utf-8")
        result = self.world.run("hook", verb, f"--{agent}")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(reason, result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_claude_event_node_not_a_list(self) -> None:
        for event in ("PreToolUse", "SessionStart", "PostToolUse"):
            with self.subTest(event=event):
                self.refuse(".claude/settings.json", json.dumps({"hooks": {event: {}}}), "claude", f"{event} must be a list")

    def test_codex_hooks_not_an_object(self) -> None:
        self.refuse(".codex/hooks.json", '{"hooks": []}', "codex", "hooks must be an object")

    def test_cursor_unsupported_version(self) -> None:
        self.refuse(".cursor/hooks.json", '{"version": 2, "hooks": {}}', "cursor", "unsupported Cursor hooks version")
        self.refuse(".cursor/hooks.json", '{"version": 2, "hooks": {}}', "cursor", "unsupported Cursor hooks version", "remove")

    def test_unparseable_json_names_the_file(self) -> None:
        self.refuse(".claude/settings.json", "{not json", "claude", "settings.json")

    def test_opencode_target_without_the_marker_is_not_touched(self) -> None:
        self.refuse(".opencode/plugins/qew-hooks.js", "// mine\n", "opencode", "not managed by Qew")


class RepositoryRootWalk(unittest.TestCase):
    """The root is the nearest ancestor holding `.git` or `.jj`, never a private `.git`."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()

    def test_jj_only_checkout_installs_at_the_root_from_a_subdirectory(self) -> None:
        """Hooks must be possible in a jj-only checkout without a forbidden private `.git`."""
        (self.base / ".jj").mkdir()
        sub = self.base / "apps" / "web"
        sub.mkdir(parents=True)
        result = run_in(sub, "hook", "install")
        self.assertEqual(result.returncode, 0, result.stderr)
        for relative in FILES.values():
            self.assertTrue((self.base / relative).is_file(), relative)
        self.assertEqual(sorted(p.name for p in sub.iterdir()), [])
        self.assertFalse((self.base / ".git").exists())
        removed = run_in(sub, "hook", "remove")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse((self.base / FILES["opencode"]).exists())
        self.assertEqual(read(self.base / FILES["claude"]), {})

    def test_colocated_checkout_root_wins_over_nested_directories(self) -> None:
        """Config must not split between nested directories."""
        (self.base / ".git").mkdir()
        (self.base / ".jj").mkdir()
        sub = self.base / "crates"
        sub.mkdir()
        self.assertEqual(run_in(sub, "hook", "install", "--claude").returncode, 0)
        self.assertTrue((self.base / FILES["claude"]).is_file())
        self.assertFalse((sub / ".claude").exists())

    def test_outside_any_repository_creates_nothing(self) -> None:
        """Refuse with exit 2 and leave the directory empty."""
        for verb in ("install", "remove"):
            result = run_in(self.base, "hook", verb)
            self.assertEqual(result.returncode, 2)
            self.assertIn("not inside a Git or jj repository", result.stderr)
        self.assertEqual(listing(self.base), [])


if __name__ == "__main__":
    unittest.main()
