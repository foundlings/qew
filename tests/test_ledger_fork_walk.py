"""`qew ledger fork`: a new ledger identity that keeps the work and leaves the authority behind.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import subprocess
import sys
import tomllib
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import PROJECT_ROOT, QUOTE, World, git


class LedgerForkWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git", remote=True, replication="\n[replication]\nauto_forge_sync = false\nfetch_interval_s = 0\n")
        self.addCleanup(self.world.close)
        self.held = self.world.create("--title", "Held work", "--quote", QUOTE)
        self.done = self.world.create("--title", "Dropped work", "--quote", QUOTE)
        self.open = self.world.create("--title", "Still a draft")
        self.assertEqual(self.world.run("start", self.held).returncode, 0)
        self.assertEqual(self.world.run("close", self.done, "--reason", "not_planned").returncode, 0)
        ledger = self.world.ledger
        (ledger / "mirrors" / self.held).mkdir(parents=True)
        (ledger / "mirrors" / self.held / "forgejo-x.json").write_text(json.dumps({"forge": "forgejo", "url": "https://x/o/r", "kind": "issue", "number": 1}))
        (ledger / "boards").mkdir()
        (ledger / "boards" / "forgejo-x.json").write_text(json.dumps({"id": "1"}))
        divergence = {"type": "forge_divergence", "at": "2026-01-01T00:00:00Z", "actor": "x", "target": "forgejo-x", "field": "state", "forge": "closed", "ledger": "open"}
        (ledger / "events" / self.held / "zzzzzzzzz.json").write_text(json.dumps(divergence))
        self.fork_remote = self.world.base / "fork.git"
        git(self.world.base, "init", "-q", "--bare", "-b", "main", str(self.fork_remote))

    def fork(self, *extra: str):
        return self.world.run("ledger", "fork", "--to", str(self.fork_remote), *extra)

    def clone(self) -> World:
        target = self.world.base / "fork-clone"
        git(self.world.base, "clone", "-q", str(self.fork_remote), str(target))
        return target

    def in_fork(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "qew.cli", *arguments], capture_output=True, text=True, check=False,
                              env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "QEW_LEDGER": str(self.world.base / "fork-clone"), "QEW_OFFLINE": "1"})

    def test_the_fork_keeps_items_and_history_and_drops_mirrors_claims_and_divergences(self) -> None:
        """Prevents a fork inheriting the origin's authority: same items and ids, no mirrors, boards, owner or open divergence, lineage recorded."""
        result = self.fork()
        self.assertEqual(result.returncode, 0, result.stderr)
        clone = self.clone()
        self.assertEqual(sorted(p.name for p in (clone / "items").iterdir()), sorted(p.name for p in (self.world.ledger / "items").iterdir()))
        self.assertFalse((clone / "mirrors").exists())
        self.assertFalse((clone / "boards").exists())
        meta = tomllib.loads((clone / "qew-ledger.toml").read_text())
        origin_head = git(self.world.ledger, "rev-parse", "main")
        self.assertEqual(meta["forked_from"], f"test@{origin_head}")
        self.assertTrue(meta["ledger_id"])
        listed = self.in_fork("list").stdout
        self.assertIn(self.held, listed)
        shown = self.in_fork("show", self.held).stdout
        self.assertIn("state: approved", shown)
        self.assertNotIn("owner:", shown)
        self.assertEqual(self.in_fork("check").returncode, 0)
        self.assertIn("state: closed", self.in_fork("show", self.done).stdout)
        self.assertEqual(len(self.world.events(self.done)), len(list((clone / "events" / self.done).iterdir())))

    def test_merging_the_fork_back_into_the_origin_is_refused(self) -> None:
        """Prevents a fork flowing back: an origin pointed at the fork's remote refuses to merge and stays as it was."""
        self.assertEqual(self.fork().returncode, 0)
        before = git(self.world.ledger, "rev-parse", "main")
        git(self.world.ledger, "remote", "set-url", "origin", str(self.fork_remote))
        result = self.world.run("list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fork", result.stderr)
        self.assertEqual(git(self.world.ledger, "rev-parse", "main"), before)

    def test_primary_names_the_fresh_target_and_a_bad_one_is_refused(self) -> None:
        """Prevents a fork silently reusing the old authority: --primary prints the config for the new target, junk is refused."""
        result = self.fork("--primary", "forgejo-new.example-o-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('primary = "forgejo-new.example-o-r"', result.stdout)
        self.assertEqual(self.fork("--primary", "bad target").returncode, 2)


if __name__ == "__main__":
    unittest.main()
