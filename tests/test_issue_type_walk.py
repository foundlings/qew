"""GitHub issue types: a per-mirror map from ledger facts to type names, pushed, and a human's change is a divergence.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class IssueTypeWalk(unittest.TestCase):
    def rig(self, options: str = ""):
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n{options}'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        return fake, world

    def sync(self, world: World) -> None:
        result = world.run("sync", "--all", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_map_sets_the_type_by_priority_then_kind_and_a_change_later_follows(self) -> None:
        """Prevents types set by hand: the map decides, priority first then kind, and a later edit of the item follows."""
        fake, world = self.rig('issue_types = { issue = "Task", epic = "Feature", p1 = "Bug" }\n')
        plain = world.create("--kind", "issue", "--title", "Plain", "--quote", QUOTE)
        urgent = world.create("--kind", "issue", "--title", "Urgent", "--priority", "1", "--quote", QUOTE)
        big = world.create("--kind", "epic", "--title", "Big", "--quote", QUOTE)
        self.sync(world)
        types = {i["title"]: i.get("type") for i in fake.issues.values()}
        self.assertEqual(types, {"Plain": "Task", "Urgent": "Bug", "Big": "Feature"})
        world.run("set", urgent, "--priority", "2")
        self.sync(world)
        self.assertEqual({i["title"]: i.get("type") for i in fake.issues.values()}["Urgent"], "Task")

    def test_without_a_map_no_type_is_sent_or_compared(self) -> None:
        """Prevents surprising existing mirrors: no `issue_types` option means no type in any request and no divergence."""
        fake, world = self.rig()
        world.create("--kind", "issue", "--title", "Plain", "--quote", QUOTE)
        self.sync(world)
        issue = next(iter(fake.issues.values()))
        issue["type"] = "Bug"
        self.sync(world)
        self.assertFalse(any(isinstance(b, dict) and "type" in b for _m, p, b in fake.requests if "/issues" in p and b))
        self.assertIn("divergences=0", world.run("status").stdout)

    def test_a_human_changing_the_type_on_the_forge_is_a_divergence_not_an_overwrite(self) -> None:
        """Prevents silent clobbering: a type changed on GitHub is reported for accept or reject, like every other field."""
        fake, world = self.rig('issue_types = { issue = "Task" }\n')
        world.create("--kind", "issue", "--title", "Plain", "--quote", QUOTE)
        self.sync(world)
        issue = next(iter(fake.issues.values()))
        issue["type"] = "Bug"
        self.sync(world)
        self.assertEqual(issue["type"], "Bug")
        self.assertIn("divergences=1", world.run("status").stdout)


if __name__ == "__main__":
    unittest.main()
