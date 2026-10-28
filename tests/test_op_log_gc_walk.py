"""The jj operation log of a ledger is collected automatically by a qew write.

Adversary: an op log that grows without bound (a foundlings ledger reached 2.2 GB), a collection that needs a manual step, one
that runs on every sync, one that removes operations younger than the keep window, and a git ledger being asked to run jj.
"""

import subprocess
import time
import unittest

from tests.ledger_harness import World, jj, jj_available

KEEP = "\n[replication]\nauto_forge_sync = false\nforge_concurrency = 1\nop_log_keep_days = 0.00002\n"  # 1.7 seconds


def operations(world: World) -> list[str]:
    out = jj(world.ledger, "--ignore-working-copy", "--no-pager", "op", "log", "--no-graph", "-T", 'id.short(12) ++ "\\n"')
    return [op for op in out.split() if op != "000000000000"]  # the root operation always stays


@unittest.skipUnless(jj_available(), "jj is not installed")
class OpLogGcWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="jj", replication=KEEP)
        self.addCleanup(self.world.close)
        for number in range(3):
            self.world.create("--title", f"item {number}")

    def test_sync_drops_operations_older_than_the_window_and_keeps_the_recent_ones(self) -> None:
        """Prevents an unbounded op log: after the window passes, one sync abandons the old operations, no manual step."""
        before = operations(self.world)
        time.sleep(2.2)
        recent = self.world.create("--title", "fresh")  # a write: collecting is part of the ordinary command, no verb to remember
        after = operations(self.world)
        self.assertLess(len(after), len(before) + 6)
        self.assertFalse(set(before) & set(after), "operations older than the window survived")
        self.assertTrue((self.world.ledger / ".qew" / "last-gc").is_file())
        self.assertEqual(self.world.run("show", recent).returncode, 0)

    def test_the_collection_runs_at_most_once_a_day(self) -> None:
        """Prevents a scan of the whole op log on every write: the marker holds the next run off."""
        time.sleep(2.2)
        self.world.create("--title", "first")
        mid = operations(self.world)
        time.sleep(2.2)
        self.world.create("--title", "later")
        self.assertTrue(set(mid) <= set(operations(self.world)), "a second write within a day collected again")

    def test_zero_turns_the_collection_off_and_a_git_ledger_is_left_alone(self) -> None:
        """Prevents a surprise: keep_days = 0 keeps everything, and a git ledger never runs jj."""
        off = World(ledger_vcs="jj", replication=KEEP.replace("0.00002", "0"))
        self.addCleanup(off.close)
        off.create("--title", "x")
        before = operations(off)
        time.sleep(2.2)
        off.create("--title", "y")
        self.assertTrue(set(before) <= set(operations(off)))
        plain = World(ledger_vcs="git", replication=KEEP)
        self.addCleanup(plain.close)
        plain.create("--title", "z")
        time.sleep(2.2)
        plain.create("--title", "w")
        self.assertFalse((plain.ledger / ".qew" / "last-gc").exists())


if __name__ == "__main__":
    unittest.main()
