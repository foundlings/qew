"""Walks of the board's sort, group and filter options (GitHub Projects style), offline.

Adversary: a silently ignored key, a card hidden by grouping, and a malformed filter
that renders an empty board that looks like there is no work.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_item import Item, Kind
from qew.ledger_store import Ledger
from tests.ledger_harness import QUOTE, World


class BoardViewWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.ledger = Ledger(self.world.ledger, "TST")

    def put(self, title, priority=2, created="2026-01-01T00:00:00Z", updated="", kind=Kind.ISSUE, parent="", labels=()) -> str:
        item_id = self.ledger.new_id(kind.value)
        self.ledger.write_item(Item(id=item_id, kind=kind, title=title, created=created, priority=priority, parent=parent, labels=tuple(labels)))
        self.ledger.append_event(item_id, "drafted", at=updated or created)
        return item_id

    def board(self, *arguments: str) -> dict:
        result = self.world.run("board", "--json", *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def titles(self, *arguments: str) -> list:
        data = self.board(*arguments)
        return [card["title"] for group in data["groups"] for card in group["cards"]]

    def test_each_sort_key_orders_cards_as_documented(self) -> None:
        """Prevents a flag that is accepted and then ignored."""
        self.put("b-mid", priority=2, created="2026-01-02T00:00:00Z", updated="2026-03-01T00:00:00Z")
        self.put("a-top", priority=0, created="2026-01-03T00:00:00Z", updated="2026-01-05T00:00:00Z")
        self.put("c-low", priority=4, created="2026-01-01T00:00:00Z", updated="2026-02-01T00:00:00Z")
        self.assertEqual(self.titles("--sort", "priority"), ["a-top", "b-mid", "c-low"])
        self.assertEqual(self.titles("--sort", "priority:desc"), ["c-low", "b-mid", "a-top"])
        self.assertEqual(self.titles("--sort", "title"), ["a-top", "b-mid", "c-low"])
        self.assertEqual(self.titles("--sort", "created"), ["c-low", "b-mid", "a-top"])
        self.assertEqual(self.titles("--sort", "updated:desc"), ["b-mid", "c-low", "a-top"])

    def test_an_unknown_sort_or_group_key_exits_2_naming_the_valid_keys(self) -> None:
        """Prevents a typo silently falling back to the default order."""
        sort = self.world.run("board", "--sort", "colour")
        self.assertEqual(sort.returncode, 2)
        self.assertIn("priority", sort.stderr)
        self.assertIn("updated", sort.stderr)
        group = self.world.run("board", "--group-by", "colour")
        self.assertEqual(group.returncode, 2)
        self.assertIn("epic", group.stderr)

    def test_group_by_epic_lists_each_epics_children_and_orphans_once(self) -> None:
        """Prevents orphan cards vanishing when the board is grouped."""
        epic = self.put("The epic", kind=Kind.EPIC)
        self.put("child one", parent=epic)
        self.put("child two", parent=epic)
        self.put("loose")
        data = self.board("--group-by", "epic")
        by_name = {g["name"]: [c["title"] for c in g["cards"]] for g in data["groups"]}
        self.assertEqual(sorted(by_name[epic]), ["child one", "child two"])
        self.assertEqual(sorted(by_name["(none)"]), ["The epic", "loose"])
        self.assertEqual(sum(g["count"] for g in data["groups"]), 4)

    def test_group_by_label_shows_a_multi_label_card_under_each_label(self) -> None:
        """Prevents hiding the overlap between labels."""
        self.put("both", labels=("area:a", "area:b"))
        self.put("bare")
        by_name = {g["name"]: [c["title"] for c in g["cards"]] for g in self.board("--group-by", "label")["groups"]}
        self.assertEqual(by_name, {"(none)": ["bare"], "area:a": ["both"], "area:b": ["both"]})

    def test_group_by_owner_and_parent(self) -> None:
        """Prevents owner and parent sections from disagreeing with the ledger."""
        w = self.world
        item = w.create("--kind", "issue", "--title", "mine", "--quote", QUOTE)
        w.run("start", item)
        w.create("--kind", "issue", "--title", "nobody's", "--quote", QUOTE)
        owners = {g["name"]: [c["title"] for c in g["cards"]] for g in self.board("--group-by", "owner")["groups"]}
        self.assertEqual(owners, {"(none)": ["nobody's"], "tester": ["mine"]})
        parent = self.put("p", kind=Kind.EPIC)
        self.put("kid", parent=parent)
        parents = {g["name"]: len(g["cards"]) for g in self.board("--group-by", "parent")["groups"]}
        self.assertEqual(parents[parent], 1)

    def test_filter_narrows_cards_and_counts_with_and_or_terms(self) -> None:
        """Prevents a filter that shows cards but leaves the counts stale."""
        epic = self.put("E", kind=Kind.EPIC, priority=1)
        self.put("one", priority=1, labels=("x",), parent=epic)
        self.put("two", priority=3, labels=("x",))
        self.put("three", priority=3)
        self.assertEqual(sorted(self.titles("--filter", "label:x")), ["one", "two"])
        self.assertEqual(sorted(self.titles("--filter", "label:x priority:3")), ["two"])
        self.assertEqual(sorted(self.titles("--filter", "kind:epic,task")), ["E"])
        self.assertEqual(self.titles("--filter", f"epic:{epic}"), ["one"])
        self.assertEqual(self.titles("--filter", f"parent:{epic}"), ["one"])
        data = self.board("--filter", "priority:3")
        self.assertEqual(sum(g["count"] for g in data["groups"]), 2)

    def test_filter_by_owner_and_state(self) -> None:
        """Prevents owner and state terms being accepted and ignored."""
        w = self.world
        mine = w.create("--kind", "issue", "--title", "mine", "--quote", QUOTE)
        w.create("--kind", "issue", "--title", "other", "--quote", QUOTE)
        w.run("start", mine)
        self.assertEqual(self.titles("--filter", "owner:tester"), ["mine"])
        self.assertEqual(self.titles("--filter", "state:claimed"), ["mine"])

    def test_a_malformed_filter_exits_2_instead_of_an_empty_board(self) -> None:
        """Prevents an empty board that looks like no work."""
        self.put("card")
        for bad in ("nonsense", "colour:red", "priority:"):
            result = self.world.run("board", "--filter", bad)
            self.assertEqual(result.returncode, 2, bad)
            self.assertIn("filter", result.stderr)


if __name__ == "__main__":
    unittest.main()
