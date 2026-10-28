"""A big ledger commit still commits the files qew wrote outside its own directories.

Each test is a scenario; its docstring names the failure it prevents. Past 100 KB of paths the jj commit names qew's owned
directories instead of every file; foundlings' 20,880-file migration lost `qew-ledger.toml` that way (schema_version stayed 2
in every committed revision while the working copy held 3).
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_repo
from qew.ledger_repo import LedgerRepo
from tests.ledger_harness import World


class CommitVerifyWalk(unittest.TestCase):
    def test_the_directory_fallback_still_names_files_outside_the_owned_directories(self) -> None:
        """Prevents the fallback dropping qew-ledger.toml: owned directories by root, any other written file by itself."""
        paths = [f"items/{n}/item.md" for n in range(20)] + ["events/x/e.json", "qew-ledger.toml"]
        with mock.patch.object(ledger_repo, "JJ_FILESET_BYTES", 10):
            filesets = ledger_repo._jj_filesets(paths)
        self.assertIn('root:"items"', filesets)
        self.assertIn('root:"events"', filesets)
        self.assertIn('root-file:"qew-ledger.toml"', filesets)
        self.assertNotIn('root-file:"items/0/item.md"', filesets)
        self.assertEqual(ledger_repo._jj_filesets(["qew-ledger.toml"]), ['root-file:"qew-ledger.toml"'])

    def test_a_big_commit_on_a_jj_ledger_leaves_the_toml_committed(self) -> None:
        """Prevents a committed ledger that disagrees with its working copy: nothing the write touched stays uncommitted."""
        world = World(ledger_vcs="jj")
        self.addCleanup(world.close)
        for number in range(3):
            (world.home("items", f"A{number}")).mkdir(parents=True)
            (world.home("items", f"A{number}") / "item.md").write_text("x\n")
        toml = world.ledger / "qew-ledger.toml"
        toml.write_text(toml.read_text().replace("schema_version = 7", "schema_version = 7  # edited"))
        repo = LedgerRepo(world.ledger, "main")
        paths = [f"items/A{n}/item.md" for n in range(3)] + ["qew-ledger.toml"]
        with mock.patch.object(ledger_repo, "JJ_FILESET_BYTES", 10):
            repo.commit(paths, "qew: big write")
        left = subprocess.run(["jj", "--no-pager", "diff", "--summary"], cwd=world.ledger, capture_output=True, text=True).stdout
        self.assertEqual(left.strip(), "", left)


if __name__ == "__main__":
    unittest.main()
