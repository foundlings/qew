"""Schema 6: a ledger whose working tree is a git repository has `core.untrackedCache` set, once, by `qew migrate-layout`, `qew init`
and `qew ledger fork`; nothing runs on every command.

Each test is a scenario; its docstring names the failure it prevents. (git status of foundlings-ledger: 2 s, 0.5 s with the cache.)
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World, jj_available
from tests.test_init_walk import init


def setting(world: World) -> str:
    return subprocess.run(["git", "-C", str(world.ledger), "config", "--local", "--get", "core.untrackedCache"],
                          capture_output=True, text=True, check=False).stdout.strip()


def downgrade(world: World, version: int = 5) -> None:
    toml = world.ledger / "qew-ledger.toml"
    toml.write_text(toml.read_text().replace("schema_version = 6", f"schema_version = {version}"))


class UntrackedCacheWalk(unittest.TestCase):
    def test_migrating_a_schema_5_git_ledger_sets_the_cache_once(self) -> None:
        """Prevents a slow `git status` on a large ledger: the migration sets core.untrackedCache and declares schema 6."""
        world = World(ledger_vcs="git")
        self.addCleanup(world.close)
        world.create("--title", "Work", "--quote", QUOTE)
        downgrade(world)
        self.assertEqual(setting(world), "")
        preview = world.run("migrate-layout", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("schema_version 5 -> 6", preview.stdout)
        self.assertIn("untrackedCache", preview.stdout)
        self.assertEqual(setting(world), "", "a dry run changes nothing")
        done = world.run("migrate-layout")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(setting(world), "true")
        self.assertIn("schema_version = 6", (world.ledger / "qew-ledger.toml").read_text())
        again = world.run("migrate-layout")
        self.assertIn("already schema_version 6", again.stdout)
        self.assertEqual(world.run("check").returncode, 0)

    def test_a_schema_5_ledger_keeps_working_until_it_is_migrated(self) -> None:
        """Prevents stranding a fleet again: reads, claims and creates work on schema 5."""
        world = World(ledger_vcs="git")
        self.addCleanup(world.close)
        item = world.create("--title", "Work", "--quote", QUOTE)
        downgrade(world)
        for command in (("list",), ("start", item), ("create", "--title", "More", "--field", "phase=1")):
            self.assertEqual(world.run(*command).returncode, 0, command)

    @unittest.skipUnless(jj_available(), "jj is not installed")
    def test_a_colocated_jj_ledger_gets_it_too_and_a_ledger_without_git_none(self) -> None:
        """Prevents a failed migration: colocated jj has a .git (harmless, helps a hand-run git status); no .git means nothing to set."""
        colocated = World(ledger_vcs="jj")
        self.addCleanup(colocated.close)
        downgrade(colocated)
        self.assertEqual(colocated.run("migrate-layout").returncode, 0)
        self.assertEqual(setting(colocated), "true")
        plain = World()
        self.addCleanup(plain.close)
        downgrade(plain)
        done = plain.run("migrate-layout")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse((plain.ledger / ".git").exists())

    def test_a_new_ledger_is_created_with_it(self) -> None:
        """Prevents new ledgers starting slow: init in a git ledger directory sets the cache and writes schema 6."""
        world = World(ledger_vcs="git")
        self.addCleanup(world.close)
        fresh = world.base / "fresh-code"
        fresh.mkdir()
        subprocess.run(["git", "-C", str(fresh), "init", "-q", "-b", "main"], check=True)
        ledger = world.base / "fresh-ledger"
        ledger.mkdir()
        subprocess.run(["git", "-C", str(ledger), "init", "-q", "-b", "main"], check=True)
        result = init(fresh, "--ledger", "../fresh-ledger")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("schema_version = 6", (ledger / "qew-ledger.toml").read_text())
        value = subprocess.run(["git", "-C", str(ledger), "config", "--local", "--get", "core.untrackedCache"], capture_output=True, text=True)
        self.assertEqual(value.stdout.strip(), "true")


if __name__ == "__main__":
    unittest.main()
