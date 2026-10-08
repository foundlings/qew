"""Walk of git-store resolution for repository discovery inside jj workspaces.

Adversary: repository inference failing with "not a git repository" in a secondary jj workspace, a broken
pointer silently falling back to the wrong repository, and a plain git checkout's own inference overridden.
"""

import contextlib
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from tests.ledger_harness import World, git, jj
from qew import vcs
from qew.errors import QewError


@contextlib.contextmanager
def inside(path: Path):
    before = os.getcwd()
    os.chdir(path)
    vcs.checkout_repository.cache_clear()
    try:
        yield
    finally:
        os.chdir(before)
        vcs.checkout_repository.cache_clear()


class WorkspaceGitStoreWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.main = self.world.base / "main"
        jj(self.world.base, "git", "init", str(self.main))
        store = (self.main / ".jj" / "repo" / "store" / "git_target").read_text(encoding="utf-8").strip()
        self.git_dir = (self.main / ".jj" / "repo" / "store" / store).resolve()
        git(self.main, "--git-dir", str(self.git_dir), "remote", "add", "origin", "https://example.invalid/acme/widgets.git")
        self.secondary = self.world.base / "secondary"
        jj(self.main, "workspace", "add", str(self.secondary))

    def test_a_secondary_workspace_resolves_the_main_git_store_and_the_repository(self) -> None:
        """Commands must not fail with 'not a git repository' inside a jj workspace."""
        self.assertFalse((self.secondary / ".git").exists())
        with inside(self.secondary):
            self.assertEqual(vcs.workspace_git_dir(), self.git_dir)
            self.assertEqual(vcs.checkout_repository(), "acme/widgets")

    def test_the_resolved_store_is_passed_to_the_git_subprocess(self) -> None:
        """The subprocess must see the main checkout's git directory."""
        real = subprocess.run
        calls = []

        def spy(argv, *args, **kwargs):
            calls.append(list(argv))
            return real(argv, *args, **kwargs)

        with inside(self.secondary), mock.patch("qew.vcs.subprocess.run", side_effect=spy):
            vcs.checkout_repository()
        self.assertEqual(calls[0][:3], ["git", "--git-dir", str(self.git_dir)])

    def test_a_broken_pointer_fails_loudly_and_runs_no_subprocess(self) -> None:
        """No silent fallback to the wrong repository."""
        (self.secondary / ".jj" / "repo").write_text("../../nowhere/.jj/repo", encoding="utf-8")
        with inside(self.secondary), mock.patch("qew.vcs.subprocess.run") as run:
            with self.assertRaises(QewError) as caught:
                vcs.workspace_git_dir()
            self.assertEqual(caught.exception.exit_code, 3)
            self.assertIn("cannot resolve the git store of this jj workspace", str(caught.exception))
            self.assertIsNone(vcs.checkout_repository())
            run.assert_not_called()

    def test_a_plain_git_checkout_keeps_the_tools_own_inference(self) -> None:
        """No git directory may be injected where git finds the repository unaided."""
        git(self.world.code, "remote", "add", "origin", "https://example.invalid/acme/plain.git")
        real = subprocess.run
        calls = []

        def spy(argv, *args, **kwargs):
            calls.append(list(argv))
            return real(argv, *args, **kwargs)

        with inside(self.world.code), mock.patch("qew.vcs.subprocess.run", side_effect=spy):
            self.assertIsNone(vcs.workspace_git_dir())
            self.assertEqual(vcs.checkout_repository(), "acme/plain")
        self.assertNotIn("--git-dir", calls[0])


if __name__ == "__main__":
    unittest.main()
