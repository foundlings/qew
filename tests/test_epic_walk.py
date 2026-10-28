"""Epics follow their children: a derived claim at read time and automatic completion with the last child.

Adversary: an epic that looks idle while a child is worked, stale holders, every linked epic reading as in
progress, an epic left open forever, a premature epic completion, and a child completion reported as failed.
"""

import json
import os
import stat
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World
from tests.lifecycle_support import event_types

BOB = {"QEW_ACTOR": "bob"}


def finish(world: World, item: str, **env) -> None:
    result = world.run("evidence", item, "--change", world.commit("work"), env=env or None)
    assert result.returncode == 0, result.stderr
    result = world.run("complete", item, env=env or None)
    assert result.returncode == 0, result.stderr


def make_epic(world: World, *children: str):
    epic = world.create("--kind", "epic", "--title", "Epic", "--draft")
    ids = [world.create("--kind", "issue", "--title", title, "--parent", epic, "--draft") for title in children]
    world.approve(epic, *ids)
    return (epic, *ids)


class DerivedEpicClaimWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def holders(self, epic: str) -> list:
        return json.loads(self.world.run("show", epic, "--json").stdout).get("holders")

    def test_a_claimed_child_puts_the_epic_in_progress_for_that_actor_and_list_agrees(self) -> None:
        """Prevents an epic that looks idle while a child is worked."""
        w = self.world
        epic, child = make_epic(w, "Child")
        w.run("start", child)
        self.assertEqual(self.holders(epic), ["tester"])
        self.assertIn("state: claimed", w.run("show", epic).stdout)
        row = next(line for line in w.run("list", "--kind", "epic").stdout.splitlines() if epic in line)
        self.assertIn("claimed", row)
        self.assertIn("@tester", row)
        self.assertNotIn("claimed", {e["type"] for e in w.events(epic)})

    def test_every_holder_is_listed_and_the_epic_returns_to_approved_when_children_stop(self) -> None:
        """Prevents stale derived holders."""
        w = self.world
        epic, one, two = make_epic(w, "One", "Two")
        w.run("start", one)
        w.run("start", two, env=BOB)
        self.assertEqual(self.holders(epic), ["bob", "tester"])
        w.run("release", one, "--reason", "pause")
        w.run("release", two, "--reason", "pause", env=BOB)
        self.assertEqual(self.holders(epic), [])
        self.assertIn("state: approved", w.run("show", epic).stdout)

    def test_only_open_direct_non_epic_children_count(self) -> None:
        """Prevents every linked epic reading as in progress: grandchildren, child epics and needs do not count."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Epic", "--draft")
        sub = w.create("--kind", "epic", "--title", "Sub", "--draft")
        grand = w.create("--kind", "issue", "--title", "Grand", "--parent", sub, "--draft")
        linked = w.create("--kind", "issue", "--title", "Linked", "--needs", grand, "--draft")
        w.approve(epic, sub, grand, linked)
        w.run("start", grand)
        self.assertEqual(self.holders(epic), [])
        self.assertEqual(self.holders(sub), ["tester"])
        self.assertIn("state: approved", w.run("show", epic).stdout)

    def test_a_closed_epic_shows_nothing_derived(self) -> None:
        """Prevents phantom activity on finished plans."""
        w = self.world
        epic, child = make_epic(w, "Child")
        w.run("start", child)
        w.run("close", epic, "--reason", "not_planned")
        self.assertEqual(self.holders(epic), [])
        self.assertIn("state: closed", w.run("show", epic).stdout)


class EpicCompletionWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_the_last_finished_child_completes_an_unclaimed_epic_with_the_count(self) -> None:
        """Prevents epics left open forever, and an epic completed while a child is still open."""
        w = self.world
        epic, first, second = make_epic(w, "First", "Second")
        w.run("start", first)
        w.run("start", second)
        finish(w, first)
        self.assertNotIn("completed", event_types(w, epic))
        finish(w, second)
        done = w.events(epic)[-1]
        self.assertEqual((done["type"], done.get("children_finished")), ("completed", 2))
        self.assertEqual(w.run("check").returncode, 0)

    def test_closing_the_last_open_child_also_completes_when_one_child_was_completed(self) -> None:
        """Prevents a plan that delivered something staying open because the rest was dropped."""
        w = self.world
        epic, done, dropped = make_epic(w, "Done", "Dropped")
        w.run("start", done)
        finish(w, done)
        self.assertNotIn("completed", event_types(w, epic))
        w.run("close", dropped, "--reason", "not_planned")
        self.assertEqual(w.events(epic)[-1]["type"], "completed")

    def test_an_epic_whose_children_were_all_dropped_delivered_nothing_and_stays_open(self) -> None:
        """Prevents completing an epic that delivered nothing."""
        w = self.world
        epic, only = make_epic(w, "Only")
        w.run("close", only, "--reason", "not_planned")
        self.assertNotIn("completed", event_types(w, epic))

    def test_a_failing_epic_step_only_warns_and_leaves_the_child_completed(self) -> None:
        """Prevents a successful child completion reported as failed, inviting a retry."""
        w = self.world
        epic, child = make_epic(w, "Child")
        w.run("start", child)
        w.run("evidence", child, "--change", w.commit("work"))
        events = w.home("events", epic)
        events.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(events.chmod, stat.S_IRWXU)
        result = w.run("complete", child)
        events.chmod(stat.S_IRWXU)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(w.events(child)[-1]["type"], "completed")
        self.assertIn(f"qew complete {epic}", result.stderr)
        self.assertNotIn("completed", event_types(w, epic))


if __name__ == "__main__":
    unittest.main()
