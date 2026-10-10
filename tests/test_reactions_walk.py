"""Reactions on a forge issue are shown, never pushed and never work state.

Each test is a scenario; its docstring names the failure it prevents. The counts ride in the issue payload the pull already
reads, are kept in the local mirror state, and `qew show` prints them.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class ReactionsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.item = self.world.create("--kind", "issue", "--title", "Popular", "--quote", QUOTE)

    def sync(self) -> None:
        result = self.world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_counts_are_shown_follow_the_forge_and_disappear_at_zero(self) -> None:
        """Prevents invisible interest: a reaction made on GitHub shows in qew show, changes with it and clears."""
        self.sync()
        issue = next(iter(self.fake.issues.values()))
        issue["reactions"] = {"+1": 3, "heart": 1, "eyes": 0}
        self.sync()
        self.assertIn("reactions: +1 3, heart 1", self.world.run("show", self.item).stdout)
        issue["reactions"] = {"+1": 4}
        self.sync()
        self.assertIn("reactions: +1 4", self.world.run("show", self.item).stdout)
        issue["reactions"] = {}
        self.sync()
        self.assertNotIn("reactions:", self.world.run("show", self.item).stdout)

    def test_a_reaction_is_not_work_state(self) -> None:
        """Prevents noise becoming history: reactions add no event, no divergence, nothing pending."""
        self.sync()
        events = len(self.world.events(self.item))
        issue = next(iter(self.fake.issues.values()))
        issue["reactions"] = {"hooray": 2}
        self.sync()
        status = self.world.run("status").stdout
        self.assertIn("divergences=0", status)
        self.assertIn("pending=0", status)
        self.assertEqual(len(self.world.events(self.item)), events)
        data = json.loads(self.world.run("show", self.item, "--json").stdout)
        self.assertEqual(data["reactions"], {"hooray": 2})


if __name__ == "__main__":
    unittest.main()
