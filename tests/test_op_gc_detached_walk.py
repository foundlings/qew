"""The jj operation log is collected by a detached collector in short steps, never inside a writer's lock for minutes.

Adversary: a write that waits for or runs the collection itself, ten writers queued behind one slow `jj op abandon` or
`jj util gc`, a collector that holds the write lock for a whole log, two collectors at once, and no way to collect on demand.
"""

import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.ledger_harness import World, jj, jj_available
from tests.test_op_log_gc_walk import KEEP, operations

SHIM = """#!/bin/sh
echo "$*" >> "$SHIM_LOG"
case "$*" in
  *"util gc"*) sleep "${SLOW_GC_S:-0}" ;;
esac
exec "$REAL_JJ" "$@"
"""


def real_jj() -> str:
    return subprocess.run(["which", "jj"], capture_output=True, text=True, check=True).stdout.strip()


@unittest.skipUnless(jj_available(), "jj is not installed")
class DetachedCollectionWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="jj", replication=KEEP + "op_log_max_hold_s = 0.1\n")
        self.addCleanup(self.world.close)
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        shim = Path(self.scratch.name) / "jj"
        shim.write_text(SHIM, encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        self.log = Path(self.scratch.name) / "jj.log"
        self.env = {"PATH": f"{self.scratch.name}{os.pathsep}{os.environ['PATH']}", "SHIM_LOG": str(self.log), "REAL_JJ": real_jj(), "SLOW_GC_S": "6"}
        for number in range(5):
            self.world.create("--title", f"item {number}")

    def ripe(self) -> None:
        """Old operations exist and the daily marker is gone, so the next write starts a collection."""
        time.sleep(2.2)
        (self.world.ledger / ".qew" / "last-gc").unlink(missing_ok=True)
        self.log.unlink(missing_ok=True)

    def calls(self, word: str) -> int:
        return sum(1 for line in self.log.read_text(encoding="utf-8").splitlines() if word in line) if self.log.exists() else 0

    def wait_for(self, word: str, seconds: float = 30.0) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.calls(word):
            time.sleep(0.1)
        self.assertTrue(self.calls(word), f"the collector never ran `{word}`")

    def test_a_write_does_not_run_or_wait_for_the_collection(self) -> None:
        """Prevents a writer sitting minutes in the op-log collection it triggered: a slow gc must not slow `qew create`."""
        self.ripe()
        started = time.monotonic()
        run = self.world.run("create", "--title", "fresh", env=self.env)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertLess(time.monotonic() - started, 4.0, "the write waited for the collection")

    def test_writers_proceed_while_the_collector_runs_a_slow_gc(self) -> None:
        """Prevents ten queued writers behind one slow `jj util gc`: another write goes through while the collector sits in it."""
        self.ripe()
        trigger = subprocess.Popen([sys.executable, "-m", "qew.cli", "create", "--title", "trigger"], cwd=self.world.code, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env={**os.environ, **self.env, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                                                                   "QEW_ACTOR": "tester", "QEW_UNDER_TEST": "1"})
        self.addCleanup(trigger.wait)
        self.wait_for("util gc")
        started = time.monotonic()
        run = self.world.run("create", "--title", "during", env={**self.env, "QEW_LOCK_WAIT_S": "2"})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertLess(time.monotonic() - started, 4.0, "a write queued behind the collection")

    def test_the_hold_budget_bounds_how_many_recent_operations_are_kept(self) -> None:
        """Prevents one `op abandon` rewriting a whole 14 day log under the write lock (its time grows with the operations kept)."""
        wide = World(ledger_vcs="jj", replication=KEEP.replace("0.00002", "14") + "op_log_max_hold_s = 0.1\n")
        self.addCleanup(wide.close)
        for number in range(20):
            wide.create("--title", f"busy {number}")
        before = operations(wide)
        self.assertGreater(len(before), 40)
        (wide.ledger / ".qew" / "last-gc").unlink(missing_ok=True)
        (wide.ledger / ".qew" / "gc-done").unlink(missing_ok=True)  # the setup writes ran a collection that found nothing old
        self.assertEqual(wide.run("create", "--title", "trigger", env={**self.env, "SLOW_GC_S": "0"}).returncode, 0)
        end = time.monotonic() + 30
        while time.monotonic() < end and not (wide.ledger / ".qew" / "gc-done").exists():
            time.sleep(0.1)
        after = operations(wide)
        self.assertLess(len(after), 25, "the budget kept the whole window")
        self.assertFalse(set(before[len(after):]) & set(after), "an old operation survived the cut")
        self.assertTrue((wide.ledger / ".qew" / "gc-rate").is_file(), "the measured rate was not kept for the next budget")
        self.assertEqual(wide.run("check").returncode, 0)

    def test_only_one_collector_runs_at_a_time(self) -> None:
        """Prevents a second writer starting a second collector while one is in `util gc`."""
        self.ripe()
        self.world.run("create", "--title", "one", env=self.env)
        self.wait_for("util gc")
        (self.world.ledger / ".qew" / "last-gc").unlink(missing_ok=True)
        self.world.run("create", "--title", "two", env=self.env)
        time.sleep(1.5)
        self.assertEqual(self.calls("util gc"), 1)

    def test_gc_now_collects_in_the_foreground(self) -> None:
        """Prevents having to wait a day (or edit a marker) to collect on demand, and a verb that returns before it is done."""
        before = operations(self.world)
        time.sleep(2.2)
        run = self.world.run("gc", "--now")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("abandoned", run.stdout)
        self.assertFalse(set(before) & set(operations(self.world)))
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_the_lock_message_names_holder_age_and_the_collection(self) -> None:
        """Prevents a stalled writer not knowing whether it waits for a person, a sync or the op-log collection."""
        from qew import filelock

        ledger_lock = self.world.ledger / ".qew" / "lock"
        with filelock.hold(ledger_lock, verb="gc", who="tester", wait_s=0, what="the ledger"):
            run = self.world.run("create", "--title", "blocked", env={"QEW_LOCK_WAIT_S": "0.3"})
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertIn("`gc`", run.stderr)
        self.assertIn("pid", run.stderr)
        self.assertIn("op-log collection", run.stderr)

    def test_reads_do_not_wait_for_the_write_lock(self) -> None:
        """Prevents `qew search`, `show` or `ready` queueing behind a long write: reads run on what is on disk."""
        from qew import filelock

        item = self.world.create("--title", "findable thing")
        ledger_lock = self.world.ledger / ".qew" / "lock"
        with filelock.hold(ledger_lock, verb="evidence", who="tester", wait_s=0, what="the ledger"):
            started = time.monotonic()
            for argv in (("search", "findable"), ("show", item), ("ready",), ("check",)):
                run = self.world.run(*argv, env={"QEW_LOCK_WAIT_S": "0.3"})
                self.assertEqual(run.returncode, 0, f"{argv}: {run.stderr}")
            self.assertLess(time.monotonic() - started, 15.0)


    def test_a_loose_object_that_gc_pruned_under_a_writer_is_retried(self) -> None:
        """Prevents a write rolled back by `jj util gc` pruning an object directory at the moment jj persists a new object."""
        flag = Path(self.scratch.name) / "failed-once"
        shim = Path(self.scratch.name) / "jj"
        shim.write_text(SHIM.replace('exec "$REAL_JJ" "$@"', f'''case "$*" in
  *commit*) if [ ! -e "{flag}" ]; then touch "{flag}"; printf 'Internal error: Failed to snapshot the working copy\\nCaused by:\\n1: Could not write object of type tree\\n2: Could not persist loose object, "path"="x"\\n' >&2; exit 255; fi ;;
esac
exec "$REAL_JJ" "$@"'''), encoding="utf-8")
        run = self.world.run("create", "--title", "retried", env=self.env)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(flag.exists(), "the failure was never injected")
        self.assertEqual(self.world.run("check").returncode, 0)

if __name__ == "__main__":
    unittest.main()
