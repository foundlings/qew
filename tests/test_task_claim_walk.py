"""A ledger-only task is claimed through its nearest mirrored ancestor when a forge is the claim authority.

Each test is a scenario; its docstring names the failure it prevents. Mirrors that select only epics and issues never give a
task an issue of its own, so the claim reservation (a marker comment naming the task) lives on the issue above it.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class TaskClaimWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\nselect = "kind:epic,issue"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        slug = ledger_config.mirror_slug(ledger_config.load(self.world.code).mirrors[0])
        config = self.world.code / ".qew" / "config.toml"
        config.write_text(config.read_text() + f'\n[authority]\nprimary = "{slug}"\n', encoding="utf-8")
        self.epic = self.world.create("--kind", "epic", "--title", "Phase", "--quote", QUOTE)
        self.task = self.world.create("--kind", "task", "--title", "Ledger-only work", "--parent", self.epic, "--quote", QUOTE)
        self.assertEqual(self.world.run("sync", env=ENV).returncode, 0)

    def comments(self) -> list[str]:
        return [c["body"] for issue in self.fake.issues.values() for c in issue["discussion"]]

    def test_a_task_without_an_issue_is_claimed_through_the_issue_above_it(self) -> None:
        """Prevents tasks that nobody can ever start: the claim marker names the task and sits on the epic's issue."""
        self.assertEqual(len(self.fake.issues), 1)
        started = self.world.run("start", self.task, env=ENV)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertTrue(any(f"qew:claim {self.task} tester" in body for body in self.comments()), self.comments())

    def test_a_second_login_cannot_take_the_same_task_and_a_release_frees_it(self) -> None:
        """Prevents two agents on one task: the earlier marker wins, a release marker frees it for the next."""
        self.assertEqual(self.world.run("start", self.task, env=ENV).returncode, 0)
        rival = self.world.run("start", self.task, env={**ENV, "QEW_ACTOR": "rival"})
        self.assertNotEqual(rival.returncode, 0, rival.stdout + rival.stderr)
        freed = self.world.run("release", self.task, "--reason", "done for now", env=ENV)
        self.assertEqual(freed.returncode, 0, freed.stdout + freed.stderr)
        self.assertTrue(any(f"qew:release {self.task}" in body for body in self.comments()))
        self.assertEqual(self.world.run("start", self.task, env={**ENV, "QEW_ACTOR": "rival"}).returncode, 0)

    def test_a_task_with_no_mirrored_ancestor_still_says_what_to_do(self) -> None:
        """Prevents the misleading `run qew sync` hint: a parentless task is never mirrored, so the refusal names that cause and the remedy."""
        orphan = self.world.create("--kind", "task", "--title", "Floating", "--quote", QUOTE)
        refused = self.world.run("start", orphan, env=ENV)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn(f"{orphan} is a task", refused.stderr)
        self.assertIn("not mirrored", refused.stderr)
        self.assertIn(f"qew set {orphan} --parent", refused.stderr)
        self.assertNotIn("qew sync", refused.stderr)


if __name__ == "__main__":
    unittest.main()
