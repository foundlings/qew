"""A jj or git call that outlives its timeout is asked to stop before it is killed.

Adversary: a SIGKILL in the middle of a ref transaction leaves every `.lock` file the transaction had taken (thousands of
`refs/jj/keep/*.lock` after one killed gc), and the repository then refuses all writes until a human removes them.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_repo
from qew.ledger_repo import CommandTimeout, LedgerRepo

HOLDER = """
import signal, sys, time
lock = sys.argv[1]
open(lock, "w").close()
def stop(*_):
    import os
    os.remove(lock)  # what git and jj do when they are asked to stop: release the locks they hold
    sys.exit(143)
signal.signal(signal.SIGTERM, stop)
print("ready", flush=True)
time.sleep(60)
"""


class SpawnTerminationWalk(unittest.TestCase):
    def test_a_timed_out_call_releases_its_locks_because_it_is_terminated_first(self) -> None:
        """Prevents a killed jj leaving its ref locks behind: the call gets SIGTERM and a grace period before SIGKILL."""
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            lock = path / "ref.lock"
            repo = LedgerRepo(path)
            with self.assertRaises(CommandTimeout):
                repo._spawn([sys.executable, "-c", HOLDER, str(lock)], 1.0, None)
            self.assertFalse(lock.exists(), "the lock file was left behind")

    def test_a_call_that_ignores_the_request_is_still_killed(self) -> None:
        """Prevents a hung call outliving its timeout for ever once it is asked politely."""
        stubborn = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        with tempfile.TemporaryDirectory() as raw:
            repo = LedgerRepo(Path(raw))
            with mock.patch.object(ledger_repo, "TERMINATE_GRACE_S", 0.5, create=True), self.assertRaises(CommandTimeout):
                repo._spawn([sys.executable, "-c", stubborn], 0.5, None)


if __name__ == "__main__":
    unittest.main()
