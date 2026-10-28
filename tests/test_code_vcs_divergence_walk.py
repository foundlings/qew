"""Resolving a landed change in a busy jj repository: a conflicted target bookmark and a divergent change id still resolve.

Each test is a scenario; its docstring names the failure it prevents (seen live: completing an item from a temporary workspace
reported that main could not be resolved, after the merge was already confirmed).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import code_vcs
from qew.errors import QewError
from tests.ledger_harness import jj, jj_available


def commit_id(repo: Path, revset: str) -> str:
    return jj(repo, "--ignore-working-copy", "log", "--no-graph", "-r", revset, "-T", "commit_id").strip()


def change_id(repo: Path, revset: str) -> str:
    return jj(repo, "--ignore-working-copy", "log", "--no-graph", "-r", revset, "-T", "change_id.short(12)").strip().split()[0]


@unittest.skipUnless(jj_available(), "jj is not installed")
class CodeVcsDivergenceWalk(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        jj(self.repo, "git", "init")
        jj(self.repo, "describe", "-m", "base")
        jj(self.repo, "bookmark", "create", "main", "-r", "@")
        jj(self.repo, "new", "-m", "work")
        self.work = change_id(self.repo, "@")
        jj(self.repo, "bookmark", "set", "main", "-r", "@")

    def operation(self) -> str:
        return jj(self.repo, "--ignore-working-copy", "op", "log", "--no-graph", "-n", "1", "-T", "id.short(12)").strip()

    def test_a_divergent_change_id_that_reached_main_is_landed(self) -> None:
        """Prevents 'unknown to this checkout' for evidence whose change id became divergent: jj refuses a divergent change id as a
        bare revset, so the lookup must name all its commits."""
        before = self.operation()
        original = commit_id(self.repo, self.work)
        jj(self.repo, "--at-op", before, "describe", "-r", original, "-m", "work, rewritten elsewhere")
        jj(self.repo, "describe", "-r", original, "-m", "work, rewritten here")
        divergent = jj(self.repo, "log", "--no-graph", "-r", f"change_id({self.work})", "-T", 'commit_id ++ "\\n"').split()
        self.assertEqual(len(divergent), 2, divergent)
        jj(self.repo, "bookmark", "set", "main", "-r", divergent[0], "--allow-backwards")
        where, commit = code_vcs.landing(self.repo, "jj", self.work, "main")
        self.assertEqual(where, code_vcs.Landing.LANDED)
        self.assertTrue(code_vcs.is_ancestor(self.repo, "jj", self.work, "main"))
        self.assertIn(commit, divergent)

    def test_a_conflicted_target_bookmark_still_resolves(self) -> None:
        """Prevents 'target main does not exist in this repo' for a bookmark that two sides moved: a change in either side is on it."""
        jj(self.repo, "new", "-m", "left", self.work)
        left = change_id(self.repo, "@")
        jj(self.repo, "new", "-m", "right", self.work)
        before = self.operation()
        jj(self.repo, "--at-op", before, "bookmark", "set", "main", "-r", left, "--allow-backwards")
        jj(self.repo, "--at-op", before, "bookmark", "set", "main", "-r", change_id(self.repo, "@"), "--allow-backwards")
        listed = jj(self.repo, "bookmark", "list", "main")
        self.assertIn("conflicted", listed)
        where, _commit = code_vcs.landing(self.repo, "jj", left, "main")
        self.assertEqual(where, code_vcs.Landing.LANDED)
        self.assertTrue(code_vcs.is_ancestor(self.repo, "jj", self.work, "main"))
        self.assertEqual(code_vcs.landing_many(self.repo, "jj", [left, self.work, self.work + "x"], "main")[left][0], code_vcs.Landing.LANDED)

    def remote(self) -> Path:
        """A bare git remote named origin, with main pushed: main@origin then exists beside the local main."""
        bare = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(bare)], check=False))
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        jj(self.repo, "git", "remote", "add", "origin", str(bare))
        jj(self.repo, "git", "push", "--bookmark", "main")
        return bare

    def test_a_change_on_the_forges_main_is_landed_when_local_main_is_behind_or_diverged(self) -> None:
        """Prevents 'is not an ancestor of main' after a forge merge: local main sat on a commit without the merged change
        (a duplicate local merge), while main@origin holds it. The forge's main is the branch the change landed on."""
        self.remote()
        jj(self.repo, "bookmark", "set", "main", "-r", f"{self.work}-", "--allow-backwards")
        self.assertEqual(jj(self.repo, "--ignore-working-copy", "log", "--no-graph", "-r", f"::main & {self.work}", "-T", "change_id").strip(), "")
        where, _commit = code_vcs.landing(self.repo, "jj", self.work, "main")
        self.assertEqual(where, code_vcs.Landing.LANDED)
        self.assertTrue(code_vcs.is_ancestor(self.repo, "jj", self.work, "main"))
        self.assertEqual(code_vcs.landing_many(self.repo, "jj", [self.work, self.work + "x", self.work], "main")[self.work][0], code_vcs.Landing.LANDED)

    def test_a_change_only_in_this_checkout_is_not_landed_by_the_remote_fallback(self) -> None:
        """Prevents the fallback landing unpushed work: a change on neither main nor main@origin stays not an ancestor."""
        self.remote()
        jj(self.repo, "new", "-m", "unpushed")
        local = change_id(self.repo, "@")
        where, _commit = code_vcs.landing(self.repo, "jj", local, "main")
        self.assertEqual(where, code_vcs.Landing.NOT_ANCESTOR)

    def test_a_target_that_does_not_exist_is_still_refused(self) -> None:
        """Prevents the fallback hiding a real mistake: a branch name nothing in the repo carries is exit 3 naming it."""
        with self.assertRaises(QewError) as caught:
            code_vcs.landing(self.repo, "jj", self.work, "no-such-branch")
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertIn("no-such-branch", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
