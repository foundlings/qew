"""scripts/rewrite-identity.sh takes one identity out of a repository's history and puts another in."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "rewrite-identity.sh"
AVAILABLE = shutil.which("git-filter-repo") is not None
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *arguments: str, author: str = "oldname") -> str:
    environment = {**ENV, "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author}@example.invalid",
                   "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": f"{author}@example.invalid"}
    return subprocess.run(["git", *arguments], cwd=cwd, env=environment, capture_output=True, text=True, check=True).stdout


def script(*arguments: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(SCRIPT), *arguments], cwd=cwd, env=ENV, capture_output=True, text=True, check=False)


@unittest.skipUnless(AVAILABLE, "git-filter-repo is not installed")
class RewriteIdentityWalk(unittest.TestCase):
    def fixture(self, directory: Path) -> Path:
        repo = directory / "ledger"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "events").mkdir()
        (repo / "events" / "a.json").write_text('{"actor": "oldname", "type": "claimed", "owner": "oldname"}\n')
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "qew: start X by oldname")
        (repo / "notes.md").write_text("handled by oldname\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "second")
        bare = directory / "remote.git"
        git(directory, "init", "-q", "--bare", str(bare))
        git(repo, "remote", "add", "origin", str(bare))
        return repo

    def occurrences(self, repo: Path) -> str:
        return git(repo, "log", "--all", "--format=%an|%ae|%cn|%ce|%B", "-S", "oldname") + git(repo, "log", "--all", "--format=%an|%ae|%B")

    def test_the_old_identity_disappears_from_authors_messages_and_file_history(self) -> None:
        """Prevents a leaked identity surviving in any commit: author fields, subjects and every file revision are rewritten."""
        with tempfile.TemporaryDirectory() as directory:
            repo = self.fixture(Path(directory))
            done = script("oldname", "887", str(repo), cwd=Path(directory))
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertNotIn("oldname", self.occurrences(repo))
            self.assertEqual(git(repo, "log", "--format=%an", "main").split(), ["887", "887"])
            self.assertIn('"actor": "887"', (repo / "events" / "a.json").read_text())
            self.assertEqual(git(repo, "log", "-S", "oldname", "--all", "--oneline").strip(), "")
            self.assertEqual(git(repo, "remote", "get-url", "origin").strip(), str(Path(directory) / "remote.git"))

    def test_a_dry_run_reports_what_would_change_and_changes_nothing(self) -> None:
        """Prevents surprises: the dry run counts occurrences and leaves history untouched."""
        with tempfile.TemporaryDirectory() as directory:
            repo = self.fixture(Path(directory))
            before = git(repo, "rev-parse", "HEAD")
            done = script("--dry-run", "oldname", "887", str(repo), cwd=Path(directory))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertRegex(done.stdout, r"commits touching the text: [1-9]")
            self.assertEqual(git(repo, "rev-parse", "HEAD"), before)

    def test_a_dirty_tree_and_an_unsafe_name_are_refused(self) -> None:
        """Prevents rewriting under uncommitted work or with a name that could break the filter expression."""
        with tempfile.TemporaryDirectory() as directory:
            repo = self.fixture(Path(directory))
            (repo / "scratch").write_text("x")
            self.assertEqual(script("oldname", "887", str(repo), cwd=Path(directory)).returncode, 2)
            (repo / "scratch").unlink()
            self.assertEqual(script("old'name", "887", str(repo), cwd=Path(directory)).returncode, 2)


if __name__ == "__main__":
    unittest.main()
