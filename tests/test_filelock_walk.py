"""The one lock module: kernel-released locks, so a dead holder never leaves anything to clean up."""

import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

SRC = Path(__file__).resolve().parents[1] / "src" / "qew"
HOLDER = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "from qew import filelock\n"
    "with filelock.hold(Path(sys.argv[1]), verb='complete', who='holder-actor'):\n"
    "    print('held', flush=True)\n"
    "    time.sleep(60)\n"
)


def start_holder(path: Path) -> subprocess.Popen:
    process = subprocess.Popen([sys.executable, "-c", HOLDER, str(path)], stdout=subprocess.PIPE, text=True,
                               env={**os.environ, "PYTHONPATH": str(SRC.parent)})
    assert process.stdout.readline().strip() == "held"
    return process


class FileLockWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "lock"

    def test_a_killed_holder_leaves_a_lock_file_that_blocks_nobody(self) -> None:
        """Prevents a stale lock after kill -9: the next writer proceeds at once and nothing was cleaned up by hand."""
        from qew import filelock
        holder = start_holder(self.path)
        holder.send_signal(signal.SIGKILL)
        holder.wait()
        self.assertTrue(self.path.exists(), "the lock file stays: it is inert")
        started = time.monotonic()
        with filelock.hold(self.path, verb="create", wait_s=5):
            pass
        self.assertLess(time.monotonic() - started, 2.0)

    def test_a_live_holder_is_named_when_the_wait_runs_out(self) -> None:
        """Prevents an anonymous 'locked' error: the message names the holder's actor, verb, pid and age."""
        from qew import filelock
        holder = start_holder(self.path)
        self.addCleanup(holder.kill)
        started = time.monotonic()
        with self.assertRaises(filelock.LockTimeout) as caught:
            with filelock.hold(self.path, verb="create", wait_s=0.6):
                pass
        message = str(caught.exception)
        self.assertGreaterEqual(time.monotonic() - started, 0.5)
        self.assertIn("holder-actor", message)
        self.assertIn("`complete`", message)
        self.assertIn(f"pid {holder.pid}", message)
        self.assertEqual(caught.exception.exit_code, 3)

    def test_holder_text_is_never_trusted_for_liveness(self) -> None:
        """Prevents a leftover pid file acting as a lock: a file naming a live pid but held by nobody is free."""
        from qew import filelock
        self.path.write_text(f'{{"pid": {os.getpid()}, "verb": "complete", "who": "ghost", "since": 1}}\n', encoding="utf-8")
        with filelock.hold(self.path, verb="create", wait_s=1):
            pass
        self.assertFalse(filelock.is_held(self.path))

    def test_is_held_sees_a_live_holder_without_taking_the_lock(self) -> None:
        """Prevents the worker probe blocking or stealing: is_held reports a live holder and releases at once."""
        from qew import filelock
        holder = start_holder(self.path)
        self.addCleanup(holder.kill)
        self.assertTrue(filelock.is_held(self.path))
        holder.kill()
        holder.wait()
        self.assertFalse(filelock.is_held(self.path))

    def test_a_second_hold_in_one_process_waits_instead_of_deadlocking_silently(self) -> None:
        """Prevents re-entrance hiding a bug: a nested hold on the same path times out loudly."""
        from qew import filelock
        with filelock.hold(self.path, verb="outer", wait_s=1):
            with self.assertRaises(filelock.LockTimeout):
                with filelock.hold(self.path, verb="inner", wait_s=0.3):
                    pass

    def test_the_windows_branch_locks_a_byte_past_the_text_and_maps_busy_to_a_wait(self) -> None:
        """Prevents the Windows path being untested: with msvcrt standing in, a busy byte lock is retried, then acquired."""
        script = (
            "import sys, types\n"
            "from pathlib import Path\n"
            "calls = []\n"
            "fake = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0)\n"
            "state = {'busy': 2}\n"
            "def locking(fd, mode, size):\n"
            "    calls.append((mode, size))\n"
            "    if mode == fake.LK_NBLCK and state['busy'] > 0:\n"
            "        state['busy'] -= 1\n"
            "        raise OSError(13, 'busy')\n"
            "fake.locking = locking\n"
            "sys.modules['msvcrt'] = fake\n"
            "sys.modules['fcntl'] = None\n"
            "from qew import filelock\n"
            "assert filelock.BACKEND == 'msvcrt', filelock.BACKEND\n"
            "with filelock.hold(Path(sys.argv[1]), verb='create', wait_s=5):\n"
            "    pass\n"
            "assert [m for m, _ in calls].count(fake.LK_NBLCK) == 3, calls\n"
            "assert calls[-1][0] == fake.LK_UNLCK, calls\n"
            "print('ok')\n"
        )
        result = subprocess.run([sys.executable, "-c", script, str(self.path)], capture_output=True, text=True,
                                env={**os.environ, "PYTHONPATH": str(SRC.parent)})
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "ok"), result.stderr)


class RemoteRefsWalk(unittest.TestCase):
    def test_fetch_and_push_never_run_together_in_one_checkout(self) -> None:
        """Prevents 'Failed to update refs: refs/remotes/origin/main': a push and a fetch of one checkout take turns."""
        from tests.ledger_harness import QUOTE, World
        from qew import filelock
        w = World(ledger_vcs="jj", remote=True)
        self.addCleanup(w.close)
        lock = w.ledger / ".qew" / "remote.lock"
        holder = start_holder(lock)
        self.addCleanup(holder.kill)
        threading.Timer(1.5, holder.kill).start()
        started = time.monotonic()
        result = w.run("create", "--title", "Waits for the remote refs", "--quote", QUOTE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(time.monotonic() - started, 1.0, "the write did not wait for the remote refs lock")
        self.assertFalse(filelock.is_held(lock))


class LockSourceGate(unittest.TestCase):
    def test_only_the_lock_module_touches_the_os_locking_apis(self) -> None:
        """Prevents a POSIX-only lock creeping back: fcntl, msvcrt and flock appear in filelock.py alone."""
        offenders = []
        for path in sorted(SRC.glob("*.py")):
            if path.name == "filelock.py":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"^\s*(import|from)\s+(fcntl|msvcrt)\b|\bflock\(|\blockf\(", line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_no_sentinel_file_locks(self) -> None:
        """Prevents pid files or O_EXCL sentinels as locks: nothing outside the lock module creates a `.lock` file exclusively."""
        offenders = []
        for path in sorted(SRC.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if path.name != "filelock.py" and re.search(r"O_EXCL[^\n]*\.lock|\.lock[^\n]*O_EXCL", text):
                offenders.append(path.name)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
