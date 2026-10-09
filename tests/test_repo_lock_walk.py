"""A crashed git leaves .git/index.lock behind; qew waits, names it, and never deletes it behind your back."""

import os
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


def make_old(path: Path, seconds: int) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


class RepoLockWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World(ledger_vcs="git")
        self.addCleanup(self.w.close)
        self.lock = self.w.ledger / ".git" / "index.lock"

    def test_a_lock_that_goes_away_is_waited_out(self) -> None:
        """Prevents a failed write because another agent's git commit held index.lock for a moment."""
        self.lock.write_text("", encoding="utf-8")
        threading.Timer(1.5, lambda: self.lock.unlink()).start()
        started = time.monotonic()
        result = self.w.run("create", "--title", "Waits", "--quote", QUOTE, env={"QEW_REPO_LOCK_WAIT_S": "20"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(time.monotonic() - started, 1.0)
        self.assertEqual(self.w.ledger_dirty(), [])

    def test_a_stale_lock_is_named_and_left_in_place(self) -> None:
        """Prevents silent deletion and anonymous failure: the error names the file, its age and says qew did not remove it."""
        self.lock.write_text("", encoding="utf-8")
        make_old(self.lock, 3600)
        result = self.w.run("create", "--title", "Blocked", "--quote", QUOTE, env={"QEW_REPO_LOCK_WAIT_S": "2"})
        self.assertEqual(result.returncode, 3)
        self.assertIn("index.lock", result.stderr)
        self.assertIn("stale", result.stderr)
        self.assertIn("not removed", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(self.lock.exists(), "qew never deletes a repository lock itself")

    def test_the_write_that_could_not_commit_leaves_nothing_half_written(self) -> None:
        """Prevents a refused commit leaving the item file behind: the session rolls back and the next writer is clean."""
        self.lock.write_text("", encoding="utf-8")
        make_old(self.lock, 3600)
        self.w.run("create", "--title", "Blocked", "--quote", QUOTE, env={"QEW_REPO_LOCK_WAIT_S": "1"})
        self.lock.unlink()
        self.assertEqual(self.w.run("create", "--title", "After", "--quote", QUOTE).returncode, 0)
        self.assertEqual(self.w.ledger_dirty(), [])
        self.assertEqual(len(list((self.w.ledger / "items").glob("*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
