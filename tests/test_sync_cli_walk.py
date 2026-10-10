"""Command walks of `qew sync` and `qew project ensure` that need no forge.

Adversary: sync pushing an invalid ledger to forges, a dry run that touches the
network or writes mirror files, and an unknown forge failing silently.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World

MIRRORS = """
[[mirror]]
forge = "forgejo"
repo = "https://git.example/o/r"
[[mirror]]
forge = "gitlab"
repo = "https://lab.example/g/r"
select = "kind:epic"
"""


class SyncCliWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(MIRRORS)
        self.addCleanup(self.world.close)

    def test_dry_run_plans_creates_per_target_and_writes_nothing(self) -> None:
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Epic", "--draft")
        w.create("--kind", "issue", "--title", "Issue", "--draft")
        result = w.run("sync", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(sum("create" in line for line in lines), 3, lines)
        self.assertFalse((w.ledger / "mirrors").exists())
        self.assertIn(epic, result.stdout)

    def test_dry_run_target_filter_and_unknown_target(self) -> None:
        w = self.world
        w.create("--kind", "epic", "--title", "Epic", "--draft")
        only = w.run("sync", "--dry-run", "--target", "gitlab")
        self.assertEqual(only.returncode, 0, only.stderr)
        self.assertNotIn("forgejo", only.stdout)
        missing = w.run("sync", "--dry-run", "--target", "nowhere")
        self.assertEqual(missing.returncode, 2)

    def test_sync_refuses_an_invalid_ledger_before_any_forge_call(self) -> None:
        w = self.world
        a = w.create("--kind", "epic", "--title", "A", "--draft")
        b = w.create("--kind", "epic", "--title", "B", "--needs", a, "--draft")
        path = w.ledger / "items" / f"{a}" / "item.md"  # `qew set` refuses a cycle; a hand merge can still produce one
        path.write_text(path.read_text().replace("needs: \n", f"needs: {b}\n", 1))
        for arguments in (["sync"], ["sync", "--dry-run"]):
            result = w.run(*arguments)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("qew check", result.stderr)

    def test_an_event_with_an_unsafe_actor_is_reported_but_never_blocks_a_sync(self) -> None:
        """Prevents one polluted event (a test artifact in a shared ledger) stopping all mirroring until someone repairs history."""
        import json

        w = self.world
        item = w.create("--kind", "issue", "--title", "Polluted", "--draft")
        (w.ledger / "events" / item / "ZZZZZZZZA.json").write_text(
            json.dumps({"type": "field", "key": "title", "old": "a", "new": "b", "at": "2026-01-01T00:00:00Z", "actor": "bad login,x"}))
        self.assertEqual(w.run("check").returncode, 1)
        self.assertEqual(w.run("sync", "--dry-run").returncode, 0)

    def test_sync_without_mirror_targets_says_so(self) -> None:
        w = self.world
        (w.code / ".qew" / "config.toml").write_text('[ledger]\npath = "../ledger"\nprefix = "TST"\n')
        result = w.run("sync")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no mirror targets", result.stdout)

    def test_an_unknown_forge_is_a_loud_error(self) -> None:
        w = self.world
        (w.code / ".qew" / "config.toml").write_text(
            '[ledger]\npath = "../ledger"\nprefix = "TST"\n[[mirror]]\nforge = "sourcehut"\nrepo = "https://x.example/o/r"\n'
        )
        w.create("--kind", "issue", "--title", "A", "--draft")
        result = w.run("sync")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown forge", result.stderr)


if __name__ == "__main__":
    unittest.main()
