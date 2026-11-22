"""`QEW_TRACE=1` names every jj or git call a ledger write makes, with its wall time.

Adversary: a slow ledger write (a 100 s fetch and merge on a big ledger) that the trace reports as one opaque phase, so nobody
can tell a snapshot from an op-log replay from a network fetch.
"""

import os
import re
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import filelock
from tests.ledger_harness import World


class TraceCommandsWalk(unittest.TestCase):
    def test_a_traced_write_names_its_jj_commit_with_a_duration(self) -> None:
        """Prevents a slow write being undiagnosable: the commit appears as `run jj ... commit` with its seconds."""
        world = World("", ledger_vcs="jj")
        self.addCleanup(world.close)
        run = world.run("create", "--title", "traced", "--draft", env={"QEW_TRACE": "1"})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertRegex(run.stderr, re.compile(r"trace\s+[\d.]+s \+\s*[\d.]+s run jj .*commit"))
        quiet = world.run("create", "--title", "untraced", "--draft")
        self.assertNotIn("trace ", quiet.stderr)

    def test_a_traced_write_names_the_lock_wait_and_its_holder(self) -> None:
        """Prevents seconds spent queueing for a lock showing up as untraced gaps between jj calls: the wait is a line of its own."""
        world = World("", ledger_vcs="jj")
        self.addCleanup(world.close)
        lock = world.ledger / ".qew" / "lock"
        taken, release = threading.Event(), threading.Event()

        def hold() -> None:
            with filelock.hold(lock, verb="evidence", who="other", wait_s=0, what="the ledger"):
                taken.set()
                release.wait(10)

        holder = threading.Thread(target=hold)
        holder.start()
        self.addCleanup(holder.join)
        self.assertTrue(taken.wait(5))
        threading.Timer(1.0, release.set).start()
        run = world.run("create", "--title", "queued", "--draft", env={"QEW_TRACE": "1", "QEW_LOCK_WAIT_S": "20"})
        self.assertEqual(run.returncode, 0, run.stderr)
        waited = re.search(r"\+\s*([\d.]+)s lock lock wait .*evidence", run.stderr)
        self.assertIsNotNone(waited, run.stderr)
        self.assertGreaterEqual(float(waited.group(1)), 0.5)


if __name__ == "__main__":
    unittest.main()
