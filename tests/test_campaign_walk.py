"""Walks of epics as campaigns: completing one, and reading its progress.

Adversary: completing a plan that still has open work or no work at all, a
repeat completion writing a second event, and a wrong `complete` suggestion.
"""

import json
import unittest

from tests.ledger_harness import World


def event_count(world: World, item: str) -> int:
    return len(world.events(item))


class CampaignCompleteWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def finished_epic(self):
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Epic")
        child = w.create("--kind", "issue", "--title", "Child", "--parent", epic)
        w.approve(epic, child)
        w.run("start", epic)
        w.run("start", child)
        w.run("evidence", child, "--change", w.commit())
        w.run("complete", child)
        return epic, child

    def test_epic_completes_over_finished_children_and_is_verified(self) -> None:
        """Completing then showing must agree: no unverified state change."""
        epic, _ = self.finished_epic()
        self.assertEqual(self.world.run("complete", epic).returncode, 0)
        self.assertIn("state: completed", self.world.run("show", epic).stdout)

    def test_epic_with_open_children_is_refused_without_writing(self) -> None:
        """The refusal counts the open children and adds no event."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Epic")
        for title in ("A", "B"):
            w.approve(w.create("--kind", "issue", "--title", title, "--parent", epic))
        w.approve(epic)
        w.run("start", epic)
        before = event_count(w, epic)
        result = w.run("complete", epic)
        self.assertEqual(result.returncode, 2)
        self.assertIn("2 open children", result.stderr)
        self.assertEqual(event_count(w, epic), before)

    def test_epic_without_children_is_refused(self) -> None:
        """An empty plan has no completed work; completing it would prove nothing."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Empty")
        w.approve(epic)
        w.run("start", epic)
        result = w.run("complete", epic)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no completed work", result.stderr)

    def test_completing_a_completed_epic_is_a_no_op(self) -> None:
        """The second call is quiet and unchanged: no second event, no error."""
        epic, _ = self.finished_epic()
        self.world.run("complete", epic)
        before = event_count(self.world, epic)
        again = self.world.run("complete", epic)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(event_count(self.world, epic), before)


if __name__ == "__main__":
    unittest.main()
