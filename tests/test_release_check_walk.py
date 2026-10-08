"""Walks of the local release script's source checks.

Adversary: a build and an SBOM describing different commits, a dirty or moved checkout attested as
clean, a tag whose version does not match the package, and a movable tag name used as the source.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMAND = PROJECT_ROOT / "scripts" / "release.py"
GIT_ENV = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid", "GIT_COMMITTER_NAME": "T",
           "GIT_COMMITTER_EMAIL": "t@e.invalid"}


def git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=cwd, env={**os.environ, **GIT_ENV}, capture_output=True,
                          text=True, check=True).stdout.strip()


class ReleaseCheckWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "src" / "qew").mkdir(parents=True)
        (self.root / "src" / "qew" / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "release commit")
        self.sha = git(self.root, "rev-parse", "HEAD")

    def check(self, tag: str = "v1.2.3", sha: str = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [os.sys.executable, str(COMMAND), "check", "--root", str(self.root), "--tag", tag,
             "--source-sha", sha or self.sha],
            capture_output=True, text=True, check=False, timeout=60)

    def test_the_recorded_commit_with_a_clean_tree_and_matching_version_passes(self) -> None:
        """Golden: the source the SBOM will name is the source that was checked out."""
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_moved_or_dirty_checkout_is_refused_with_the_word_differs(self) -> None:
        """The attested commit must be the built commit."""
        (self.root / "extra.txt").write_text("dirty\n", encoding="utf-8")
        dirty = self.check()
        self.assertEqual(dirty.returncode, 2)
        self.assertIn("differs", dirty.stderr)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "moved on")
        moved = self.check()
        self.assertEqual(moved.returncode, 2)
        self.assertIn("differs", moved.stderr)
        self.assertIn(self.sha, moved.stderr)

    def test_a_version_that_does_not_match_the_tag_fails_before_any_build(self) -> None:
        """A wrong tag must stop the release before artifacts exist."""
        result = subprocess.run(
            [os.sys.executable, str(COMMAND), "build", "--root", str(self.root), "--tag", "v9.9.9",
             "--source-sha", self.sha, "--out", str(self.root / "out")],
            capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn("1.2.3", result.stderr)
        self.assertIn("v9.9.9", result.stderr)
        self.assertFalse((self.root / "out").exists())
        self.assertFalse((self.root / "dist").exists())

    def test_a_tag_name_is_never_accepted_as_the_source(self) -> None:
        """A movable tag must not stand in for a commit id."""
        git(self.root, "tag", "v1.2.3")
        for source in ("v1.2.3", "main", "HEAD", self.sha[:12], self.sha.upper()):
            with self.subTest(source=source):
                result = self.check(sha=source)
                self.assertEqual(result.returncode, 2)
                self.assertIn("full commit id", result.stderr)


if __name__ == "__main__":
    unittest.main()
