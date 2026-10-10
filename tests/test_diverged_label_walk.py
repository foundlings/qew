"""An issue with an open divergence leaves the board, is labeled qew:diverged and (in managed mode) loses qew:managed.

Each test is a scenario; its docstring names the failure it prevents. Divergences wait for a human only in `report` mode; the
default settles them in the same sync, so nothing is labeled then.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
REPORT = '\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = "report"\n'


class DivergedLabelWalk(unittest.TestCase):
    def rig(self, options: str = ""):
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n{options}'
        world = World(mirrors, replication=REPORT)
        self.addCleanup(world.close)
        self.assertEqual(world.run("project", "ensure", env=ENV).returncode, 0)
        item = world.create("--kind", "issue", "--title", "Mirrored work", "--quote", QUOTE)
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        issue = next(iter(fake.issues.values()))
        return fake, world, item, issue

    def card(self, fake: FakeGitHub, issue: dict):
        return fake.item_value("1", issue["node_id"], "Status")

    def test_a_diverged_issue_is_labeled_taken_off_the_board_and_comes_back_when_settled(self) -> None:
        """Prevents a contested issue sitting on the board as if it were fine: label, no card, restored after the decision."""
        fake, world, item, issue = self.rig()
        self.assertEqual(self.card(fake, issue), "Ready")
        fake.set_state(issue, "closed", "not_planned")
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        self.assertIn("divergences=1", world.run("status").stdout)
        self.assertIn("qew:diverged", issue["labels"])
        self.assertIsNone(self.card(fake, issue))
        self.assertEqual(issue["state"], "closed")  # report mode: the forge value is kept until a human decides
        self.assertEqual(world.run("reject", item).returncode, 0)
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        self.assertNotIn("qew:diverged", issue["labels"])
        self.assertEqual(issue["state"], "open")
        self.assertEqual(self.card(fake, issue), "Ready")

    def test_managed_mode_also_drops_qew_managed_so_filtered_views_hide_it(self) -> None:
        """Prevents board views that filter on qew:managed still showing a contested issue: the label goes and returns."""
        fake, world, item, issue = self.rig('mode = "overlay"\n')
        self.assertIn("qew:managed", issue["labels"])
        fake.set_state(issue, "closed", "not_planned")
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        self.assertIn("qew:diverged", issue["labels"])
        self.assertNotIn("qew:managed", issue["labels"])
        self.assertEqual(world.run("reject", item).returncode, 0)
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        self.assertIn("qew:managed", issue["labels"])
        self.assertNotIn("qew:diverged", issue["labels"])

    def test_the_default_settles_the_divergence_in_the_same_sync_so_nothing_is_labeled(self) -> None:
        """Prevents noise in the normal case: with the default policy the issue is restored and never carries the label."""
        fake, world, item, issue = self.rig()
        config = world.code / ".qew" / "config.toml"
        config.write_text(config.read_text().replace('divergences = "report"\n', ""), encoding="utf-8")
        fake.set_state(issue, "closed", "not_planned")
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        self.assertNotIn("qew:diverged", issue["labels"])
        self.assertEqual(issue["state"], "open")
        self.assertEqual(self.card(fake, issue), "Ready")


if __name__ == "__main__":
    unittest.main()
