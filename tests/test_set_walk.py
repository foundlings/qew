"""Walks of `qew set`: additive labels, body files, kind changes, audited and read back.

Adversary: two writers clobbering each other's labels, an edit with no audit trail, a kind change that
orphans a tree, and contradictory flags guessed at.
"""

import json
import unittest

from tests.ledger_harness import World
from tests.plan_support import snapshot


class SetWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def fields(self, item: str) -> dict:
        return {e["key"]: (e["old"], e["new"]) for e in self.world.events(item) if e["type"] == "field"}

    def test_one_call_changes_every_field_with_one_audit_event_per_key(self) -> None:
        """A partial or unaudited edit leaves the history unable to explain the header."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Plan")
        item = w.create("--title", "Old", "--label", "x", "--label", "keep")
        note = w.base / "body.md"
        note.write_text("New body\nwith two lines\n")
        result = w.run("set", item, "--title", "New", "--body-file", str(note), "--priority", "1", "--kind", "task",
                       "--add-label", "y", "--remove-label", "x", "--parent", epic, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        changed = json.loads(result.stdout)["changed"]
        self.assertEqual(sorted(c["key"] for c in changed), ["body", "kind", "labels", "parent", "priority", "title"])
        self.assertEqual(sorted(self.fields(item)), ["body", "kind", "labels", "parent", "priority", "title"])
        shown = json.loads(w.run("show", item, "--json").stdout)
        self.assertEqual((shown["title"], shown["kind"], shown["priority"], shown["parent"]), ("New", "task", 1, epic))
        self.assertEqual(sorted(shown["labels"]), ["keep", "y"])
        self.assertEqual(shown["body"].strip(), "New body\nwith two lines")
        self.assertEqual(w.run("check").returncode, 0)

    def test_adding_and_removing_labels_is_idempotent_and_leaves_the_others(self) -> None:
        """Two writers adding different labels must not clobber each other."""
        w = self.world
        item = w.create("--title", "A", "--label", "one")
        w.run("set", item, "--add-label", "two")
        w.run("set", item, "--add-label", "three")
        before = snapshot(w)
        again = w.run("set", item, "--add-label", "two", "--remove-label", "absent")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(snapshot(w), before)
        labels = json.loads(w.run("show", item, "--json").stdout)["labels"]
        self.assertEqual(sorted(labels), ["one", "three", "two"])

    def test_a_kind_change_that_would_orphan_children_is_refused(self) -> None:
        """Turning an epic with children into a task would leave children under an impossible parent."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Plan")
        w.create("--title", "Child", "--parent", epic)
        before = snapshot(w)
        result = w.run("set", epic, "--kind", "task")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot sit under", result.stderr)
        self.assertEqual(snapshot(w), before)

    def test_replacing_and_editing_labels_in_one_call_is_refused_and_a_negative_priority_too(self) -> None:
        """Contradictory flags are a mistake to report, not a rule to guess."""
        w = self.world
        item = w.create("--title", "A", "--label", "one")
        before = snapshot(w)
        mixed = w.run("set", item, "--labels", "a", "--add-label", "b")
        self.assertEqual(mixed.returncode, 2)
        self.assertEqual(w.run("set", item, "--priority", "-1").returncode, 2)
        self.assertEqual(snapshot(w), before)


if __name__ == "__main__":
    unittest.main()
