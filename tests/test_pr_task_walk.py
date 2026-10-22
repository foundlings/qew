"""A task's pull request opens, readies and merges through qew without the task being an issue on the forge.

Each test is a scenario; its docstring names the failure it prevents. The mirror selects epics and issues only, so the task has
no issue of its own: the pull request is recorded on the task, and the issue number and target come from the nearest mirrored
ancestor, as task claims do.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld

BASE = dict(state="open", draft=False, mergeable=True, checks="passing", body="")


class TaskPullRequestWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(mirror_options='select = "kind:epic,issue"\n')
        self.addCleanup(self.w.close)
        self.epic = self.w.item("Phase", kind="epic", claim=False)
        self.task = self.w.item("Part", kind="task", parent=self.epic)

    def test_a_task_pull_request_opens_readies_and_merges_through_qew(self) -> None:
        """Prevents the QEW_ALLOW_FORGE_BYPASS detour: open, ready and merge all work for a ledger-only task."""
        opened = self.w.open(self.task)
        self.assertEqual(opened.code, 0, opened)
        self.assertEqual(len(self.w.mirror_files(self.task)), 1)
        self.assertEqual(self.w.mirror_files(self.epic), [])
        epic_issue = self.w.ledger.load_mirrors(self.epic)[0]["number"]
        self.assertIn(f"#{epic_issue}", self.w.forge.pulls[1]["body"] if isinstance(self.w.forge.pulls[1], dict) else str(self.w.forge.pulls[1]))
        sha = self.w.commit("work")
        self.w.forge.edit_pull(1, head_sha=sha, **BASE)
        self.assertEqual(self.w.qew("evidence", self.task, "--change", sha).code, 0)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)
        merged = self.w.qew("pr", "merge", "1")
        self.assertEqual(merged.code, 0, merged)
        self.assertEqual(len(self.w.forge.merged), 1)

    def test_an_issue_the_mirror_selects_but_has_not_synced_still_asks_for_sync(self) -> None:
        """Prevents borrowing the parent's issue for work that should have its own: only unselected kinds fall back."""
        unsynced = self.w.item("New issue", kind="issue", parent=self.epic, sync=False)
        refused = self.w.open(unsynced)
        self.assertEqual(refused.code, 2, refused)
        self.assertIn("qew push", refused.err)


if __name__ == "__main__":
    unittest.main()
