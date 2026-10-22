"""The body edit history of a newly imported issue is recorded once as body_revision events; the current body stays the item body.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class BodyHistoryWalk(unittest.TestCase):
    def rig(self, options: str = ""):
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n{options}'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        issue = fake.new_issue({"title": "Edited on GitHub", "body": "third wording"})
        issue["body_edits"] = [
            {"editor": "dana", "at": "2025-04-02T09:00:00Z", "text": "second wording"},
            {"editor": "erik", "at": "2025-04-01T09:00:00Z", "text": "first wording " + "x" * 9000},
        ]
        return fake, world

    def sync(self, world: World) -> None:
        result = world.run("sync", "--all", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_prior_bodies_are_kept_oldest_first_with_editor_time_hash_and_a_text_limit(self) -> None:
        """Prevents losing what the body used to say: each earlier wording is a revision, once, bounded, not the item body."""
        _fake, world = self.rig()
        self.sync(world)
        item = world.run("list").stdout.split()[0]
        revisions = [e for e in world.events(item) if e["type"] == "body_revision"]
        self.assertEqual([(r["who"], r["when"]) for r in revisions], [("erik", "2025-04-01T09:00:00Z"), ("dana", "2025-04-02T09:00:00Z")])
        self.assertTrue(revisions[0]["text"].startswith("first wording"))
        self.assertLessEqual(len(revisions[0]["text"]), 4000)
        self.assertEqual(len(revisions[0]["hash"]), 64)
        self.assertEqual(revisions[1]["text"], "second wording")
        self.assertEqual(json.loads(world.run("show", item, "--json").stdout)["body"].strip(), "third wording")
        self.sync(world)
        self.assertEqual(len([e for e in world.events(item) if e["type"] == "body_revision"]), 2)
        self.assertEqual(world.run("check").returncode, 0)

    def test_a_mirror_can_switch_it_off(self) -> None:
        """Prevents a GraphQL call per imported issue on a big repository: `body_history = false` makes none."""
        fake, world = self.rig("body_history = false\n")
        self.sync(world)
        item = world.run("list").stdout.split()[0]
        self.assertEqual([e for e in world.events(item) if e["type"] == "body_revision"], [])
        self.assertNotIn("IssueBodyHistory", fake.graphql_operations())


if __name__ == "__main__":
    unittest.main()
