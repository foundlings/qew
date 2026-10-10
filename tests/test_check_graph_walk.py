"""Walks of the graph invariants `qew check` reports, each with its own failing ledger and a count.

Adversary: structure that parses and folds fine yet is wrong as a graph (files belonging to no
item, a child filed under another area's parent, an absurdly deep needs chain, work waiting on
an item that was closed), passing check and then silently never becoming ready.
"""

import unittest

from tests.ledger_harness import World
from tests.spec_loader import load, spec

SUMMARY = "graph invariants: orphans={o} cross_epic={c} depth={d} closed_needs={n}"


def counts(o=0, c=0, d=0, n=0) -> str:
    return SUMMARY.format(o=o, c=c, d=d, n=n)


class CheckGraphWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def failing(self, fragment: str, summary: str) -> None:
        result = self.world.run("check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(fragment, result.stdout)
        self.assertIn(summary, result.stdout)

    def test_a_sound_graph_reports_no_invariant_line(self) -> None:
        """Nothing to count means nothing is printed, so the output stays short for agents."""
        load(self.world, spec("E", kind="epic", labels=["area:a"]), spec("A", parent="E", labels=["area:a"]), spec("B", needs=["A"]))
        result = self.world.run("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("graph invariants", result.stdout)

    def test_files_that_belong_to_no_item_are_counted_as_orphans(self) -> None:
        """Events and mirrors left behind by a deleted item are named and counted."""
        load(self.world, spec("A"))
        ghost = self.world.ledger / "events" / "GHOST"
        ghost.mkdir()
        (ghost / "X.json").write_text("{}")
        (self.world.ledger / "mirrors" / "OTHER").mkdir(parents=True)
        self.failing("events/GHOST: belongs to no item", counts(o=2))

    def test_a_child_filed_under_another_areas_parent_is_a_cross_epic_parent(self) -> None:
        """C carries area:b but sits under an epic of area:a; its siblings do not trip the check."""
        load(
            self.world,
            spec("E", kind="epic", labels=["area:a"]),
            spec("A", parent="E", labels=["area:a", "phase:1"]),
            spec("C", parent="E", labels=["area:b"]),
            spec("U", parent="E"),
        )
        self.failing("items/C/item.md: area b differs from parent E (area a)", counts(c=1))

    def test_a_needs_chain_deeper_than_the_limit_is_a_warning_reported_once_at_its_deepest_item(self) -> None:
        """Thirty edges pass silently; the thirty-first warns on one line and never fails the check (real corpora chain this deep)."""
        chain = [spec("N0")] + [spec(f"N{i}", needs=[f"N{i - 1}"]) for i in range(1, 31)]
        load(self.world, *chain)
        quiet = self.world.run("check")
        self.assertEqual(quiet.returncode, 0, quiet.stdout + quiet.stderr)
        self.assertNotIn("exceeds", quiet.stdout)
        load(self.world, spec("N31", needs=["N30"]))
        warned = self.world.run("check")
        self.assertEqual(warned.returncode, 0, warned.stdout + warned.stderr)
        self.assertIn("items/N31/item.md: needs chain of 31 edges exceeds the limit of 30", warned.stdout)
        self.assertIn(counts(d=1), warned.stdout)

    def test_work_waiting_on_a_closed_item_is_reported_as_unreachable(self) -> None:
        """P can never become ready because X is closed; completed dependents of closed work are history."""
        load(self.world, spec("X"), spec("P", needs=["X"]), spec("Q", needs=["X"]))
        self.assertEqual(self.world.run("close", "X", "--reason", "not_planned").returncode, 0)
        self.assertEqual(self.world.run("close", "Q", "--reason", "not_planned").returncode, 0)
        self.failing("items/P/item.md: needs closed item X", counts(n=1))


if __name__ == "__main__":
    unittest.main()
