"""Walks of completing an epic: `qew complete EPIC` is the plan-completion verb.

Adversary: closing a plan over open or unreleased children, completing an unapproved plan, or a rerun
writing a second `completed` event that makes the ledger invalid.
"""

import unittest

from tests.ledger_harness import World


class EpicCompleteWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        w = self.world
        self.epic = w.create("--kind", "epic", "--title", "Plan", "--draft")
        self.child = w.create("--kind", "issue", "--title", "Child", "--parent", self.epic, "--draft")
        w.approve(self.epic, self.child)
        w.run("start", self.epic)

    def finish(self, item: str) -> None:
        w = self.world
        self.assertIn(w.run("start", item).returncode, (0, 5))  # 5: the epic claim already claimed it
        w.run("evidence", item, "--change", w.commit())
        self.assertEqual(w.run("complete", item).returncode, 0)

    def types(self, item: str) -> list:
        return [e["type"] for e in self.world.events(item)]

    def test_golden_epic_completes_when_every_child_is_done(self) -> None:
        """Closing a plan must need no manual checking once its children are finished."""
        self.finish(self.child)
        result = self.world.run("complete", self.epic)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{self.epic} completed")

    def test_an_open_child_refuses_with_the_count_and_the_id_and_writes_nothing(self) -> None:
        """A plan closed over live work hides that work."""
        before = self.types(self.epic)
        result = self.world.run("complete", self.epic)
        self.assertEqual(result.returncode, 2)
        self.assertIn("1 open child", result.stderr)
        self.assertIn(self.child, result.stderr)
        self.assertEqual(self.types(self.epic), before)
        second = self.world.create("--kind", "issue", "--title", "Second", "--parent", self.epic, "--draft")
        again = self.world.run("complete", self.epic)
        self.assertIn("2 open children", again.stderr)
        self.assertIn(second, again.stderr)

    def test_a_draft_child_blocks_completion_like_any_open_child(self) -> None:
        """Unreleased work under an approved plan must not be hidden by closing the plan."""
        self.finish(self.child)
        draft = self.world.create("--kind", "issue", "--title", "Not yet released", "--parent", self.epic, "--draft")
        result = self.world.run("complete", self.epic)
        self.assertEqual(result.returncode, 2)
        self.assertIn(draft, result.stderr)

    def test_a_child_closed_as_not_planned_counts_as_done(self) -> None:
        """Abandoned work must not hold a plan open forever."""
        self.assertEqual(self.world.run("close", self.child, "--reason", "not_planned").returncode, 0)
        self.assertEqual(self.world.run("complete", self.epic).returncode, 0)

    def test_an_epic_that_was_closed_instead_of_completed_is_not_completable(self) -> None:
        """Closing and completing are different facts; the gate must not blur them."""
        self.assertEqual(self.world.run("close", self.child, "--reason", "not_planned").returncode, 0)
        self.assertEqual(self.world.run("close", self.epic, "--reason", "not_planned").returncode, 0)
        result = self.world.run("complete", self.epic)
        self.assertEqual(result.returncode, 2)
        self.assertIn("closed", result.stderr)

    def test_completing_twice_writes_one_event_and_the_ledger_stays_valid(self) -> None:
        """A rerun must be an explicit no-op: a second `completed` is an illegal transition that would make `qew check` fail."""
        self.finish(self.child)
        self.assertEqual(self.world.run("complete", self.epic).returncode, 0)
        again = self.world.run("complete", self.epic)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("completed (unchanged)", again.stdout)
        self.assertEqual(self.types(self.epic).count("completed"), 1)
        self.assertEqual(self.world.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
