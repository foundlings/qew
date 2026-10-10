"""A slow jj push must not fail a command that finished: jj gets a proportionate timeout, and a timeout is offline, not exit 3.

Each test is a scenario; its docstring names the failure it prevents (seen live on foundlings, 20,874 items: `qew sync`
exited 3 with `jj --no-pager git timed out after 5.0s` after the work had completed). A shim `jj` stands in for a slow
snapshot or network: it sleeps before a push, then runs the real jj.
"""

import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

SLOW = "\n[replication]\ntimeout_s = 1\nfetch_interval_s = 0\nauto_forge_sync = false\nforge_concurrency = 1\n"


@unittest.skipUnless(shutil.which("jj"), "needs jj")
class SlowJjWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="jj", remote=True, replication=SLOW)
        self.addCleanup(self.world.close)
        shim = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(shim, ignore_errors=True))
        real = shutil.which("jj")
        script = shim / "jj"
        script.write_text(f'#!/bin/sh\ncase " $* " in *" git push "*) sleep 3;; esac\nexec "{real}" "$@"\n')
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        self.slow = {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}

    def status(self) -> str:
        return self.world.run("status").stdout

    def test_a_push_slower_than_the_network_timeout_still_succeeds_because_jj_gets_a_proportionate_one(self) -> None:
        """Prevents the 5s network timeout also bounding jj's local snapshot: a 3s push with timeout_s = 1 completes."""
        result = self.world.run("create", "--kind", "issue", "--title", "Slow push", "--quote", QUOTE, env=self.slow)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unpushed=0", self.status())

    def test_a_timeout_is_offline_not_exit_3_and_the_next_command_pushes(self) -> None:
        """Prevents a finished command reading as failed: a push that really times out keeps the commit local and warns."""
        forced = {**self.slow, "QEW_JJ_TIMEOUT_S": "1"}
        result = self.world.run("create", "--kind", "issue", "--title", "Timed out", "--quote", QUOTE, env=forced)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("timed out", result.stderr)
        self.assertIn("unpushed=1", self.status())
        again = self.world.run("sync")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("unpushed=0", self.status())


if __name__ == "__main__":
    unittest.main()
