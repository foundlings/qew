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
from qew import pr_links


class RetargetWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
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
        opened = self.w.open(item, "--closes", item)
        self.assertEqual(opened.code, 0, opened)
        self.w.forge.edit_pull(number, head_sha=sha, base_branch=base)
        git(self.code, "checkout", "-q", "--detach", self.base)
        return item

    def record(self, item: str) -> dict:
        return next(m for m in self.w.ledger.load_mirrors(item) if m["kind"] == "pr")

    def duplicate_as_hostless(self, item: str, **changes: object) -> None:
        linked = self.record(item)
        key = linked["_target"]
        record = {
            name: linked[name]
            for name in ("forge", "url", "number", "pr_url", "head", "opener", "draft", "state", "synced_at")
        }
        record.update(changes)
        pr_links.declare_pr(self.w.ledger, key, record)

    def test_a_stacked_pull_request_moves_to_main_and_the_ledger_records_it(self) -> None:
        """Prevents a stacked pull request being stuck on its lane branch: the forge base and the ledger record both say main."""
        self.pull_request(1, None)
        item = self.pull_request(2, "branch-1", base="branch-1")
        run = self.w.qew("pr", "retarget", "2", "--base", "main")
        self.assertEqual(run.code, 0, run)
        self.assertIn("main", run.out)
        self.assertEqual(self.w.forge.pulls[2].base_branch, "main")
        self.assertEqual(self.record(item).get("base"), "main")
        self.assertEqual(self.w.qew("check").code, 0)
        writes = self.w.forge.count("update_pr_base")
        again = self.w.qew("pr", "retarget", "2", "--base", "main")
        self.assertEqual(again.code, 0, again)
        self.assertEqual(self.w.forge.count("update_pr_base"), writes)

    def test_a_union_over_a_stacked_member_names_the_retarget_that_would_let_it_join(self) -> None:
        """Prevents the dead-end refusal `do not share one base branch`: it names the member whose base is another member's branch and the command."""
        self.pull_request(1, None)
        self.pull_request(2, "branch-1", base="branch-1")
        run = self.w.qew("pr", "union", "1", "2")
        self.assertEqual(run.code, 2, run)
        self.assertIn("share one base branch", run.err)
        self.assertIn("qew pr retarget 2 --base main", run.err)

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

    def test_matching_hostless_and_item_linked_records_resolve_to_the_item_record(self) -> None:
        """A stale hostless mirror of the same PR must not make target identity ambiguous."""
        item = self.pull_request(1, None, base="branch-1")
        self.duplicate_as_hostless(item)

        retargeted = self.w.qew("pr", "retarget", "1", "--base", "main")
        self.assertEqual(retargeted.code, 0, retargeted)
        self.assertEqual(self.w.forge.pulls[1].base_branch, "main")

        readied = self.w.qew("pr", "ready", "1")
        self.assertEqual(readied.code, 0, readied)
        self.assertFalse(self.w.forge.pulls[1].draft)

        body_file = self.code / "body.md"
        body_file.write_text("Updated body\n", encoding="utf-8")
        replaced = self.w.qew("pr", "body", "1", "--body-file", str(body_file))
        self.assertEqual(replaced.code, 0, replaced)
        self.assertEqual(self.w.forge.pulls[1].body, "Updated body\n")

    def test_hostless_and_item_linked_records_with_different_heads_fail_closed(self) -> None:
        """A duplicate number cannot hide a contradictory head behind target deduplication."""
        item = self.pull_request(1, None)
        self.duplicate_as_hostless(item, head="other-head")

        run = self.w.qew("pr", "retarget", "1", "--base", "main")
        self.assertEqual(run.code, 2, run)
        self.assertIn("conflicting", run.err)
        self.assertEqual(self.w.forge.count("update_pr_base"), 0)

    def test_hostless_and_item_linked_records_with_different_states_fail_closed(self) -> None:
        """A duplicate number with contradictory open/draft state is never guessed between."""
        item = self.pull_request(1, None)
        self.duplicate_as_hostless(item, state="merged")

        run = self.w.qew("pr", "ready", "1")
        self.assertEqual(run.code, 2, run)
        self.assertIn("conflicting", run.err)
        self.assertTrue(self.w.forge.pulls[1].draft)


if __name__ == "__main__":
    unittest.main()
