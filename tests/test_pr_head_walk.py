"""Walks of how `qew pr open` finds the pushed head branch: git, Jujutsu, and an explicit `--head`.

Adversary: a pull request for commits the forge cannot see, a probe failure that hides which command
broke, git run inside a Jujutsu workspace (the shared colocated HEAD moves), and a branch that
belongs to another ledger item.
"""

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import git
from tests.pr_harness import PrWorld


def shim(directory: Path, name: str, script: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class HeadWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Work")
        self.shims = tempfile.TemporaryDirectory()
        self.addCleanup(self.shims.cleanup)
        self.dir = Path(self.shims.name)
        self.path = {"PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}"}

    def derive(self, *extra: str, env=None):
        return self.w.qew("pr", "open", self.item, "--base", "main", *extra, env=env)

    def push_main(self) -> None:
        (self.w.world.code / ".git" / "info" / "exclude").write_text(".qew/\n")
        remote = self.w.world.base / "origin.git"
        git(self.w.world.base, "init", "-q", "--bare", "-b", "main", str(remote))
        git(self.w.world.code, "remote", "add", "origin", str(remote))
        git(self.w.world.code, "push", "-q", "-u", "origin", "main")

    def test_a_clean_branch_that_matches_its_upstream_is_the_head(self) -> None:
        self.push_main()
        run = self.derive()
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.w.forge.pull_specs[1].head, "main")

    def test_a_branch_ahead_of_its_upstream_is_refused_before_any_forge_call(self) -> None:
        """Prevents a pull request for commits the forge cannot see."""
        self.push_main()
        self.w.commit("local only")
        run = self.derive()
        self.assertEqual(run.code, 3)
        self.assertIn("must exactly match its upstream", run.err)
        self.assertEqual(self.w.forge.count("open_pr"), 0)

    def test_an_explicit_head_skips_every_probe(self) -> None:
        """Prevents derivation failures blocking a caller who knows the branch."""
        shim(self.dir, "git", "echo 'git is broken' >&2\nexit 9\n")
        run = self.derive("--head", "feat/x", env=self.path)
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.w.forge.pull_specs[1].head, "feat/x")

    def test_hostile_head_names_are_refused(self) -> None:
        for head in ("-rf", 'a"b', "a\\b", " "):
            with self.subTest(head=head):
                self.assertEqual(self.derive("--head", head).code, 2)
        self.assertEqual(self.w.forge.count("open_pr"), 0)

    def test_a_branch_named_for_another_item_is_refused_with_both_ids(self) -> None:
        """Prevents a pull request attached to the wrong item."""
        other = self.w.item("Other work")
        run = self.derive("--head", f"feat/{other}-parser")
        self.assertEqual(run.code, 2)
        self.assertIn(other, run.err)
        self.assertIn(self.item, run.err)
        self.assertEqual(self.w.forge.count("open_pr"), 0)

    def test_a_branch_naming_this_item_or_no_item_is_accepted(self) -> None:
        self.assertEqual(self.derive("--head", f"feat/{self.item}-parser").code, 0)
        self.assertEqual(self.derive("--head", "feat/plain-name").code, 0)

    def test_a_detached_head_names_the_head_escape_hatch(self) -> None:
        """Prevents an opaque error."""
        git(self.w.world.code, "checkout", "-q", "--detach")
        run = self.derive()
        self.assertEqual(run.code, 3)
        self.assertIn("--head", run.err)

    def test_a_failing_probe_names_the_command_and_its_exit_status(self) -> None:
        """Prevents opaque errors."""
        shim(self.dir, "git", "echo boom >&2\nexit 7\n")
        run = self.derive(env=self.path)
        self.assertEqual(run.code, 3)
        self.assertIn("git symbolic-ref", run.err)
        self.assertIn("exit 7", run.err)


class JujutsuHeadWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Work")
        (self.w.world.code / ".jj").mkdir()
        self.shims = tempfile.TemporaryDirectory()
        self.addCleanup(self.shims.cleanup)
        self.dir = Path(self.shims.name)
        self.gitlog = self.dir / "git-was-run"
        shim(self.dir, "git", f"echo \"$@\" >> {self.gitlog}\nexit 99\n")
        self.path = {"PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}"}

    def jj(self, drift: bool = False, fail_bookmarks: bool = False) -> None:
        shim(
            self.dir,
            "jj",
            "case \"$*\" in\n"
            + ("  *'bookmark list'*) echo 'jj: bookmark list exploded' >&2; exit 5 ;;\n" if fail_bookmarks else "  *'bookmark list'*) echo feature ;;\n")
            + "  *'|'*) " + ("echo abc123" if drift else "") + " ;;\n"
            "  *remote_bookmarks*) echo abc123 ;;\n"
            "esac\n",
        )

    def open(self):
        return self.w.qew("pr", "open", self.item, "--base", "main", env=self.path)

    def test_the_bookmark_at_the_working_copy_is_the_head_and_git_is_never_run(self) -> None:
        """Prevents touching the shared colocated git HEAD."""
        self.jj()
        run = self.open()
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.w.forge.pull_specs[1].head, "feature")
        self.assertFalse(self.gitlog.exists())

    def test_a_bookmark_ahead_of_origin_is_refused(self) -> None:
        """Prevents a pull request from a stale remote bookmark."""
        self.jj(drift=True)
        run = self.open()
        self.assertEqual(run.code, 3)
        self.assertIn("must exactly match its upstream", run.err)
        self.assertEqual(self.w.forge.count("open_pr"), 0)

    def test_a_failed_jj_probe_names_the_command_and_status(self) -> None:
        self.jj(fail_bookmarks=True)
        run = self.open()
        self.assertEqual(run.code, 3)
        self.assertIn("jj bookmark list", run.err)
        self.assertIn("exit 5", run.err)
        self.assertFalse(self.gitlog.exists())


if __name__ == "__main__":
    unittest.main()
