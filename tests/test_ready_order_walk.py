"""Walks of `qew ready` ordering: priority, then how much work an item unblocks, then id.

Adversary: a leaf item with an early id starving the item that unblocks a whole chain, a
non-deterministic order between runs, and a dependency cycle downstream of ready work being
silently ignored instead of refused.
"""

import json
import re
import unittest

from tests.ledger_harness import World
from tests.spec_loader import load


def item(item_id: str, priority: int = 2, needs=()) -> dict:
    return {"id": item_id, "kind": "issue", "title": f"Work {item_id}", "priority": priority, "needs": list(needs)}


def ids(output: str) -> list:
    return [line.split()[0] for line in output.splitlines()]


class ReadyOrderWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_ready_orders_by_priority_then_chain_depth_then_direct_dependents_then_id(self) -> None:
        """A2 has the earliest id yet unblocks nothing; it must not outrank work that unblocks chains."""
        w = self.world
        load(
            w,
            item("Z1", priority=1),
            item("A2"),
            item("B2"),
            item("C2", needs=["B2"]),
            item("D2", needs=["C2"]),
            item("E2"),
            item("F2", needs=["E2"]),
            item("G2", needs=["E2"]),
            item("H2"),
            item("I2", needs=["H2"]),
            item("J2"),
        )
        first = w.run("ready")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(ids(first.stdout), ["Z1", "B2", "E2", "H2", "A2", "J2"])
        self.assertEqual(w.run("ready").stdout, first.stdout, "the order is deterministic")
        as_json = json.loads(w.run("ready", "--json").stdout)
        self.assertEqual([row["id"] for row in as_json["items"]], ids(first.stdout))

    def test_a_dependency_cycle_downstream_of_ready_work_is_refused_not_ignored(self) -> None:
        """Walking dependents of R must not loop forever or guess an order when P and Q need each other."""
        w = self.world
        load(w, item("R"), item("P", needs=["R"]), item("Q", needs=["P"]))
        path = w.home("items", "P") / "item.md"
        path.write_text(re.sub(r"(?m)^needs: R$", "needs: R Q", path.read_text()))
        result = w.run("ready")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("cycle", result.stderr)


if __name__ == "__main__":
    unittest.main()
