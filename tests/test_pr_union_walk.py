"""Walks of `qew pr union`: the heads of several open pull requests become one pushed union commit.

Adversary: a squash that loses the change ids evidence cites, a conflict pushed anyway, a union forced over a branch that
moved, someone else's branch rewritten without the override, and a working copy disturbed by the build.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import git
from tests.pr_harness import PrWorld


class UnionWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.code = self.w.world.code
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.bare = Path(scratch.name) / "remote.git"
        git(Path(scratch.name), "init", "-q", "--bare", str(self.bare))
        git(self.code, "remote", "add", "origin", str(self.bare))
        self.write("base.txt", "base\n", "base")
        self.base = git(self.code, "rev-parse", "HEAD")
        self.shas: dict[int, str] = {}

    def write(self, name: str, text: str, message: str) -> None:
        (self.code / name).write_text(text, encoding="utf-8")
        git(self.code, "add", name)
        git(self.code, "commit", "-q", "-m", message)

    def pull_request(self, number: int, name: str, text: str, parent: str | None = None) -> None:
        """Open pull request `number` on branch-`number` holding one commit that writes `name`."""
        item = self.w.item(f"Work {number}")
        git(self.code, "checkout", "-q", "-b", f"branch-{number}", parent or self.base)
        self.write(name, text, f"work {number}")
        sha = git(self.code, "rev-parse", "HEAD")
        git(self.code, "push", "-q", "origin", f"branch-{number}")
        self.w.head = f"branch-{number}"
        self.assertEqual(self.w.open(item).code, 0)
        self.w.forge.edit_pull(number, head_sha=sha)
        self.shas[number] = sha
        git(self.code, "checkout", "-q", "--detach", self.base)

    def remote(self, number: int) -> str:
        return git(self.bare, "rev-parse", f"branch-{number}")

    def test_sibling_heads_become_one_pushed_union_without_touching_the_working_copy(self) -> None:
        """Prevents a squash or a disturbed checkout: the lead's branch gets a merge that keeps every head as an ancestor, the other
        branches stay where they were, and the local checkout does not move."""
        for number in (1, 2, 3):
            self.pull_request(number, f"f{number}.txt", f"{number}\n")
        here = git(self.code, "rev-parse", "HEAD")
        run = self.w.qew("pr", "union", "3", "1", "2")
        self.assertEqual(run.code, 0, run)
        pushed = self.remote(3)
        self.assertNotEqual(pushed, self.shas[3])
        for number in (1, 2, 3):
            git(self.bare, "merge-base", "--is-ancestor", self.shas[number], pushed)  # raises when it is not an ancestor
        self.assertEqual((self.remote(1), self.remote(2)), (self.shas[1], self.shas[2]))
        self.assertEqual(git(self.code, "rev-parse", "HEAD"), here)
        self.assertEqual(git(self.code, "status", "--porcelain"), "")
        self.assertIn(pushed[:12], run.out)

    def test_a_conflict_between_heads_is_refused_and_nothing_is_pushed(self) -> None:
        """Prevents a conflicted union reaching the forge: the refusal names the pull request and the file, exit 2."""
        self.pull_request(1, "f1.txt", "1\n")
        self.pull_request(2, "same.txt", "two\n")
        self.pull_request(3, "same.txt", "three\n")
        run = self.w.qew("pr", "union", "1", "2", "3")
        self.assertEqual(run.code, 2, run)
        self.assertIn("same.txt", run.err)
        self.assertIn("pr #3", run.err)
        self.assertEqual(self.remote(1), self.shas[1])

    def test_heads_that_already_form_a_union_push_nothing(self) -> None:
        """Prevents a pointless rewrite: when one head already contains the others the command says which pull request to merge."""
        self.pull_request(1, "f1.txt", "1\n")
        self.pull_request(2, "f2.txt", "2\n", parent=self.shas[1])
        run = self.w.qew("pr", "union", "1", "2")
        self.assertEqual(run.code, 0, run)
        self.assertIn("pr #2 already contains", run.out)
        self.assertEqual((self.remote(1), self.remote(2)), (self.shas[1], self.shas[2]))

    def test_someone_elses_lead_needs_the_override_and_a_dry_run_pushes_nothing(self) -> None:
        """Prevents rewriting another actor's branch silently: refused without --other-author --reason; --dry-run never pushes."""
        self.pull_request(1, "f1.txt", "1\n")
        self.pull_request(2, "f2.txt", "2\n")
        refused = self.w.qew("pr", "union", "1", "2", actor="stranger")
        self.assertNotEqual(refused.code, 0, refused)
        self.assertEqual(self.remote(1), self.shas[1])
        dry = self.w.qew("pr", "union", "1", "2", "--dry-run")
        self.assertEqual(dry.code, 0, dry)
        self.assertEqual(self.remote(1), self.shas[1])
        done = self.w.qew("pr", "union", "1", "2", "--other-author", "--reason", "integrator batch", actor="stranger")
        self.assertEqual(done.code, 0, done)
        self.assertNotEqual(self.remote(1), self.shas[1])


if __name__ == "__main__":
    unittest.main()
