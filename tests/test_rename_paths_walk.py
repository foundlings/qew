"""A renamed ledger file is two paths, never one `old -> new` string.

Adversary: `git status --porcelain` prints a rename as `old -> new`; passing that line to `git add --pathspec-from-file` as one
path fails with `pathspec ... did not match any files`, so a write whose forge call had already happened never reached the ledger.
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
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *argv], cwd=path, capture_output=True, text=True, check=True).stdout


class RenamePathsWalk(unittest.TestCase):
    def test_a_staged_rename_is_listed_as_both_paths_and_commits(self) -> None:
        """Prevents `pathspec 'old -> new' did not match any files` when a baseline file was renamed in the worktree."""
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            git(path, "init", "-q", "-b", "main")
            (path / "baselines").mkdir()
            (path / "baselines" / "a b.json").write_text("{}", encoding="utf-8")
            os.environ["GIT_AUTHOR_NAME"] = os.environ["GIT_COMMITTER_NAME"] = "t"
            os.environ["GIT_AUTHOR_EMAIL"] = os.environ["GIT_COMMITTER_EMAIL"] = "t@t"
            repo = LedgerRepo(path)
            repo.commit(["baselines/a b.json"], "first")
            git(path, "mv", "baselines/a b.json", "baselines/c.json")
            dirty = repo.dirty_owned()
            self.assertEqual(dirty, ["baselines/a b.json", "baselines/c.json"])
            repo.commit(dirty, "rename")
            self.assertEqual(git(path, "status", "--porcelain").strip(), "")

    def test_a_file_renamed_in_the_worktree_without_staging_commits(self) -> None:
        """Prevents the unstaged shape (a deleted path plus an untracked one) losing the delete half of the rename."""
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            git(path, "init", "-q", "-b", "main")
            (path / "baselines").mkdir()
            (path / "baselines" / "a.json").write_text("{}", encoding="utf-8")
            os.environ["GIT_AUTHOR_NAME"] = os.environ["GIT_COMMITTER_NAME"] = "t"
            os.environ["GIT_AUTHOR_EMAIL"] = os.environ["GIT_COMMITTER_EMAIL"] = "t@t"
            repo = LedgerRepo(path)
            repo.commit(["baselines/a.json"], "first")
            (path / "baselines" / "a.json").rename(path / "baselines" / "b.json")
            repo.commit(repo.dirty_owned(), "rename")
            self.assertEqual(git(path, "status", "--porcelain").strip(), "")


if __name__ == "__main__":
    unittest.main()
