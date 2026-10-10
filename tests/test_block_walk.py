"""Walks of `qew block` and `qew unblock`: minimal, idempotent, validated dependency edits.

Adversary: a dependency that does not gate readiness, audit noise from repeated edits, a cycle or
self edge written by an incremental edit, and finished work edited by accident.
"""

import json
import unittest

from tests.ledger_harness import World
from tests.plan_support import listing, snapshot


class BlockWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        w = self.world
        self.a, self.b = w.create("--title", "A", "--draft"), w.create("--title", "B", "--draft")
        w.approve(self.a, self.b)

    def fields(self, item: str) -> list:
        return [e for e in self.world.events(item) if e["type"] == "field"]

    def test_block_adds_the_edge_and_moves_the_item_from_ready_to_blocked(self) -> None:
        """A dependency that does not gate readiness is no dependency."""
        w = self.world
        self.assertIn(self.a, w.run("ready").stdout)
        result = w.run("block", self.a, "--on", self.b, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"item": self.a, "needs": [self.b], "changed": True})
        self.assertNotIn(self.a, w.run("ready").stdout)
        self.assertIn(self.a, w.run("blocked").stdout)
        self.assertEqual([(e["key"], e["new"]) for e in self.fields(self.a)], [("needs", self.b)])

    def test_blocking_twice_is_a_no_op_without_a_second_audit_event(self) -> None:
        """Repeated edits must not add audit noise or rewrite the header."""
        w = self.world
        w.run("block", self.a, "--on", self.b)
        before = snapshot(w)
        again = w.run("block", self.a, "--on", self.b)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(snapshot(w), before)

    def test_a_cycle_and_a_self_block_are_refused_before_any_write(self) -> None:
        """Incremental edits must not bypass the graph rules."""
        w = self.world
        w.run("block", self.a, "--on", self.b)
        before = snapshot(w)
        cycle = w.run("block", self.b, "--on", self.a)
        self.assertEqual(cycle.returncode, 2)
        self.assertIn("cycle", cycle.stderr)
        self.assertEqual(w.run("block", self.a, "--on", self.a).returncode, 2)
        self.assertEqual(snapshot(w), before)

    def test_unblock_removes_the_edge_and_the_item_is_ready_again_and_an_absent_edge_is_a_no_op(self) -> None:
        """Removing a dependency must not resurrect others, and removing nothing must not write."""
        w = self.world
        third = w.create("--title", "C", "--draft")
        w.approve(third)
        w.run("block", self.a, "--on", self.b)
        w.run("block", self.a, "--on", third)
        self.assertEqual(w.run("unblock", self.a, "--from", self.b).returncode, 0)
        rows = {r["id"]: r for r in listing(w)}
        self.assertEqual(rows[self.a]["needs"], [third])
        w.run("unblock", self.a, "--from", third)
        self.assertIn(self.a, w.run("ready").stdout)
        before = snapshot(w)
        noop = w.run("unblock", self.a, "--from", self.b)
        self.assertEqual(noop.returncode, 0, noop.stderr)
        self.assertIn("unchanged", noop.stdout)
        self.assertEqual(snapshot(w), before)

    def test_finished_work_needs_closed_for_block_and_unblock(self) -> None:
        """Silent edits of finished work are refused; the deliberate form works."""
        w = self.world
        w.run("close", self.a, "--reason", "not_planned")
        for command in (("block", self.a, "--on", self.b), ("unblock", self.a, "--from", self.b)):
            refused = w.run(*command)
            self.assertEqual(refused.returncode, 2, command)
            self.assertIn("--closed", refused.stderr)
        self.assertEqual(w.run("block", self.a, "--on", self.b, "--closed").returncode, 0)
        self.assertEqual(w.run("unblock", self.a, "--from", self.b, "--closed").returncode, 0)


if __name__ == "__main__":
    unittest.main()
