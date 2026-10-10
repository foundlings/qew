"""Custom Projects v2 fields: a per-mirror map from ledger facts to text and single-select fields on the card.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"


class ProjectFieldsWalk(unittest.TestCase):
    def rig(self, options: str = 'project_fields = { Area = "label:area", Kind = "kind" }\n'):
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n{options}'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        self.assertEqual(world.run("project", "ensure", env=ENV).returncode, 0)
        fake.add_field("1", "Area")
        fake.add_field("1", "Kind", ("issue", "epic"))
        return fake, world

    def sync(self, world: World) -> None:
        result = world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def value(self, fake: FakeGitHub, field: str):
        return fake.field_value("1", next(iter(fake.issues.values()))["node_id"], field)

    def test_mapped_facts_land_in_text_and_single_select_fields_and_follow_changes(self) -> None:
        """Prevents card fields maintained by hand: the map sets them, a changed fact updates them, and nothing else is sent."""
        fake, world = self.rig()
        item = world.create("--kind", "issue", "--title", "Billing", "--label", "area:billing", "--quote", QUOTE)
        self.sync(world)
        self.assertEqual((self.value(fake, "Area"), self.value(fake, "Kind")), ("billing", "issue"))
        sets = fake.graphql_operations().count("SetCardField")
        self.sync(world)
        self.assertEqual(fake.graphql_operations().count("SetCardField"), sets)
        world.run("set", item, "--remove-label", "area:billing", "--add-label", "area:ledger")
        self.sync(world)
        self.assertEqual(self.value(fake, "Area"), "ledger")
        self.assertEqual(world.run("check").returncode, 0)

    def test_a_human_editing_a_mapped_field_is_a_divergence_not_an_overwrite(self) -> None:
        """Prevents silent clobbering: a card field changed in the Projects UI is reported, then rejected to restore it."""
        fake, world = self.rig()
        item = world.create("--kind", "issue", "--title", "Billing", "--label", "area:billing", "--quote", QUOTE)
        self.sync(world)
        node = next(iter(fake.issues.values()))["node_id"]
        fake.set_field_value("1", node, "Area", "by hand")
        self.sync(world)
        self.assertEqual(self.value(fake, "Area"), "by hand")
        self.assertIn("divergences=1", world.run("status").stdout)
        self.sync(world)
        self.assertIn("divergences=1", world.run("status").stdout)
        self.assertEqual(world.run("reject", item).returncode, 0)
        self.sync(world)
        self.assertEqual(self.value(fake, "Area"), "billing")
        self.assertIn("divergences=0", world.run("status").stdout)

    def test_project_status_reads_a_board_that_has_a_text_field_nobody_mapped(self) -> None:
        """Prevents `qew project status` failing with 'malformed GitHub response: value.name' on any board that has a text field."""
        fake, world = self.rig("")
        world.create("--kind", "issue", "--title", "Plain", "--quote", QUOTE)
        self.sync(world)
        fake.set_field_value("1", next(iter(fake.issues.values()))["node_id"], "Area", "typed by a human")
        result = world.run("project", "status", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_without_the_option_no_field_is_read_or_written(self) -> None:
        """Prevents surprising existing boards: no `project_fields` option means no field query and no field write."""
        fake, world = self.rig("")
        world.create("--kind", "issue", "--title", "Plain", "--label", "area:billing", "--quote", QUOTE)
        self.sync(world)
        self.assertEqual([op for op in fake.graphql_operations() if op in ("ProjectFields", "SetCardField")], [])
        self.assertIsNone(self.value(fake, "Area"))


if __name__ == "__main__":
    unittest.main()
