"""Walks of `qew pr retarget N --base BRANCH`: a pull request's base moves on the forge and the ledger records it.

Adversary: a stacked pull request that can never join a union because its base is another branch, a retarget that silently drops
the commits of the old base (a head that does not contain the merge base of old and new base), a finished pull request
rewritten, and a forge that says changed while the base stayed.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import git
from tests.pr_harness import PrWorld


class RetargetWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.code = self.w.world.code
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        bare = Path(scratch.name) / "remote.git"
        git(Path(scratch.name), "init", "-q", "--bare", str(bare))
        git(self.code, "remote", "add", "origin", str(bare))
        self.write("base.txt", "base\n", "base")
        git(self.code, "push", "-q", "origin", "HEAD:refs/heads/main")
        self.base = git(self.code, "rev-parse", "HEAD")

    def write(self, name: str, text: str, message: str) -> None:
        (self.code / name).write_text(text, encoding="utf-8")
        git(self.code, "add", name)
        git(self.code, "commit", "-q", "-m", message)

    def pull_request(self, number: int, parent: str | None, base: str = "main", orphan: bool = False) -> str:
        item = self.w.item(f"Work {number}")
        if orphan:
            git(self.code, "checkout", "-q", "--orphan", f"branch-{number}")
            git(self.code, "rm", "-rqf", ".")
        else:
            git(self.code, "checkout", "-q", "-b", f"branch-{number}", parent or self.base)
        self.write(f"f{number}.txt", f"{number}\n", f"work {number}")
        sha = git(self.code, "rev-parse", "HEAD")
        git(self.code, "push", "-q", "origin", f"branch-{number}")
        self.w.head = f"branch-{number}"
        self.assertEqual(self.w.open(item).code, 0)
        self.w.forge.edit_pull(number, head_sha=sha, base_branch=base)
        git(self.code, "checkout", "-q", "--detach", self.base)
        return item

    def record(self, item: str) -> dict:
        return next(m for m in self.w.ledger.load_mirrors(item) if m["kind"] == "pr")

    def test_a_stacked_pull_request_moves_to_main_and_the_ledger_records_it(self) -> None:
        """Prevents a stacked pull request being stuck on its lane branch: the forge base and the ledger record both say main."""
        self.pull_request(1, None)
        item = self.pull_request(2, "branch-1", base="branch-1")
        run = self.w.qew("pr", "retarget", "2", "--base", "main")
        self.assertEqual(run.code, 0, run)
        self.assertIn("main", run.out)
        self.assertEqual(self.w.forge.pulls[2].base_branch, "main")
        self.assertEqual(self.record(item).get("base"), "main")
        writes = self.w.forge.count("update_pr_base")
        again = self.w.qew("pr", "retarget", "2", "--base", "main")
        self.assertEqual(again.code, 0, again)
        self.assertEqual(self.w.forge.count("update_pr_base"), writes)

    def test_a_head_without_the_old_and_new_bases_common_ancestor_is_refused_and_nothing_changes(self) -> None:
        """Prevents retargeting a head that does not contain the merge base of old and new base: the pull request would no longer show
        what it replaces."""
        self.pull_request(1, None)
        item = self.pull_request(2, None, base="branch-1", orphan=True)
        run = self.w.qew("pr", "retarget", "2", "--base", "main")
        self.assertEqual(run.code, 2, run)
        self.assertIn("merge base", run.err)
        self.assertEqual(self.w.forge.pulls[2].base_branch, "branch-1")
        self.assertIsNone(self.record(item).get("base"))

    def test_a_finished_pull_request_is_never_retargeted(self) -> None:
        """Prevents rewriting a merged pull request's history on the forge."""
        self.pull_request(1, None)
        self.w.forge.edit_pull(1, state="merged")
        run = self.w.qew("pr", "retarget", "1", "--base", "other")
        self.assertEqual(run.code, 2, run)
        self.assertEqual(self.w.forge.count("update_pr_base"), 0)

    def test_a_forge_that_keeps_the_old_base_is_an_indeterminate_failure(self) -> None:
        """Prevents false success when the forge accepted the call and changed nothing."""
        self.pull_request(1, None)
        self.pull_request(2, "branch-1", base="branch-1")
        self.w.forge.base_write_ignored = True
        run = self.w.qew("pr", "retarget", "2", "--base", "main")
        self.assertEqual(run.code, 4, run)
        self.assertEqual(self.w.forge.pulls[2].base_branch, "branch-1")


if __name__ == "__main__":
    unittest.main()
