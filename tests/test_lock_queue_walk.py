"""The ledger lock serves waiters in arrival order, the wait is settable, and the queue can never deadlock.

Adversary: waiters polling a kernel lock win by luck, so an unlucky agent times out again and again behind busier ones; and
the fix itself must not turn into a lock-out (a dead, stopped or forever-fresh waiter ahead in the queue blocking everyone).
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import filelock
from tests.ledger_harness import World
from tests.test_filelock_walk import start_holder


def marker(path: Path, pid: int, arrival_ns: int, thread: int = 1) -> Path:
    found = path.with_name(f"{path.name}.waiting.{pid}.{thread}.{arrival_ns}")
    found.touch()
    return found


class LockQueueWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "lock"

    def test_waiters_take_the_lock_in_arrival_order(self) -> None:
        """Prevents starvation by luck: four waiters arrive 0.3s apart behind a holder and are served first come, first served."""
        holder = start_holder(self.path)
        self.addCleanup(holder.kill)
        order: list[int] = []

        def wait(number: int) -> None:
            with filelock.hold(self.path, verb=f"w{number}", wait_s=30):
                order.append(number)
                time.sleep(0.05)

        threads = []
        for number in range(4):
            thread = threading.Thread(target=wait, args=(number,))
            thread.start()
            threads.append(thread)
            time.sleep(0.3)
        holder.kill()
        holder.wait()
        for thread in threads:
            thread.join(30)
        self.assertEqual(order, [0, 1, 2, 3])

    def test_the_marker_of_a_dead_waiter_ahead_blocks_nobody(self) -> None:
        """Prevents a crashed waiter locking everyone out: its marker names a pid that is gone, so it is not in the queue."""
        gone = start_holder(Path(self.directory.name) / "other")
        gone.kill()
        gone.wait()
        marker(self.path, gone.pid, 1)
        started = time.monotonic()
        with filelock.hold(self.path, verb="create", wait_s=5):
            pass
        self.assertLess(time.monotonic() - started, 1.0)

    def test_the_marker_of_a_stopped_waiter_ahead_blocks_nobody(self) -> None:
        """Prevents a frozen waiter (alive, but no longer touching its marker) holding the queue: a stale marker is skipped."""
        stale = marker(self.path, os.getpid(), 1, thread=2)
        old = time.time() - 60
        os.utime(stale, (old, old))
        started = time.monotonic()
        with filelock.hold(self.path, verb="create", wait_s=5):
            pass
        self.assertLess(time.monotonic() - started, 1.0)

    def test_a_waiter_ahead_that_stays_fresh_forever_is_waited_for_only_up_to_the_cap(self) -> None:
        """Prevents the deadlock the queue could cause: a live waiter ahead that never takes the lock delays this one by at most
        DEFER_MAX_S, then the kernel lock is tried regardless."""
        ahead = marker(self.path, os.getpid(), 1, thread=2)
        stop = threading.Event()

        def keep_fresh() -> None:
            while not stop.is_set():
                ahead.touch()
                time.sleep(0.1)

        thread = threading.Thread(target=keep_fresh)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(stop.set)
        original = filelock.DEFER_MAX_S
        filelock.DEFER_MAX_S = 1.0
        self.addCleanup(setattr, filelock, "DEFER_MAX_S", original)
        started = time.monotonic()
        with filelock.hold(self.path, verb="create", wait_s=10):
            pass
        waited = time.monotonic() - started
        self.assertGreaterEqual(waited, 0.9)
        self.assertLess(waited, 4.0)

    def test_a_timeout_says_how_many_waiters_were_ahead(self) -> None:
        """Prevents an agent retrying blind: the timeout names the queue it stood in."""
        holder = start_holder(self.path)
        self.addCleanup(holder.kill)
        ahead = marker(self.path, os.getpid(), 1, thread=2)
        self.addCleanup(ahead.unlink, missing_ok=True)
        with self.assertRaises(filelock.LockTimeout) as caught:
            with filelock.hold(self.path, verb="create", wait_s=0.6):
                pass
        self.assertIn("1 waiting ahead", str(caught.exception))


class LockWaitSettingWalk(unittest.TestCase):
    def held_ledger(self, replication: str) -> tuple[World, object]:
        world = World(replication=replication)
        self.addCleanup(world.close)
        path = world.ledger / ".qew" / "lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        holder = start_holder(path)
        self.addCleanup(holder.kill)
        return world, holder

    def test_lock_wait_s_in_the_config_sets_how_long_a_verb_waits(self) -> None:
        """Prevents the fixed 60 seconds: a verb behind a long holder gives up after the configured wait and says so."""
        world, _ = self.held_ledger("\n[replication]\nlock_wait_s = 1\n")
        started = time.monotonic()
        run = world.run("create", "--title", "Behind the lock", "--draft")
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertIn("waited 1s", run.stderr)
        self.assertLess(time.monotonic() - started, 20)

    def test_the_environment_overrides_the_configured_wait(self) -> None:
        """Prevents an agent being unable to wait longer for one command: QEW_LOCK_WAIT_S wins over the config."""
        world, _ = self.held_ledger("\n[replication]\nlock_wait_s = 300\n")
        started = time.monotonic()
        run = world.run("create", "--title", "Behind the lock", "--draft", env={"QEW_LOCK_WAIT_S": "1"})
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertIn("waited 1s", run.stderr)
        self.assertLess(time.monotonic() - started, 20)


if __name__ == "__main__":
    unittest.main()
