"""Item fields: named key/value data beside labels, in the item header, with CLI, manifest and plan-file parity.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class ItemFieldsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)

    def show(self, item: str) -> dict:
        return json.loads(self.world.run("show", item, "--json").stdout)

    def listed(self, *arguments: str) -> list[str]:
        out = self.world.run("list", "--json", *arguments)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return sorted(json.loads(line)["id"] for line in out.stdout.splitlines())

    def commits(self) -> int:
        return int(subprocess.run(["git", "-C", str(self.world.ledger), "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout)

    def test_a_field_is_first_class_data_not_a_label(self) -> None:
        """Prevents fields living as label strings: create, show, set and clear keep them apart from the labels."""
        item = self.world.create("--title", "Field work", "--label", "area:ops", "--field", "phase=12", "--field", "wp=WP-7")
        data = self.show(item)
        self.assertEqual(data["fields"], {"phase": ["12"], "wp": ["WP-7"]})
        self.assertEqual(data["labels"], ["area:ops"])
        self.assertIn("phase: 12", self.world.run("show", item).stdout)
        self.assertEqual(self.world.run("set", item, "--field", "phase=13").returncode, 0)
        self.assertEqual(self.show(item)["fields"]["phase"], ["13"])
        self.assertEqual(self.world.run("set", item, "--clear-field", "wp").returncode, 0)
        self.assertEqual(self.show(item)["fields"], {"phase": ["13"]})
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_field_may_hold_several_values(self) -> None:
        """Prevents the one-value limit: a repeated NAME=VALUE is one field with several values, kept in order."""
        item = self.world.create("--title", "Two plans", "--field", "plan=alpha", "--field", "plan=beta")
        self.assertEqual(self.show(item)["fields"], {"plan": ["alpha", "beta"]})
        self.assertEqual(self.listed("--field", "plan=beta"), [item])
        self.assertEqual(self.listed("--field", "plan=gamma"), [])

    def test_list_and_search_find_items_by_field(self) -> None:
        """Prevents fields being invisible to reads: list filters by NAME=VALUE (and NAME alone), search matches values."""
        one = self.world.create("--title", "One", "--field", "phase=12")
        two = self.world.create("--title", "Two", "--field", "phase=13")
        self.world.create("--title", "Three")
        self.assertEqual(self.listed("--field", "phase=12"), [one])
        self.assertEqual(self.listed("--field", "phase"), sorted([one, two]))
        self.assertEqual(self.listed("--field", "phase=12", "--field", "phase=13"), [])
        found = self.world.run("search", "13")
        self.assertIn(two, found.stdout)
        self.assertNotIn(one, found.stdout)

    def test_a_malformed_field_is_refused_before_anything_is_written(self) -> None:
        """Prevents corrupt headers: a bad name, an empty or multi-line value, and a missing `=` all refuse."""
        for bad in ("Phase=1", "phase", "phase=", "ph ase=1", "phase=a\nb", "=x"):
            with self.subTest(field=bad):
                before = self.commits()
                out = self.world.run("create", "--title", "Bad", "--field", bad)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertEqual(self.commits(), before)

    def test_set_manifest_carries_fields_with_labels_in_one_commit(self) -> None:
        """Prevents 20,000 single sets: one manifest converts a label into a field, in one commit, and a rerun is unchanged."""
        items = [self.world.create("--title", f"Item {n}", "--label", f"phase:{n}") for n in range(3)]
        path = self.world.base / "manifest.jsonl"
        path.write_text("\n".join(json.dumps({"item": i, "fields": {"phase": str(n), "wp": ["a", "b"]}, "remove_label": f"phase:{n}"})
                                   for n, i in enumerate(items)) + "\n", encoding="utf-8")
        before = self.commits()
        out = self.world.run("set", "--manifest", str(path))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("changed 3", out.stdout)
        self.assertEqual(self.commits(), before + 1)
        data = self.show(items[1])
        self.assertEqual((data["fields"], data["labels"]), ({"phase": ["1"], "wp": ["a", "b"]}, []))
        again = self.world.run("set", "--manifest", str(path))
        self.assertIn("changed 0", again.stdout)
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_manifest_row_can_remove_a_field_and_a_bad_row_refuses_the_whole_file(self) -> None:
        """Prevents half-applied manifests: an empty list removes a field; one bad name changes nothing."""
        item = self.world.create("--title", "Item", "--field", "phase=1", "--field", "wp=x")
        other = self.world.create("--title", "Other")
        path = self.world.base / "m.jsonl"
        path.write_text(json.dumps({"item": other, "fields": {"phase": "9"}}) + "\n" + json.dumps({"item": item, "fields": {"Bad Name": "1"}}) + "\n")
        before = self.commits()
        self.assertEqual(self.world.run("set", "--manifest", str(path)).returncode, 2)
        self.assertEqual((self.commits(), self.show(other).get("fields", {})), (before, {}))
        path.write_text(json.dumps({"item": item, "fields": {"wp": []}}) + "\n")
        self.assertEqual(self.world.run("set", "--manifest", str(path)).returncode, 0)
        self.assertEqual(self.show(item)["fields"], {"phase": ["1"]})

    def test_a_manifest_changes_finished_items_only_with_closed(self) -> None:
        """Prevents a conversion that cannot reach history: finished items refuse a manifest unless --closed is given."""
        finished = self.world.create("--kind", "task", "--title", "Done work", "--label", "phase:3")
        self.assertEqual(self.world.run("close", finished, "--reason", "not_planned").returncode, 0)
        path = self.world.base / "m.jsonl"
        path.write_text(json.dumps({"item": finished, "fields": {"phase": "3"}, "remove_label": "phase:3"}) + "\n")
        self.assertEqual(self.world.run("set", "--manifest", str(path)).returncode, 2)
        done = self.world.run("set", "--manifest", str(path), "--closed")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = self.show(finished)
        self.assertEqual((data["fields"], data["labels"]), ({"phase": ["3"]}, []))
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_plan_file_creates_items_with_fields(self) -> None:
        """Prevents `create --from-file` dropping fields: each entry's `fields` object lands on its item."""
        plan = self.world.base / "plan.jsonl"
        plan.write_text(json.dumps({"title": "From plan", "fields": {"phase": "4", "plan": ["a", "b"]}}) + "\n")
        out = self.world.run("create", "--from-file", str(plan), "--quote", QUOTE)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        item = out.stdout.split()[0]
        self.assertEqual(self.show(item)["fields"], {"phase": ["4"], "plan": ["a", "b"]})

    def test_a_field_change_is_an_audited_event(self) -> None:
        """Prevents silent edits: every field change is a `field` event naming the key, old and new value."""
        item = self.world.create("--title", "Audited", "--field", "phase=1")
        self.world.run("set", item, "--field", "phase=2")
        changes = [e for e in self.world.events(item) if e["type"] == "field" and e.get("key") == "fields"]
        self.assertTrue(changes, self.world.events(item))
        self.assertIn("phase", changes[-1]["new"])


class FieldsSchemaWalk(unittest.TestCase):
    def test_a_schema_4_ledger_migrates_to_5_and_new_ledgers_start_at_5(self) -> None:
        """Prevents an old qew misreading the new header key: the schema is 5, and migrate-layout moves a 4 ledger to it."""
        world = World()
        self.addCleanup(world.close)
        item = world.create("--title", "Old", "--quote", QUOTE)
        toml = world.ledger / "qew-ledger.toml"
        self.assertIn("schema_version = 5", toml.read_text())
        toml.write_text(toml.read_text().replace("schema_version = 5", "schema_version = 4"))
        self.assertNotEqual(world.run("list").returncode, 0)
        done = world.run("migrate-layout")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("schema_version 4 -> 5", done.stdout)
        self.assertIn("schema_version = 5", toml.read_text())
        self.assertIn(item, world.run("list").stdout)


if __name__ == "__main__":
    unittest.main()
