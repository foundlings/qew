"""Walks of branch cleanup: `qew pr merge` removes its head branch, `qew branch prune` clears the backlog.

Adversary: deleting the base, the target or the default branch, a head another open pull request still
uses, an unmerged branch, a deletion on a dry run, a forge that fails half way, and a failed delete
turning a successful merge into an error.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.forge import ForgeUnavailable, PullRequestSpec, Unsupported
from tests.pr_harness import PrWorld


def other_pull(head: str, base: str = "release") -> PullRequestSpec:
    return PullRequestSpec(head, base, "Other work", "", False)


def ready_world(case: unittest.TestCase) -> PrWorld:
    """A world with pull request 1 ready to merge from branch `feature` into `main`, both present on the forge."""
    w = PrWorld()
    case.addCleanup(w.close)
    item = w.item("Work")
    case.assertEqual(w.open(item).code, 0)
    sha = w.commit("work")
    w.forge.edit_pull(1, head_sha=sha)
    case.assertEqual(w.qew("evidence", item, "--change", sha).code, 0)
    case.assertEqual(w.qew("pr", "ready", "1").code, 0)
    w.forge.add_branch("feature")
    w.forge.add_branch("main")
    w.forge.add_branch("release")
    return w


class MergeBranchWalk(unittest.TestCase):
    def test_a_merge_removes_its_head_branch_and_says_so(self) -> None:
        """Prevents merged branches piling up on the forge."""
        w = ready_world(self)
        run = w.qew("pr", "merge", "1")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(w.forge.deleted_branches, ["feature"])
        self.assertNotIn("feature", w.forge.branches)
        self.assertIn("branch feature deleted", run.out)

    def test_a_head_another_open_pull_request_uses_survives_the_merge(self) -> None:
        """Prevents closing a second pull request by deleting the branch under it."""
        w = ready_world(self)
        w.forge.open_pull_request(other_pull("feature"))
        run = w.qew("pr", "merge", "1")
        self.assertEqual(run.code, 0, run)
        self.assertIn("feature", w.forge.branches)
        self.assertEqual(w.forge.deleted_branches, [])
        self.assertIn("branch feature kept", run.out)

    def test_the_base_and_the_target_branch_are_never_deleted(self) -> None:
        """Prevents a pull request whose head is a long-lived branch deleting it."""
        for head in ("release", "main"):  # the pull request's own base, and the configured target branch
            with self.subTest(head=head):
                w = ready_world(self)
                w.forge.edit_pull(1, head_branch=head, base_branch="release")
                run = w.qew("pr", "merge", "1")
                self.assertEqual(run.code, 0, run)
                self.assertEqual(w.forge.deleted_branches, [])
                self.assertIn(head, w.forge.branches)

    def test_a_forge_that_cannot_delete_only_warns_and_the_merge_stands(self) -> None:
        """Prevents a cleanup failure from reporting a landed merge as failed."""
        for name in ("unsupported", "forge down"):
            with self.subTest(name):
                w = ready_world(self)
                if name == "unsupported":
                    w.forge.raise_on["delete_branch"] = Unsupported("delete_branch")
                else:
                    w.forge.raise_on["delete_branch"] = ForgeUnavailable("down")
                run = w.qew("pr", "merge", "1")
                self.assertEqual(run.code, 0, run)
                self.assertIn("pr #1 merged", run.out)
                self.assertIn("branch feature not deleted", run.err)
                self.assertEqual(len(w.forge.merged), 1)


class PruneWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        forge = self.w.forge
        forge.add_branch("main")
        forge.add_branch("done-a", ("main",))
        forge.add_branch("done-b", ("main",))
        forge.add_branch("wip")
        forge.add_branch("in-review", ("main",))
        forge.add_branch("locked", ("main",))
        forge.protected_branches.add("locked")
        forge.open_pull_request(other_pull("in-review", "main"))

    def names(self) -> list:
        return sorted(self.w.forge.branches)

    def test_prune_deletes_only_merged_unused_branches_and_prints_each_name(self) -> None:
        """Prevents losing unmerged work, a branch under review or a protected branch."""
        run = self.w.qew("branch", "prune")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.names(), ["in-review", "locked", "main", "wip"])
        for name in ("done-a", "done-b"):
            self.assertIn(f"deleted {name}", run.out)
        self.assertNotIn("wip", run.out)
        self.assertEqual(self.w.qew("branch", "prune").out.count("deleted"), 0)

    def test_the_base_and_the_default_branch_are_never_deleted_even_when_merged(self) -> None:
        """Prevents pruning the branch everything else is measured against, or the repository's default."""
        forge = self.w.forge
        forge.default_branch = "trunk"
        forge.add_branch("trunk", ("main",))
        self.assertEqual(self.w.qew("branch", "prune").code, 0)
        self.assertIn("trunk", forge.branches)
        self.assertIn("main", forge.branches)
        forge.add_branch("develop")
        forge.add_branch("main", ("develop",))  # the target branch is merged into the base: still kept
        forge.add_branch("done-c", ("develop",))
        forge.add_branch("trunk", ("develop",))
        run = self.w.qew("branch", "prune", "--base", "develop")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(forge.deleted_branches[-1], "done-c")
        for kept in ("trunk", "main", "develop"):
            self.assertIn(kept, forge.branches)

    def test_dry_run_lists_what_would_go_and_deletes_nothing(self) -> None:
        """Prevents a preview that deletes."""
        before = self.names()
        run = self.w.qew("branch", "prune", "--dry-run")
        self.assertEqual(run.code, 0, run)
        self.assertIn("would delete done-a", run.out)
        self.assertIn("would delete done-b", run.out)
        self.assertEqual(self.names(), before)
        self.assertEqual(self.w.forge.count("delete_branch"), 0)

    def test_a_forge_that_is_down_leaves_every_branch(self) -> None:
        """Prevents guessing a branch is merged when the forge could not say."""
        before = self.names()
        self.w.forge.down = True
        run = self.w.qew("branch", "prune")
        self.assertEqual(run.code, 1, run)
        self.assertEqual(self.w.forge.deleted_branches, [])
        self.w.forge.down = False
        self.assertEqual(self.names(), before)

    def test_a_forge_without_branch_support_is_reported_not_failed(self) -> None:
        """Prevents a GitLab target breaking a multi-target prune."""
        self.w.forge.supports_branches = False
        run = self.w.qew("branch", "prune")
        self.assertEqual(run.code, 0, run)
        self.assertIn("branches unsupported", run.out)


if __name__ == "__main__":
    unittest.main()
