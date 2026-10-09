"""An import larger than one command line can name: the ledger commit must not pass every path as an argument.

Adversary: `git add -- <20000 paths>` dying with "Argument list too long" after the files were written, then a rollback
that leaves the events behind, so the ledger fails its own check.
"""

import json
import os
import stat
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_repo import _jj_filesets
from tests.ledger_harness import World

ITEMS = 6000
PAD = "x" * 100  # long ids make the touched paths exceed ARG_MAX (about 1 MiB on macOS, 2 MiB on Linux) at this count
EVENT = {"type": "drafted", "at": "2026-10-08T00:00:00Z", "actor": "importer", "source": "plan.md:1"}


def plan() -> str:
    return "\n".join(json.dumps({"id": f"big-{PAD}-{n}", "kind": "task", "title": f"Item {n}", "events": [EVENT]})
                     for n in range(ITEMS)) + "\n"


class LargeImportWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)

    def test_an_import_of_many_long_ids_commits_once_and_passes_check(self) -> None:
        """Prevents a plan bigger than the OS argument limit being unimportable."""
        result = self.world.run("create", "--from-file", "-", "--import", stdin=plan(), timeout=240)
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-400:])
        checked = self.world.run("check", timeout=240)
        self.assertEqual(checked.returncode, 0, checked.stdout[-400:])
        self.assertIn(f"ok {ITEMS}", checked.stdout)

    def test_a_failed_commit_rolls_back_every_file_the_import_created(self) -> None:
        """Prevents a refused commit leaving events behind with no item, which fails the ledger's own check."""
        hook = self.world.ledger / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        small = "\n".join(json.dumps({"id": f"small-{n}", "kind": "task", "title": f"Item {n}", "events": [EVENT]})
                          for n in range(5)) + "\n"
        result = self.world.run("create", "--from-file", "-", "--import", stdin=small)
        self.assertNotEqual(result.returncode, 0)
        leftovers = [p.name for folder in ("items", "events") for p in (self.world.ledger / folder).iterdir()]
        self.assertEqual(leftovers, [])

    def test_jj_names_each_path_until_the_list_no_longer_fits_then_the_owned_directories(self) -> None:
        """Prevents a jj ledger commit with tens of thousands of `root-file:` arguments dying on the argument limit."""
        self.assertEqual(_jj_filesets(["items/A.md", "events/A/1.json"]), ['root-file:"items/A.md"', 'root-file:"events/A/1.json"'])
        many = [f"events/{PAD}-{n}/1.json" for n in range(3000)] + ["items/A.md"]
        self.assertEqual(_jj_filesets(many), ['root:"items"', 'root:"events"'])


if __name__ == "__main__":
    unittest.main()
