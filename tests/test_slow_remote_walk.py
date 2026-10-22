"""A slow ledger remote is a timeout, not an outage: it says so, names the setting, counts the work other machines cannot see.

Each test is a scenario; its docstring names the failure it prevents (seen live: on a slow link a ledger fetch took 15 s and a
push 8 s, `[replication] timeout_s` was its 5 s default, qew called the remote unreachable, and 126 commits stayed local until
the integrator found a pull request record that had never left the machine). A shim `git` stands in for the slow network.
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

QUIET = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\nforge_concurrency = 1\n"


class SlowRemoteWalk(unittest.TestCase):
    def shim(self, seconds: int) -> dict[str, str]:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(directory, ignore_errors=True))
        script = directory / "git"
        script.write_text(f'#!/bin/sh\ncase " $* " in *" push "*|*" fetch "*) sleep {seconds};; esac\nexec "{shutil.which("git")}" "$@"\n')
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return {"PATH": f"{directory}{os.pathsep}{os.environ['PATH']}"}

    def test_a_remote_slower_than_five_seconds_still_gets_the_push_with_the_default_timeout(self) -> None:
        """Prevents a slow link silently turning every write local: the default timeout covers a push that takes 8 s."""
        world = World(ledger_vcs="git", remote=True, replication=QUIET)
        self.addCleanup(world.close)
        result = world.run("create", "--kind", "issue", "--title", "Slow link", "--quote", QUOTE, env=self.shim(8))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("timed out", result.stderr)
        self.assertIn("unpushed=0", world.run("status").stdout)

    def test_a_timeout_names_the_setting_counts_the_unpushed_commits_and_shows_in_status(self) -> None:
        """Prevents a timeout reading as an outage: the warning says it timed out, names timeout_s, says how many commits other
        machines cannot see, and `qew status` keeps the reason."""
        world = World(ledger_vcs="git", remote=True, replication=QUIET + "timeout_s = 1\n")
        self.addCleanup(world.close)
        slow = self.shim(3)
        world.run("create", "--kind", "issue", "--title", "First", "--quote", QUOTE, env=slow)
        second = world.run("create", "--kind", "issue", "--title", "Second", "--quote", QUOTE, env=slow)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("timed out after", second.stderr)
        self.assertIn("timeout_s", second.stderr)
        self.assertRegex(second.stderr, r"\d+ ledger commits? not on the remote")
        status = world.run("status").stdout
        self.assertIn("remote=offline", status)
        self.assertIn("timed out after", status)
        self.assertIn("timeout_s", status)


if __name__ == "__main__":
    unittest.main()
