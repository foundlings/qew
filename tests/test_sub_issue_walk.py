"""The ledger parent shows as GitHub's native sub-issue relation; a human's change there is a divergence, never silent.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class SubIssueWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.epic = self.world.create("--kind", "epic", "--title", "Phase", "--quote", QUOTE)
        self.other = self.world.create("--kind", "epic", "--title", "Other phase", "--quote", QUOTE)
        self.child = self.world.create("--kind", "issue", "--title", "Work", "--parent", self.epic, "--quote", QUOTE)

    def sync(self) -> None:
        result = self.world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def number(self, title: str) -> int:
        return next(n for n, i in self.fake.issues.items() if i["title"] == title)

    def parent_of(self, title: str):
        return self.fake.issues[self.number(title)].get("parent")

    def sub_issue_calls(self) -> int:
        return sum(1 for _m, path, _b in self.fake.requests if "/sub_issue" in path)

    def test_a_child_becomes_a_sub_issue_of_its_parent_and_a_rerun_sends_nothing(self) -> None:
        """Prevents the forge showing a flat list: the ledger parent is GitHub's parent too, set once."""
        self.sync()
        self.assertEqual(self.parent_of("Work"), self.number("Phase"))
        calls = self.sub_issue_calls()
        self.assertGreater(calls, 0)
        self.sync()
        self.assertEqual(self.sub_issue_calls(), calls)

    def test_a_mirror_can_switch_the_mapping_off(self) -> None:
        """Prevents a surprise burst of mutating calls: `sub_issues = false` on the mirror sets no parents at all."""
        self.world.close()
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\nsub_issues = false\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        epic = self.world.create("--kind", "epic", "--title", "Quiet phase", "--quote", QUOTE)
        self.world.create("--kind", "issue", "--title", "Quiet work", "--parent", epic, "--quote", QUOTE)
        self.sync()
        self.assertEqual(self.sub_issue_calls(), 0)
        self.assertIn("Quiet work", [i["title"] for i in self.fake.issues.values()])

    def test_sub_issue_calls_go_through_the_create_pacing(self) -> None:
        """Prevents bypassing the pacer: with a limit of 1 create per minute and a short time budget, the sync stops as paced
        before it has set the parent (creates and parent calls share the limit)."""
        import time
        from qew import ledger_config
        from qew.forge_github import GitHub
        from qew.forge_http import urllib_transport
        from qew.ledger_store import Ledger
        from qew.sync import Target, sync_target
        config = ledger_config.load(self.world.code)
        mirror = config.mirrors[0]
        paced = type(mirror)(mirror.forge, mirror.repo, mirror.project, mirror.select, {**mirror.options, "create_per_minute": 2})
        target = Target(paced, ledger_config.mirror_slug(paced), GitHub(paced, urllib_transport(), ENV))
        report = sync_target(Ledger(self.world.ledger, "TST"), target, config, deadline=time.monotonic() + 2)
        self.assertIn("paced", report.stopped or "")
        self.assertEqual(self.sub_issue_calls(), 0)

    def test_moving_and_removing_the_parent_follow_the_ledger(self) -> None:
        """Prevents a stale tree on the forge: a new parent replaces the old, no parent removes the relation."""
        self.sync()
        self.assertEqual(self.world.run("set", self.child, "--parent", self.other).returncode, 0)
        self.sync()
        self.assertEqual(self.parent_of("Work"), self.number("Other phase"))
        self.assertEqual(self.world.run("set", self.child, "--no-parent").returncode, 0)
        self.sync()
        self.assertIsNone(self.parent_of("Work"))

    def test_a_parent_changed_on_github_is_a_divergence_and_reject_restores_it(self) -> None:
        """Prevents a silent override either way: the human's move is reported, `qew reject` puts the ledger's parent back."""
        self.sync()
        self.fake.issues[self.number("Work")]["parent"] = self.number("Other phase")
        self.sync()
        status = self.world.run("status").stdout
        self.assertIn("divergences=1", status)
        self.assertIn("parent", status)
        self.assertEqual(self.world.run("reject", self.child).returncode, 0)
        self.sync()
        self.assertEqual(self.parent_of("Work"), self.number("Phase"))
        self.assertIn("divergences=0", self.world.run("status").stdout)

    def test_accepting_a_parent_moved_on_github_makes_it_the_ledger_parent(self) -> None:
        """Prevents a one-way door: `qew accept` takes the human's move into the ledger and the next sync leaves it alone."""
        self.sync()
        self.fake.issues[self.number("Work")]["parent"] = self.number("Other phase")
        self.sync()
        self.assertEqual(self.world.run("accept", self.child).returncode, 0)
        self.assertIn(f"parent: {self.other}", self.world.run("show", self.child).stdout)
        calls = self.sub_issue_calls()
        self.sync()
        self.assertEqual(self.sub_issue_calls(), calls)
        self.assertEqual(self.parent_of("Work"), self.number("Other phase"))
        self.assertIn("divergences=0", self.world.run("status").stdout)


if __name__ == "__main__":
    unittest.main()
