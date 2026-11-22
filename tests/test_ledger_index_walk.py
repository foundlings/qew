"""Walks of the stored edge index a lazy snapshot answers `children`, `blocks` and the claim count from.

Adversary: `complete` and `start` parsing every item three times (15 s on 28,000 items) to learn who a parent's children are and who
a completed item releases; and the cure answering differently from the full snapshot: a stale edge after a write, a hand edit or a
merge, a damaged file, or an item the index forgot.
"""

import unittest

from tests.ledger_harness import QUOTE, World  # first: it puts src/ on the path
from qew import ledger_graph
from qew.ledger_store import open_ledger

TRACE = {"QEW_TRACE": "1"}


class LedgerIndexWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(schema="9")
        self.addCleanup(self.world.close)
        w = self.world
        self.epic = w.create("--kind", "epic", "--title", "Epic", "--quote", QUOTE)
        self.first = w.create("--title", "First", "--parent", self.epic, "--quote", QUOTE)
        self.second = w.create("--title", "Second", "--parent", self.epic, "--quote", QUOTE)
        self.third = w.create("--title", "Third", "--quote", QUOTE)
        self.assertEqual(w.run("set", self.second, "--needs", self.first).returncode, 0)
        self.assertEqual(w.run("start", self.first).returncode, 0)

    def snapshots(self):
        ledger = open_ledger(self.world.ledger, "TST")
        return ledger.snapshot(), ledger.snapshot(lazy=True)

    def same_as_full(self) -> None:
        full, lazy = self.snapshots()
        self.assertEqual(ledger_graph.children(lazy), ledger_graph.children(full))
        self.assertEqual(ledger_graph.blocks(lazy), ledger_graph.blocks(full))
        self.assertEqual(lazy.claims_of("tester"), sum(1 for f in full.folds.values() if f.owner == "tester" and f.state.value == "claimed"))
        self.assertEqual(lazy.items.built(), 0, "the edge questions built items")
        self.assertEqual(lazy.folds.built(), 0, "the claim question built folds")

    def test_the_lazy_snapshot_answers_the_edge_questions_without_reading_items(self) -> None:
        """Prevents children, blocks and the claim count parsing every item: they come from the index and equal the full snapshot's."""
        self.same_as_full()

    def test_a_qew_write_moves_the_index(self) -> None:
        """Prevents a stale edge after a write: a new parent, a new need, a claim and a completion are all seen."""
        self.same_as_full()
        w = self.world
        self.assertEqual(w.run("set", self.third, "--parent", self.epic).returncode, 0)
        self.same_as_full()
        self.assertEqual(w.run("set", self.third, "--needs", self.second).returncode, 0)
        self.same_as_full()
        self.assertEqual(w.run("release", self.first, "--reason", "test").returncode, 0)  # the claim count moves
        self.same_as_full()

    def test_a_hand_edit_of_an_item_file_is_seen(self) -> None:
        """Prevents the index hiding an edit made outside qew: the item file's own signature is checked."""
        self.same_as_full()
        path = next((self.world.ledger / "items").rglob(f"{self.third}/item.md"))
        path.write_text(path.read_text(encoding="utf-8").replace("\nparent: \n", f"\nparent: {self.epic}\n", 1), encoding="utf-8")
        full, lazy = self.snapshots()
        self.assertEqual(ledger_graph.children(lazy), ledger_graph.children(full))
        self.assertIn(self.third, ledger_graph.children(lazy)[self.epic])

    def test_a_damaged_index_is_read_again(self) -> None:
        """Prevents a damaged local file failing a write or hiding an item: it is rebuilt from the ledger."""
        self.same_as_full()
        (self.world.ledger / ".qew" / "index.json").write_text("{ not json", encoding="utf-8")
        self.same_as_full()

    def test_complete_takes_the_lazy_snapshot_and_equals_the_full_one(self) -> None:
        """Prevents complete parsing the ledger for children and releases: its trace names a lazy snapshot and the outcome is unchanged."""
        w = self.world
        self.same_as_full()  # the first use of the index builds it from the full snapshot, once; every later use only reads what changed
        sha = w.commit("the work")
        self.assertEqual(w.run("evidence", self.first, "--change", sha).returncode, 0)
        run = w.run("complete", self.first, env=TRACE)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("ledger snapshot (lazy)", run.stderr)
        self.assertNotIn("ledger snapshot (full)", run.stderr)
        self.assertIn(f"ready: {self.second}", run.stdout)  # the completion released its dependent

    def test_a_ledger_with_migrated_ids_answers_like_the_full_snapshot(self) -> None:
        """Prevents the aliased ledger (foundlings') falling into one-by-one reads of every item: the index covers old ids and is exact."""
        from tests.test_migrate_ids_walk import Fixture, letters_world

        world = letters_world(schema="9")
        self.addCleanup(world.close)
        Fixture(world)
        self.assertEqual(world.run("migrate-ids", "--to", "base35-time").returncode, 0)
        ledger = open_ledger(world.ledger, "TST")
        full, lazy = ledger.snapshot(), ledger.snapshot(lazy=True)
        self.assertEqual(ledger_graph.children(lazy), ledger_graph.children(full))
        self.assertEqual(ledger_graph.blocks(lazy), ledger_graph.blocks(full))
        self.assertEqual(lazy.claims_of("tester"), full.claims_of("tester"))
        self.assertEqual(lazy.items.built(), 0)
        self.assertEqual(ledger_graph.children(ledger.snapshot(lazy=True)), ledger_graph.children(full))  # and again, from the stored file

    def test_damage_elsewhere_stops_a_verb_that_needs_the_edges_loudly(self) -> None:
        """Prevents an epic completing over a child nobody could read: complete names the damage instead of guessing."""
        w = self.world
        other = w.create("--title", "Other", "--quote", QUOTE)
        self.same_as_full()  # the index exists and is sound
        path = next((w.ledger / "items").rglob(f"{other}/item.md"))
        path.write_text("not an item file", encoding="utf-8")
        sha = w.commit("the work")
        self.assertEqual(w.run("evidence", self.first, "--change", sha).returncode, 0)  # evidence reads no edges
        run = w.run("complete", self.first)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn(other, run.stderr)


if __name__ == "__main__":
    unittest.main()
