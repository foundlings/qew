"""A mirrored epic or issue shows its subtree's progress: tasks stay ledger-only, yet the board column follows them.

Each test is a scenario; its docstring names the failure it prevents. The parent's own state never changes: this is the
derived display state (`sync_plan.column`) shared by sync, the offline board and `qew ready`.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import cmd_project, cmd_sync, ledger_config, sync as sync_module, sync_plan
from qew.forge_github import GitHub
from qew.forge_http import urllib_transport
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class RollupWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def status(self, item: str) -> str:
        return sync_plan.column(Ledger(self.world.ledger, "TST").snapshot(), item)

    def state(self, item: str) -> str:
        return Ledger(self.world.ledger, "TST").snapshot().folds[item].state.value

    def make(self, kind: str, *arguments: str) -> str:
        return self.world.create("--kind", kind, "--title", f"{kind} work", *arguments, "--quote", QUOTE)

    def tree(self) -> tuple[str, str, list[str]]:
        epic = self.make("epic")
        issue = self.make("issue", "--parent", epic)
        tasks = [self.make("task", "--parent", issue) for _ in range(3)]
        return epic, issue, tasks

    def finish(self, task: str) -> None:
        self.assertEqual(self.world.run("start", task).returncode, 0)
        self.assertEqual(self.world.run("complete", task, "--no-commit", "no code needed").returncode, 0)

    def test_a_claimed_task_anywhere_below_moves_every_ancestor_to_in_progress(self) -> None:
        """Prevents 26 of 29 open epics sitting in Ready: a claimed task two levels down shows on the issue and the epic."""
        epic, issue, tasks = self.tree()
        self.assertEqual((self.status(epic), self.status(issue)), ("Backlog", "Ready"))
        self.assertEqual(self.world.run("start", tasks[0]).returncode, 0)
        self.assertEqual((self.status(epic), self.status(issue)), ("In progress", "In progress"))
        self.assertEqual((self.state(epic), self.state(issue)), ("approved", "approved"))

    def test_finished_work_with_open_work_left_is_in_progress(self) -> None:
        """Prevents progress hiding between claims: one task done and others still open is In progress, nobody claiming."""
        epic, issue, tasks = self.tree()
        self.finish(tasks[0])
        self.assertEqual((self.status(epic), self.status(issue)), ("In progress", "In progress"))

    def test_open_work_that_is_all_gated_shows_gated(self) -> None:
        """Prevents a gate nobody sees: when everything still open waits on something outside, the parent is Gated."""
        epic, issue, tasks = self.tree()
        for task in tasks:
            self.assertEqual(self.world.run("set", task, "--add-label", "gated", "--reason", "waits on a key").returncode, 0)
        self.assertEqual((self.status(epic), self.status(issue)), ("Gated", "Gated"))
        self.assertEqual(self.world.run("set", tasks[2], "--remove-label", "gated").returncode, 0)
        self.assertEqual((self.status(epic), self.status(issue)), ("Backlog", "Ready"))

    def test_a_claim_beats_a_gate_and_done_plus_gated_remainder_is_gated(self) -> None:
        """Prevents the wrong word: someone working shows In progress; finished work with only gated work left shows Gated."""
        epic, issue, tasks = self.tree()
        self.finish(tasks[0])
        for task in tasks[1:]:
            self.world.run("set", task, "--add-label", "gated", "--reason", "waits on a key")
        self.assertEqual((self.status(epic), self.status(issue)), ("Gated", "Gated"))

    def test_the_parents_own_labels_and_state_still_win(self) -> None:
        """Prevents the roll-up overriding a human gate: needs:approval on the issue stays, a done parent stays Done."""
        epic, issue, tasks = self.tree()
        self.world.run("set", issue, "--add-label", "needs:approval")
        self.world.run("start", tasks[0])
        self.assertEqual(self.status(issue), "Needs approval")
        self.assertEqual(self.status(epic), "In progress")

    def test_an_item_without_children_is_as_before(self) -> None:
        """Prevents the roll-up touching leaves: a plain issue is Ready, a claimed one In progress."""
        plain = self.make("issue")
        self.assertEqual(self.status(plain), "Ready")


class RollupSyncWalk(unittest.TestCase):
    def test_the_mirrored_issue_card_follows_a_ledger_only_task(self) -> None:
        """Prevents the board never showing task progress: claiming a ledger-only task moves the mirrored issue's card."""
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\nselect = "kind:epic,issue"\n'
        world = World(mirrors)
        self.addCleanup(world.close)
        sync_module.READBACK_DELAY = 0.0
        sync_module.READBACK_ATTEMPTS = 3
        issue = world.create("--kind", "issue", "--title", "Mirrored", "--quote", QUOTE)
        task = world.create("--kind", "task", "--title", "Ledger only", "--parent", issue, "--quote", QUOTE)

        def sync():
            config = ledger_config.load(world.code)
            if not (world.ledger / "boards").exists():
                cmd_project.ensure(config, None, build=lambda m: GitHub(m, urllib_transport(), ENV))
            mirror = cmd_sync.mirrors_with_boards(Ledger(world.ledger, "TST"), config)[0]
            forge = GitHub(mirror, urllib_transport(), ENV)
            return sync_target(Ledger(world.ledger, "TST"), Target(mirror, ledger_config.mirror_slug(mirror), forge), config)

        self.assertEqual(sync().failures, [])
        node = fake.issues[1]["node_id"]
        self.assertEqual(fake.item_value("1", node, "Status"), "Ready")
        self.assertEqual(world.run("start", task).returncode, 0)
        self.assertEqual(sync().failures, [])
        self.assertEqual(fake.item_value("1", node, "Status"), "In progress")


if __name__ == "__main__":
    unittest.main()
