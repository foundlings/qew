"""Walks of the lazy snapshot's safety net: it is never worse than the full one, and it is what a write verb gets by default.

Adversary: a verb that turns out to touch every item (a graph walk, `dict(snapshot.items)`) reading them one by one through the
lazy maps: 44 s on 28,000 items against the full snapshot's 5 s. And a write verb nobody converted, parsing the whole ledger
under the lock because lazy was opt-in.
"""

import unittest
from unittest import mock

from tests.ledger_harness import QUOTE, World  # first: it puts src/ on the path
from qew import ledger_store
from qew.ledger_store import Ledger, open_ledger

TRACE = {"QEW_TRACE": "1"}
ITEMS = 8
AFTER = 3


class AdaptiveSnapshotWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(schema="9")
        self.addCleanup(self.world.close)
        self.ids = [self.world.create("--title", f"Item {n}", "--quote", QUOTE) for n in range(ITEMS)]

    def reads_for(self, ask) -> int:
        """How many items were read one by one while `ask` used a lazy snapshot."""
        ledger = open_ledger(self.world.ledger, "TST")
        calls = [0]
        original = Ledger._read_item

        def counting(self, *args, **kwargs):
            calls[0] += 1
            return original(self, *args, **kwargs)

        with mock.patch.object(ledger_store, "ADAPT_AFTER", AFTER, create=True), mock.patch.object(Ledger, "_read_item", counting):
            ask(ledger.snapshot(lazy=True), ledger.snapshot())
        return calls[0]

    def test_iterating_every_item_builds_the_full_snapshot_once_not_item_by_item(self) -> None:
        """Prevents a graph walk reading 28,000 items one at a time: past a few single loads, or on any bulk iteration, the full build serves."""
        def ask(lazy, full) -> None:
            self.assertEqual([item.id for item in lazy.items.values()], [item.id for item in full.items.values()])
            self.assertEqual({key: fold.state for key, fold in lazy.folds.items()}, {key: fold.state for key, fold in full.folds.items()})

        self.assertLessEqual(self.reads_for(ask), AFTER)

    def test_dict_of_a_lazy_map_is_also_served_by_the_full_build(self) -> None:
        """Prevents `dict(snapshot.items)` (keys, then one lookup each) being the slow path."""
        def ask(lazy, full) -> None:
            self.assertEqual({key: item.title for key, item in dict(lazy.items).items()}, {key: item.title for key, item in full.items.items()})
            self.assertEqual(sorted(len(events) for events in dict(lazy.events).values()), sorted(len(events) for events in full.events.values()))

        self.assertLessEqual(self.reads_for(ask), AFTER)

    def test_a_write_verb_takes_the_lazy_snapshot_without_asking(self) -> None:
        """Prevents every unconverted write verb parsing the whole ledger under the lock: lazy is the default, QEW_NO_LAZY=1 the way back."""
        run = self.world.run("set", self.ids[0], "--priority", "3", env=TRACE)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("ledger snapshot (lazy)", run.stderr)
        self.assertNotIn("ledger snapshot (full)", run.stderr)
        again = self.world.run("set", self.ids[0], "--priority", "4", env={**TRACE, "QEW_NO_LAZY": "1"})
        self.assertIn("ledger snapshot (full)", again.stderr)


if __name__ == "__main__":
    unittest.main()
