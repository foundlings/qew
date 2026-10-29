"""Detached background sync after every write: token-free, locked, offline-safe.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import World

ENV = {"FORGEJO_TOKEN": TOKEN}


def wait_for(condition, seconds: float = 40.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


class BackgroundSyncWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'

    def world(self, replication: str = "\n[replication]\nbackground_sync = true\n", vcs: str = "git") -> World:
        w = World(self.mirrors, ledger_vcs=vcs, replication=replication)
        self.addCleanup(w.close)
        return w

    def test_a_write_returns_before_the_sync_finishes_and_prints_one_short_line(self) -> None:
        """Prevents agents waiting on forges or reading sync chatter: the command returns at once with its one result line and no sync banner."""
        self.fake.delay = 1.0
        w = self.world()
        result = w.run("create", "--title", "Live", "--draft", env=ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(self.fake.issues, {})
        self.assertTrue(wait_for(lambda: len(self.fake.issues) == 1), "the background worker never created the issue")

    def test_concurrent_writes_never_double_sync(self) -> None:
        """Prevents duplicate forge issues when several writes each start a worker (one lock, one issue per item), and the opposite
        loss: a worker that finds the lock held exits, so the worker holding it must look at the queue again after it lets go, or the
        last write of a burst is never synced."""
        w = self.world()
        for n in range(6):
            w.run("create", "--title", f"Burst {n}", "--draft", env=ENV)
        self.assertTrue(wait_for(lambda: len(self.fake.issues) == 6), f"{len(self.fake.issues)} of 6 items synced")
        time.sleep(1.0)
        self.assertEqual(len(self.fake.issues), 6)
        self.assertEqual(len(list((w.ledger / "mirrors").glob("*/*.json"))), 6)
        self.assertFalse((w.ledger / ".qew" / "sync.queue").exists() and (w.ledger / ".qew" / "sync.queue").read_text().strip(), "an id was left queued")

    def test_a_failing_forge_leaves_pending_work_that_the_next_command_clears(self) -> None:
        """Prevents a forge outage losing updates: the backlog persists and a later command syncs it."""
        w = self.world("\n[replication]\nsync_retry_s = 0\nfetch_interval_s = 0\n")
        self.fake.down = True
        w.run("create", "--title", "Outage", "--draft", env=ENV)
        self.assertTrue(wait_for(lambda: (w.ledger / ".qew" / "sync.pending").exists()))
        self.assertEqual(self.fake.issues, {})
        self.fake.down = False
        w.run("ready", env=ENV)
        self.assertTrue(wait_for(lambda: len(self.fake.issues) == 1), "the next command did not retry")

    def test_offline_mode_and_the_switch_spawn_nothing(self) -> None:
        """Prevents surprise network traffic: QEW_OFFLINE and background_sync = false mean no worker."""
        w = self.world("\n[replication]\nbackground_sync = false\nauto_forge_sync = false\n")
        result = w.run("create", "--title", "Quiet", "--draft", env=ENV)
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        off = self.world()
        quiet = off.run("create", "--title", "Quiet", "--draft", env={**ENV, "QEW_OFFLINE": "1"})
        self.assertEqual(len(quiet.stdout.strip().splitlines()), 1)
        time.sleep(1.0)
        self.assertEqual(self.fake.issues, {})

    def test_without_the_background_worker_the_sync_runs_inline_within_the_write(self) -> None:
        """Prevents auto_forge_sync meaning nothing when the worker is off: the inline path syncs before returning."""
        w = self.world("\n[replication]\nbackground_sync = false\n")
        w.run("create", "--title", "Inline", "--draft", env=ENV)
        self.assertEqual(len(self.fake.issues), 1)

    def test_the_worker_writes_a_log_and_its_mirrors_are_committed(self) -> None:
        """Prevents mirror files stuck uncommitted after a background run and a log nobody can read."""
        w = self.world()
        w.run("create", "--title", "Logged", "--draft", env=ENV)
        self.assertTrue(wait_for(lambda: len(self.fake.issues) == 1))
        self.assertTrue(wait_for(lambda: any(s.startswith("qew: sync") for s in w.ledger_log())))
        self.assertTrue((w.ledger / ".qew" / "sync.log").exists())
        self.assertEqual(w.ledger_dirty(), [])


if __name__ == "__main__":
    unittest.main()
