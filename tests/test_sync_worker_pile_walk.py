"""A detached sync worker that finds another worker running exits at once.

Adversary: workers that queue for the ledger write lock before they ever look at `sync.lock`, so one write per few seconds
piles up a dozen waiting processes (seen live: 12 detached syncs on an already swapping machine), and a spawn that starts a
second worker while the first has not taken its lock yet.
"""

import os
import time
import unittest

from qew import filelock, syncworker
from tests.ledger_harness import World


class SyncWorkerPileWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        self.world.create("--title", "something to sync")
        self.cache = self.world.ledger / ".qew"
        self.cache.mkdir(exist_ok=True)

    def test_a_losing_worker_exits_at_once_even_while_writers_hold_the_write_lock(self) -> None:
        """Prevents the pile-up: with the write lock busy and a worker running, a new worker exits without waiting for the queue."""
        with filelock.hold(self.cache / "sync.lock", verb="sync worker", wait_s=0):
            with filelock.hold(self.cache / "lock", verb="create", wait_s=0):
                started = time.monotonic()
                result = self.world.run("sync", env={"QEW_BACKGROUND": "1", "QEW_NO_FETCH": "1", "QEW_LOCK_WAIT_S": "30"}, timeout=25)
                waited = time.monotonic() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(waited, 10, f"the losing worker waited {waited:.0f}s for the write lock")
        self.assertNotIn("locked by", result.stderr)

    def test_the_spawn_check_holds_off_a_second_spawn_made_moments_after_the_first(self) -> None:
        """Prevents a burst of writes starting one worker each: a spawn only seconds old counts as the worker running."""
        (self.cache / "sync.spawned").write_text(str(time.time()), encoding="utf-8")
        self.assertTrue(syncworker.worker_running(self.world_config()))

    def world_config(self) -> object:
        from qew import ledger_config

        previous = os.getcwd()
        os.chdir(self.world.code)
        try:
            return ledger_config.load()
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
