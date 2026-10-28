"""A read command on a jj ledger never snapshots the working copy: the fetch, its queries and the recovery check ran jj commands that
each scanned every file (165,000 on foundlings: 1 to 3 s each unloaded, 8 s of ledger fetch+merge under load).

Each test is a scenario; its docstring names the failure it prevents. A `jj` shim on PATH records every call.
"""

import os
import shutil
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World, jj_available

EVERY = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\nforge_concurrency = 1\n"


@unittest.skipUnless(jj_available(), "jj is not installed")
class ReadCostWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="jj", remote=True, replication=EVERY)
        self.addCleanup(self.world.close)
        self.item = self.world.create("--title", "Work", "--quote", QUOTE)
        shims = self.world.base / "shims"
        shims.mkdir()
        self.log = self.world.base / "jj-calls.log"
        real = shutil.which("jj")
        shim = shims / "jj"
        shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.log}"\nexec "{real}" "$@"\n')
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        self.env = {"PATH": f"{shims}{os.pathsep}{os.environ['PATH']}"}

    def calls(self) -> list[str]:
        calls: list[str] = []
        for line in (self.log.read_text().splitlines() if self.log.exists() else []):
            if line.startswith("--"):
                calls.append(line)
            elif calls:
                calls[-1] += " " + line  # an argument with a newline in it (a revset template)
        return calls

    def read(self, *argv: str):
        self.log.unlink(missing_ok=True)
        result = self.world.run(*argv, env=self.env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_a_quiet_read_runs_no_jj_command_that_snapshots_the_working_copy(self) -> None:
        """Prevents ~8 s of ledger fetch+merge per read: with nothing to merge or push every jj call ignores the working copy."""
        self.read("list")
        calls = self.calls()
        self.assertTrue(calls, "the read was expected to fetch")
        for call in calls:
            self.assertIn("--ignore-working-copy", call, f"`jj {call}` snapshots the working copy")
        self.assertFalse([c for c in calls if " push" in c or c.startswith("diff") or " diff" in c], calls)

    def test_a_crashed_writer_is_still_recovered_by_the_next_read(self) -> None:
        """Prevents lost work: a killed command leaves its marker, and the next read commits the files it left behind."""
        event = next((self.world.home("events", self.item)).glob("*.json"))
        stray = event.with_name("9999999zz.json")
        stray.write_text('{"type": "commented", "at": "2026-10-11T00:00:00Z", "actor": "tester", "text": "left behind"}\n')
        (self.world.ledger / ".qew").mkdir(exist_ok=True)
        (self.world.ledger / ".qew" / "writing").write_text("pid 1\n")
        self.read("list")
        self.assertEqual(self.world.ledger_dirty(), [])
        self.assertFalse((self.world.ledger / ".qew" / "writing").exists())

    def test_a_write_snapshots_the_working_copy_once_and_still_sweeps_strays(self) -> None:
        """Prevents ~3 s of repeated scans per write: only the commit snapshots; a stray file is swept from that same snapshot."""
        stray = next((self.world.home("events", self.item)).glob("*.json")).with_name("9999999zz.json")
        stray.write_text('{"type": "commented", "at": "2026-10-11T00:00:00Z", "actor": "tester", "text": "left behind"}\n')
        self.read("create", "--title", "Another", "--quote", QUOTE)
        snapshotting = [c for c in self.calls() if "--ignore-working-copy" not in c]
        self.assertEqual(len(snapshotting) <= 2, True, snapshotting)  # the commit, and the commit of the swept stray
        self.assertTrue(all(" commit " in f" {c} " for c in snapshotting), snapshotting)
        self.assertEqual(self.world.ledger_dirty(), [], "the stray was committed too")

    def test_a_clean_write_leaves_no_marker(self) -> None:
        """Prevents every later read scanning for leftovers: the marker is gone once the write has committed."""
        self.world.create("--title", "More work", "--quote", QUOTE)
        self.assertFalse((self.world.ledger / ".qew" / "writing").exists())


if __name__ == "__main__":
    unittest.main()
