"""Item fields on the forge: a mapped field fills a board field, fields never become labels, `project ensure` creates the
text fields, and `sync --prune-labels --all-labels` deletes labels nothing uses any more.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\n"
MAP = 'project_fields = { Phase = "field:phase", Plan = "field:plan" }\n'


class FieldMirrorWalk(unittest.TestCase):
    def rig(self, options: str = MAP) -> tuple[FakeGitHub, World]:
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n{options}'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        return fake, world

    def sync(self, world: World, *arguments: str) -> str:
        result = world.run("sync", *arguments, env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def value(self, fake: FakeGitHub, field: str) -> str | None:
        return fake.field_value("1", next(iter(fake.issues.values()))["node_id"], field)

    def test_a_mapped_field_fills_the_board_field_and_is_never_a_label(self) -> None:
        """Prevents 1,841 labels: a field reaches the card as a board field value, joined for several values, and no label is made."""
        fake, world = self.rig()
        self.assertEqual(world.run("project", "ensure", env=ENV).returncode, 0)
        item = world.create("--title", "Phased", "--field", "phase=12", "--field", "plan=a", "--field", "plan=b", "--quote", QUOTE)
        self.sync(world)
        self.assertEqual((self.value(fake, "Phase"), self.value(fake, "Plan")), ("12", "a, b"))
        self.assertFalse([name for name in fake.label_store if name.startswith(("phase", "plan", "12"))], fake.label_store)
        sets = fake.graphql_operations().count("SetCardField")
        self.sync(world)
        self.assertEqual(fake.graphql_operations().count("SetCardField"), sets)
        world.run("set", item, "--field", "phase=13")
        self.sync(world)
        self.assertEqual(self.value(fake, "Phase"), "13")
        world.run("set", item, "--clear-field", "plan")
        self.sync(world)
        self.assertEqual(world.run("check").returncode, 0)

    def test_project_ensure_creates_the_missing_text_fields_once(self) -> None:
        """Prevents hand-made board fields: ensure adds a text field per mapped name and a rerun adds none."""
        fake, world = self.rig()
        out = world.run("project", "ensure", env=ENV)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        names = [f["name"] for f in fake.project("1")["fields"]]
        self.assertEqual((names.count("Phase"), names.count("Plan")), (1, 1), names)
        self.assertTrue(next(f for f in fake.project("1")["fields"] if f["name"] == "Phase").get("text"))
        world.run("project", "ensure", env=ENV)
        self.assertEqual([f["name"] for f in fake.project("1")["fields"]].count("Phase"), 1)

    def test_a_label_replaced_by_a_field_drops_off_the_issue(self) -> None:
        """Prevents the converted labels lingering: once the item holds a field instead, the issue loses the label."""
        fake, world = self.rig()
        self.assertEqual(world.run("project", "ensure", env=ENV).returncode, 0)
        item = world.create("--title", "Phased", "--label", "phase:12", "--quote", QUOTE)
        self.sync(world)
        self.assertIn("phase:12", next(iter(fake.issues.values()))["labels"])
        world.run("set", item, "--remove-label", "phase:12", "--field", "phase=12")
        self.sync(world)
        self.assertNotIn("phase:12", next(iter(fake.issues.values()))["labels"])

    def test_prune_all_labels_deletes_the_unused_and_keeps_what_is_used_or_qew_owns(self) -> None:
        """Prevents a label sweep eating live data: unused labels go; in-use, wanted and gated stay; a dry run deletes nothing."""
        fake, world = self.rig("")
        world.create("--title", "Work", "--label", "area:ops", "--quote", QUOTE)
        self.sync(world)
        for name in ("phase:12", "wp:alpha", "elf3", "owner:santa", "bug", "gated", "qew:managed-old"):
            fake.label_store[name] = {"color": "ededed", "description": ""}
        fake.new_issue({"title": "Foreign", "body": "", "labels": ["bug"]})
        before = set(fake.label_store)
        dry = world.run("sync", "--prune-labels", "--all-labels", "--dry-run", env=ENV)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn("phase:12", dry.stdout)
        self.assertEqual(set(fake.label_store), before)
        done = world.run("sync", "--prune-labels", "--all-labels", env=ENV)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(before - set(fake.label_store), {"phase:12", "wp:alpha", "elf3", "owner:santa", "qew:managed-old"})
        self.assertTrue({"bug", "gated", "area:ops"} <= set(fake.label_store))

    def test_plain_prune_still_leaves_a_humans_labels(self) -> None:
        """Prevents widening the old command: without --all-labels only the qew namespaces are pruned."""
        fake, world = self.rig("")
        world.create("--title", "Work", "--quote", QUOTE)
        self.sync(world)
        fake.label_store["elf3"] = {"color": "ededed", "description": ""}
        self.assertEqual(world.run("sync", "--prune-labels", env=ENV).returncode, 0)
        self.assertIn("elf3", fake.label_store)


if __name__ == "__main__":
    unittest.main()
