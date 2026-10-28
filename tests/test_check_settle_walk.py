"""`qew check` does not report another process's write in flight as a ledger problem.

Adversary (seen on foundlings): the check reads without the ledger lock, so a writer or checkout caught half-way showed as
`events/ID: belongs to no item`; `qew sync` ran the same check and refused with "the ledger has 3 problem(s)", which were gone
at the next look. The re-scan must never take the lock, so it cannot deadlock.
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_check
from qew.ledger_store import Ledger
from tests.ledger_harness import World
from tests.test_filelock_walk import start_holder


class CheckSettleWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.item = self.world.run("create", "--title", "Subject", "--draft").stdout.split()[0]
        self.ledger = Ledger(self.world.ledger, "TST")
        self.item_file = self.world.home("items", self.item) / "item.md"
        self.aside = self.world.ledger / ".qew" / "aside.md"
        self.aside.parent.mkdir(parents=True, exist_ok=True)
        self.item_file.rename(self.aside)  # the event stays, its item is missing: what a reader sees mid-write
        self.lock = self.world.ledger / ".qew" / "lock"
        self.lock.parent.mkdir(parents=True, exist_ok=True)

    def orphans(self) -> list[str]:
        return [p for p in ledger_check.check(self.ledger, deep=False) if "belongs to no item" in p]

    def test_an_orphan_event_that_resolves_while_a_writer_holds_the_lock_is_not_reported(self) -> None:
        """Prevents a write in flight failing `qew check` and `qew sync`: the writer finishes, the item appears, the check is clean."""
        holder = start_holder(self.lock)
        self.addCleanup(holder.kill)

        def finish() -> None:
            time.sleep(1.0)
            self.aside.rename(self.item_file)
            holder.kill()

        thread = threading.Thread(target=finish)
        thread.start()
        self.addCleanup(thread.join)
        self.assertEqual(self.orphans(), [])

    def test_an_orphan_event_that_stays_is_reported_after_the_bound(self) -> None:
        """Prevents hiding a real orphan behind a long writer: the bound ends the wait and the problem is reported."""
        holder = start_holder(self.lock)
        self.addCleanup(holder.kill)
        original = ledger_check.SETTLE_S
        ledger_check.SETTLE_S = 1.0
        self.addCleanup(setattr, ledger_check, "SETTLE_S", original)
        started = time.monotonic()
        self.assertEqual(len(self.orphans()), 1)
        self.assertLess(time.monotonic() - started, 8.0)

    def test_an_orphan_event_with_no_writer_is_reported_at_once(self) -> None:
        """Prevents a delay on every real problem: with the lock free there is nothing to wait for."""
        started = time.monotonic()
        self.assertEqual(len(self.orphans()), 1)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_the_checking_process_holding_the_lock_itself_gets_its_problems_at_once(self) -> None:
        """Prevents a writer waiting out the bound on its own lock: only another process's write is waited for."""
        from qew import filelock

        started = time.monotonic()
        with filelock.hold(self.lock, verb="create", wait_s=5):
            self.assertEqual(len(self.orphans()), 1)
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
