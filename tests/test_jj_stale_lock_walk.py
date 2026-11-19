"""A jj ledger whose `.git/packed-refs.lock` a killed process left behind is named as a stale lock at once, with the command that removes it.

Adversary: jj's own wording ("Could not lock packed refs ... The lockfile at '...' might need manual deletion") matches no git pattern,
so every write waited out the whole repository-lock wait and failed with a generic message, or retried for minutes.
"""

import os
import time
import unittest
from pathlib import Path

from tests.ledger_harness import World, jj_available

APPROVAL = "Alexander: 'yes, remove that dead lock, nothing is running'"


@unittest.skipUnless(jj_available(), "jj is not installed")
class JjStaleLockWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="jj")
        self.addCleanup(self.world.close)
        self.world.create("--title", "first")
        self.lock = self.world.ledger / ".git" / "packed-refs.lock"

    def leave(self, seconds_old: float) -> None:
        self.lock.write_text("", encoding="utf-8")
        stamp = time.time() - seconds_old
        os.utime(self.lock, (stamp, stamp))

    def test_a_dead_packed_refs_lock_is_named_stale_at_once_with_the_remedy(self) -> None:
        """Prevents a write waiting out the whole repository-lock wait on a lock nobody holds, then failing with a generic message."""
        self.leave(4000)
        started = time.monotonic()
        run = self.world.run("create", "--title", "doomed", env={"QEW_REPO_LOCK_WAIT_S": "120"})
        self.assertLess(time.monotonic() - started, 30.0, "the write waited for a lock that is dead")
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertIn("packed-refs.lock", run.stderr)
        self.assertIn("stale", run.stderr)
        self.assertIn("qew unlock --stale", run.stderr)
        self.assertEqual(run.stdout.strip(), "", "an id was printed for a write that was rolled back")
        self.assertTrue(self.lock.exists(), "qew removed a lock without a human's words")

    def test_the_audited_removal_lets_writes_through_and_leaves_nothing_behind(self) -> None:
        """Prevents a rolled-back create leaving directories or a half item once the lock is cleared."""
        self.leave(4000)
        self.world.run("create", "--title", "doomed", env={"QEW_REPO_LOCK_WAIT_S": "120"})
        removed = self.world.run("unlock", "--stale", "--quote", APPROVAL)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.world.run("create", "--title", "after").returncode, 0)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_young_lock_is_waited_for_then_named_held(self) -> None:
        """Prevents a live process's lock (seconds old) being called dead."""
        self.leave(1)
        run = self.world.run("create", "--title", "waits", env={"QEW_REPO_LOCK_WAIT_S": "2"})
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertIn("still held", run.stderr)
        self.assertNotIn("looks stale", run.stderr)


if __name__ == "__main__":
    unittest.main()
