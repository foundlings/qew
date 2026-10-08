"""Walks of the offline `qew board`: columns derived from the ledger alone, no forge, no network.

Adversary: a title that hides escape or direction characters, a forge outage, an empty
ledger, and counts that drift between the board and the lists.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_item import Item, Kind
from qew.ledger_store import Ledger, now
from tests.ledger_harness import QUOTE, World

STATUSES = ["Draft", "Backlog", "Ready", "Needs approval", "In progress", "In review", "Done"]
DEAD_FORGE = '[[mirror]]\nforge = "github"\nrepo = "http://127.0.0.1:9/o/r"\napi = "http://127.0.0.1:9/api/v3"\n'


class BoardWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(DEAD_FORGE)
        self.addCleanup(self.world.close)

    def board(self, *arguments: str) -> dict:
        result = self.world.run("board", "--json", *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def column_of(self, item_id: str) -> str:
        for group in self.board()["groups"]:
            if any(card["id"] == item_id for card in group["cards"]):
                return group["name"]
        raise AssertionError(f"{item_id} is on no column")

    def test_an_item_walks_the_columns_as_events_are_written_with_the_forge_down(self) -> None:
        """Prevents the board disagreeing with readiness, or a board read needing the network."""
        w = self.world
        item = w.create("--kind", "issue", "--title", "Walk")
        seen = [self.column_of(item)]
        w.approve(item)
        seen.append(self.column_of(item))
        w.run("start", item)
        seen.append(self.column_of(item))
        w.run("evidence", item, "--change", w.commit())
        w.run("complete", item)
        seen.append(self.column_of(item))
        self.assertEqual(seen, ["Draft", "Ready", "In progress", "Done"])

    def test_json_lists_all_seven_columns_with_stable_card_fields(self) -> None:
        """Prevents format drift: ids, columns and counts round trip through the JSON."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "E", "--quote", QUOTE)
        child = w.create("--kind", "issue", "--title", "C", "--parent", epic, "--priority", "1", "--label", "area:x", "--quote", QUOTE)
        data = self.board()
        self.assertEqual([g["name"] for g in data["groups"]], STATUSES)
        self.assertEqual({g["name"]: g["count"] for g in data["groups"]}["Ready"], 1)
        card = next(c for g in data["groups"] for c in g["cards"] if c["id"] == child)
        self.assertEqual(
            sorted(card),
            ["created", "epic", "id", "kind", "labels", "owner", "parent", "priority", "short", "state", "status", "title", "updated"],
        )
        self.assertEqual((card["status"], card["priority"], card["epic"], card["labels"]), ("Ready", 1, epic, ["area:x"]))
        self.assertEqual(data["version"], 1)

    def test_an_empty_ledger_renders_every_column_with_a_zero_count(self) -> None:
        """Prevents a blank screen that looks like a crash."""
        result = self.world.run("board")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in STATUSES:
            self.assertIn(f"{name} (0)", result.stdout)

    def test_text_shows_cards_and_escapes_hostile_titles(self) -> None:
        """Prevents a title from injecting terminal escapes or hiding direction overrides."""
        w = self.world
        plain = w.create("--kind", "issue", "--title", "Plain card", "--quote", QUOTE)
        w.create("--kind", "issue", "--title", "evil \x1b[31mred ‮flip", "--quote", QUOTE)
        out = w.run("board").stdout
        self.assertIn("Plain card", out)
        self.assertIn(plain[:4], out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("‮", out)

    def test_columns_prints_one_count_line_per_column_matching_the_ledger(self) -> None:
        """Prevents the compact summary drifting from the full board."""
        w = self.world
        w.create("--kind", "issue", "--title", "A", "--quote", QUOTE)
        w.create("--kind", "issue", "--title", "B")
        out = w.run("board", "columns").stdout.splitlines()
        self.assertEqual(out, [f"{name} {n}" for name, n in zip(STATUSES, [1, 0, 1, 0, 0, 0, 0])])

    def test_compact_output_is_fixed_format_and_small_for_two_hundred_cards(self) -> None:
        """Prevents agent output from outgrowing a token budget."""
        w = self.world
        ledger = Ledger(w.ledger, "TST")
        for n in range(200):
            item_id = ledger.new_id("issue")
            ledger.write_item(Item(id=item_id, kind=Kind.ISSUE, title=f"Card number {n} with a title", created=now()))
            ledger.append_event(item_id, "drafted")
        out = w.run("board", "--compact").stdout
        self.assertLess(len(out.encode()), 200 * 56)
        lines = [line for line in out.splitlines() if not line.startswith("#")]
        self.assertEqual(len(lines), 200)
        self.assertRegex(lines[0], r"^\S+ p\d \S+ .+$")


if __name__ == "__main__":
    unittest.main()
