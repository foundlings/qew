"""A renamed file in a jj ledger is recovered, swept and diffed as its real old and new paths.

`jj diff --summary` prints a rename as `R dir/{old.json => new.json}`. Treating that text as a path made recovery commit a file
that does not exist, leave the rename uncommitted, and retry on every command; seen live as one `qew sync` holding the ledger
locks for 23 minutes while seven writers queued behind it.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_repo
from qew.ledger_repo import LedgerRepo
from tests.ledger_harness import World

OLD = "baselines/0430q94hw/github-github.com-foundlings-foundlings-pr-4856/043ewiqf9.json"
NEW = "baselines/0430q94hw/github-github.com-foundlings-foundlings-pr-4856/043eywu5n.json"


def jj(path: Path, *arguments: str) -> str:
    return subprocess.run(["jj", "--no-pager", *arguments], cwd=path, capture_output=True, text=True, check=True).stdout


class RenameSummaryWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="jj")
        self.addCleanup(self.world.close)
        self.ledger = self.world.ledger
        (self.ledger / OLD).parent.mkdir(parents=True)
        (self.ledger / OLD).write_text('{"baseline": "a long enough body that jj sees a rename and not a delete plus an add"}\n')
        jj(self.ledger, "commit", "--message", "baseline")
        self.repo = LedgerRepo(self.ledger, "main")

    def test_summary_paths_expand_a_rename_into_both_real_paths(self) -> None:
        """Prevents a brace expression being used as a path: every shape jj prints for a rename gives real files."""
        expand = ledger_repo.summary_paths
        self.assertEqual(expand("R a/{x.json => y.json}"), ["a/x.json", "a/y.json"])
        self.assertEqual(expand("R {a => b}/x.json"), ["a/x.json", "b/x.json"])
        self.assertEqual(expand("R a/{ => sub}/x.json"), ["a/x.json", "a/sub/x.json"])
        self.assertEqual(expand("R old.json => new.json"), ["old.json", "new.json"])
        self.assertEqual(expand("M items/A/item.md\\nA events/e.json".replace("\\n", "\n")), ["items/A/item.md", "events/e.json"])

    def test_a_renamed_baseline_is_listed_as_its_real_paths(self) -> None:
        """Prevents recovery naming a file that does not exist: the working copy rename lists old and new."""
        (self.ledger / OLD).rename(self.ledger / NEW)
        self.assertEqual(self.repo.dirty_owned(), sorted([OLD, NEW]))

    def test_a_write_sweeps_a_pending_rename_in_one_pass(self) -> None:
        """Prevents the endless recovery: committing one file also commits the rename, and the working copy is left clean."""
        (self.ledger / OLD).rename(self.ledger / NEW)
        (self.ledger / "items" / "A").mkdir(parents=True)
        (self.ledger / "items" / "A" / "item.md").write_text("x\n")
        self.repo.commit(["items/A/item.md"], "qew: write")
        self.assertEqual(jj(self.ledger, "diff", "--summary").strip(), "")
        self.assertEqual(self.repo.dirty_owned(), [])

    def test_changed_between_names_the_real_files_of_a_renamed_baseline(self) -> None:
        """Prevents a fetch reporting a brace expression as a changed ledger path."""
        before = jj(self.ledger, "log", "--no-graph", "-r", "@-", "-T", "commit_id").strip()
        (self.ledger / OLD).rename(self.ledger / NEW)
        jj(self.ledger, "commit", "--message", "rename")
        after = jj(self.ledger, "log", "--no-graph", "-r", "@-", "-T", "commit_id").strip()
        self.assertEqual(sorted(self.repo.changed_between(before, after)), sorted([OLD, NEW]))


if __name__ == "__main__":
    unittest.main()
