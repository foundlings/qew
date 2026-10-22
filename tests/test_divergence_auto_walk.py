"""Sync settles divergences itself: the ledger is the source of truth, so a forge-side change is undone in the same sync.

Each test is a scenario; its docstring names the failure it prevents. `[replication] divergences = "report"` keeps the old
behavior (the divergence waits for `qew accept` or `qew reject`).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"


class DivergenceAutoWalk(unittest.TestCase):
    def rig(self, replication: str = OFF):
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        world = World(mirrors, replication=replication)
        self.addCleanup(world.close)
        item = world.create("--kind", "issue", "--title", "Mirrored work", "--quote", QUOTE)
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)
        return fake, world, item

    def test_a_human_closing_the_issue_is_undone_in_the_same_sync_and_recorded(self) -> None:
        """Prevents divergences piling up for a human to settle: the ledger says open, so the sync reopens the issue."""
        fake, world, item = self.rig()
        issue = next(iter(fake.issues.values()))
        fake.set_state(issue, "closed", "not_planned")
        result = world.run("sync", "--all", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(issue["state"], "open")
        self.assertIn("divergences=0", world.run("status").stdout)
        settled = [e for e in world.events(item) if e["type"] == "divergence_rejected"]
        self.assertEqual(len(settled), 1)
        self.assertIn("auto", settled[0].get("reason", ""))
        self.assertEqual(world.run("check").returncode, 0)

    def test_report_mode_keeps_the_divergence_for_a_human(self) -> None:
        """Prevents surprising anyone who wants to review forge-side edits: `divergences = "report"` changes nothing on the forge."""
        fake, world, item = self.rig(OFF + 'divergences = "report"\n')
        issue = next(iter(fake.issues.values()))
        fake.set_state(issue, "closed", "not_planned")
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)
        self.assertEqual(issue["state"], "closed")
        self.assertIn("divergences=1", world.run("status").stdout)


if __name__ == "__main__":
    unittest.main()
