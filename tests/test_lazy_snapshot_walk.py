"""A snapshot of a cached ledger parses an item's events only when something asks for them.

Reading 27,450 items and 214,298 events took 9 s a command, most of it parsing and folding events no command looked at.
Items stay eager (the whole graph needs their headers); each item's folded state and events are built on first access, from text the cache
validated when it entered it. Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_store
from qew.ledger_store import Ledger
from tests.ledger_harness import World


class LazySnapshotWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World()
        self.addCleanup(self.w.close)
        self.ids = [self.w.create("--kind", "issue", "--title", f"Item {n}", "--draft") for n in range(12)]
        self.w.approve(*self.ids)
        self.w.run("start", self.ids[0])
        self.w.run("block", self.ids[1], "--on", self.ids[2])
        self.ledger = Ledger(self.w.ledger, "TST", schema=int(os.environ.get("QEW_TEST_SCHEMA", "7")))
        self.ledger.snapshot()  # the first read fills the cache (and validates the events as they enter it)

    def parsed(self, action) -> int:  # type: ignore[no-untyped-def]
        calls = 0
        real = ledger_store.parse_event_trusted

        def counted(name: str, text: str):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            return real(name, text)

        with mock.patch.object(ledger_store, "parse_event_trusted", counted):
            action()
        return calls

    def test_building_the_snapshot_parses_no_events_but_the_edges_that_make_the_needs(self) -> None:
        """Prevents every command paying to parse and fold every event of a 27,000 item ledger, though most ask about a few items."""
        holder: list = []
        calls = self.parsed(lambda: holder.append(self.ledger.snapshot(verify=False)))
        self.assertLessEqual(calls, 2, calls)  # the one edge event (and no more)
        snapshot = holder[0]
        self.assertEqual(snapshot.items[self.ids[1]].needs, (self.ids[2],))

    def test_an_item_is_folded_on_first_access_and_once(self) -> None:
        """Prevents a lazy fold being redone on every read of the same item."""
        snapshot = self.ledger.snapshot()
        first = self.parsed(lambda: snapshot.folds[self.ids[0]])
        again = self.parsed(lambda: snapshot.folds[self.ids[0]])
        self.assertGreater(first, 0)
        self.assertEqual(again, 0)

    def test_a_lazy_snapshot_is_the_same_as_an_uncached_one(self) -> None:
        """Prevents the lazy path changing what any command sees: every item, fold and event list equals the eager read."""
        lazy = self.ledger.snapshot()
        with mock.patch.dict(os.environ, {"QEW_NO_CACHE": "1"}):
            eager = Ledger(self.w.ledger, "TST", schema=self.ledger.schema).snapshot()
        self.assertEqual(list(lazy.items), list(eager.items))
        self.assertEqual(list(lazy.folds), list(eager.folds))
        for item_id in eager.items:
            self.assertEqual(lazy.items[item_id], eager.items[item_id])
            self.assertEqual(lazy.folds[item_id], eager.folds[item_id])
            self.assertEqual([e.id for e in lazy.events[item_id]], [e.id for e in eager.events[item_id]])
        self.assertEqual(len(lazy.folds), len(eager.folds))


if __name__ == "__main__":
    unittest.main()
