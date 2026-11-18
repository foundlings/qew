"""A ledger commit of paths a concurrent writer already committed is not an error.

The sync worker and a foreground command can both commit the same baseline files; the second `git commit` finds nothing to commit and
used to fail its sync round, dropping the items that round had taken from the queue. Each test is a scenario; its docstring names the
failure it prevents.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_repo import LedgerRepo


def git(path: Path, *argv: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *argv], cwd=path, capture_output=True, text=True, check=True)
    return done.stdout


class CommitNothingWalk(unittest.TestCase):
    def test_committing_paths_that_are_already_committed_is_quiet(self) -> None:
        """Prevents a second writer's commit of the same files failing a sync round and losing the items it had taken."""
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            git(path, "init", "-q", "-b", "main")
            (path / "a.json").write_text("{}", encoding="utf-8")
            os.environ["GIT_AUTHOR_NAME"] = os.environ["GIT_COMMITTER_NAME"] = "t"
            os.environ["GIT_AUTHOR_EMAIL"] = os.environ["GIT_COMMITTER_EMAIL"] = "t@t"
            repo = LedgerRepo(path)
            repo.commit(["a.json"], "first")
            repo.commit(["a.json"], "second: nothing left to commit")
            self.assertEqual(git(path, "log", "--format=%s").split("\n")[:1], ["first"])

    def test_a_real_commit_failure_is_still_an_error(self) -> None:
        """Prevents the quiet case swallowing a failure that is not `nothing to commit`."""
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            git(path, "init", "-q", "-b", "main")
            repo = LedgerRepo(path)
            with self.assertRaises(Exception):
                repo.commit(["missing.json"], "no such path")


if __name__ == "__main__":
    unittest.main()
