"""The forge's issue timeline is recorded once, when an issue is first imported: who did what and when, without state effect.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class TimelineImportWalk(unittest.TestCase):
    def rig(self, options: str = ""):
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n{options}'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        issue = fake.new_issue({"title": "Filed on GitHub", "body": "human text"})
        issue["timeline"] = [
            {"event": "labeled", "actor": {"login": "dana"}, "created_at": "2025-03-01T09:00:00Z", "label": {"name": "bug"}},
            {"event": "assigned", "actor": {"login": "erik"}, "created_at": "2025-03-02T09:00:00Z", "assignee": {"login": "frida"}},
            {"event": "renamed", "actor": {"login": "dana"}, "created_at": "2025-03-03T09:00:00Z", "rename": {"from": "old title", "to": "Filed on GitHub"}},
        ]
        return fake, world

    def imported(self, world: World) -> str:
        return world.run("list").stdout.split()[0]

    def test_the_timeline_of_an_imported_issue_is_recorded_once_and_listed(self) -> None:
        """Prevents lost provenance on import: who labeled, assigned and renamed, with times, once and without state effect."""
        fake, world = self.rig()
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        item = self.imported(world)
        events = [e for e in world.events(item) if e["type"] == "forge_timeline"]
        self.assertEqual([(e["kind"], e["actor"], e["when"]) for e in events], [
            ("labeled", "dana", "2025-03-01T09:00:00Z"), ("assigned", "erik", "2025-03-02T09:00:00Z"), ("renamed", "dana", "2025-03-03T09:00:00Z")])
        shown = world.run("show", item).stdout
        self.assertIn("2025-03-01T09:00:00Z dana labeled bug", shown)
        self.assertIn("2025-03-03T09:00:00Z dana renamed old title -> Filed on GitHub", shown)
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        self.assertEqual(len([e for e in world.events(item) if e["type"] == "forge_timeline"]), 3)
        self.assertEqual(world.run("check").returncode, 0)
        self.assertIn("divergences=0", world.run("status").stdout)

    def test_a_mirror_can_switch_it_off(self) -> None:
        """Prevents a surprise call per imported issue on a large repository: `timeline = false` reads none."""
        fake, world = self.rig("timeline = false\n")
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        item = self.imported(world)
        self.assertEqual([e for e in world.events(item) if e["type"] == "forge_timeline"], [])
        self.assertFalse(any("/events" in path for _m, path, _b in fake.requests))


if __name__ == "__main__":
    unittest.main()
